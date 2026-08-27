"""Build prompts and run Explain with AI for a validation issue."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from ..i18n import _, get_language, language_display_name
from ..models import CheckResult, Issue
from .context import gather_issue_context
from .litellm_client import ensure_credentials_ready, litellm_available
from .resources import (
    authoritative_guidance_for_explain,
    is_mathml_quality_issue,
    is_web_html_issue,
    kb_article_body_for_prompt,
    kb_article_body_prompt_block,
    resources_prompt_block,
)
from .session import ExplainSession, ProviderError

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


@dataclass
class ExplainResult:
    ok: bool
    text: str = ""
    error_key: str | None = None
    session: ExplainSession | None = None


def _language_name() -> str:
    return language_display_name()


def _section_headings() -> tuple[str, str, str, str, str]:
    """Localized H2 titles matching CheckMate's UI language."""
    return (
        _("What this means"),
        _("Why it matters"),
        _("Where in the file"),
        _("How to fix"),
        _("Learn more"),
    )


def build_system_prompt(issue: Issue) -> str:
    lang = _language_name()
    lang_code = get_language()
    resources = resources_prompt_block(issue)
    guidance = authoritative_guidance_for_explain(issue)
    h1, h2, h3, h4, h5 = _section_headings()
    if is_mathml_quality_issue(issue):
        role = (
            "You are a MathML accessibility assistant inside CheckMate. "
            "Explain quality warnings clearly to people remediating MathML."
        )
        scope = (
            "- This issue is MathML markup. Do not discuss EPUB package documents, "
            "PDF tagging, or rewriting a whole book.\n"
            "- Prefer the Nordic MathML Guidelines. Mention that the check can "
            "false-positive."
        )
    elif is_web_html_issue(issue):
        role = (
            "You are a web accessibility assistant inside CheckMate. "
            "Explain checker messages clearly to people remediating HTML web pages."
        )
        scope = (
            '- This issue is on a web page. Do not discuss EPUB, eBraille, '
            "DAISY talking books, or audiobooks.\n"
            '- Do not offer to rewrite the whole site; focus on this issue.'
        )
    else:
        role = (
            "You are an accessibility publishing assistant inside CheckMate, "
            "a validation tool. Explain checker messages clearly to publishers "
            "and remediators."
        )
        scope = "- Do not offer to rewrite the whole book; focus on this issue."
    return f"""{role} Be accurate and practical.

LANGUAGE (mandatory):
- The CheckMate UI language is {lang} (code: {lang_code}).
- Write the entire reply in {lang}, including all headings, bullet points, and link titles.
- Do not use English unless the UI language is English.
- Checker codes and file paths may stay in their original form.

Structure your reply with these exact markdown headings (and no others as top-level headings):

## {h1}
## {h2}
## {h3}
## {h4}
## {h5}

{guidance}

Rules:
- In "{h5}", use only the trusted resources listed below (you may omit irrelevant ones).
- In "{h5}", write each resource as a markdown link: `[Title](https://example.com/)`.
{scope}
- Keep each section concise (a short paragraph or a few bullets).
- Use markdown (headings, lists, links, fenced code) so the reply can be shown as HTML.

{resources}
"""


def build_user_prompt(ctx: dict[str, str], *, kb_body: str = "") -> str:
    lang = _language_name()
    lines = [
        f"Explain this validation issue. Reply entirely in {lang}.",
        f"- Severity: {ctx.get('severity', '')}",
        f"- Source: {ctx.get('source', '') or '—'}",
        f"- Code: {ctx.get('code', '')}",
        f"- Location: {ctx.get('location', '') or '—'}",
        f"- Message: {ctx.get('message', '')}",
    ]
    if ctx.get("publication_kind"):
        kind = ctx["publication_kind"]
        if kind == "html":
            lines.append("- Host: HTML web page (not a packaged publication)")
        elif kind == "svg":
            lines.append("- Host: SVG document")
        elif kind == "css":
            lines.append("- Host: CSS stylesheet")
        elif kind == "xml":
            lines.append("- Host: XML document")
        elif kind == "mathml":
            lines.append("- Host: MathML")
        else:
            lines.append(f"- Publication kind: {kind}")
    if ctx.get("tool"):
        lines.append(f"- Checker: {ctx['tool']}")
    if ctx.get("file_member"):
        lines.append(f"- File: {ctx['file_member']}")
    snippet = (ctx.get("snippet") or "").strip()
    if snippet:
        lines.append("")
        lines.append("Flagged markup at the reported location (may be compacted):")
        lines.append("```")
        lines.append(snippet)
        lines.append("```")
    if ctx.get("file_excerpt"):
        lines.append("")
        lines.append("Relevant file excerpt (line numbers when available):")
        lines.append("```")
        lines.append(ctx["file_excerpt"])
        lines.append("```")
    body_block = kb_article_body_prompt_block(kb_body, for_fix=False)
    if body_block:
        lines.append("")
        lines.append(body_block)
    return "\n".join(lines)


def _looks_truncated(text: str, finish_reason: str | None) -> bool:
    if (finish_reason or "").lower() in {"length", "max_tokens"}:
        return True
    t = text or ""
    if not t.strip():
        return False
    # Unclosed markdown fence (common when cut mid-example).
    return t.count("```") % 2 == 1


def _continue_prompt() -> str:
    lang = _language_name()
    return (
        f"Your previous reply was cut off before it finished.\n"
        f"Continue from exactly where you stopped. "
        f"Do not repeat completed sections. "
        f"Close any open code fences. "
        f"Reply entirely in {lang}."
    )


def _merge_continuation(first: str, second: str) -> str:
    a = (first or "").rstrip()
    b = (second or "").lstrip()
    if not b:
        return a
    if not a:
        return b
    return f"{a}\n\n{b}"


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _status(cb: StatusCallback | None, message: str) -> None:
    if cb is not None:
        try:
            cb(message)
        except Exception:
            logger.debug("AI status callback failed", exc_info=True)


def explain_issue(
    issue: Issue,
    result: CheckResult | None = None,
    *,
    target_path: str | None = None,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
) -> ExplainResult:
    from .litellm_client import DEFAULT_EXPLAIN_MAX_TOKENS

    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled")

    if not litellm_available():
        logger.error("Explain requested but litellm is not installed")
        return ExplainResult(ok=False, error_key="no_litellm")

    _status(status_callback, _("Checking AI credentials…"))
    ok, err = ensure_credentials_ready()
    if not ok:
        logger.warning("Explain credentials not ready: %s", err)
        return ExplainResult(ok=False, error_key=err or "no_key")
    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled")

    ctx = gather_issue_context(issue, result, target_path=target_path)
    kb_body = kb_article_body_for_prompt(issue)
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

    _status(status_callback, _("Explaining…"))
    logger.info("Explain request starting model=%s code=%s", session.model, issue.code)
    try:
        text = session.ask(
            system=build_system_prompt(issue),
            user=build_user_prompt(ctx, kb_body=kb_body),
            max_tokens=DEFAULT_EXPLAIN_MAX_TOKENS,
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
        logger.exception("Explain provider error")
        return ExplainResult(
            ok=False, error_key="provider_error", text=str(e), session=session
        )

    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)

    if not (text or "").strip():
        logger.warning("Explain returned empty response model=%s", session.model)
        return ExplainResult(ok=False, error_key="empty_response", session=session)

    if _looks_truncated(text, session.last_finish_reason):
        logger.warning(
            "Explain truncated model=%s finish_reason=%s; requesting continuation",
            session.model,
            session.last_finish_reason,
        )
        if _cancelled(cancel_event):
            return ExplainResult(ok=False, error_key="cancelled", session=session)
        _status(status_callback, _("Continuing truncated reply…"))
        try:
            cont = session.followup(
                _continue_prompt(),
                max_tokens=DEFAULT_EXPLAIN_MAX_TOKENS,
            )
            text = _merge_continuation(text, cont)
            if session.messages and session.messages[-1].get("role") == "assistant":
                session.messages[-1]["content"] = text
        except ProviderError as e:
            return ExplainResult(
                ok=False,
                error_key=e.error_key,
                text=e.detail,
                session=session,
            )
        except Exception as e:
            logger.exception("Explain continuation failed")
            return ExplainResult(
                ok=False, error_key="provider_error", text=str(e), session=session
            )

        if _looks_truncated(text, session.last_finish_reason):
            logger.warning(
                "Explain still truncated after continuation model=%s finish_reason=%s",
                session.model,
                session.last_finish_reason,
            )
            note = _(
                "\n\n---\n*Note: The AI reply was cut off again. "
                "Ask a follow-up such as “Please continue.”*"
            )
            text = (text or "").rstrip() + note

    logger.info("Explain request completed model=%s", session.model)
    return ExplainResult(ok=True, text=text, session=session)


def ask_followup(
    session: ExplainSession,
    question: str,
    *,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
) -> ExplainResult:
    q = (question or "").strip()
    if not q:
        return ExplainResult(ok=False, error_key="empty_question", session=session)
    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)

    lang = _language_name()
    h1, h2, h3, h4, h5 = _section_headings()
    _status(status_callback, _("Thinking…"))
    logger.info("Follow-up request starting model=%s", session.model)
    try:
        text = session.followup(
            f"Follow-up question about the same issue.\n"
            f"Reply entirely in {lang}.\n\n"
            f"Answer this question directly in a natural, conversational way. "
            f"Do NOT reuse the structured explanation layout with headings such as "
            f"## {h1}, ## {h2}, ## {h3}, ## {h4}, or ## {h5}. "
            f"Prefer short paragraphs or a few bullets; include a brief code example "
            f"only when it helps. Stay focused on what was asked.\n\n"
            f"Question:\n{q}"
        )
    except ProviderError as e:
        return ExplainResult(
            ok=False,
            error_key=e.error_key,
            text=e.detail,
            session=session,
        )
    except Exception as e:
        logger.exception("Follow-up provider error")
        return ExplainResult(
            ok=False, error_key="provider_error", text=str(e), session=session
        )

    if _cancelled(cancel_event):
        return ExplainResult(ok=False, error_key="cancelled", session=session)
    logger.info("Follow-up request completed model=%s", session.model)
    return ExplainResult(ok=True, text=text, session=session)


def error_message_for_key(key: str | None, detail: str = "") -> str:
    mapping = {
        "no_litellm": _(
            "AI support could not be loaded. Reinstall CheckMate, or check the "
            "debugging log for import errors."
        ),
        "no_code": _("No AI credentials found. Configure API keys or an unlock code in FIDO."),
        "no_key": _("No API key is available for the selected AI model. Check FIDO settings or your unlock code."),
        "no_model": _("No AI model is selected in FIDO settings."),
        "not_found": _("The AI services unlock code was not found. Check the code in FIDO."),
        "network": _("Could not reach the unlock server or AI provider. Check your connection."),
        "timeout": _(
            "The AI request timed out. Try again, or check your connection and FIDO settings."
        ),
        "cancelled": _("The AI request was cancelled."),
        "bad_json": _("The unlock server returned invalid data."),
        "expired": _("The AI services unlock code has expired."),
        "parse": _("The unlock data could not be processed."),
        "unlock_failed": _("Could not refresh AI credentials from the unlock code."),
        "unlock_empty": _("The unlock code did not provide usable API keys."),
        "empty_response": _("The AI returned an empty response."),
        "empty_question": _("Enter a follow-up question."),
        "provider_error": _("The AI provider returned an error."),
        "no_session": _("Explain the issue first, then ask a follow-up."),
        "no_source": _("Could not read the local article."),
        "bad_export": _(
            "Could not open that folder as a Fido alt-text export. "
            "Choose a folder that contains alt_text_export.csv and an images folder."
        ),
        "no_vision": _(
            "The selected AI model does not appear to support image input. "
            "Choose a vision-capable model in FIDO, then try again."
        ),
    }
    base = mapping.get(key or "", _("Could not explain this issue."))
    if detail and key in {
        "provider_error",
        "timeout",
        "network",
        "no_key",
        "no_model",
        "bad_export",
        "no_vision",
    }:
        return f"{base}\n\n{detail}"
    return base
