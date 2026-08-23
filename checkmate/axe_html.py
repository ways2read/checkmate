"""Run axe-core against HTML pages using Ace's bundled Node + Puppeteer Chrome."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .ace_check import (
    _ace_run_env,
    _bundled_ace,
    _bundled_node_exe,
    ace_package_dir,
    ruleset_label_from_tags,
    _severity_from_impact,
)
from .models import CheckResult, Issue, Severity, Verdict
from .paths import application_dir, bundled_ace_dir
from .subprocess_util import elapsed_progress_message, run_capturing

AXE_HTML_DISPLAY_NAME = "axe"
_PROGRESS_RE = re.compile(r"^PROGRESS\s+(\d+)\s+(\d+)\s+(.*)$")
_RUNNER_TIMEOUT_S = 600.0

ProgressCallback = Callable[..., None]


def _emit_progress(progress, message: str, *, announce: bool = True) -> None:
    if not progress:
        return
    try:
        progress(message, announce=announce)
    except TypeError:
        progress(message)


def axe_html_runner_path() -> Path | None:
    """Locate ``axe_html_runner.js`` next to the app or in the source tree."""
    candidates = [
        Path(__file__).resolve().parent / "axe_html_runner.js",
        application_dir() / "scripts" / "axe_html_runner.js",
        Path(__file__).resolve().parents[1] / "scripts" / "axe_html_runner.js",
    ]
    if getattr(sys, "_MEIPASS", None):
        meipass = Path(sys._MEIPASS)
        candidates.insert(0, meipass / "scripts" / "axe_html_runner.js")
        candidates.insert(0, meipass / "axe_html_runner.js")
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_system_chrome() -> Path | None:
    """Installed Google Chrome / Chromium, if present."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        for root in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ):
            if root:
                candidates.append(
                    Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
                )
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(
                Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"
            )
    elif sys.platform == "darwin":
        candidates.append(
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        )
        candidates.append(
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium")
        )
    else:
        for name in (
            "google-chrome-stable",
            "google-chrome",
            "chromium-browser",
            "chromium",
        ):
            candidates.append(Path("/usr/bin") / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_puppeteer_chrome() -> Path | None:
    """Chrome for Testing from Ace's Puppeteer cache, else installed Chrome."""
    roots: list[Path] = []
    env = os.environ.get("PUPPETEER_CACHE_DIR")
    if env:
        roots.append(Path(env))
    ace_env = _ace_run_env().get("PUPPETEER_CACHE_DIR")
    if ace_env:
        roots.append(Path(ace_env))
    ace = bundled_ace_dir()
    if ace.is_dir():
        roots.append(ace / "puppeteer")
    pkg = ace_package_dir()
    if pkg is not None:
        roots.append(pkg / "puppeteer")
        roots.append(pkg / "node_modules" / "puppeteer")
    home = Path.home()
    roots.append(home / ".cache" / "puppeteer")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "puppeteer")
        roots.append(Path(local) / "chrome-for-testing")
    if sys.platform == "win32":
        names = ("chrome.exe",)
    else:
        names = (
            "chrome",
            "google-chrome",
            "google-chrome-stable",
            "Google Chrome for Testing",
            "Chromium",
        )
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen or not root.is_dir():
            continue
        seen.add(key)
        try:
            for name in names:
                for candidate in root.rglob(name):
                    if candidate.is_file():
                        return candidate
        except OSError:
            continue
    return find_system_chrome()


def html_axe_available() -> bool:
    """True when the runner script and an Ace install with Node modules exist."""
    if axe_html_runner_path() is None:
        return False
    return _node_and_ace_root() is not None


def _node_and_ace_root() -> tuple[list[str], Path] | None:
    """Return ``(node_argv, ace_package_dir)`` for the HTML axe runner.

    *ace_package_dir* must be the folder Node can require ``puppeteer`` from
    (bundled ``ace/``, or ``node_modules/@daisy/ace`` next to a user CLI).
    System Node's install dir is not that folder.
    """
    bundled = _bundled_ace()
    if bundled is not None:
        cmd, root = bundled
        node = cmd[0] if cmd else ""
        if node:
            return [node], root
    root = bundled_ace_dir()
    node_exe = _bundled_node_exe(root) if root.is_dir() else None
    pkg = ace_package_dir()
    if node_exe is not None and (pkg is not None or _is_usable_ace_root(root)):
        return [str(node_exe)], pkg or root
    from shutil import which

    found = which("node")
    if found and pkg is not None:
        return [found], pkg
    if found and root.is_dir() and _is_usable_ace_root(root):
        return [found], root
    return None


def _is_usable_ace_root(path: Path) -> bool:
    nm = path / "node_modules"
    return nm.is_dir() and (
        (nm / "puppeteer").is_dir()
        or (nm / "puppeteer-core").is_dir()
        or (nm / "@daisy" / "ace-cli").is_dir()
        or (nm / "@daisy" / "axe-core-for-ace").is_dir()
        or (nm / "@daisy" / "ace").is_dir()
    )


def _snippet_from_node(node: dict[str, Any]) -> str:
    html = str(node.get("html") or "").strip()
    if html:
        compact = re.sub(r"\s+", " ", html)
        return compact[:240]
    return ""


def _target_selector(node: dict[str, Any]) -> str:
    target = node.get("target")
    if isinstance(target, list) and target:
        first = target[0]
        if isinstance(first, str):
            return first
        if isinstance(first, list) and first:
            return str(first[0])
    return ""


def issues_from_axe_results(
    axe: dict[str, Any] | None,
    *,
    page_url: str = "",
    include_incomplete: bool = True,
) -> list[Issue]:
    """Map native axe JSON (violations / incomplete) to ``Issue`` rows."""
    if not isinstance(axe, dict):
        return []
    issues: list[Issue] = []

    def _add(entry: dict[str, Any], *, incomplete: bool = False) -> None:
        rule_id = str(entry.get("id") or "axe").strip() or "axe"
        help_text = str(entry.get("help") or entry.get("description") or "").strip()
        help_url = str(entry.get("helpUrl") or "").strip()
        impact = str(entry.get("impact") or "").strip()
        tags = entry.get("tags")
        ruleset = ruleset_label_from_tags(tags)
        nodes = entry.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            nodes = [{}]
        for node in nodes:
            if not isinstance(node, dict):
                node = {}
            selector = _target_selector(node)
            location = " · ".join(p for p in (page_url, selector) if p)
            snippet = _snippet_from_node(node)
            failure = ""
            any_list = node.get("any") or node.get("all") or node.get("none")
            if isinstance(any_list, list) and any_list:
                first = any_list[0]
                if isinstance(first, dict):
                    failure = str(first.get("message") or "").strip()
            message = help_text or rule_id
            if incomplete:
                message = f"Needs review: {message}"
            if failure and failure not in message:
                message = f"{message} ({failure})"
            extra_help = snippet
            issues.append(
                Issue(
                    severity=(
                        Severity.INFO if incomplete else _severity_from_impact(impact)
                    ),
                    code=rule_id,
                    message=message,
                    location=location,
                    source=AXE_HTML_DISPLAY_NAME,
                    help_url=help_url,
                    help_title=help_text,
                    help_text=extra_help,
                    impact="" if incomplete else impact,
                    ruleset=ruleset,
                )
            )

    violations = axe.get("violations")
    if isinstance(violations, list):
        for item in violations:
            if isinstance(item, dict):
                _add(item)
    if include_incomplete:
        incomplete = axe.get("incomplete")
        if isinstance(incomplete, list):
            for item in incomplete:
                if isinstance(item, dict):
                    _add(item, incomplete=True)
    return issues


def _counts_from_issues(issues: list[Issue]) -> dict[str, int]:
    counts = {
        "fatals": 0,
        "errors": 0,
        "warnings": 0,
        "infos": 0,
        "usages": 0,
    }
    for issue in issues:
        if issue.severity == Severity.FATAL:
            counts["fatals"] += 1
        elif issue.severity == Severity.ERROR:
            counts["errors"] += 1
        elif issue.severity == Severity.WARNING:
            counts["warnings"] += 1
        elif issue.severity == Severity.INFO:
            counts["infos"] += 1
        elif issue.severity == Severity.USAGE:
            counts["usages"] += 1
    return counts


def _verdict_from_counts(counts: dict[str, int]) -> Verdict:
    if counts["fatals"] or counts["errors"]:
        return Verdict.FAILED
    if counts["warnings"]:
        return Verdict.PASSED_WITH_WARNINGS
    return Verdict.PASSED


def _https_to_http(url: str) -> str:
    text = (url or "").strip()
    if text.lower().startswith("https://"):
        return "http://" + text[8:]
    return ""


def _friendly_navigation_error(error: str, url: str) -> str:
    """Turn Chromium net:: errors into a short explanation with a next step."""
    text = (error or "").strip()
    lower = text.lower()
    http_alt = _https_to_http(url)
    if "err_ssl_protocol_error" in lower or "err_ssl_version" in lower:
        hint = f" Try {http_alt} — this host may not support HTTPS." if http_alt else ""
        return (
            f"HTTPS failed for {url or 'this page'} "
            f"(the TLS handshake did not complete).{hint}"
        )
    if "err_cert" in lower or "err_ssl_pinned" in lower:
        return (
            f"The HTTPS certificate for {url or 'this page'} was rejected "
            f"({text})."
        )
    if "err_name_not_resolved" in lower:
        return f"Could not look up the host for {url or 'this page'}."
    if "err_connection_refused" in lower:
        return f"The server refused the connection for {url or 'this page'}."
    if "err_connection_timed_out" in lower or (
        "timeout" in lower and "net::" in lower
    ):
        return f"Timed out loading {url or 'this page'}."
    if "err_empty_response" in lower:
        return f"The server returned an empty response for {url or 'this page'}."
    return text


def parse_axe_runner_output(data: dict[str, Any]) -> tuple[list[Issue], list[dict[str, Any]]]:
    """Return (issues, image records) from the Node runner JSON."""
    issues: list[Issue] = []
    images: list[dict[str, Any]] = []
    pages = data.get("pages")
    if not isinstance(pages, list):
        return issues, images
    for page in pages:
        if not isinstance(page, dict):
            continue
        url = str(page.get("url") or "")
        error = str(page.get("error") or "").strip()
        if error:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    code="axe-error",
                    message=_friendly_navigation_error(error, url),
                    location=url,
                    source=AXE_HTML_DISPLAY_NAME,
                )
            )
        issues.extend(issues_from_axe_results(page.get("axe"), page_url=url))
        raw_images = page.get("images")
        if isinstance(raw_images, list):
            for rec in raw_images:
                if isinstance(rec, dict):
                    if not rec.get("pageUrl"):
                        rec = dict(rec)
                        rec["pageUrl"] = url
                    images.append(rec)
    return issues, images


def run_axe_on_urls(
    urls: list[str],
    *,
    progress: ProgressCallback | None = None,
    images_only: bool = False,
) -> tuple[CheckResult, list[dict[str, Any]]]:
    """Run the Puppeteer axe runner. Returns (result, image records)."""
    pages = [u for u in urls if u]
    empty = CheckResult(
        verdict=Verdict.ERROR,
        error_message="No HTML pages to check.",
        tool_name=AXE_HTML_DISPLAY_NAME,
    )
    if not pages:
        return empty, []

    runner = axe_html_runner_path()
    node_info = _node_and_ace_root()
    chrome = find_puppeteer_chrome()
    if runner is None or node_info is None:
        return (
            CheckResult(
                verdict=Verdict.ERROR,
                error_message=(
                    "axe could not find Ace’s Puppeteer modules. CheckMate uses "
                    "the same Ace install as EPUB checks (ace-puppeteer), not "
                    "system Node alone. Reinstall Ace, or use a packaged "
                    "CheckMate build that bundles Ace."
                ),
                tool_name=AXE_HTML_DISPLAY_NAME,
            ),
            [],
        )

    node_cmd, ace_root = node_info
    env = _ace_run_env()
    env["ACE_ROOT"] = str(ace_root)
    cache = env.get("PUPPETEER_CACHE_DIR")
    if cache and not Path(cache).is_dir():
        env.pop("PUPPETEER_CACHE_DIR", None)
    require_roots: list[str] = [str(ace_root)]
    node_modules = ace_root / "node_modules"
    if node_modules.is_dir():
        require_roots.append(str(node_modules))
        daisy = node_modules / "@daisy"
        for name in ("ace", "ace-cli", "ace-core", "axe-core-for-ace"):
            nested = daisy / name
            if nested.is_dir():
                require_roots.append(str(nested))
        existing = env.get("NODE_PATH", "")
        env["NODE_PATH"] = (
            str(node_modules)
            if not existing
            else os.pathsep.join([str(node_modules), existing])
        )
    env["ACE_REQUIRE_ROOTS"] = os.pathsep.join(require_roots)

    started = datetime.now().astimezone()
    with tempfile.TemporaryDirectory(prefix="checkmate-axe-") as tmp:
        tmp_path = Path(tmp)
        pages_file = tmp_path / "pages.json"
        out_file = tmp_path / "out.json"
        pages_file.write_text(json.dumps(pages), encoding="utf-8")
        cmd = [
            *node_cmd,
            str(runner),
            "--pages-file",
            str(pages_file),
            "--out",
            str(out_file),
            "--load-delay-ms",
            "1000",
        ]
        if chrome is not None and chrome.is_file():
            cmd.extend(["--chrome", str(chrome)])
        if images_only:
            cmd.append("--images-only")

        def on_line(line: str) -> None:
            match = _PROGRESS_RE.match(line.strip())
            if not match:
                return
            current, total, extra = (
                match.group(1),
                match.group(2),
                (match.group(3) or "").strip(),
            )
            if extra and not extra.lower().startswith(("http://", "https://")):
                _emit_progress(progress, extra)
                return
            _emit_progress(progress, f"Checking page {current} of {total}…")

        axe_label = "Running axe…"

        try:
            proc = run_capturing(
                cmd,
                timeout=_RUNNER_TIMEOUT_S,
                env=env,
                cwd=str(ace_root) if ace_root.is_dir() else None,
                on_line=on_line,
                heartbeat=lambda elapsed: _emit_progress(
                    progress,
                    elapsed_progress_message(axe_label, elapsed) or axe_label,
                    announce=False,
                ),
                heartbeat_interval=1.0,
            )
        except subprocess.TimeoutExpired:
            return (
                CheckResult(
                    verdict=Verdict.ERROR,
                    error_message="axe timed out after 10 minutes.",
                    tool_name=AXE_HTML_DISPLAY_NAME,
                    checked_at=started,
                ),
                [],
            )
        except OSError as exc:
            return (
                CheckResult(
                    verdict=Verdict.ERROR,
                    error_message=f"Failed to start axe: {exc}",
                    tool_name=AXE_HTML_DISPLAY_NAME,
                    checked_at=started,
                ),
                [],
            )

        if not out_file.is_file():
            err = (proc.stderr or proc.stdout or "").strip()[:800]
            return (
                CheckResult(
                    verdict=Verdict.ERROR,
                    error_message=err or "axe produced no output.",
                    tool_name=AXE_HTML_DISPLAY_NAME,
                    raw_log=(proc.stderr or "") + "\n" + (proc.stdout or ""),
                    exit_code=proc.returncode,
                    checked_at=started,
                ),
                [],
            )
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return (
                CheckResult(
                    verdict=Verdict.ERROR,
                    error_message=f"Could not parse axe JSON: {exc}",
                    tool_name=AXE_HTML_DISPLAY_NAME,
                    raw_log=out_file.read_text(encoding="utf-8", errors="replace")[:4000],
                    exit_code=proc.returncode,
                    checked_at=started,
                ),
                [],
            )

    if not isinstance(data, dict):
        return (
            CheckResult(
                verdict=Verdict.ERROR,
                error_message="axe JSON was not an object.",
                tool_name=AXE_HTML_DISPLAY_NAME,
                checked_at=started,
            ),
            [],
        )

    issues, images = parse_axe_runner_output(data)
    counts = _counts_from_issues(issues)
    log_bits = []
    if proc.stderr:
        log_bits.append(proc.stderr.strip())
    result = CheckResult(
        verdict=_verdict_from_counts(counts),
        fatals=counts["fatals"],
        errors=counts["errors"],
        warnings=counts["warnings"],
        infos=counts["infos"],
        usages=counts["usages"],
        issues=issues,
        raw_log="\n".join(log_bits),
        exit_code=proc.returncode,
        tool_name=AXE_HTML_DISPLAY_NAME,
        checked_at=started,
        html_images=images,
    )
    return result, images
