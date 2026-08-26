"""Run DAISY Pipeline 2 validators (2.02, DAISY 3, DTBook, NIMAS)."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from .models import CheckResult, Verdict
from .pipeline_client import (
    DAISY202_SCRIPT,
    DAISY3_SCRIPT,
    DTBOOK_SCRIPT,
    NIMAS_SCRIPT,
    PipelineScript,
    create_pipeline_job,
    delete_job,
    download_job_outputs,
    fetch_job_log,
    job_messages_text,
    path_to_ncc,
    probe_pipeline,
    wait_for_job,
)
from .pipeline_report import parse_pipeline_html_report, parse_pipeline_xml_report
from .publication import (
    PublicationKind,
    find_dtbook_for_target,
    find_opf_for_target,
    is_pipeline_kind,
)

PIPELINE_NEEDED_MESSAGE = (
    "DAISY 2.02, DAISY 3, DTBook, and NIMAS checking needs a local "
    "DAISY Pipeline 2 webservice running in local mode "
    "(typically http://127.0.0.1:8181/ws). Install the DAISY Pipeline "
    "desktop app and leave it running, then try again."
)

_PROFILE_LABELS = {
    PublicationKind.DAISY202: "DAISY 2.02",
    PublicationKind.DAISY3: "DAISY 3",
    PublicationKind.DTBOOK: "DTBook",
    PublicationKind.NIMAS: "NIMAS",
}


def _counts_from_issues(issues) -> dict[str, int]:
    counts = {
        "fatals": 0,
        "errors": 0,
        "warnings": 0,
        "infos": 0,
        "usages": 0,
    }
    from .models import Severity

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


def _verdict_from_job(job_status: str, counts: dict[str, int]) -> Verdict:
    if job_status == "ERROR":
        return Verdict.ERROR
    if counts["fatals"] or counts["errors"] or job_status == "FAIL":
        if counts["fatals"] or counts["errors"]:
            return Verdict.FAILED
        if job_status == "FAIL":
            return Verdict.FAILED
    if counts["warnings"]:
        return Verdict.PASSED_WITH_WARNINGS
    if job_status == "SUCCESS":
        return Verdict.PASSED
    return Verdict.FAILED


def _script_for_kind(kind: PublicationKind) -> PipelineScript:
    if kind == PublicationKind.DAISY202:
        return DAISY202_SCRIPT
    if kind == PublicationKind.DTBOOK:
        return DTBOOK_SCRIPT
    if kind == PublicationKind.NIMAS:
        return NIMAS_SCRIPT
    return DAISY3_SCRIPT


def _source_for_kind(target: Path, kind: PublicationKind) -> Path | None:
    if kind == PublicationKind.DAISY202:
        return path_to_ncc(target)
    if kind == PublicationKind.DTBOOK:
        return find_dtbook_for_target(target)
    return find_opf_for_target(target)


def run_daisy202_check(
    target: Path,
    *,
    progress=None,
) -> CheckResult:
    """Validate a DAISY 2.02 folder via Pipeline."""
    return run_pipeline_check(
        target, kind=PublicationKind.DAISY202, progress=progress
    )


def run_pipeline_check(
    target: Path,
    *,
    kind: PublicationKind,
    progress=None,
) -> CheckResult:
    """Validate a DAISY/DTBook/NIMAS target via a local Pipeline webservice."""
    target = target.expanduser().resolve()
    checked_at = datetime.now().astimezone()
    if not is_pipeline_kind(kind):
        kind = PublicationKind.DAISY202

    script = _script_for_kind(kind)
    source = _source_for_kind(target, kind)
    profile = _PROFILE_LABELS.get(kind, script.nicename)

    def _error(message: str, *, log: str = "") -> CheckResult:
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=message,
            raw_log=log,
            tool_name="DAISY Pipeline",
            checked_at=checked_at,
            target_path=str(target),
            extra_meta=[("Validation profile", profile)],
        )

    if source is None:
        return _error(
            f"Could not find the {profile} document to send to DAISY Pipeline."
        )

    status = probe_pipeline()
    if status is None:
        return _error(PIPELINE_NEEDED_MESSAGE)

    label = script.progress_label
    if progress:
        progress(label)

    job_id: str | None = None
    try:
        job_id = create_pipeline_job(status, source, script)
        job_status, job_xml = wait_for_job(
            status, job_id, progress=progress, progress_label=label
        )
        messages = job_messages_text(job_xml)
        job_log = fetch_job_log(status, job_id)

        html_text = ""
        xml_texts: list[str] = []
        with tempfile.TemporaryDirectory(prefix="checkmate-pipeline-") as tmp:
            html_path, xml_paths = download_job_outputs(status, job_id, Path(tmp))
            if html_path is not None and html_path.is_file():
                html_text = html_path.read_text(encoding="utf-8", errors="replace")
            for xml_path in xml_paths:
                try:
                    xml_texts.append(
                        xml_path.read_text(encoding="utf-8", errors="replace")
                    )
                except OSError:
                    continue

        issues, info_lines = (
            parse_pipeline_html_report(html_text) if html_text else ([], [])
        )
        if not issues:
            xml_issues: list = []
            for blob in xml_texts:
                xml_issues.extend(parse_pipeline_xml_report(blob))
            if xml_issues:
                issues = xml_issues

        for issue in issues:
            if not issue.source:
                issue.source = "DAISY Pipeline"

        counts = _counts_from_issues(issues)
        verdict = _verdict_from_job(job_status, counts)

        log_parts: list[str] = []
        if messages:
            log_parts.append(messages)
        if job_log.strip():
            if log_parts:
                log_parts.append("")
            log_parts.append("--- Job log ---")
            log_parts.append(job_log.strip())
        if info_lines:
            if log_parts:
                log_parts.append("")
            log_parts.append("--- Timing / info ---")
            log_parts.extend(info_lines)
        if issues:
            if log_parts:
                log_parts.append("")
            log_parts.append("--- Issues ---")
            log_parts.extend(i.summary_line() for i in issues)

        extra_meta = [
            ("Validation profile", profile),
            ("Pipeline script", script.script_id),
        ]
        if status.version:
            extra_meta.append(("DAISY Pipeline version", status.version))

        if verdict == Verdict.ERROR and not issues:
            return CheckResult(
                verdict=Verdict.ERROR,
                error_message="DAISY Pipeline job ended with an error.",
                raw_log="\n".join(log_parts).strip(),
                tool_name="DAISY Pipeline",
                tool_version=status.version,
                checked_at=checked_at,
                target_path=str(target),
                extra_meta=extra_meta,
            )

        if not html_text and not issues:
            if job_status == "SUCCESS":
                verdict = Verdict.PASSED
            elif job_status == "FAIL":
                verdict = Verdict.FAILED
            else:
                return CheckResult(
                    verdict=Verdict.ERROR,
                    error_message="DAISY Pipeline returned no validation report.",
                    raw_log="\n".join(log_parts).strip(),
                    tool_name="DAISY Pipeline",
                    tool_version=status.version,
                    checked_at=checked_at,
                    target_path=str(target),
                    extra_meta=extra_meta,
                )

        return CheckResult(
            verdict=verdict,
            fatals=counts["fatals"],
            errors=counts["errors"],
            warnings=counts["warnings"],
            infos=counts["infos"],
            usages=counts["usages"],
            issues=issues,
            raw_log="\n".join(log_parts).strip(),
            tool_name="DAISY Pipeline",
            tool_version=status.version,
            checked_at=checked_at,
            target_path=str(target),
            extra_meta=extra_meta,
        )
    except TimeoutError as exc:
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=str(exc),
            tool_name="DAISY Pipeline",
            tool_version=status.version,
            checked_at=checked_at,
            target_path=str(target),
            extra_meta=[("Validation profile", profile)],
        )
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=f"DAISY Pipeline check failed: {exc}",
            tool_name="DAISY Pipeline",
            tool_version=status.version,
            checked_at=checked_at,
            target_path=str(target),
            extra_meta=[("Validation profile", profile)],
        )
    finally:
        if job_id is not None:
            delete_job(status, job_id)
