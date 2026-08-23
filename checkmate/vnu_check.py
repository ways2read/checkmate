"""Run the W3C Nu HTML Checker (vnu.jar) and map JSON messages to issues."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

from .java_util import cached_java, detect_java
from .models import CheckResult, Issue, Severity, Verdict
from .paths import (
    bundled_vnu_version_file,
    find_vnu_jar,
    vnu_dir,
    vnu_uses_bundled_copy,
    vnu_version_file,
)
from .subprocess_util import elapsed_progress_message, run_capturing

VNU_DISPLAY_NAME = "Nu HTML Checker"
VNU_JAR_URL = (
    "https://github.com/validator/validator/releases/latest/download/vnu.jar"
)
_VNU_TIMEOUT_S = 120.0
_VERSION_RE = re.compile(r"(\d{2,4}[.\-]\d{1,2}[.\-]\d{1,2}|\d+\.\d+(?:\.\d+)?)")

ProgressCallback = Callable[..., None]


def _emit_progress(progress, message: str, *, announce: bool = True) -> None:
    if not progress:
        return
    try:
        progress(message, announce=announce)
    except TypeError:
        progress(message)


def vnu_version_text(jar: Path | None = None) -> str:
    """Best-effort Nu HTML Checker version from the version file or the jar."""
    for path in (vnu_version_file(), bundled_vnu_version_file()):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    jar = jar or find_vnu_jar()
    if jar is None:
        return ""
    java = cached_java() or detect_java()
    if java is None:
        return ""
    try:
        proc = run_capturing(
            [java.path, "-jar", str(jar), "--version"],
            timeout=30,
        )
    except (OSError, TimeoutError):
        return ""
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    match = _VERSION_RE.search(combined)
    return match.group(1) if match else combined.strip().splitlines()[0] if combined.strip() else ""


def vnu_available() -> bool:
    return find_vnu_jar() is not None


def ensure_vnu_jar(progress: ProgressCallback | None = None) -> Path:
    """Return vnu.jar, downloading into app-data when neither copy exists."""
    existing = find_vnu_jar()
    if existing is not None:
        return existing
    _emit_progress(
        progress,
        f"{VNU_DISPLAY_NAME} not found. Downloading latest release…",
    )
    return download_vnu_jar(vnu_dir(), progress=progress)


def download_vnu_jar(
    target_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    url: str = VNU_JAR_URL,
    timeout: float = 180.0,
) -> Path:
    """Download ``vnu.jar`` into *target_dir* and write ``bundled_version.txt``."""
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / "vnu.jar"
    _emit_progress(progress, f"Downloading {VNU_DISPLAY_NAME}…")
    response = requests.get(url, timeout=timeout, stream=True)
    response.raise_for_status()
    data = bytearray()
    for chunk in response.iter_content(chunk_size=1024 * 256):
        if chunk:
            data.extend(chunk)
    dest.write_bytes(bytes(data))
    version = _probe_vnu_version(dest) or "latest"
    version_path = target_dir / "bundled_version.txt"
    try:
        version_path.write_text(version + "\n", encoding="utf-8")
    except OSError:
        pass
    installed = target_dir / "installed_version.txt"
    try:
        installed.write_text(version + "\n", encoding="utf-8")
    except OSError:
        pass
    _emit_progress(progress, f"Bundled {VNU_DISPLAY_NAME} {version}")
    return dest


def _probe_vnu_version(jar: Path) -> str:
    java = cached_java() or detect_java()
    if java is None:
        return ""
    try:
        proc = run_capturing(
            [java.path, "-jar", str(jar), "--version"],
            timeout=30,
        )
    except (OSError, TimeoutError):
        return ""
    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    match = _VERSION_RE.search(combined)
    return match.group(1) if match else ""


def severity_from_vnu_message(msg: dict) -> Severity:
    """Map a Nu ``messages[]`` item to CheckMate severity."""
    typ = str(msg.get("type") or "").strip().lower()
    sub = str(msg.get("subtype") or "").strip().lower()
    if typ in {"error", "non-document-error"}:
        if sub == "fatal":
            return Severity.FATAL
        return Severity.ERROR
    if typ == "info" and sub == "warning":
        return Severity.WARNING
    if typ == "info":
        return Severity.INFO
    return Severity.WARNING


def _shorten_vnu_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return text
    if parsed.scheme == "file":
        path = unquote(parsed.path or "")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return path.replace("/", "\\") if "\\" in path or len(path) > 1 and path[1] == ":" else path
    return text


def location_from_vnu_message(msg: dict) -> str:
    url = _shorten_vnu_url(str(msg.get("url") or ""))
    line = msg.get("lastLine")
    col = msg.get("lastColumn")
    loc = ""
    if line is not None:
        loc = f"{line}:{col}" if col is not None else str(line)
    if url and loc:
        return f"{url}:{loc}"
    return url or loc


def issues_from_vnu_json(data: dict | list | None, *, default_url: str = "") -> list[Issue]:
    """Map Nu JSON ``messages[]`` (or a bare list) to ``Issue`` rows."""
    messages: list = []
    if isinstance(data, dict):
        raw = data.get("messages")
        if isinstance(raw, list):
            messages = raw
    elif isinstance(data, list):
        messages = data
    issues: list[Issue] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()
        if not message:
            continue
        typ = str(item.get("type") or "info").strip().lower()
        sub = str(item.get("subtype") or "").strip().lower()
        code = sub or typ or "vnu"
        location = location_from_vnu_message(item)
        if not location and default_url:
            location = default_url
        extract = str(item.get("extract") or "").strip()
        help_text = extract
        issues.append(
            Issue(
                severity=severity_from_vnu_message(item),
                code=code,
                message=message,
                location=location,
                source=VNU_DISPLAY_NAME,
                help_text=help_text,
            )
        )
    return issues


def _extract_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


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


def run_vnu_on_urls(
    urls: list[str],
    *,
    jar: Path | None = None,
    progress: ProgressCallback | None = None,
) -> CheckResult:
    """Check each URL with vnu.jar and merge messages into one result."""
    java = cached_java() or detect_java()
    if java is None:
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=(
                "Java was not found. Install a Java Runtime (JRE 17 or newer), "
                "or use a packaged build that includes a bundled runtime."
            ),
            tool_name=VNU_DISPLAY_NAME,
        )
    try:
        jar = jar or ensure_vnu_jar(progress=progress)
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=f"Could not install {VNU_DISPLAY_NAME}: {exc}",
            tool_name=VNU_DISPLAY_NAME,
        )

    pages = [u for u in urls if u]
    if not pages:
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message="No HTML pages to check.",
            tool_name=VNU_DISPLAY_NAME,
        )

    version = vnu_version_text(jar)
    all_issues: list[Issue] = []
    logs: list[str] = []
    last_code = 0
    started = datetime.now().astimezone()
    total = len(pages)
    for index, url in enumerate(pages, start=1):
        label = f"Checking page {index} of {total}…"
        _emit_progress(progress, label)
        cmd = [
            java.path,
            "-jar",
            str(jar),
            "--format",
            "json",
            "--stdout",
            url,
        ]
        try:
            proc = run_capturing(
                cmd,
                timeout=_VNU_TIMEOUT_S,
                heartbeat=lambda elapsed, lbl=label: _emit_progress(
                    progress,
                    elapsed_progress_message(lbl, elapsed) or lbl,
                    announce=False,
                ),
                heartbeat_interval=1.0,
            )
        except subprocess.TimeoutExpired:
            all_issues.append(
                Issue(
                    severity=Severity.ERROR,
                    code="vnu-timeout",
                    message=f"{VNU_DISPLAY_NAME} timed out on {url}",
                    location=url,
                    source=VNU_DISPLAY_NAME,
                )
            )
            logs.append(f"{url}: timed out")
            last_code = 1
            continue
        except OSError as exc:
            return CheckResult(
                verdict=Verdict.ERROR,
                error_message=f"Failed to start Java: {exc}",
                tool_name=VNU_DISPLAY_NAME,
                tool_version=version,
                checked_at=started,
            )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        last_code = proc.returncode if proc.returncode is not None else last_code
        data = _extract_json_object(stdout) or _extract_json_object(stderr)
        if data is None:
            if proc.returncode not in (0, 1):
                all_issues.append(
                    Issue(
                        severity=Severity.ERROR,
                        code="vnu-error",
                        message=(stderr or stdout or "Nu HTML Checker produced no JSON.").strip()[:500],
                        location=url,
                        source=VNU_DISPLAY_NAME,
                    )
                )
            logs.append(f"{url}: no JSON (exit {proc.returncode})")
            continue
        page_issues = issues_from_vnu_json(data, default_url=url)
        all_issues.extend(page_issues)
        logs.append(f"{url}: {len(page_issues)} message(s)")

    counts = _counts_from_issues(all_issues)
    return CheckResult(
        verdict=_verdict_from_counts(counts),
        fatals=counts["fatals"],
        errors=counts["errors"],
        warnings=counts["warnings"],
        infos=counts["infos"],
        usages=counts["usages"],
        issues=all_issues,
        raw_log="\n".join(logs),
        exit_code=last_code,
        tool_name=VNU_DISPLAY_NAME,
        tool_version=version,
        checked_at=started,
        extra_meta=[("Nu HTML Checker version", version)] if version else [],
    )


def vnu_status_part() -> str | None:
    """Short status-bar fragment, or None when Nu is not present."""
    jar = find_vnu_jar()
    if jar is None:
        return None
    version = vnu_version_text(jar)
    from .i18n import _

    if version:
        if vnu_uses_bundled_copy():
            return _("Nu HTML Checker {version} (bundled)", version=version)
        return _("Nu HTML Checker {version}", version=version)
    return _("Nu HTML Checker")
