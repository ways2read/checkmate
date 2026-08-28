"""Run eBraille Checker / EPUBCheck / veraPDF and parse results."""

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
    verapdf_uses_bundled_copy,
)
from .publication import (
    PublicationKind,
    classify_publication,
    classify_target,
    is_html_url,
    is_pipeline_kind,
    is_vnu_document_kind,
)
from .settings import (
    epub_checkers,
    verapdf_flavour,
    verapdf_flavour_label,
)
from .subprocess_util import elapsed_progress_message, run_capturing
from .updater import (
    EBRAILLE_TOOL,
    EPUBCHECK_TOOL,
    VERAPDF_TOOL,
    ToolSpec,
    ensure_tool_installed,
    read_effective_version,
)


PACKAGED_SUFFIXES = {".ebrl", ".epub", ".zip", ".pdf"}


def is_packaged_path(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in PACKAGED_SUFFIXES


def _emit_progress(progress, message: str, *, announce: bool = True) -> None:
    """Call ``progress`` with optional announce; tolerate older one-arg callbacks."""
    if not progress:
        return
    try:
        progress(message, announce=announce)
    except TypeError:
        progress(message)


def is_exploded_path(path: Path) -> bool:
    return path.is_dir()


def tool_for_kind(kind: PublicationKind) -> ToolSpec | None:
    if kind == PublicationKind.EBRAILLE:
        return EBRAILLE_TOOL
    if kind == PublicationKind.EPUB:
        return EPUBCHECK_TOOL
    if kind == PublicationKind.PDF:
        return VERAPDF_TOOL
    return None


_UNSUPPORTED_PATH_MESSAGE = (
    "Choose a packaged .ebrl, .epub, or .pdf file, an HTML, SVG, CSS, MathML, "
    "or XML file, an http(s) URL, a DTBook XML or NIMAS/DAISY package, or an "
    "exploded eBraille/EPUB/DAISY publication folder."
)


def _stamp_result(
    result: CheckResult,
    *,
    target: Path | str,
    tool: ToolSpec | None = None,
    checked_at: datetime | None = None,
) -> CheckResult:
    """Attach publication path, checker identity, and timestamp for reports."""
    result.target_path = str(target)
    result.checked_at = checked_at or datetime.now().astimezone()
    if tool is not None:
        result.tool_name = tool.display_name
        # Prefer a version already taken from the tool's own report JSON.
        if not result.tool_version:
            result.tool_version = read_effective_version(tool) or ""
        # Tag issues so the Source column / filter can name the checker
        # (e.g. veraPDF, eBraille Checker) even for single-tool runs.
        for issue in result.issues:
            if not issue.source:
                issue.source = tool.display_name
    from .ai.context import attach_location_snippets

    attach_location_snippets(result)
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


def _ensure_meta(result: CheckResult, *pairs: tuple[str, str]) -> CheckResult:
    """Append missing ``extra_meta`` rows (does not replace existing labels)."""
    existing = {label for label, _ in result.extra_meta}
    for label, value in pairs:
        if not value or label in existing:
            continue
        result.extra_meta.append((label, value))
    return result


def _ensure_validation_profile(result: CheckResult, profile: str) -> CheckResult:
    """Ensure a Validation profile row is present for reports / result pane."""
    return _ensure_meta(result, ("Validation profile", profile))


def _annotate_clipboard_source(result: CheckResult, raw: str) -> CheckResult:
    """Mark results that came from File → Check clipboard…"""
    from .clipboard_markup import is_clipboard_snapshot_path, is_clipboard_snippet_path

    text = str(raw or "").strip().strip('"')
    if not text or is_html_url(text):
        return result
    try:
        path = Path(text).expanduser()
    except (OSError, ValueError):
        return result
    if is_clipboard_snapshot_path(path):
        result = _ensure_meta(result, ("Source", "Clipboard"))
        if is_clipboard_snippet_path(path):
            result = _ensure_meta(result, ("Checked as", "HTML snippet"))
        return result
    return result


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
                or "report" in data
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


def _verapdf_rule_code(summary: dict) -> str:
    clause = str(summary.get("clause") or "").strip()
    test_number = summary.get("testNumber")
    if clause and test_number not in (None, ""):
        return f"{clause}-{test_number}"
    if clause:
        return clause
    return str(summary.get("specification") or "rule")


def _verapdf_failed_check_count(summary: dict) -> int:
    try:
        return max(int(summary.get("failedChecks") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _verapdf_task_exception(data: dict) -> dict | None:
    report = data.get("report") if isinstance(data.get("report"), dict) else data
    if not isinstance(report, dict):
        return None
    jobs = report.get("jobs")
    if not isinstance(jobs, list):
        return None
    for job in jobs:
        if not isinstance(job, dict):
            continue
        exception = job.get("taskException")
        if isinstance(exception, dict):
            return exception
    return None


def _simplify_verapdf_exception_message(message: str) -> str:
    text = " ".join((message or "").split())
    if not text:
        return "Unexpected error during validation."
    # Collapse nested "caused by exception:" chains to the root cause.
    marker = "caused by exception:"
    if marker in text.lower():
        parts = re.split(re.escape(marker), text, flags=re.IGNORECASE)
        text = parts[-1].strip() or text
    if text.lower().startswith("exception:"):
        text = text[len("exception:") :].strip()
    return text


def _verapdf_tool_error_message(exception: dict, *, flavour: str) -> str:
    exc_type = str(exception.get("type") or "").strip().upper()
    detail = _simplify_verapdf_exception_message(
        str(
            exception.get("exceptionMessage")
            or exception.get("exception")
            or ""
        )
    )
    profile = {
        "ua1": "PDF/UA-1",
        "ua2": "PDF/UA-2",
    }.get(flavour, flavour)
    if exc_type == "PARSE":
        return (
            f"veraPDF could not open this PDF ({profile}).\n\n{detail}"
        )
    return (
        f"veraPDF stopped with an internal error while validating this PDF "
        f"({profile}). This is a tool bug on some files, not a normal "
        f"accessibility finding.\n\n{detail}"
    )


def _issues_from_verapdf_json(data: dict) -> list[Issue]:
    """Map veraPDF JSON into Issues — one row per failed rule (GUI-style).

    veraPDF's summary count is ``failedRules``. A single rule can fail thousands
    of times (``failedChecks``); the GUI lists rules, not every occurrence.
    Tool crashes (``taskException``) are handled separately as Verdict.ERROR.
    """
    issues: list[Issue] = []
    report = data.get("report") if isinstance(data.get("report"), dict) else data
    jobs = report.get("jobs") if isinstance(report, dict) else None
    if not isinstance(jobs, list):
        return issues

    for job in jobs:
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("taskException"), dict):
            continue

        results = job.get("validationResult") or []
        if not isinstance(results, list):
            results = [results] if isinstance(results, dict) else []
        for result in results:
            if not isinstance(result, dict):
                continue
            details = result.get("details") or {}
            if not isinstance(details, dict):
                continue
            summaries = details.get("ruleSummaries") or []
            if not isinstance(summaries, list):
                continue
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                status = str(
                    summary.get("status") or summary.get("ruleStatus") or ""
                ).lower()
                if status not in ("failed", "fail"):
                    continue
                code = _verapdf_rule_code(summary)
                description = str(summary.get("description") or "").strip()
                checks = summary.get("checks") or []
                if not isinstance(checks, list):
                    checks = []
                sample = next((c for c in checks if isinstance(c, dict)), None)
                error_message = ""
                location = ""
                if sample is not None:
                    error_message = str(sample.get("errorMessage") or "").strip()
                    location = str(sample.get("context") or "").strip()
                message = description or error_message or "Failed validation rule"
                if (
                    error_message
                    and description
                    and error_message != description
                ):
                    message = f"{description} — {error_message}"
                failed_total = _verapdf_failed_check_count(summary)
                if failed_total > 1:
                    message = (
                        f"{message} ({failed_total} failures)"
                        if message
                        else f"{failed_total} failures"
                    )
                issues.append(
                    Issue(
                        severity=Severity.ERROR,
                        code=code,
                        message=message,
                        location=location,
                    )
                )

    issues.sort(
        key=lambda i: (
            SEVERITY_ORDER.get(i.severity, 99),
            i.code,
            i.location,
            i.message,
        )
    )
    return issues


def _counts_from_verapdf_json(data: dict, issues: list[Issue]) -> dict[str, int]:
    """Count veraPDF failures like the GUI: failed rules, not failed checks."""
    counts = {
        "fatals": 0,
        "errors": 0,
        "warnings": 0,
        "infos": 0,
        "usages": 0,
    }
    report = data.get("report") if isinstance(data.get("report"), dict) else data
    jobs = report.get("jobs") if isinstance(report, dict) else None
    if isinstance(jobs, list):
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if isinstance(job.get("taskException"), dict):
                counts["fatals"] += 1
                continue
            results = job.get("validationResult") or []
            if not isinstance(results, list):
                results = [results] if isinstance(results, dict) else []
            for result in results:
                if not isinstance(result, dict):
                    continue
                details = result.get("details") or {}
                if not isinstance(details, dict):
                    continue
                try:
                    failed_rules = int(details.get("failedRules") or 0)
                except (TypeError, ValueError):
                    failed_rules = 0
                if failed_rules:
                    counts["errors"] += failed_rules
                    continue
                # Fall back to counting failed rule summaries.
                summaries = details.get("ruleSummaries") or []
                if isinstance(summaries, list):
                    for summary in summaries:
                        if not isinstance(summary, dict):
                            continue
                        status = str(
                            summary.get("status") or summary.get("ruleStatus") or ""
                        ).lower()
                        if status in ("failed", "fail"):
                            counts["errors"] += 1

    if any(counts.values()):
        return counts

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


def _verapdf_build_meta(report: dict) -> tuple[str | None, str | None]:
    """Return (version, build_date_iso) from report.buildInformation if present."""
    info = report.get("buildInformation")
    if not isinstance(info, dict):
        return None, None
    details = info.get("releaseDetails") or []
    if not isinstance(details, list) or not details:
        return None, None

    preferred = None
    for detail in details:
        if isinstance(detail, dict) and detail.get("id") == "apps":
            preferred = detail
            break
    if preferred is None:
        preferred = next((d for d in details if isinstance(d, dict)), None)
    if preferred is None:
        return None, None

    version = str(preferred.get("version") or "").strip() or None
    build_date = None
    raw = preferred.get("buildDate")
    if isinstance(raw, (int, float)) and raw > 0:
        try:
            build_date = datetime.fromtimestamp(raw / 1000.0).astimezone().isoformat(
                timespec="seconds"
            )
        except (OverflowError, OSError, ValueError):
            build_date = None
    elif isinstance(raw, str) and raw.strip():
        build_date = raw.strip()
    return version, build_date


def _verapdf_extra_meta(data: dict) -> list[tuple[str, str]]:
    """Extract GUI-style summary fields that are stable in veraPDF JSON."""
    report = data.get("report") if isinstance(data.get("report"), dict) else data
    if not isinstance(report, dict):
        return []

    meta: list[tuple[str, str]] = []
    version, build_date = _verapdf_build_meta(report)
    # We only ship the greenfield installer; the GUI labels this "GreenField".
    meta.append(("Parser", "Greenfield"))
    if build_date:
        meta.append(("Build date", build_date))

    jobs = report.get("jobs")
    job = jobs[0] if isinstance(jobs, list) and jobs and isinstance(jobs[0], dict) else None
    if job is not None:
        processing = job.get("processingTime")
        if isinstance(processing, dict):
            duration = str(processing.get("duration") or "").strip()
            if duration:
                meta.append(("Processing time", duration))

        results = job.get("validationResult") or []
        if not isinstance(results, list):
            results = [results] if isinstance(results, dict) else []
        for result in results:
            if not isinstance(result, dict):
                continue
            profile = str(result.get("profileName") or "").strip()
            if profile:
                meta.append(("Validation profile", profile))
            details = result.get("details")
            if isinstance(details, dict):
                try:
                    passed_rules = int(details.get("passedRules") or 0)
                    failed_rules = int(details.get("failedRules") or 0)
                except (TypeError, ValueError):
                    passed_rules = failed_rules = 0
                total_rules = passed_rules + failed_rules
                if total_rules:
                    meta.append(("Total rules in profile", str(total_rules)))
                try:
                    passed_checks = int(details.get("passedChecks") or 0)
                except (TypeError, ValueError):
                    passed_checks = 0
                try:
                    failed_checks = int(details.get("failedChecks") or 0)
                except (TypeError, ValueError):
                    failed_checks = 0
                if passed_checks or failed_checks:
                    meta.append(("Passed checks", str(passed_checks)))
                    meta.append(("Failed checks", str(failed_checks)))
            break

    # Prefer JSON version when present (same as GUI "Version").
    if version:
        # Put version near the top, after parser is fine; callers may also set
        # tool_version from this value.
        meta.insert(0, ("Version", version))
    return meta


def parse_verapdf_output(
    stdout: str,
    stderr: str,
    exit_code: int,
    *,
    flavour: str = "ua2",
) -> CheckResult:
    combined = "\n".join(part for part in (stdout, stderr) if part)
    data = _extract_json_object(stdout) or _extract_json_object(combined)
    if data is None:
        verdict = Verdict.PASSED if exit_code == 0 else Verdict.FAILED
        result = CheckResult(
            verdict=verdict,
            raw_log=combined,
            exit_code=exit_code,
            error_message=""
            if exit_code == 0
            else "Could not parse structured results; see the full log.",
        )
        return _ensure_validation_profile(result, verapdf_flavour_label(flavour))

    extra_meta = _verapdf_extra_meta(data)
    version_from_json = ""
    display_meta: list[tuple[str, str]] = []
    for label, value in extra_meta:
        if label == "Version":
            version_from_json = value
            continue
        display_meta.append((label, value))

    exception = _verapdf_task_exception(data)
    if exception is not None:
        error_message = _verapdf_tool_error_message(exception, flavour=flavour)
        result = CheckResult(
            verdict=Verdict.ERROR,
            raw_log=_compose_raw_log(
                "\n".join(p for p in (stderr,) if p),
                data=data,
                issues=[],
            )
            or combined,
            exit_code=exit_code,
            error_message=error_message,
            extra_meta=display_meta,
            tool_version=version_from_json,
        )
        return _ensure_validation_profile(result, verapdf_flavour_label(flavour))

    issues = _issues_from_verapdf_json(data)
    counts = _counts_from_verapdf_json(data, issues)
    verdict = _verdict_from_counts(counts, exit_code)
    result = CheckResult(
        verdict=verdict,
        fatals=counts["fatals"],
        errors=counts["errors"],
        warnings=counts["warnings"],
        infos=counts["infos"],
        usages=counts["usages"],
        issues=issues,
        raw_log=_compose_raw_log(
            "\n".join(p for p in (stderr,) if p),
            data=data,
            issues=issues,
        )
        or combined,
        exit_code=exit_code,
        extra_meta=display_meta,
        tool_version=version_from_json,
    )
    return _ensure_validation_profile(result, verapdf_flavour_label(flavour))


def build_verapdf_command(
    java: JavaInfo,
    jar: Path,
    target: Path,
    *,
    flavour: str = "ua2",
) -> list[str]:
    """Build the veraPDF CLI for PDF/UA validation (default: UA-2)."""
    return [
        java.path,
        "-Djava.awt.headless=true",
        "-jar",
        str(jar),
        "--flavour",
        flavour,
        "--format",
        "json",
        # One sample location per failed rule is enough; the GUI counts rules.
        "--maxfailuresdisplayed",
        "1",
        str(target),
    ]


def _run_verapdf_once(
    *,
    java: JavaInfo,
    jar: Path,
    target: Path,
    flavour: str,
    progress=None,
    progress_label: str | None = None,
) -> tuple[list[str], CheckResult]:
    cmd = build_verapdf_command(java, jar, target, flavour=flavour)
    label = progress_label or "Checking with veraPDF…"

    def heartbeat(elapsed: float) -> None:
        msg = elapsed_progress_message(label, elapsed)
        if msg is not None:
            _emit_progress(progress, msg, announce=False)

    proc = run_capturing(
        cmd,
        timeout=600,
        heartbeat=heartbeat if progress else None,
        heartbeat_interval=1.0,
    )
    result = parse_verapdf_output(
        proc.stdout or "",
        proc.stderr or "",
        proc.returncode,
        flavour=flavour,
    )
    result.command = cmd
    return cmd, result


def run_check(
    target: Path | str,
    *,
    exploded: bool | None = None,
    progress=None,
) -> CheckResult:
    raw = str(target).strip().strip('"')
    checked_at = datetime.now().astimezone()
    kind = classify_target(raw)
    if kind == PublicationKind.HTML:
        from .html_check import run_html_check

        result = run_html_check(raw, progress=progress)
        if result.checked_at is None:
            result.checked_at = checked_at
        if not result.target_path:
            result.target_path = raw
        from .ai.context import attach_location_snippets

        annotated = _annotate_clipboard_source(result, raw)
        attach_location_snippets(annotated)
        return annotated

    if is_vnu_document_kind(kind):
        from .vnu_check import run_vnu_document_check

        if not is_html_url(raw):
            path = Path(raw).expanduser().resolve()
            if not path.exists():
                return _stamp_result(
                    CheckResult(
                        verdict=Verdict.ERROR,
                        error_message=f"Path not found: {path}",
                    ),
                    target=path,
                    checked_at=checked_at,
                )
            raw = str(path)
        result = run_vnu_document_check(raw, kind=kind, progress=progress)
        if result.checked_at is None:
            result.checked_at = checked_at
        if not result.target_path:
            result.target_path = raw
        from .ai.context import attach_location_snippets

        attach_location_snippets(result)
        return result

    target = Path(raw).expanduser().resolve()
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
    if is_pipeline_kind(kind):
        from .pipeline_check import run_pipeline_check
        from .ai.context import attach_location_snippets

        result = run_pipeline_check(target, kind=kind, progress=progress)
        attach_location_snippets(result)
        return result

    # EPUB Ace-only: skip EPUBCheck/Java entirely (Settings → EPUB checkers).
    if kind == PublicationKind.EPUB and epub_checkers() == "ace":
        return _run_ace_only_check(
            target, progress=progress, checked_at=checked_at
        )

    tool = tool_for_kind(kind)
    if tool is None:
        return _stamp_result(
            CheckResult(
                verdict=Verdict.ERROR,
                error_message=_UNSUPPORTED_PATH_MESSAGE,
            ),
            target=target,
            checked_at=checked_at,
        )

    # Reuse the session's detection result; a fresh probe (and on Windows a
    # Program Files scan) only happens when no working Java was found before,
    # so a JRE installed mid-session is still picked up.
    java = cached_java() or detect_java()
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
                    error_message=_UNSUPPORTED_PATH_MESSAGE,
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )

    if kind == PublicationKind.PDF:
        # Honour Settings flavour (default PDF/UA-2). Some files trigger an
        # internal veraPDF NPE on UA-2; fall back to PDF/UA-1 only when the
        # user chose UA-2.
        chosen_flavour = verapdf_flavour()
        label = f"Checking with {tool.display_name}…"
        _emit_progress(progress, label)
        try:
            _cmd, result = _run_verapdf_once(
                java=java,
                jar=jar,
                target=target,
                flavour=chosen_flavour,
                progress=progress,
                progress_label=label,
            )
        except subprocess.TimeoutExpired:
            return _stamp_result(
                CheckResult(
                    verdict=Verdict.ERROR,
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
                    error_message=f"Failed to start Java: {exc}",
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )

        if (
            chosen_flavour == "ua2"
            and result.verdict == Verdict.ERROR
            and result.error_message
            and "internal error" in result.error_message.lower()
        ):
            try:
                _cmd_ua1, ua1_result = _run_verapdf_once(
                    java=java,
                    jar=jar,
                    target=target,
                    flavour="ua1",
                    progress=progress,
                    progress_label=label,
                )
            except (subprocess.TimeoutExpired, OSError):
                return _stamp_result(
                    result, target=target, tool=tool, checked_at=checked_at
                )
            if ua1_result.verdict != Verdict.ERROR:
                note = (
                    "PDF/UA-2 hit an internal veraPDF error; "
                    "results are from PDF/UA-1."
                )
                ua1_result.extra_meta = [("Note", note), *ua1_result.extra_meta]
                if ua1_result.raw_log:
                    ua1_result.raw_log = f"{note}\n\n{ua1_result.raw_log}"
                else:
                    ua1_result.raw_log = note
                return _stamp_result(
                    ua1_result, target=target, tool=tool, checked_at=checked_at
                )

        return _stamp_result(
            result, target=target, tool=tool, checked_at=checked_at
        )

    label = f"Checking with {tool.display_name}…"
    _emit_progress(progress, label)

    if kind == PublicationKind.EBRAILLE:
        validation_profile = "ebraille"
    else:
        validation_profile = "default"

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

        def heartbeat(elapsed: float) -> None:
            msg = elapsed_progress_message(label, elapsed)
            if msg is not None:
                _emit_progress(progress, msg, announce=False)

        try:
            proc = run_capturing(
                cmd,
                timeout=600,
                heartbeat=heartbeat if progress else None,
                heartbeat_interval=1.0,
            )
        except subprocess.TimeoutExpired:
            return _stamp_result(
                _ensure_validation_profile(
                    CheckResult(
                        verdict=Verdict.ERROR,
                        command=cmd,
                        error_message="Check timed out after 10 minutes.",
                    ),
                    validation_profile,
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )
        except OSError as exc:
            return _stamp_result(
                _ensure_validation_profile(
                    CheckResult(
                        verdict=Verdict.ERROR,
                        command=cmd,
                        error_message=f"Failed to start Java: {exc}",
                    ),
                    validation_profile,
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
            epub_result = _stamp_result(
                _ensure_validation_profile(
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
                    validation_profile,
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )
            return _with_optional_ace(
                epub_result, target=target, kind=kind, progress=progress
            )

        # No JSON file: try stdout/stderr JSON, then the Messages: summary line.
        result = parse_checker_output(stdout, stderr, proc.returncode)
        if not (
            result.verdict == Verdict.ERROR
            and result.error_message
            and "Could not parse" in result.error_message
        ):
            result.command = cmd
            epub_result = _stamp_result(
                _ensure_validation_profile(result, validation_profile),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )
            return _with_optional_ace(
                epub_result, target=target, kind=kind, progress=progress
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
            epub_result = _stamp_result(
                _ensure_validation_profile(
                    CheckResult(
                        verdict=verdict,
                        fatals=summary["fatals"],
                        errors=summary["errors"],
                        warnings=summary["warnings"],
                        infos=summary["infos"],
                        usages=summary["usages"],
                        raw_log=log,
                        exit_code=proc.returncode,
                        command=cmd,
                    ),
                    validation_profile,
                ),
                target=target,
                tool=tool,
                checked_at=checked_at,
            )
            return _with_optional_ace(
                epub_result, target=target, kind=kind, progress=progress
            )

        result.command = cmd
        epub_result = _stamp_result(
            _ensure_validation_profile(result, validation_profile),
            target=target,
            tool=tool,
            checked_at=checked_at,
        )
        return _with_optional_ace(
            epub_result, target=target, kind=kind, progress=progress
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


def _verdict_rank(verdict: Verdict) -> int:
    return {
        Verdict.ERROR: 0,
        Verdict.FAILED: 1,
        Verdict.PASSED_WITH_WARNINGS: 2,
        Verdict.PASSED: 3,
    }.get(verdict, 0)


def _counts_from_issues_list(issues: list[Issue]) -> dict[str, int]:
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


def _merge_epubcheck_and_ace(
    epub_result: CheckResult,
    ace_result: CheckResult,
) -> CheckResult:
    """Combine EPUBCheck + Ace into one CheckResult (worst verdict wins)."""
    from .ace_check import ACE_DISPLAY_NAME

    epub_issues = [
        Issue(
            severity=issue.severity,
            code=issue.code,
            message=issue.message,
            location=issue.location,
            source=issue.source or EPUBCHECK_TOOL.display_name,
            help_url=issue.help_url,
            help_title=issue.help_title,
            help_text=issue.help_text,
            impact=issue.impact,
            ruleset=issue.ruleset,
            snippet=issue.snippet,
        )
        for issue in epub_result.issues
    ]
    ace_issues = [
        Issue(
            severity=issue.severity,
            code=issue.code,
            message=issue.message,
            location=issue.location,
            source=issue.source or ACE_DISPLAY_NAME,
            help_url=issue.help_url,
            help_title=issue.help_title,
            help_text=issue.help_text,
            impact=issue.impact,
            ruleset=issue.ruleset,
            snippet=issue.snippet,
        )
        for issue in ace_result.issues
    ]
    # Ace ERROR with only error_message — ensure a visible issue.
    if (
        ace_result.verdict == Verdict.ERROR
        and ace_result.error_message
        and not ace_issues
    ):
        ace_issues.append(
            Issue(
                severity=Severity.ERROR,
                code="ace-error",
                message=ace_result.error_message,
                source=ACE_DISPLAY_NAME,
            )
        )

    issues = epub_issues + ace_issues
    counts = _counts_from_issues_list(issues)
    # Per-tool share of the totals, on the same (issue-count) basis so the
    # breakdown always adds up to the combined counts.
    source_counts = [
        (EPUBCHECK_TOOL.display_name, _counts_from_issues_list(epub_issues)),
        (ACE_DISPLAY_NAME, _counts_from_issues_list(ace_issues)),
    ]

    # Ace infra/parse ERROR while EPUBCheck completed should not make the
    # combined headline "Could not complete check" — surface as Failed.
    ace_verdict = ace_result.verdict
    if ace_verdict == Verdict.ERROR and epub_result.verdict != Verdict.ERROR:
        ace_verdict = Verdict.FAILED

    if _verdict_rank(epub_result.verdict) <= _verdict_rank(ace_verdict):
        verdict = epub_result.verdict
    else:
        verdict = ace_verdict
    if counts["fatals"] or counts["errors"]:
        if verdict != Verdict.ERROR:
            verdict = Verdict.FAILED
    elif counts["warnings"] and verdict == Verdict.PASSED:
        verdict = Verdict.PASSED_WITH_WARNINGS

    log_parts: list[str] = []
    if epub_result.raw_log.strip():
        log_parts.append("--- EPUBCheck ---")
        log_parts.append(epub_result.raw_log.strip())
    if ace_result.raw_log.strip():
        if log_parts:
            log_parts.append("")
        log_parts.append("--- Ace ---")
        log_parts.append(ace_result.raw_log.strip())
    elif ace_result.error_message:
        if log_parts:
            log_parts.append("")
        log_parts.append("--- Ace ---")
        log_parts.append(ace_result.error_message)

    extra_meta = list(epub_result.extra_meta)
    epub_ver = (epub_result.tool_version or "").strip()
    ace_ver = (ace_result.tool_version or "").strip()
    if epub_ver:
        extra_meta.append(("EPUBCheck version", epub_ver))
    if ace_ver:
        extra_meta.append(("Ace version", ace_ver))
    if not any(label == "Ace rules" for label, _ in extra_meta):
        extra_meta.append(("Ace rules", "Full axe-core ruleset"))

    # Keep tool_version empty so "Checker: EPUBCheck + Ace v5.3.0 + Ace …"
    # does not look like two Ace products. Versions live in extra_meta.
    return CheckResult(
        verdict=verdict,
        fatals=counts["fatals"],
        errors=counts["errors"],
        warnings=counts["warnings"],
        infos=counts["infos"],
        usages=counts["usages"],
        issues=issues,
        raw_log="\n".join(log_parts).strip(),
        exit_code=epub_result.exit_code,
        command=epub_result.command,
        error_message="",
        tool_name="EPUBCheck + Ace",
        tool_version="",
        checked_at=epub_result.checked_at,
        target_path=epub_result.target_path,
        extra_meta=extra_meta,
        source_counts=source_counts,
    )


def _run_ace_only_check(
    target: Path,
    *,
    progress=None,
    checked_at: datetime | None = None,
) -> CheckResult:
    """Run Ace alone when Settings → EPUB checkers is Ace only."""
    from .ace_check import ACE_DISPLAY_NAME, run_ace_check

    when = checked_at or datetime.now().astimezone()
    ace_result = run_ace_check(target, progress=progress)
    if ace_result is None:
        return _stamp_result(
            CheckResult(
                verdict=Verdict.ERROR,
                error_message=(
                    "Ace was not found. Install Ace, or choose EPUBCheck "
                    "(or EPUBCheck + Ace) in Tools → Settings…"
                ),
                tool_name=ACE_DISPLAY_NAME,
            ),
            target=target,
            checked_at=when,
        )
    if ace_result.checked_at is None:
        ace_result.checked_at = when
    if not ace_result.target_path:
        ace_result.target_path = str(target)
    return _attach_optional_mathml_quality(ace_result, target, progress=progress)


def _attach_optional_mathml_quality(
    result: CheckResult,
    target: Path,
    *,
    progress=None,
) -> CheckResult:
    """Append Nordic MathML quality warnings when the user has opted in."""
    from .mathml_quality import attach_mathml_quality
    from .settings import mathml_nordic_guidelines

    if not mathml_nordic_guidelines():
        from .ai.context import attach_location_snippets

        attach_location_snippets(result)
        return result
    _emit_progress(progress, "Checking MathML quality…")
    result = attach_mathml_quality(result, str(target))
    from .ai.context import attach_location_snippets

    attach_location_snippets(result)
    return result


def _with_optional_ace(
    epub_result: CheckResult,
    *,
    target: Path,
    kind: PublicationKind,
    progress=None,
) -> CheckResult:
    """After EPUBCheck, optionally run Ace and merge (EPUB only)."""
    if kind != PublicationKind.EPUB:
        return epub_result
    # Settings → EPUB checkers: EPUBCheck only skips Ace.
    if epub_checkers() == "epubcheck":
        return _attach_optional_mathml_quality(
            epub_result, target, progress=progress
        )
    # Skip Ace when EPUBCheck never really started (infra already surfaced).
    if (
        epub_result.verdict == Verdict.ERROR
        and not epub_result.issues
        and epub_result.error_message
        and (
            "Java" in epub_result.error_message
            or "install" in epub_result.error_message.lower()
            or "timed out" in epub_result.error_message.lower()
            or "Failed to start Java" in epub_result.error_message
        )
    ):
        return epub_result

    from .ace_check import run_ace_check

    ace_result = run_ace_check(target, progress=progress)
    if ace_result is None:
        return _attach_optional_mathml_quality(
            epub_result, target, progress=progress
        )
    return _attach_optional_mathml_quality(
        _merge_epubcheck_and_ace(epub_result, ace_result),
        target,
        progress=progress,
    )


def checker_status_text() -> str:
    from .i18n import _
    from .ace_check import ace_uses_bundled_copy, probe_ace
    from .pipeline_client import probe_pipeline_for_status

    java = cached_java()
    parts = [
        _tool_status_part(EBRAILLE_TOOL, bundled=checker_uses_bundled_copy()),
        _tool_status_part(EPUBCHECK_TOOL, bundled=epubcheck_uses_bundled_copy()),
        _tool_status_part(VERAPDF_TOOL, bundled=verapdf_uses_bundled_copy()),
    ]
    ace_version = probe_ace()
    if ace_version:
        if ace_uses_bundled_copy():
            parts.append(_("Ace {version} (bundled)", version=ace_version))
        else:
            parts.append(_("Ace {version}", version=ace_version))
    try:
        from .vnu_check import vnu_status_part

        vnu_part = vnu_status_part()
        if vnu_part:
            parts.append(vnu_part)
    except Exception:
        pass
    pipeline = probe_pipeline_for_status()
    if pipeline is not None:
        if pipeline.version:
            parts.append(_("Pipeline {version}", version=pipeline.version))
        else:
            parts.append(_("Pipeline"))
    if java:
        parts.append(java.label)
    else:
        parts.append(_("Java not found"))
    return " · ".join(parts)
