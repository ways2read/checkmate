"""Run Ace by DAISY (CLI) when available on PATH and parse report.json."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .models import CheckResult, Issue, Severity, Verdict
from .subprocess_util import hidden_run_kwargs

ACE_DISPLAY_NAME = "Ace"

_ACE_PATH_CACHE: Path | None | bool = False  # False = unset
_ACE_VERSION_CACHE: str | None | bool = False


def find_ace() -> Path | None:
    """Return the Ace CLI executable if it is on PATH."""
    global _ACE_PATH_CACHE
    if _ACE_PATH_CACHE is not False:
        return _ACE_PATH_CACHE if isinstance(_ACE_PATH_CACHE, Path) else None

    for name in ("ace", "ace.cmd", "ace.exe"):
        found = shutil.which(name)
        if found:
            path = Path(found)
            _ACE_PATH_CACHE = path
            return path
    _ACE_PATH_CACHE = None
    return None


def probe_ace() -> str | None:
    """Return Ace version string, or None if Ace is not available."""
    global _ACE_VERSION_CACHE
    if _ACE_VERSION_CACHE is not False:
        return _ACE_VERSION_CACHE if isinstance(_ACE_VERSION_CACHE, str) else None

    ace = find_ace()
    if ace is None:
        _ACE_VERSION_CACHE = None
        return None
    try:
        proc = subprocess.run(
            [str(ace), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
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
    if not version or proc.returncode not in (0, None):
        # Some wrappers still print the version on stdout with 0.
        if not version:
            _ACE_VERSION_CACHE = None
            return None
    _ACE_VERSION_CACHE = version
    return version


def clear_ace_cache() -> None:
    """Reset PATH/version caches (tests / after install)."""
    global _ACE_PATH_CACHE, _ACE_VERSION_CACHE
    _ACE_PATH_CACHE = False
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
            help_block = test.get("help") or {}
            if isinstance(help_block, dict):
                help_msg = str(help_block.get("dct:description") or "").strip()
                help_url = str(help_block.get("url") or "").strip()
                extras = [p for p in (help_msg, help_url) if p]
                if extras:
                    if message:
                        message = f"{message} — " + " — ".join(extras)
                    else:
                        message = " — ".join(extras)
            if not message:
                message = str(test.get("dct:description") or code).strip()
            issues.append(
                Issue(
                    severity=_severity_from_impact(test.get("earl:impact")),
                    code=code,
                    message=message,
                    location=_location_for_assertion(doc_url, assertion),
                    source=ACE_DISPLAY_NAME,
                )
            )
    return issues


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _parse_error_message(stdout: str, stderr: str) -> str:
    combined = _strip_ansi("\n".join(p for p in (stdout, stderr) if p))
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

    Returns ``None`` when Ace is not on PATH (caller should skip silently).
    Otherwise returns a CheckResult (pass/fail or tool error).
    """
    target = target.expanduser().resolve()
    ace = find_ace()
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
            str(ace),
            "--outdir",
            str(outdir),
            "--force",
            str(target),
        ]
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
