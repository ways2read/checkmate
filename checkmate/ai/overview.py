"""AI overview of a full CheckMate validation report."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from ..i18n import _, get_language, language_display_name
from ..models import CheckResult, Issue, Severity
from .explain import ExplainResult
from .litellm_client import (
    DEFAULT_EXPLAIN_MAX_TOKENS,
    ensure_credentials_ready,
    litellm_available,
)
from .session import ExplainSession, ProviderError

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]

_MAX_UNIQUE_ISSUES = 50
_MAX_MESSAGE_CHARS = 180
_DEFAULT_OVERVIEW_MAX_TOKENS = DEFAULT_EXPLAIN_MAX_TOKENS


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _status(cb: StatusCallback | None, message: str) -> None:
    if cb is not None:
        try:
            cb(message)
        except Exception:
            logger.debug("AI status callback failed", exc_info=True)


def _looks_truncated(text: str, finish_reason: str | None) -> bool:
    if (finish_reason or "").lower() in {"length", "max_tokens"}:
        return True
    t = text or ""
    if not t.strip():
        return False
    return t.count("```") % 2 == 1


def _merge_continuation(first: str, second: str) -> str:
    a = (first or "").rstrip()
    b = (second or "").lstrip()
    if not b:
        return a
    if not a:
        return b
    return f"{a}\n\n{b}"


def _language_name() -> str:
    return language_display_name()


def _section_headings() -> tuple[str, str, str, str, str]:
    return (
        _("Overall assessment"),
        _("Main themes"),
        _("Suggested priorities"),
        _("Practical next steps"),
        _("Caveats"),
    )


def _unique_issue_rows(issues: list[Issue]) -> list[tuple[Issue, int]]:
    """First instance of each (source, code) with occurrence counts."""
    groups: dict[tuple[str, str], list] = {}
    order: list[tuple[str, str]] = []
    for issue in issues:
        key = (issue.source or "", issue.code or "")
        if key not in groups:
            groups[key] = [issue, 1]
            order.append(key)
        else:
            groups[key][1] += 1
    return [(groups[key][0], int(groups[key][1])) for key in order]


def _severity_rank(severity: Severity) -> int:
    order = {
        Severity.FATAL: 0,
        Severity.ERROR: 1,
        Severity.WARNING: 2,
        Severity.INFO: 3,
        Severity.USAGE: 4,
        Severity.UNKNOWN: 5,
    }
    return order.get(severity, 5)


def _trim_message(message: str) -> str:
    text = " ".join((message or "").split())
    if len(text) <= _MAX_MESSAGE_CHARS:
        return text
    return text[: _MAX_MESSAGE_CHARS - 1].rstrip() + "…"


def build_overview_context(result: CheckResult) -> dict[str, str]:
    """Compact report summary for the overview prompt (no file excerpts)."""
    ctx: dict[str, str] = {
        "verdict": result.verdict.label,
        "headline": result.headline,
        "fatals": str(result.fatals),
        "errors": str(result.errors),
        "warnings": str(result.warnings),
        "infos": str(result.infos),
        "usages": str(result.usages),
        "issue_total": str(len(result.issues)),
    }
    if result.target_path:
        ctx["target_path"] = result.target_path
        ctx["target_name"] = Path(result.target_path).name
    if result.tool_name:
        tool = result.tool_name
        if result.tool_version:
            tool = f"{tool} {result.tool_version}".strip()
        ctx["tool"] = tool
    if result.error_message:
        ctx["error_message"] = result.error_message.strip()

    try:
        from ..publication import classify_publication

        if result.target_path:
            ctx["publication_kind"] = classify_publication(
                Path(result.target_path)
            ).value
    except Exception:
        pass

    source_lines: list[str] = []
    for source, counts in result.source_counts:
        sub = CheckResult._severity_parts(
            int(counts.get("fatals", 0) or 0),
            int(counts.get("errors", 0) or 0),
            int(counts.get("warnings", 0) or 0),
        )
        source_lines.append(
            f"{source}: " + (", ".join(sub) if sub else _("no errors or warnings"))
        )
    if source_lines:
        ctx["source_counts"] = "\n".join(source_lines)

    meta_lines = [
        f"{label}: {value}"
        for label, value in result.extra_meta
        if (value or "").strip()
    ]
    if meta_lines:
        ctx["extra_meta"] = "\n".join(meta_lines)

    # Prefer higher-severity unique codes first so the capped list stays useful.
    rows = _unique_issue_rows(list(result.issues))
    rows.sort(key=lambda pair: (_severity_rank(pair[0].severity), pair[0].code or ""))
    truncated = len(rows) > _MAX_UNIQUE_ISSUES
    rows = rows[:_MAX_UNIQUE_ISSUES]
    lines: list[str] = []
    for issue, count in rows:
        code = issue.code or "—"
        if count > 1:
            code = f"{code} ×{count}"
        loc = (issue.location or "").strip() or "—"
        msg = _trim_message(issue.message)
        source = issue.source or "—"
        lines.append(
            f"- [{issue.severity.label}] {source} · {code} — {msg} — {loc}"
        )
    ctx["unique_issue_count"] = str(len(rows))
    ctx["issues_list"] = "\n".join(lines) if lines else ""
    if truncated:
        ctx["issues_truncated"] = "1"
    return ctx


def build_overview_system_prompt() -> str:
    lang = _language_name()
    lang_code = get_language()
    h1, h2, h3, h4, h5 = _section_headings()
    return f"""You are an accessibility publishing assistant inside CheckMate, a validation tool.
Write a concise overview of a full validation report for publishers and remediators.

LANGUAGE (mandatory):
- The CheckMate UI language is {lang} (code: {lang_code}).
- Write the entire reply in {lang}, including all headings and bullets.
- Do not use English unless the UI language is English.
- Checker codes and file paths may stay in their original form.

Structure your reply with these exact markdown headings (and no others as top-level headings):

## {h1}
## {h2}
## {h3}
## {h4}
## {h5}

Rules:
- Base the overview only on the report summary and issue list provided.
- Do not invent issues, conformance rules, or file contents that are not in the input.
- If the publication passed with no problems, say so briefly and keep later sections short.
- If the issue list was truncated, say that the overview covers the highest-severity unique codes only.
- Group related findings into themes; do not restate every line from the issue list.
- Prefer a practical remediation order (blocking fatals/errors before warnings).
- Keep each section concise (a short paragraph or a few bullets).
- Use markdown (headings, lists, optional fenced code) so the reply can be shown as HTML.
- Do not offer to rewrite the whole publication.
"""


def build_overview_user_prompt(ctx: dict[str, str]) -> str:
    lang = _language_name()
    lines = [
        f"Write an AI overview of this CheckMate validation report. Reply entirely in {lang}.",
        f"- Verdict: {ctx.get('verdict', '')}",
        f"- Headline: {ctx.get('headline', '')}",
        f"- Fatals: {ctx.get('fatals', '0')}; Errors: {ctx.get('errors', '0')}; "
        f"Warnings: {ctx.get('warnings', '0')}; Infos: {ctx.get('infos', '0')}; "
        f"Usages: {ctx.get('usages', '0')}",
        f"- Total issue rows: {ctx.get('issue_total', '0')}",
        f"- Unique codes included: {ctx.get('unique_issue_count', '0')}",
    ]
    if ctx.get("publication_kind"):
        lines.append(f"- Publication kind: {ctx['publication_kind']}")
    if ctx.get("target_name"):
        lines.append(f"- Publication: {ctx['target_name']}")
    if ctx.get("tool"):
        lines.append(f"- Checker: {ctx['tool']}")
    if ctx.get("error_message"):
        lines.append(f"- Tool error: {ctx['error_message']}")
    if ctx.get("source_counts"):
        lines.append("")
        lines.append("Per-checker counts:")
        lines.append(ctx["source_counts"])
    if ctx.get("extra_meta"):
        lines.append("")
        lines.append("Extra metadata:")
        lines.append(ctx["extra_meta"])
    lines.append("")
    if ctx.get("issues_list"):
        lines.append("Unique issues (severity · source · code — message — location):")
        lines.append(ctx["issues_list"])
        if ctx.get("issues_truncated"):
            lines.append("")
            lines.append(
                "Note: the list above is truncated to the highest-severity unique codes."
            )
    else:
        lines.append("No individual issues were listed in the report.")
    return "\n".join(lines)


def _continue_prompt() -> str:
    lang = _language_name()
    return (
        f"Your previous reply was cut off before it finished.\n"
        f"Continue from exactly where you stopped. "
        f"Do not repeat completed sections. "
        f"Close any open code fences. "
        f"Reply entirely in {lang}."
    )


def explain_overview(
    result: CheckResult,
    *,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
) -> ExplainResult:
    """Generate a report-level AI overview for *result*."""
    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled")

    if not litellm_available():
        logger.error("Overview requested but litellm is not installed")
        return ExplainResult(ok=False, error_key="no_litellm")

    _status(status_callback, _("Checking AI credentials…"))
    ok, err = ensure_credentials_ready()
    if not ok:
        logger.warning("Overview credentials not ready: %s", err)
        return ExplainResult(ok=False, error_key=err or "no_key")
    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled")

    ctx = build_overview_context(result)
    try:
        session = ExplainSession.create()
    except RuntimeError as e:
        return ExplainResult(ok=False, error_key=str(e) or "no_key")

    _status(status_callback, _("Checking AI connection…"))
    conn_ok, conn_err, conn_detail = session.check_connection(cancel_event=cancel_event)
    if not conn_ok:
        return ExplainResult(
            ok=False,
            error_key=conn_err or "network",
            text=conn_detail,
            session=session,
        )
    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)

    _status(status_callback, _("Writing overview…"))
    logger.info(
        "Overview request starting model=%s verdict=%s issues=%s",
        session.model,
        result.verdict.value,
        len(result.issues),
    )
    try:
        text = session.ask(
            system=build_overview_system_prompt(),
            user=build_overview_user_prompt(ctx),
            max_tokens=_DEFAULT_OVERVIEW_MAX_TOKENS,
        )
    except ProviderError as e:
        return ExplainResult(
            ok=False,
            error_key=e.error_key,
            text=e.detail,
            session=session,
        )
    except RuntimeError as e:
        return ExplainResult(ok=False, error_key=str(e) or "no_key", session=session)
    except Exception as e:
        logger.exception("Overview provider error")
        return ExplainResult(
            ok=False, error_key="provider_error", text=str(e), session=session
        )

    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)

    if not (text or "").strip():
        logger.warning("Overview returned empty response model=%s", session.model)
        return ExplainResult(ok=False, error_key="empty_response", session=session)

    if _looks_truncated(text, session.last_finish_reason):
        logger.warning(
            "Overview truncated model=%s finish_reason=%s; requesting continuation",
            session.model,
            session.last_finish_reason,
        )
        if _cancelled(cancel_event):
            return ExplainResult(ok=False, error_key="cancelled", session=session)
        _status(status_callback, _("Continuing truncated reply…"))
        try:
            cont = session.followup(
                _continue_prompt(),
                max_tokens=_DEFAULT_OVERVIEW_MAX_TOKENS,
            )
            text = _merge_continuation(text, cont)
        except Exception:
            logger.exception("Overview continuation failed")

    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)

    return ExplainResult(ok=True, text=text, session=session)
