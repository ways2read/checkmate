"""Run DAISY 2.02 validation via a local Pipeline webservice."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from .models import CheckResult, Verdict
from .pipeline_client import (
    create_daisy202_job,
    delete_job,
    download_html_report,
    fetch_job_log,
    job_messages_text,
    path_to_ncc,
    probe_pipeline,
    wait_for_job,
)
from .pipeline_report import parse_daisy202_html_report


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
        # FAIL with only warnings still happened in spike when an error existed;
        # if FAIL but we only parsed warnings, treat as failed to match Pipeline.
        if counts["fatals"] or counts["errors"]:
            return Verdict.FAILED
        if job_status == "FAIL":
            return Verdict.FAILED
    if counts["warnings"]:
        return Verdict.PASSED_WITH_WARNINGS
    if job_status == "SUCCESS":
        return Verdict.PASSED
    return Verdict.FAILED


def run_daisy202_check(
    target: Path,
    *,
    progress=None,
) -> CheckResult | None:
    """Validate a DAISY 2.02 folder via Pipeline.

    Returns None when Pipeline is unavailable (caller should treat as
    unsupported / silent). Returns CheckResult on success or infra failure
    after a job was attempted.
    """
    target = target.expanduser().resolve()
    checked_at = datetime.now().astimezone()
    ncc = path_to_ncc(target)
    if ncc is None:
        return None

    status = probe_pipeline()
    if status is None:
        return None

    if progress:
        progress("Running DAISY 2.02 Validator…")

    job_id: str | None = None
    try:
        job_id = create_daisy202_job(status, ncc)
        job_status, job_xml = wait_for_job(status, job_id)
        messages = job_messages_text(job_xml)
        job_log = fetch_job_log(status, job_id)

        html_text = ""
        with tempfile.TemporaryDirectory(prefix="ebraille-pipeline-") as tmp:
            report_path = download_html_report(status, job_id, Path(tmp))
            if report_path is not None and report_path.is_file():
                html_text = report_path.read_text(encoding="utf-8", errors="replace")

        issues, info_lines = (
            parse_daisy202_html_report(html_text) if html_text else ([], [])
        )
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

        if verdict == Verdict.ERROR and not issues:
            return CheckResult(
                verdict=Verdict.ERROR,
                error_message="DAISY Pipeline job ended with an error.",
                raw_log="\n".join(log_parts).strip(),
                tool_name="DAISY Pipeline",
                tool_version=status.version,
                checked_at=checked_at,
                target_path=str(target),
            )

        if not html_text and not issues:
            # Job finished but no report — still surface job status.
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
        )
    except TimeoutError as exc:
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=str(exc),
            tool_name="DAISY Pipeline",
            tool_version=status.version,
            checked_at=checked_at,
            target_path=str(target),
        )
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=f"DAISY Pipeline check failed: {exc}",
            tool_name="DAISY Pipeline",
            tool_version=status.version,
            checked_at=checked_at,
            target_path=str(target),
        )
    finally:
        if job_id is not None:
            delete_job(status, job_id)
