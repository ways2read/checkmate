"""Run eBraille Checker / EPUBCheck and parse results."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .java_util import JavaInfo, cached_java, detect_java, has_bundled_java
from .models import CheckResult, Issue, Severity, Verdict, SEVERITY_ORDER
from .paths import (
    checker_uses_bundled_copy,
    epubcheck_uses_bundled_copy,
    find_checker_jar,
    find_epubcheck_jar,
)
from .publication import PublicationKind, classify_publication
from .subprocess_util import hidden_run_kwargs
from .updater import (
    EBRAILLE_TOOL,
    EPUBCHECK_TOOL,
    ToolSpec,
    ensure_tool_installed,
    read_effective_version,
)


PACKAGED_SUFFIXES = {".ebrl", ".epub", ".zip"}


def is_packaged_path(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in PACKAGED_SUFFIXES


def is_exploded_path(path: Path) -> bool:
    return path.is_dir()


def tool_for_kind(kind: PublicationKind) -> ToolSpec | None:
    if kind == PublicationKind.EBRAILLE:
        return EBRAILLE_TOOL
    if kind == PublicationKind.EPUB:
        return EPUBCHECK_TOOL
    return None


def _stamp_result(
    result: CheckResult,
    *,
    target: Path,
    tool: ToolSpec | None = None,
    checked_at: datetime | None = None,
) -> CheckResult:
    """Attach publication path, checker identity, and timestamp for reports."""
    result.target_path = str(target)
    result.checked_at = checked_at or datetime.now().astimezone()
    if tool is not None:
        result.tool_name = tool.display_name
        result.tool_version = read_effective_version(tool) or ""
    return result


def build_command(
    java: JavaInfo,
    jar: Path,
    target: Path,
    *,
    kind: PublicationKind,
    exploded: bool | None = None,
) -> list[str]:
    if exploded is None:
        exploded = is_exploded_path(target)
    cmd = [
        java.path,
        "-Xss4m",
        "-jar",
        str(jar),
    ]
    if kind == PublicationKind.EBRAILLE:
        cmd.extend(["--profile", "ebraille"])
    if exploded:
        cmd.extend(["-mode", "exp"])
    cmd.extend(["--json", "-"])
    cmd.append(str(target))
    return cmd


def _location_from_loc_object(loc) -> str:
    if isinstance(loc, str):
        return loc
    if not isinstance(loc, dict):
        return ""
    path = loc.get("path") or loc.get("file") or ""
    if not path:
        url = loc.get("url")
        if isinstance(url, str):
            path = url
        elif isinstance(url, dict):
            path = str(url.get("path") or "")
    line = loc.get("line")
    column = loc.get("column")
    if path and line not in (None, -1) and column not in (None, -1):
        return f"{path} ({line},{column})"
    return str(path) if path else ""


def _location_from_message(msg: dict) -> str:
    locations = msg.get("locations") or msg.get("Locations") or []
    if not locations and "path" in msg:
        return str(msg.get("path") or "")
    if not locations:
        # Flat EPUBCheck-style fields
        path = msg.get("file") or msg.get("File") or ""
        line = msg.get("line") or msg.get("Line")
        column = msg.get("column") or msg.get("Column")
        if path and line not in (None, -1) and column not in (None, -1):
            return f"{path} ({line},{column})"
        return str(path) if path else ""

    return _location_from_loc_object(locations[0])


def _message_occurrence_count(msg: dict) -> int:
    """How many times this grouped JSON message occurred.

    Stock EPUBCheck JSON deduplicates by ID+text and lists up to 25
    ``locations``, with ``additionalLocations`` for the remainder.
    """
    locations = msg.get("locations") or msg.get("Locations") or []
    listed = len(locations) if isinstance(locations, list) else 0
    extra = msg.get("additionalLocations")
    if extra is None:
        extra = msg.get("additional_locations") or 0
    try:
        extra_n = int(extra)
    except (TypeError, ValueError):
        extra_n = 0
    total = listed + max(extra_n, 0)
    return total if total > 0 else 1


def _issues_from_json(data: dict) -> list[Issue]:
    issues: list[Issue] = []
    messages = data.get("messages") or data.get("Messages") or []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        severity = Severity.from_string(
            msg.get("severity") or msg.get("Severity") or msg.get("type")
        )
        # Jackson may serialize Severity enums as objects in some builds
        if severity == Severity.UNKNOWN and isinstance(
            msg.get("severity") or msg.get("Severity"), dict
        ):
            sev_obj = msg.get("severity") or msg.get("Severity")
            severity = Severity.from_string(
                sev_obj.get("name") or sev_obj.get("value") or str(sev_obj)
            )
        code = str(msg.get("ID") or msg.get("id") or msg.get("code") or "")
        message = str(
            msg.get("message") or msg.get("Message") or msg.get("text") or ""
        ).strip()

        locations = msg.get("locations") or msg.get("Locations") or []
        if isinstance(locations, list) and locations:
            for loc in locations:
                loc_text = _location_from_loc_object(loc)
                issues.append(
                    Issue(
                        severity=severity,
                        code=code,
                        message=message,
                        location=loc_text,
                    )
                )
            try:
                extra = int(
                    msg.get("additionalLocations")
                    or msg.get("additional_locations")
                    or 0
                )
            except (TypeError, ValueError):
                extra = 0
            if extra > 0:
                issues.append(
                    Issue(
                        severity=severity,
                        code=code,
                        message=(
                            f"{message} (+{extra} additional location"
                            f"{'s' if extra != 1 else ''})"
                        ),
                        location="",
                    )
                )
        else:
            issues.append(
                Issue(
                    severity=severity,
                    code=code,
                    message=message,
                    location=_location_from_message(msg),
                )
            )

    issues.sort(
        key=lambda i: (
            SEVERITY_ORDER.get(i.severity, 99),
            i.location,
            i.code,
            i.message,
        )
    )
    return issues


def _counts_from_json(data: dict, issues: list[Issue]) -> dict[str, int]:
    """Derive severity totals from the JSON report.

    Prefer summing occurrence counts from each grouped ``messages`` entry
    (locations + additionalLocations). Do **not** trust ``checker.nError``
    etc. alone — those count unique message groups, not total hits, so they
    under-report compared with EPUBCheck's console ``Messages:`` footer.
    """
    messages = data.get("messages") or data.get("Messages") or []
    if isinstance(messages, list) and messages:
        counts = {
            "fatals": 0,
            "errors": 0,
            "warnings": 0,
            "infos": 0,
            "usages": 0,
        }
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            severity = Severity.from_string(
                msg.get("severity") or msg.get("Severity") or msg.get("type")
            )
            if severity == Severity.UNKNOWN and isinstance(
                msg.get("severity") or msg.get("Severity"), dict
            ):
                sev_obj = msg.get("severity") or msg.get("Severity")
                severity = Severity.from_string(
                    sev_obj.get("name") or sev_obj.get("value") or str(sev_obj)
                )
            n = _message_occurrence_count(msg)
            if severity == Severity.FATAL:
                counts["fatals"] += n
            elif severity == Severity.ERROR:
                counts["errors"] += n
            elif severity == Severity.WARNING:
                counts["warnings"] += n
            elif severity == Severity.INFO:
                counts["infos"] += n
            elif severity == Severity.USAGE:
                counts["usages"] += n
        if any(counts.values()):
            return counts

    # Fall back to counting expanded issues, then checker metadata.
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
    if any(counts.values()):
        return counts

    def pick(block: dict, *keys: str) -> int | None:
        for key in keys:
            if key in block and block[key] is not None:
                try:
                    return int(block[key])
                except (TypeError, ValueError):
                    continue
        return None

    for key in ("checker", "Checker", "counts"):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        mapped = {
            "fatals": pick(block, "nFatal", "fatal", "fatals"),
            "errors": pick(block, "nError", "error", "errors"),
            "warnings": pick(block, "nWarning", "warning", "warnings"),
            "infos": pick(block, "nInfo", "info", "infos"),
            "usages": pick(block, "nUsage", "usage", "usages"),
        }
        if any(v is not None for v in mapped.values()):
            return {
                "fatals": mapped["fatals"] or 0,
                "errors": mapped["errors"] or 0,
                "warnings": mapped["warnings"] or 0,
                "infos": mapped["infos"] or 0,
                "usages": mapped["usages"] or 0,
            }
    return counts


def _merge_counts_preferring_higher(
    primary: dict[str, int], secondary: dict[str, int] | None
) -> dict[str, int]:
    """Keep the larger total per severity (console footer vs JSON)."""
    if not secondary:
        return primary
    return {
        key: max(primary.get(key, 0), secondary.get(key, 0))
        for key in ("fatals", "errors", "warnings", "infos", "usages")
    }


def _verdict_from_counts(counts: dict[str, int], exit_code: int) -> Verdict:
    if counts["fatals"] or counts["errors"]:
        return Verdict.FAILED
    if exit_code not in (0, None) and not (
        counts["warnings"] or counts["infos"] or counts["usages"]
    ):
        return Verdict.FAILED
    if counts["warnings"]:
        return Verdict.PASSED_WITH_WARNINGS
    if exit_code not in (0, None):
        return Verdict.FAILED
    return Verdict.PASSED


_MESSAGES_SUMMARY_RE = re.compile(
    r"Messages:\s*"
    r"(?P<fatals>\d+)\s+fatal(?:s)?\s*/\s*"
    r"(?P<errors>\d+)\s+error(?:s)?\s*/\s*"
    r"(?P<warnings>\d+)\s+warning(?:s)?\s*/\s*"
    r"(?P<infos>\d+)\s+info(?:s)?",
    re.IGNORECASE,
)


def _counts_from_messages_summary(text: str) -> dict[str, int] | None:
    """Parse EPUBCheck's short ``Messages: N fatal / …`` footer line."""
    match = _MESSAGES_SUMMARY_RE.search(text or "")
    if match is None:
        return None
    return {
        "fatals": int(match.group("fatals")),
        "errors": int(match.group("errors")),
        "warnings": int(match.group("warnings")),
        "infos": int(match.group("infos")),
        "usages": 0,
    }


def _extract_json_object(text: str) -> dict | None:
    """Find the first top-level JSON object in mixed stdout."""
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    while start != -1:
        decoder = json.JSONDecoder()
        try:
            data, _ = decoder.raw_decode(text[start:])
            if isinstance(data, dict) and (
                "messages" in data
                or "Messages" in data
                or "checker" in data
                or "publication" in data
            ):
                return data
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


def parse_checker_output(stdout: str, stderr: str, exit_code: int) -> CheckResult:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    data = _extract_json_object(stdout) or _extract_json_object(stderr) or _extract_json_object(
        combined
    )

    if data is None:
        verdict = Verdict.PASSED if exit_code == 0 else Verdict.FAILED
        return CheckResult(
            verdict=verdict,
            raw_log=combined,
            exit_code=exit_code,
            error_message=""
            if exit_code == 0
            else "Could not parse structured results; see the full log.",
        )

    issues = _issues_from_json(data)
    counts = _counts_from_json(data, issues)
    verdict = _verdict_from_counts(counts, exit_code)
    return CheckResult(
        verdict=verdict,
        fatals=counts["fatals"],
        errors=counts["errors"],
        warnings=counts["warnings"],
        infos=counts["infos"],
        usages=counts["usages"],
        issues=issues,
        raw_log=combined,
        exit_code=exit_code,
    )


def _console_is_summary_only(raw_log: str) -> bool:
    """True when EPUBCheck only printed the short Messages/completed footer."""
    text = raw_log.strip()
    if not text:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 4:
        return False
    joined = "\n".join(lines).lower()
    return "messages:" in joined and "epubcheck completed" in joined


def _format_issues_log(issues: list[Issue]) -> str:
    if not issues:
        return ""
    return "\n".join(issue.summary_line() for issue in issues)


def _compose_raw_log(
    console_log: str,
    *,
    data: dict | None = None,
    issues: list[Issue] | None = None,
) -> str:
    """Build the Full log pane text.

    With ``--json <file>``, stock EPUBCheck often prints only a one-line
    Messages summary on the console; details live in the JSON report.
    Prefer a human-readable issue list, and keep the console text when useful.
    """
    console = (console_log or "").strip()
    issue_text = _format_issues_log(issues or [])
    parts: list[str] = []

    if console and not _console_is_summary_only(console):
        parts.append(console)
    elif console:
        parts.append(console)

    if issue_text:
        if parts:
            parts.append("")
            parts.append("--- Issues ---")
        parts.append(issue_text)
    elif data is not None and (not console or _console_is_summary_only(console)):
        if parts:
            parts.append("")
            parts.append("--- JSON report ---")
        parts.append(json.dumps(data, indent=2))

    return "\n".join(parts).strip()


def run_check(
    target: Path,
    *,
    exploded: bool | None = None,
    progress=None,
) -> CheckResult:
    target = target.expanduser().resolve()
    checked_at = datetime.now().astimezone()
    if not target.exists():
        return _stamp_result(
            CheckResult(
                verdict=Verdict.ERROR,
                error_message=f"Path not found: {target}",
            ),
            target=target,
            checked_at=checked_at,
        )

    kind = classify_publication(target)
    tool = tool_for_kind(kind)
    if tool is None:
        return _stamp_result(
            CheckResult(
                verdict=Verdict.ERROR,
                error_message=(
                    "Choose a packaged .ebrl or .epub file, or an exploded "
                    "eBraille/EPUB publication folder."
                ),
            ),
            target=target,
            checked_at=checked_at,
        )

    java = detect_java()
    if java is None:
        if has_bundled_java():
            message = (
                "The bundled Java runtime could not be started. "
                "On macOS this usually means the app needs to be re-signed "
                "with JVM entitlements (allow-jit). Reinstall from a current "
                "notarized build, or install a system JRE 17+."
            )
        else:
            message = (
                "Java was not found. Install a Java Runtime (JRE 8 or newer), "
                "or use a packaged build that includes a bundled runtime."
            )
        return _stamp_result(
            CheckResult(verdict=Verdict.ERROR, error_message=message),
            target=target,
            tool=tool,
            checked_at=checked_at,
        )

    try:
        jar = tool.find_installed_jar()
        if jar is None:
            jar = ensure_tool_installed(tool, progress=progress)
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return _stamp_result(
            CheckResult(
                verdict=Verdict.ERROR,
                error_message=f"Could not install {tool.display_name}: {exc}",
            ),
            target=target,
            tool=tool,
            checked_at=checked_at,
        )

    if exploded is None:
        if is_packaged_path(target):
            exploded = False
        elif is_exploded_path(target):
            exploded = True
        else:
            return _stamp_result(
                CheckResult(
                    verdict=Verdict.ERROR,
                    error_message=(
                        "Choose a packaged .ebrl or .epub file, or an exploded "
                        "eBraille/EPUB publication folder."
                    ),
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )

    with tempfile.TemporaryDirectory(prefix="ebraille-gui-") as tmp:
        json_path = Path(tmp) / "report.json"
        cmd = [
            java.path,
            "-Xss4m",
            "-jar",
            str(jar),
        ]
        if kind == PublicationKind.EBRAILLE:
            cmd.extend(["--profile", "ebraille"])
        if exploded:
            cmd.extend(["-mode", "exp"])
        cmd.extend(["--json", str(json_path), str(target)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
                **hidden_run_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return _stamp_result(
                CheckResult(
                    verdict=Verdict.ERROR,
                    command=cmd,
                    error_message="Check timed out after 10 minutes.",
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )
        except OSError as exc:
            return _stamp_result(
                CheckResult(
                    verdict=Verdict.ERROR,
                    command=cmd,
                    error_message=f"Failed to start Java: {exc}",
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        raw_log = "\n".join(p for p in (stdout, stderr) if p)

        data = None
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None

        if isinstance(data, dict):
            issues = _issues_from_json(data)
            counts = _counts_from_json(data, issues)
            # Console footer counts every occurrence; checker.nError counts
            # unique message groups — prefer the higher totals.
            counts = _merge_counts_preferring_higher(
                counts, _counts_from_messages_summary(raw_log)
            )
            verdict = _verdict_from_counts(counts, proc.returncode)
            return _stamp_result(
                CheckResult(
                    verdict=verdict,
                    fatals=counts["fatals"],
                    errors=counts["errors"],
                    warnings=counts["warnings"],
                    infos=counts["infos"],
                    usages=counts["usages"],
                    issues=issues,
                    raw_log=_compose_raw_log(raw_log, data=data, issues=issues),
                    exit_code=proc.returncode,
                    command=cmd,
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )

        # No JSON file: try stdout/stderr JSON, then the Messages: summary line.
        result = parse_checker_output(stdout, stderr, proc.returncode)
        if not (
            result.verdict == Verdict.ERROR
            and result.error_message
            and "Could not parse" in result.error_message
        ):
            result.command = cmd
            return _stamp_result(
                result, target=target, tool=tool, checked_at=checked_at
            )

        summary = _counts_from_messages_summary(raw_log)
        if summary is not None:
            verdict = _verdict_from_counts(summary, proc.returncode)
            note = (
                "Structured issue list unavailable; counts taken from "
                "the checker summary."
            )
            log = raw_log.strip()
            if log:
                log = f"{log}\n\n{note}"
            else:
                log = note
            return _stamp_result(
                CheckResult(
                    verdict=verdict,
                    fatals=summary["fatals"],
                    errors=summary["errors"],
                    warnings=summary["warnings"],
                    infos=summary["infos"],
                    usages=summary["usages"],
                    issues=[],
                    raw_log=log,
                    exit_code=proc.returncode,
                    command=cmd,
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )

        result.command = cmd
        return _stamp_result(
            result, target=target, tool=tool, checked_at=checked_at
        )


def _tool_status_part(tool: ToolSpec, *, bundled: bool) -> str:
    from .i18n import _

    version = read_effective_version(tool)
    jar = tool.find_installed_jar()
    if version and jar:
        if bundled:
            return _("{name} {version} (bundled)", name=tool.display_name, version=version)
        return _("{name} {version}", name=tool.display_name, version=version)
    if jar:
        return _("{name} installed", name=tool.display_name)
    return _("{name} not installed", name=tool.display_name)


def checker_status_text() -> str:
    from .i18n import _

    java = cached_java()
    parts = [
        _tool_status_part(EBRAILLE_TOOL, bundled=checker_uses_bundled_copy()),
        _tool_status_part(EPUBCHECK_TOOL, bundled=epubcheck_uses_bundled_copy()),
    ]
    if java:
        parts.append(java.label)
    else:
        parts.append(_("Java not found"))
    return " · ".join(parts)
