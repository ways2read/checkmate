"""Q&A about a Fido image report, using CheckMate's Fido credentials."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..i18n import _, get_language, language_display_name
from .explain import ExplainResult
from .fido_image_report import ImageReport
from .session import ExplainSession, ProviderError

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


def _language_name() -> str:
    return language_display_name()


def _publication_format_rules(publication_format: str) -> str:
    key = (publication_format or "").strip().lower()
    if key == "pdf":
        return (
            "This is a PDF. Alt is the figure /Alt (accessible name). PDF has no "
            "aria-details or details/summary. Longer description belongs in visible "
            "body text. Prefer tagged tables and associated MathML for equations; "
            "do not recommend EPUB-only techniques."
        )
    if key in {"epub", "ebrl", "ebraille"}:
        host = "eBraille" if key in {"ebrl", "ebraille"} else "EPUB"
        return (
            f"This is {host}. Keep short alt concise. Complex images can use an "
            "extended description (details/summary, following prose, or aria-details). "
            "Prefer real table markup and MathML; do not recommend PDF-only tagging."
        )
    return (
        "Stay with techniques that exist in this publication format. "
        "Do not recommend markup that only exists in a different format."
    )


def build_report_qa_system_prompt(publication_format: str = "") -> str:
    lang = _language_name()
    lang_code = get_language()
    rules = _publication_format_rules(publication_format)
    return f"""You are an accessibility publishing assistant inside CheckMate.
You answer questions about an image alt-text report for this document.

LANGUAGE (mandatory):
- The CheckMate UI language is {lang} (code: {lang_code}).
- Write the entire reply in {lang}.
- Do not use English unless the UI language is English.

Rules:
- Use only the inventory and (if present) sniff-test notes you were given.
- Do not invent images, alt text, or findings.
- An AI Image Sniff Test may not have been run yet; say so if the user asks
  about AI verdicts that are not in the inventory.
- Answer directly in a natural, conversational way. Prefer short paragraphs
  or a few bullets. Stay focused on what was asked.
- Tailor advice to the publication format rules below. Do not recommend
  techniques that only exist in a different format.

Publication format rules:
{rules}
"""


def ask_report_qa(
    session: ExplainSession,
    report: ImageReport,
    question: str,
    *,
    cancel_event: threading.Event | None = None,
    status_callback: StatusCallback | None = None,
) -> ExplainResult:
    """Answer a free-form question about the combined image report."""
    q = (question or "").strip()
    if not q:
        return ExplainResult(ok=False, error_key="empty_question", session=session)
    if cancel_event is not None and cancel_event.is_set():
        return ExplainResult(ok=False, error_key="cancelled", session=session)

    lang = _language_name()
    if status_callback is not None:
        status_callback(_("Thinking…"))
    try:
        if not session.messages:
            brief = report.qa_context_brief()
            user = (
                f"Here is the current image report (text only):\n\n{brief}\n\n"
                f"Reply entirely in {lang}.\n\nQuestion:\n{q}"
            )
            text = session.ask(
                system=build_report_qa_system_prompt(report.publication_format),
                user=user,
                max_tokens=4096,
            )
        else:
            text = session.followup(
                f"Reply entirely in {lang}.\n\nQuestion:\n{q}",
                max_tokens=4096,
            )
    except ProviderError as e:
        return ExplainResult(
            ok=False, error_key=e.error_key, text=e.detail, session=session
        )
    except Exception as e:
        logger.exception("Image report Q&A failed")
        return ExplainResult(
            ok=False, error_key="provider_error", text=str(e), session=session
        )

    if cancel_event is not None and cancel_event.is_set():
        return ExplainResult(ok=False, error_key="cancelled", session=session)
    return ExplainResult(ok=True, text=text, session=session)


def restore_qa_session_from_markdown(
    session: ExplainSession | None,
    report: ImageReport,
    markdown: str,
) -> bool:
    """Replay saved Q&A into the LLM thread (no API call). Return True if restored."""
    if session is None or getattr(session, "messages", None):
        return False
    from .markdown_html import conversation_turns_from_qa, followup_markdown_suffix

    text = (markdown or "").strip()
    suffix = followup_markdown_suffix(text) or text
    turns = conversation_turns_from_qa(suffix)
    pairs: list[tuple[str, str]] = []
    pending_user = ""
    for kind, _label, plain, _html in turns:
        if kind == "user":
            pending_user = plain
        elif kind == "assistant" and pending_user:
            pairs.append((pending_user, plain))
            pending_user = ""
    if not pairs:
        return False
    lang = _language_name()
    brief = report.qa_context_brief()
    q0, a0 = pairs[0]
    session.messages = [
        {
            "role": "system",
            "content": build_report_qa_system_prompt(report.publication_format),
        },
        {
            "role": "user",
            "content": (
                f"Here is the current image report (text only):\n\n{brief}\n\n"
                f"Reply entirely in {lang}.\n\nQuestion:\n{q0}"
            ),
        },
        {"role": "assistant", "content": a0},
    ]
    for question, answer in pairs[1:]:
        session.messages.append(
            {
                "role": "user",
                "content": f"Reply entirely in {lang}.\n\nQuestion:\n{question}",
            }
        )
        session.messages.append({"role": "assistant", "content": answer})
    return True


def enrich_qa_session_with_sniff(
    session: ExplainSession | None, report: ImageReport
) -> None:
    """Attach sniff-test context to an existing Q&A thread (no extra API call)."""
    if session is None or not getattr(session, "messages", None):
        return
    brief = report.qa_context_brief()
    if not brief.strip():
        return
    session.messages.append(
        {
            "role": "user",
            "content": (
                "An AI Image Sniff Test has now been completed. Use this updated "
                f"report context for later answers. Continue to reply in {_language_name()}.\n\n"
                + brief
            ),
        }
    )
    session.messages.append(
        {
            "role": "assistant",
            "content": "Understood. I will use the updated sniff-test report for later answers.",
        }
    )
