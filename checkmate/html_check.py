"""Orchestrate HTML crawl + Nu HTML Checker + axe, merge like EPUBCheck + Ace."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .axe_html import (
    AXE_HTML_DISPLAY_NAME,
    html_axe_available,
    run_axe_on_urls,
)
from .clipboard_markup import SNIPPET_AXE_CODES, is_clipboard_snippet_path
from .html_crawl import (
    DEFAULT_CRAWL_CAP,
    LocalHtmlServer,
    pages_for_html_check,
    prefer_working_page_url,
    prepare_html_target,
)
from .models import CheckResult, Issue, Severity, Verdict
from .settings import html_checkers, html_checkers_label, html_follow_links
from .vnu_check import VNU_DISPLAY_NAME, run_vnu_on_urls

ProgressCallback = Callable[..., None]


@dataclass
class HtmlSession:
    """Last successful HTML check — reused by alt-text export."""

    target: str
    pages: list[str]
    images: list[dict[str, Any]] = field(default_factory=list)
    crawl_cap: int = DEFAULT_CRAWL_CAP
    page_hash: str = ""


_LAST_SESSION: HtmlSession | None = None


def last_html_session() -> HtmlSession | None:
    return _LAST_SESSION


def remember_html_session(session: HtmlSession) -> None:
    global _LAST_SESSION
    _LAST_SESSION = session


def clear_html_session() -> None:
    global _LAST_SESSION
    _LAST_SESSION = None


def html_page_hash(pages: list[str], *, cap: int, images: list[dict[str, Any]] | None = None) -> str:
    payload = {
        "cap": cap,
        "pages": list(pages),
        "images": [
            {
                "src": rec.get("src"),
                "alt": rec.get("alt"),
                "selector": rec.get("selector"),
                "pageUrl": rec.get("pageUrl"),
            }
            for rec in (images or [])
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _emit_progress(progress, message: str, *, announce: bool = True) -> None:
    if not progress:
        return
    try:
        progress(message, announce=announce)
    except TypeError:
        progress(message)


def _verdict_from_counts(counts: dict[str, int]) -> Verdict:
    if counts["fatals"] or counts["errors"]:
        return Verdict.FAILED
    if counts["warnings"]:
        return Verdict.PASSED_WITH_WARNINGS
    return Verdict.PASSED


def drop_snippet_axe_issues(result: CheckResult) -> CheckResult:
    """Drop page-chrome axe rules that are noise for a wrapped clipboard snippet."""
    kept = [issue for issue in result.issues if issue.code not in SNIPPET_AXE_CODES]
    if len(kept) == len(result.issues):
        return result
    counts = _counts_from_issues(kept)
    result.issues = kept
    result.fatals = counts["fatals"]
    result.errors = counts["errors"]
    result.warnings = counts["warnings"]
    result.infos = counts["infos"]
    result.usages = counts["usages"]
    if result.verdict != Verdict.ERROR:
        result.verdict = _verdict_from_counts(counts)
    return result


def _verdict_rank(verdict: Verdict) -> int:
    return {
        Verdict.ERROR: 0,
        Verdict.FAILED: 1,
        Verdict.PASSED_WITH_WARNINGS: 2,
        Verdict.PASSED: 3,
    }.get(verdict, 0)


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


def _clone_issues(issues: list[Issue], *, default_source: str) -> list[Issue]:
    return [
        Issue(
            severity=issue.severity,
            code=issue.code,
            message=issue.message,
            location=issue.location,
            source=issue.source or default_source,
            help_url=issue.help_url,
            help_title=issue.help_title,
            help_text=issue.help_text,
            impact=issue.impact,
            ruleset=issue.ruleset,
        )
        for issue in issues
    ]


def merge_vnu_and_axe(
    vnu_result: CheckResult | None,
    axe_result: CheckResult | None,
    *,
    target: str,
    pages: list[str],
    images: list[dict[str, Any]],
    checked_at: datetime | None = None,
) -> CheckResult:
    """Combine Nu + axe the same way EPUBCheck + Ace are merged."""
    when = checked_at or datetime.now().astimezone()
    vnu_issues = (
        _clone_issues(vnu_result.issues, default_source=VNU_DISPLAY_NAME)
        if vnu_result is not None
        else []
    )
    axe_issues = (
        _clone_issues(axe_result.issues, default_source=AXE_HTML_DISPLAY_NAME)
        if axe_result is not None
        else []
    )
    if (
        axe_result is not None
        and axe_result.verdict == Verdict.ERROR
        and axe_result.error_message
        and not axe_issues
    ):
        axe_issues.append(
            Issue(
                severity=Severity.ERROR,
                code="axe-error",
                message=axe_result.error_message,
                source=AXE_HTML_DISPLAY_NAME,
            )
        )

    issues = vnu_issues + axe_issues
    counts = _counts_from_issues(issues)
    source_counts: list[tuple[str, dict[str, int]]] = []
    if vnu_result is not None:
        source_counts.append((VNU_DISPLAY_NAME, _counts_from_issues(vnu_issues)))
    if axe_result is not None:
        source_counts.append((AXE_HTML_DISPLAY_NAME, _counts_from_issues(axe_issues)))

    axe_verdict = axe_result.verdict if axe_result is not None else Verdict.PASSED
    vnu_verdict = vnu_result.verdict if vnu_result is not None else Verdict.PASSED
    if axe_result is not None and axe_verdict == Verdict.ERROR and vnu_verdict != Verdict.ERROR:
        axe_verdict = Verdict.FAILED

    if vnu_result is None:
        verdict = axe_verdict
    elif axe_result is None:
        verdict = vnu_verdict
    elif _verdict_rank(vnu_verdict) <= _verdict_rank(axe_verdict):
        verdict = vnu_verdict
    else:
        verdict = axe_verdict
    if counts["fatals"] or counts["errors"]:
        if verdict != Verdict.ERROR:
            verdict = Verdict.FAILED
    elif counts["warnings"] and verdict == Verdict.PASSED:
        verdict = Verdict.PASSED_WITH_WARNINGS

    log_parts: list[str] = []
    if vnu_result is not None and vnu_result.raw_log.strip():
        log_parts.append("--- Nu HTML Checker ---")
        log_parts.append(vnu_result.raw_log.strip())
    if axe_result is not None and axe_result.raw_log.strip():
        if log_parts:
            log_parts.append("")
        log_parts.append("--- axe ---")
        log_parts.append(axe_result.raw_log.strip())
    elif axe_result is not None and axe_result.error_message:
        if log_parts:
            log_parts.append("")
        log_parts.append("--- axe ---")
        log_parts.append(axe_result.error_message)

    extra_meta: list[tuple[str, str]] = []
    if vnu_result is not None:
        extra_meta.extend(vnu_result.extra_meta)
        if vnu_result.tool_version and not any(
            label == "Nu HTML Checker version" for label, _ in extra_meta
        ):
            extra_meta.append(("Nu HTML Checker version", vnu_result.tool_version))
    if pages:
        extra_meta.append(("Pages checked", str(len(pages))))

    if vnu_result is not None and axe_result is not None:
        tool_name = "Nu HTML Checker + axe"
        tool_version = ""
    elif vnu_result is not None:
        tool_name = VNU_DISPLAY_NAME
        tool_version = vnu_result.tool_version
    else:
        tool_name = AXE_HTML_DISPLAY_NAME
        tool_version = axe_result.tool_version if axe_result is not None else ""

    error_message = ""
    if verdict == Verdict.ERROR:
        if vnu_result is not None and vnu_result.verdict == Verdict.ERROR:
            error_message = vnu_result.error_message
        elif axe_result is not None:
            error_message = axe_result.error_message

    return CheckResult(
        verdict=verdict,
        fatals=counts["fatals"],
        errors=counts["errors"],
        warnings=counts["warnings"],
        infos=counts["infos"],
        usages=counts["usages"],
        issues=issues,
        raw_log="\n".join(log_parts).strip(),
        exit_code=(
            vnu_result.exit_code
            if vnu_result is not None
            else (axe_result.exit_code if axe_result is not None else None)
        ),
        error_message=error_message,
        tool_name=tool_name,
        tool_version=tool_version,
        checked_at=when,
        target_path=target,
        extra_meta=extra_meta,
        source_counts=source_counts,
        html_pages=list(pages),
        html_images=list(images),
    )


def run_html_check(target: str, *, progress: ProgressCallback | None = None) -> CheckResult:
    """Crawl a file/folder/URL, run the selected HTML checkers, merge results."""
    text = (target or "").strip().strip('"')
    checked_at = datetime.now().astimezone()
    mode = html_checkers()
    want_vnu = mode in {"both", "vnu"}
    want_axe = mode in {"both", "axe"}

    server: LocalHtmlServer | None = None
    try:
        start_url, local_root, server = prepare_html_target(text)
    except FileNotFoundError:
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=f"Path not found: {text}",
            target_path=text,
            checked_at=checked_at,
        )
    except OSError as exc:
        return CheckResult(
            verdict=Verdict.ERROR,
            error_message=f"Could not prepare HTML target: {exc}",
            target_path=text,
            checked_at=checked_at,
        )

    try:
        follow_links = html_follow_links()
        crawl_cap = DEFAULT_CRAWL_CAP if follow_links else 1
        if follow_links:
            _emit_progress(progress, "Finding linked pages…")
        else:
            _emit_progress(progress, "Preparing page…")
        start_url, https_note = prefer_working_page_url(start_url)
        pages = pages_for_html_check(
            start_url,
            follow_links=follow_links,
            cap=DEFAULT_CRAWL_CAP,
            local_root=local_root,
            progress=lambda msg: _emit_progress(progress, msg, announce=False),
        )

        vnu_result: CheckResult | None = None
        axe_result: CheckResult | None = None
        images: list[dict[str, Any]] = []

        if want_vnu:
            vnu_result = run_vnu_on_urls(
                pages, progress=progress, local_root=local_root
            )

        if want_axe:
            # Always attempt axe when the user selected it — do not skip silently
            # because Chrome/Node preflight failed. run_axe_on_urls reports why.
            axe_result, images = run_axe_on_urls(pages, progress=progress)
        elif html_axe_available():
            _, images = run_axe_on_urls(pages, progress=progress, images_only=True)

        snippet = False
        try:
            snippet = is_clipboard_snippet_path(Path(text))
        except OSError:
            snippet = False
        if snippet and axe_result is not None:
            axe_result = drop_snippet_axe_issues(axe_result)

        merged = merge_vnu_and_axe(
            vnu_result if want_vnu else None,
            axe_result if want_axe else None,
            target=text,
            pages=pages,
            images=images,
            checked_at=checked_at,
        )
        if https_note:
            merged.issues.insert(
                0,
                Issue(
                    severity=Severity.INFO,
                    code="https-unavailable",
                    message=https_note,
                    location=start_url,
                    source="CheckMate",
                ),
            )
            merged.infos += 1
            merged.extra_meta.append(("Opened as", start_url))
        if snippet:
            merged.extra_meta.append(("Checked as", "HTML snippet"))
        extra_paths: list[Path] = []
        if local_root is not None:
            from .html_crawl import url_to_local_path

            for url in pages:
                mapped = url_to_local_path(url, local_root)
                if mapped is not None and mapped.is_file():
                    extra_paths.append(mapped)
        from .mathml_quality import attach_mathml_quality
        from .publication import is_html_url
        from .settings import mathml_nordic_guidelines

        if mathml_nordic_guidelines() and (
            extra_paths or (text and not is_html_url(text))
        ):
            _emit_progress(progress, "Checking MathML quality…")
        merged = attach_mathml_quality(
            merged, text, extra_paths=extra_paths or None
        )
        if not merged.tool_name:
            merged.tool_name = html_checkers_label(mode)
        remember_html_session(
            HtmlSession(
                target=text,
                pages=pages,
                images=images,
                crawl_cap=crawl_cap,
                page_hash=html_page_hash(
                    pages, cap=crawl_cap, images=images
                ),
            )
        )
        return merged
    finally:
        if server is not None:
            server.stop()
