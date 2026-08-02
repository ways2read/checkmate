"""Build prompts and run Explain with AI for a validation issue."""

from __future__ import annotations

from dataclasses import dataclass

from ..i18n import _, get_language
from ..models import CheckResult, Issue
from .context import gather_issue_context
from .litellm_client import ensure_credentials_ready, litellm_available
from .resources import resources_prompt_block
from .session import ExplainSession


@dataclass
class ExplainResult:
    ok: bool
    text: str = ""
    error_key: str | None = None
    session: ExplainSession | None = None


def _language_name() -> str:
    return {
        "en": "English",
        "fr": "French",
        "es": "Spanish",
        "de": "German",
        "pt": "Portuguese",
    }.get(get_language(), "English")


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
    h1, h2, h3, h4, h5 = _section_headings()
    return f"""You are an accessibility publishing assistant inside CheckMate, a validation tool.
Explain checker messages clearly to publishers and remediators. Be accurate and practical.

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

Rules:
- Do not invent conformance requirements. If unsure, say what to verify.
- Prefer concrete markup/CSS/OPF steps for EPUB and eBraille.
- In "{h5}", use only the trusted resources listed below (you may omit irrelevant ones).
- In "{h5}", write each resource as a markdown link: `[Title](https://example.com/)`.
- Do not offer to rewrite the whole book; focus on this issue.
- Keep each section concise (a short paragraph or a few bullets).
- Use markdown (headings, lists, links, fenced code) so the reply can be shown as HTML.

{resources}
"""


def build_user_prompt(ctx: dict[str, str]) -> str:
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
        lines.append(f"- Publication kind: {ctx['publication_kind']}")
    if ctx.get("tool"):
        lines.append(f"- Checker: {ctx['tool']}")
    if ctx.get("file_member"):
        lines.append(f"- File: {ctx['file_member']}")
    if ctx.get("file_excerpt"):
        lines.append("")
        lines.append("Relevant file excerpt (line numbers when available):")
        lines.append("```")
        lines.append(ctx["file_excerpt"])
        lines.append("```")
    return "\n".join(lines)


def explain_issue(
    issue: Issue,
    result: CheckResult | None = None,
    *,
    target_path: str | None = None,
) -> ExplainResult:
    if not litellm_available():
        return ExplainResult(ok=False, error_key="no_litellm")

    ok, err = ensure_credentials_ready()
    if not ok:
        return ExplainResult(ok=False, error_key=err or "no_key")

    ctx = gather_issue_context(issue, result, target_path=target_path)
    try:
        session = ExplainSession.create()
        text = session.ask(
            system=build_system_prompt(issue),
            user=build_user_prompt(ctx),
        )
    except RuntimeError as e:
        return ExplainResult(ok=False, error_key=str(e) or "no_key")
    except Exception as e:
        return ExplainResult(ok=False, error_key="provider_error", text=str(e))

    if not (text or "").strip():
        return ExplainResult(ok=False, error_key="empty_response", session=session)
    return ExplainResult(ok=True, text=text, session=session)


def ask_followup(session: ExplainSession, question: str) -> ExplainResult:
    q = (question or "").strip()
    if not q:
        return ExplainResult(ok=False, error_key="empty_question", session=session)
    lang = _language_name()
    try:
        text = session.followup(
            f"Follow-up question about the same issue.\n"
            f"Reply entirely in {lang}.\n\n"
            f"{q}"
        )
    except Exception as e:
        return ExplainResult(
            ok=False, error_key="provider_error", text=str(e), session=session
        )
    return ExplainResult(ok=True, text=text, session=session)


def error_message_for_key(key: str | None, detail: str = "") -> str:
    mapping = {
        "no_litellm": _("AI support is not available (litellm is not installed)."),
        "no_code": _("No AI credentials found. Configure API keys or an unlock code in FIDO."),
        "no_key": _("No API key is available for the selected AI model. Check FIDO settings or your unlock code."),
        "no_model": _("No AI model is selected in FIDO settings."),
        "not_found": _("The AI services unlock code was not found. Check the code in FIDO."),
        "network": _("Could not reach the unlock server or AI provider. Check your connection."),
        "bad_json": _("The unlock server returned invalid data."),
        "expired": _("The AI services unlock code has expired."),
        "parse": _("The unlock data could not be processed."),
        "unlock_failed": _("Could not refresh AI credentials from the unlock code."),
        "unlock_empty": _("The unlock code did not provide usable API keys."),
        "empty_response": _("The AI returned an empty response."),
        "empty_question": _("Enter a follow-up question."),
        "provider_error": _("The AI provider returned an error."),
        "no_session": _("Explain the issue first, then ask a follow-up."),
    }
    base = mapping.get(key or "", _("Could not explain this issue."))
    if detail and key == "provider_error":
        return f"{base}\n\n{detail}"
    return base
