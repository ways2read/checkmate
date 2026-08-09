"""Run Ace by DAISY (CLI) when available on PATH and parse report.json."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .models import CheckResult, Issue, Severity, Verdict
from .paths import bundled_ace_dir
from .subprocess_util import hidden_run_kwargs, run_capturing

ACE_DISPLAY_NAME = "Ace"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_ACE_PROGRESS_RE = re.compile(r"(?i)^\s*(?:info|warn|warning|error):\s*(.+)$")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _ace_progress_message(line: str) -> str | None:
    """Map an Ace console line to a short UI progress string, or None."""
    cleaned = _strip_ansi(line).strip()
    if not cleaned:
        return None
    m = _ACE_PROGRESS_RE.match(cleaned)
    if not m:
        return None
    detail = m.group(1).strip()
    if not detail:
        return None
    # Drop leading dash used for per-document lines: "- path: N issues found"
    if detail.startswith("- "):
        detail = detail[2:].strip()
    return f"Ace: {detail}"


_ACE_PATH_CACHE: Path | None | bool = False  # False = unset
_ACE_CMD_CACHE: list[str] | None | bool = False
_ACE_VERSION_CACHE: str | None | bool = False

# Prefer the Puppeteer CLI: GUI/.app launches often inherit ELECTRON_RUN_AS_NODE
# (e.g. from Electron hosts), which breaks Ace's default Electron runner.
_ACE_CLI_NAMES = (
    "ace-puppeteer",
    "ace-puppeteer.cmd",
    "ace",
    "ace.cmd",
    "ace.exe",
)


def _extra_tool_dirs() -> list[Path]:
    """Directories where npm / Homebrew often install CLIs (missing from GUI PATH)."""
    home = Path.home()
    dirs: list[Path] = [
        home / ".npm-global" / "bin",
        home / ".local" / "bin",
        home / "n" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "npm")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Programs" / "nodejs")
        # Official Windows installer defaults to Program Files (needed so
        # %APPDATA%\npm\ace*.cmd can resolve ``node`` when GUI PATH is thin).
        for root in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ):
            if root:
                dirs.append(Path(root) / "nodejs")
    # Keep only existing dirs, preserve order, drop duplicates.
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key in seen or not d.is_dir():
            continue
        seen.add(key)
        out.append(d)
    return out


def _path_with_tool_dirs() -> str:
    """PATH for locating / running Ace, including common install locations."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in [*(str(d) for d in _extra_tool_dirs()), os.environ.get("PATH", "")]:
        for part in raw.split(os.pathsep):
            part = part.strip()
            if not part or part in seen:
                continue
            seen.add(part)
            parts.append(part)
    return os.pathsep.join(parts)


# Ace's puppeteer runner uses ACE_TIMEOUT_INITIAL as puppeteer.launch() timeout
# (default 5000). Cold Chromium starts on Windows often exceed 5s (AV scan, first
# extract), which surfaces as "Timed out … waiting for the WS endpoint URL".
_ACE_TIMEOUT_INITIAL_MS = "60000"
_ACE_TIMEOUT_EXTENSION_MS = "480000"


def _bundled_node_exe(root: Path) -> Path | None:
    for candidate in (root / "node" / "node.exe", root / "node" / "bin" / "node"):
        if candidate.is_file():
            return candidate
    return None


def _bundled_ace() -> tuple[list[str], Path] | None:
    """Return (command, bundle_root) for a complete bundled Ace, else None.

    Packaged builds ship ``ace/`` with a portable Node, ``@daisy/ace-cli``
    (Puppeteer runner), and a pinned Chromium — see scripts/ace_bundle.py.
    """
    root = bundled_ace_dir()
    node = _bundled_node_exe(root)
    script = root / "node_modules" / "@daisy" / "ace-cli" / "bin" / "ace.js"
    if node is None or not script.is_file():
        return None
    return [str(node), str(script)], root


def _ace_run_env() -> dict[str, str]:
    """Environment for Ace subprocesses.

    - Extends PATH so ``#!/usr/bin/env node`` and npm-global bins work from a
      Finder-launched .app (GUI apps get a minimal PATH).
    - Clears ``ELECTRON_RUN_AS_NODE`` so the Electron Ace runner is not forced
      into Node mode (common when the GUI itself was started from Electron).
    - Raises Ace Chromium launch / page timeouts when unset (see module constants).
    - For the bundled Ace, points Puppeteer at the bundled Chromium and puts
      the bundled Node first on PATH.
    """
    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    path = _path_with_tool_dirs()
    bundled = _bundled_ace()
    if bundled is not None:
        _cmd, root = bundled
        env["PUPPETEER_CACHE_DIR"] = str(root / "puppeteer")
        node_dirs = [str(root / "node"), str(root / "node" / "bin")]
        path = os.pathsep.join([*node_dirs, path])
    env["PATH"] = path
    env.setdefault("ACE_TIMEOUT_INITIAL", _ACE_TIMEOUT_INITIAL_MS)
    env.setdefault("ACE_TIMEOUT_EXTENSION", _ACE_TIMEOUT_EXTENSION_MS)
    return env


def find_ace() -> Path | None:
    """Return a user-installed Ace CLI executable from PATH, if any.

    Prefers ``ace-puppeteer`` when present. Searches PATH plus common npm /
    Homebrew install directories so packaged macOS apps can find a user install.
    """
    global _ACE_PATH_CACHE
    if _ACE_PATH_CACHE is not False:
        return _ACE_PATH_CACHE if isinstance(_ACE_PATH_CACHE, Path) else None

    search_path = _path_with_tool_dirs()
    for name in _ACE_CLI_NAMES:
        found = shutil.which(name, path=search_path)
        if found:
            path = Path(found)
            _ACE_PATH_CACHE = path
            return path
    _ACE_PATH_CACHE = None
    return None


def ace_command() -> list[str] | None:
    """Argv prefix for running Ace, or None when Ace is unavailable.

    Prefers the bundled copy (deterministic Node + Chromium), then a
    user-installed CLI found on PATH.
    """
    global _ACE_CMD_CACHE
    if _ACE_CMD_CACHE is not False:
        return list(_ACE_CMD_CACHE) if isinstance(_ACE_CMD_CACHE, list) else None

    bundled = _bundled_ace()
    if bundled is not None:
        _ACE_CMD_CACHE = bundled[0]
        return list(_ACE_CMD_CACHE)
    ace = find_ace()
    if ace is not None:
        _ACE_CMD_CACHE = [str(ace)]
        return list(_ACE_CMD_CACHE)
    _ACE_CMD_CACHE = None
    return None


def ace_uses_bundled_copy() -> bool:
    return _bundled_ace() is not None


def probe_ace() -> str | None:
    """Return Ace version string, or None if Ace is not available."""
    global _ACE_VERSION_CACHE
    if _ACE_VERSION_CACHE is not False:
        return _ACE_VERSION_CACHE if isinstance(_ACE_VERSION_CACHE, str) else None

    ace = ace_command()
    if ace is None:
        _ACE_VERSION_CACHE = None
        return None
    try:
        proc = subprocess.run(
            [*ace, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=_ace_run_env(),
            **hidden_run_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        _ACE_VERSION_CACHE = None
        return None
    text = (proc.stdout or proc.stderr or "").strip()
    # Prefer the last non-empty line (Ace prints just "1.4.6").
    version = ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            version = line
    # Require success: failed runs often print batch/node errors on stderr,
    # and the last line must not be treated as a version string.
    if proc.returncode not in (0, None) or not version:
        _ACE_VERSION_CACHE = None
        return None
    _ACE_VERSION_CACHE = version
    return version


def clear_ace_cache() -> None:
    """Reset command/version caches (tests / after install)."""
    global _ACE_PATH_CACHE, _ACE_CMD_CACHE, _ACE_VERSION_CACHE
    _ACE_PATH_CACHE = False
    _ACE_CMD_CACHE = False
    _ACE_VERSION_CACHE = False


def _severity_from_impact(impact: str | None) -> Severity:
    key = (impact or "").strip().lower()
    if key in {"critical", "serious"}:
        return Severity.ERROR
    if key == "moderate":
        return Severity.WARNING
    if key == "minor":
        return Severity.INFO
    return Severity.WARNING


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


def _first_str_list(value) -> str:
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _compact_html_snippet(html: str, *, limit: int = 160) -> str:
    text = " ".join(html.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _location_for_assertion(doc_url: str, assertion: dict) -> str:
    """Build a human location: file · CSS · snippet (Ace has no line/column)."""
    parts: list[str] = []
    if doc_url:
        parts.append(doc_url)
    result = assertion.get("earl:result") or {}
    if not isinstance(result, dict):
        return " · ".join(parts)
    pointer = result.get("earl:pointer") or {}
    if isinstance(pointer, dict):
        css = _first_str_list(pointer.get("css"))
        if css:
            parts.append(css)
    html = result.get("html")
    if isinstance(html, str) and html.strip():
        parts.append(_compact_html_snippet(html, limit=120))
    return " · ".join(parts)


def _issues_from_ace_report(data: dict) -> list[Issue]:
    issues: list[Issue] = []
    for doc in data.get("assertions") or []:
        if not isinstance(doc, dict):
            continue
        subject = doc.get("earl:testSubject") or {}
        doc_url = ""
        if isinstance(subject, dict):
            doc_url = str(subject.get("url") or "").strip()
        for assertion in doc.get("assertions") or []:
            if not isinstance(assertion, dict):
                continue
            result = assertion.get("earl:result") or {}
            if not isinstance(result, dict):
                continue
            outcome = str(result.get("earl:outcome") or "").strip().lower()
            if outcome != "fail":
                continue
            test = assertion.get("earl:test") or {}
            if not isinstance(test, dict):
                test = {}
            code = str(test.get("dct:title") or "ace").strip() or "ace"
            message = str(result.get("dct:description") or "").strip()
            help_url = ""
            help_title = ""
            help_text = ""
            help_block = test.get("help") or {}
            if isinstance(help_block, dict):
                help_text = str(help_block.get("dct:description") or "").strip()
                help_url = str(help_block.get("url") or "").strip()
                help_title = str(help_block.get("dct:title") or "").strip()
            if not message:
                message = str(test.get("dct:description") or code).strip()
            impact = str(test.get("earl:impact") or "").strip()
            issues.append(
                Issue(
                    severity=_severity_from_impact(impact),
                    code=code,
                    message=message,
                    location=_location_for_assertion(doc_url, assertion),
                    source=ACE_DISPLAY_NAME,
                    help_url=help_url,
                    help_title=help_title,
                    help_text=help_text,
                    impact=impact,
                )
            )
    return issues


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _parse_error_message(stdout: str, stderr: str) -> str:
    combined = _strip_ansi("\n".join(p for p in (stdout, stderr) if p))
    lower_all = combined.lower()
    # npm Ace wrappers call ``node``; thin GUI PATHs often miss Program Files.
    if (
        "'node' is not recognized" in lower_all
        or '"node" is not recognized' in lower_all
        or "node: command not found" in lower_all
        or "node: not found" in lower_all
    ):
        return (
            "Ace could not produce a report: Node.js was not found on PATH "
            "(required by the Ace CLI)."
        )
    if "waiting for the ws endpoint url" in lower_all:
        return (
            "Ace processing error: Chromium took too long to start "
            "(WS endpoint timeout). Try again; if it persists, reinstall Ace's "
            "browser with: npx puppeteer browsers install chrome"
        )
    if "could not find chrome" in lower_all:
        return (
            "Ace processing error: Chromium for Puppeteer is not installed. "
            "Run: npx puppeteer browsers install chrome"
        )
    # Prefer Ace's "Ace processing error: …" / "Failed to parse EPUB" lines.
    for line in reversed(combined.splitlines()):
        stripped = line.strip()
        lower = stripped.lower()
        if "ace processing error:" in lower:
            idx = lower.index("ace processing error:")
            return stripped[idx:].strip()
        if "failed to parse epub" in lower:
            return stripped
    for line in reversed(combined.splitlines()):
        stripped = line.strip()
        if stripped.lower().startswith("error:"):
            return stripped.split(":", 1)[-1].strip() or stripped
    return "Ace could not produce a report."


def run_ace_check(
    target: Path,
    *,
    progress=None,
) -> CheckResult | None:
    """Run Ace on ``target``.

    Returns ``None`` when Ace is unavailable (caller should skip silently).
    Otherwise returns a CheckResult (pass/fail or tool error).
    """
    target = target.expanduser().resolve()
    ace = ace_command()
    if ace is None:
        return None

    version = probe_ace() or ""
    checked_at = datetime.now().astimezone()
    if progress:
        progress("Checking with Ace…")

    with tempfile.TemporaryDirectory(prefix="ebraille-ace-") as tmp:
        outdir = Path(tmp) / "report"
        outdir.mkdir(parents=True, exist_ok=True)
        # Do not use --silent: Ace suppresses parse/processing errors that we
        # need for the merged issue list and raw log.
        cmd = [
            *ace,
            "--outdir",
            str(outdir),
            "--force",
            str(target),
        ]
        last_post = [0.0]

        def on_line(line: str) -> None:
            if not progress:
                return
            msg = _ace_progress_message(line)
            if not msg:
                return
            now = time.monotonic()
            # Throttle UI posts; always allow the first.
            if last_post[0] and (now - last_post[0]) < 0.35:
                return
            last_post[0] = now
            try:
                progress(msg, announce=False)
            except TypeError:
                progress(msg)

        try:
            proc = run_capturing(
                cmd,
                timeout=600,
                env=_ace_run_env(),
                on_line=on_line if progress else None,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                verdict=Verdict.ERROR,
                error_message="Ace timed out after 10 minutes.",
                command=cmd,
                tool_name=ACE_DISPLAY_NAME,
                tool_version=version,
                checked_at=checked_at,
                target_path=str(target),
                issues=[
                    Issue(
                        severity=Severity.ERROR,
                        code="ace-timeout",
                        message="Ace timed out after 10 minutes.",
                        source=ACE_DISPLAY_NAME,
                    )
                ],
                errors=1,
            )
        except OSError as exc:
            return CheckResult(
                verdict=Verdict.ERROR,
                error_message=f"Failed to start Ace: {exc}",
                command=cmd,
                tool_name=ACE_DISPLAY_NAME,
                tool_version=version,
                checked_at=checked_at,
                target_path=str(target),
                issues=[
                    Issue(
                        severity=Severity.ERROR,
                        code="ace-start",
                        message=f"Failed to start Ace: {exc}",
                        source=ACE_DISPLAY_NAME,
                    )
                ],
                errors=1,
            )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        raw_log = _strip_ansi(
            "\n".join(p for p in (stdout, stderr) if p)
        ).strip()
        report_path = outdir / "report.json"

        if report_path.is_file():
            try:
                data = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                msg = f"Ace could not parse EPUB: could not read report ({exc})"
                return CheckResult(
                    verdict=Verdict.ERROR,
                    error_message=msg,
                    raw_log=raw_log,
                    exit_code=proc.returncode,
                    command=cmd,
                    tool_name=ACE_DISPLAY_NAME,
                    tool_version=version,
                    checked_at=checked_at,
                    target_path=str(target),
                    issues=[
                        Issue(
                            severity=Severity.ERROR,
                            code="ace-report",
                            message=msg,
                            source=ACE_DISPLAY_NAME,
                        )
                    ],
                    errors=1,
                )

            if not isinstance(data, dict):
                msg = "Ace could not parse EPUB: report.json was not an object."
                return CheckResult(
                    verdict=Verdict.ERROR,
                    error_message=msg,
                    raw_log=raw_log,
                    exit_code=proc.returncode,
                    command=cmd,
                    tool_name=ACE_DISPLAY_NAME,
                    tool_version=version,
                    checked_at=checked_at,
                    target_path=str(target),
                    issues=[
                        Issue(
                            severity=Severity.ERROR,
                            code="ace-report",
                            message=msg,
                            source=ACE_DISPLAY_NAME,
                        )
                    ],
                    errors=1,
                )

            issues = _issues_from_ace_report(data)
            counts = _counts_from_issues(issues)
            # Prefer report-level outcome when it fails with no listed assertions.
            report_outcome = ""
            earl_result = data.get("earl:result") or {}
            if isinstance(earl_result, dict):
                report_outcome = str(
                    earl_result.get("earl:outcome") or ""
                ).strip().lower()
            verdict = _verdict_from_counts(counts)
            if report_outcome == "fail" and verdict == Verdict.PASSED:
                verdict = Verdict.FAILED
            if report_outcome == "pass" and verdict == Verdict.PASSED:
                pass

            ace_rev = version
            asserted = data.get("earl:assertedBy") or {}
            if isinstance(asserted, dict):
                release = asserted.get("doap:release") or {}
                if isinstance(release, dict):
                    rev = str(release.get("doap:revision") or "").strip()
                    if rev:
                        ace_rev = rev

            return CheckResult(
                verdict=verdict,
                fatals=counts["fatals"],
                errors=counts["errors"],
                warnings=counts["warnings"],
                infos=counts["infos"],
                usages=counts["usages"],
                issues=issues,
                raw_log=raw_log,
                exit_code=proc.returncode,
                command=cmd,
                tool_name=ACE_DISPLAY_NAME,
                tool_version=ace_rev,
                checked_at=checked_at,
                target_path=str(target),
            )

        # No report.json — typically a parse/processing failure.
        detail = _parse_error_message(stdout, stderr)
        msg = (
            detail
            if detail.lower().startswith("ace")
            else f"Ace could not parse EPUB: {detail}"
        )
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=msg,
            raw_log=raw_log,
            exit_code=proc.returncode,
            command=cmd,
            tool_name=ACE_DISPLAY_NAME,
            tool_version=version,
            checked_at=checked_at,
            target_path=str(target),
            issues=[
                Issue(
                    severity=Severity.ERROR,
                    code="ace-parse",
                    message=msg,
                    source=ACE_DISPLAY_NAME,
                )
            ],
            errors=1,
        )
