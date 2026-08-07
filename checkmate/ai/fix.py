"""Propose and apply Fix with AI for EPUB / eBraille validation issues."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..epub_package import ApplyResult, apply_text_replacement, read_member_text
from ..i18n import _, get_language, language_display_name
from ..models import CheckResult, Issue
from .context import gather_issue_context, parse_issue_location
from .litellm_client import (
    DEFAULT_FIX_MAX_TOKENS,
    ensure_credentials_ready,
    litellm_available,
)
from .session import ExplainSession

logger = logging.getLogger(__name__)

_PATCH_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.IGNORECASE | re.DOTALL,
)

# Draft / chain-of-thought noise that means the reply is not a final patch.
_DRAFT_MARKERS_RE = re.compile(
    r"(?i)\b("
    r"let'?s refine|let me refine|wait,|copying precisely|"
    r"dots?\s+\d|i'?ll try again|draft\s*\d|revised json|"
    r"here is a better|scratch that|on second thought|"
    r"thinking out loud|step[- ]by[- ]step"
    r")\b"
)

_FIX_MAX_TOKENS = DEFAULT_FIX_MAX_TOKENS
_MAX_SNIPPET_CHARS = 1200


@dataclass
class FixProposal:
    file: str
    original: str
    replacement: str
    rationale: str = ""


@dataclass
class FixResult:
    ok: bool
    text: str = ""
    error_key: str | None = None
    proposal: FixProposal | None = None
    session: ExplainSession | None = None


def _language_name() -> str:
    return language_display_name()


def fix_member_kind(member: str | None) -> str:
    """
    Classify the flagged package member for Fix prompt specialization.

    Returns one of: ``opf``, ``html``, ``css``, ``other``.
    ``html`` covers ``.xhtml``, ``.html``, and ``.htm`` content documents.
    """
    if not member:
        return "other"
    path = member.replace("\\", "/").lower().lstrip("/")
    name = Path(path).name
    suffix = Path(path).suffix
    if suffix == ".opf":
        return "opf"
    if name.startswith("package") and suffix in {".xml", ""}:
        return "opf"
    if suffix in {".xhtml", ".html", ".htm"}:
        return "html"
    if suffix == ".css":
        return "css"
    return "other"


def _member_kind_guidance(kind: str) -> str:
    """English propose hints keyed off the flagged member type."""
    if kind == "opf":
        return (
            "FILE TYPE: OPF package document.\n"
            "- Edit inside <metadata>, <manifest>, or <spine> as the issue indicates.\n"
            "- For new metadata, expand a unique existing <meta …> line or </metadata> "
            "(insert-via-replace).\n"
            "- Keep xmlns prefixes exactly as in the file; do not invent undeclared prefixes.\n"
            "- Prefer one small meta/item/itemref change; do not rewrite the package."
        )
    if kind in {"html", "xhtml"}:
        return (
            "FILE TYPE: HTML/XHTML content document.\n"
            "- Prefer fixing attributes or wrapping the flagged element locally.\n"
            "- Keep well-formed markup (quoted attributes; matched tags).\n"
            "- For a missing attribute, expand the existing start tag as \"original\".\n"
            "- Do not rewrite the whole document or unrelated sections."
        )
    if kind == "css":
        return (
            "FILE TYPE: CSS stylesheet.\n"
            "- Prefer editing the flagged rule or declaration only.\n"
            "- Keep selectors exactly as in the excerpt.\n"
            "- For a new declaration, expand the unique rule block as \"original\".\n"
            "- Do not restyle the whole stylesheet or unrelated rules."
        )
    return (
        "FILE TYPE: other package member.\n"
        "- Make the smallest unique text edit that addresses the issue.\n"
        "- Use insert-via-replace when adding content."
    )


def build_fix_system_prompt() -> str:
    lang = _language_name()
    lang_code = get_language()
    return f"""You are an accessibility publishing assistant inside CheckMate.
Propose a minimal, concrete fix for one validation issue in an EPUB or eBraille publication.

LANGUAGE (mandatory):
- The CheckMate UI language is {lang} (code: {lang_code}).
- Write the human-readable rationale in {lang}.
- Do not use English for the rationale unless the UI language is English.
- File paths, attribute names, and code may stay in their original form.

OUTPUT FORMAT (mandatory — final answer only):
1. A short markdown section titled exactly "## {_('Proposed fix')}" with 2–4 short bullets or sentences.
2. Immediately after that, exactly ONE fenced JSON code block with the language tag json.
   The JSON object must contain only these string fields:
   - "file": package-relative path (use the File given if present)
   - "original": exact substring copied from the Exact file text
   - "replacement": the corrected substring

STRICT RULES:
- Output the final answer only. Do NOT think aloud, draft, refine, or narrate.
- Do NOT write phrases like "Let's refine", "Wait", "Copying precisely", or describe Unicode/braille dot patterns.
- Do NOT output partial JSON. The JSON block must be complete and valid in one shot.
- Copy Unicode and braille characters exactly as they appear in the Exact file text (paste them; do not spell them out).
- Keep "original" and "replacement" short (prefer under {_MAX_SNIPPET_CHARS} characters each) but unique in the excerpt.
- Change as little as possible. Prefer a local markup/CSS/OPF edit.
- Do not rewrite the whole file. Do not invent conformance rules.
- Never use an empty "original". To insert new markup or CSS, copy a short unique existing
  snippet from Exact file text into "original" and put that snippet plus the new content
  in "replacement" (insert-via-replace). Prefer the smallest unique nearby anchor —
  for example an existing tag line, attribute, CSS rule, or a closing tag such as
  </metadata>, </head>, or }} in CSS.
- Copy namespace prefixes, attribute names, and selectors exactly as in the excerpt;
  do not invent undeclared prefixes or names.
- When the user message includes a FILE TYPE section, follow those member-specific hints.
- If you cannot propose a safe automated fix, explain why under "## {_('Proposed fix')}" and omit the JSON block.
- Never include secrets or unrelated content.
"""


def build_fix_user_prompt(ctx: dict[str, str]) -> str:
    lang = _language_name()
    member = ctx.get("file_member") or ""
    kind = ctx.get("member_kind") or fix_member_kind(member)
    lines = [
        f"Propose a minimal fix for this validation issue.",
        f"Reply with the final ## {_('Proposed fix')} section and one complete JSON patch only.",
        f"Rationale language: {lang}.",
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
    if member:
        lines.append(f"- File: {member}")
    lines.append("")
    lines.append(_member_kind_guidance(kind))
    raw = ctx.get("file_excerpt_raw") or ""
    numbered = ctx.get("file_excerpt") or ""
    if raw:
        lines.append("")
        lines.append(
            "Exact file text to edit (copy original from here; no line prefixes). "
            "If adding content, expand a unique existing snippet (insert-via-replace)."
        )
        lines.append("```")
        lines.append(raw)
        lines.append("```")
    elif numbered:
        lines.append("")
        lines.append("Relevant file excerpt (ignore line-number prefixes if present):")
        lines.append("```")
        lines.append(numbered)
        lines.append("```")
    else:
        lines.append("")
        lines.append(
            "No file excerpt is available. Only propose a JSON patch if you can "
            "give an original string that will uniquely match the file."
        )
    return "\n".join(lines)


def _repair_user_prompt(
    *,
    reason: str | None = None,
    member_kind: str = "other",
) -> str:
    guidance = _member_kind_guidance(member_kind)
    if reason == "no_match_in_file":
        return (
            "Your previous patch was rejected because \"original\" was not found in "
            "the Exact file text (or was not unique).\n"
            "Reply again with ONLY:\n"
            f"1. ## {_('Proposed fix')} — 2–4 short bullets\n"
            "2. One complete ```json fence containing "
            '{"file","original","replacement"} — valid JSON, no truncation.\n'
            "Paste \"original\" verbatim from the Exact file text. "
            "If you need to insert content, expand a short unique existing snippet "
            "(insert-via-replace); never use an empty original. "
            "No thinking aloud. No drafts.\n\n"
            f"{guidance}"
        )
    return (
        "Your previous reply was unusable (incomplete JSON, truncated output, "
        "or draft/thinking text).\n"
        "Reply again with ONLY:\n"
        f"1. ## {_('Proposed fix')} — 2–4 short bullets\n"
        "2. One complete ```json fence containing "
        '{"file","original","replacement"} — valid JSON, no truncation.\n'
        "No thinking aloud. No drafts. No braille-dot descriptions. "
        "Copy characters exactly from the Exact file text. "
        "Never use an empty original; use insert-via-replace to add content.\n\n"
        f"{guidance}"
    )


def _unescape_json_stringish(obj: dict) -> FixProposal | None:
    file_path = str(obj.get("file") or "").strip()
    original = obj.get("original")
    replacement = obj.get("replacement")
    if not isinstance(original, str) or not isinstance(replacement, str):
        return None
    if not file_path:
        return None
    return FixProposal(
        file=file_path.replace("\\", "/"),
        original=original,
        replacement=replacement,
    )


def _looks_truncated(text: str, finish_reason: str | None) -> bool:
    if (finish_reason or "").lower() in {"length", "max_tokens"}:
        return True
    t = text or ""
    if t.count("```") % 2 == 1:
        return True
    # Opened a JSON object in a fence but never closed it cleanly
    if '"original"' in t and t.rstrip().endswith(('"', ",", "\\", ":")):
        # Heuristic: ends mid-string / mid-field
        if "```" not in t[t.rfind("{") :]:
            return True
    try:
        # If there is a fence starting with { that doesn't parse, likely cut off
        for m in _PATCH_FENCE_RE.finditer(t):
            body = (m.group(1) or "").strip()
            if body.startswith("{") and not body.endswith("}"):
                return True
            if body.startswith("{"):
                json.loads(body)
    except json.JSONDecodeError:
        if '"original"' in t or '"replacement"' in t:
            return True
    return False


def _looks_like_draft(text: str) -> bool:
    if not text:
        return False
    if _DRAFT_MARKERS_RE.search(text):
        return True
    # Multiple competing JSON fences often mean iterative drafting
    fences = [
        m.group(1).strip()
        for m in _PATCH_FENCE_RE.finditer(text)
        if (m.group(1) or "").strip().startswith("{")
    ]
    return len(fences) > 1


def parse_fix_proposal(text: str, *, default_file: str = "") -> FixProposal | None:
    """Extract a FixProposal from model markdown + JSON fence."""
    if not (text or "").strip():
        return None

    candidates: list[str] = []
    for m in _PATCH_FENCE_RE.finditer(text):
        body = (m.group(1) or "").strip()
        if body.startswith("{"):
            candidates.append(body)
    # Prefer the last complete-looking fence (final answer), then earlier ones
    if candidates:
        candidates = list(reversed(candidates))
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])

    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        proposal = _unescape_json_stringish(data)
        if proposal is None:
            continue
        if not proposal.file and default_file:
            proposal.file = default_file
        if not proposal.original.strip():
            return None
        # Rationale: text before the fence that contained this JSON
        rationale = text
        fence_at = text.find("```")
        if fence_at >= 0:
            rationale = text[:fence_at]
        # Drop draft chatter from rationale if present
        rationale = re.sub(
            rf"^##\s*{re.escape(_('Proposed fix'))}\s*",
            "",
            rationale.strip(),
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if _DRAFT_MARKERS_RE.search(rationale):
            # Keep only lines before the first draft marker
            parts = _DRAFT_MARKERS_RE.split(rationale, maxsplit=1)
            rationale = parts[0].strip() if parts else ""
        proposal.rationale = rationale
        return proposal
    return None


def _proposal_in_publication(
    proposal: FixProposal,
    *,
    target_path: str | None,
    ctx: dict[str, str],
) -> bool:
    """True when proposal.original occurs exactly once in the target member."""
    from ..epub_package import count_occurrences

    if not proposal.original:
        return False
    raw = ctx.get("file_excerpt_raw") or ""
    if raw and count_occurrences(raw, proposal.original) == 1:
        return True
    if not target_path:
        # Fall back: original must at least appear in the excerpt we sent
        return bool(raw) and count_occurrences(raw, proposal.original) >= 1
    resolved, text = read_member_text(Path(target_path), proposal.file)
    if text is None:
        return False
    return count_occurrences(text, proposal.original) == 1


def format_fix_preview(proposal: FixProposal) -> str:
    """Markdown shown in the issue-details dialog for a proposed fix."""
    parts = [
        f"## {_('Proposed fix')}",
        proposal.rationale or _("(no rationale)"),
        "",
        f"## {_('File')}",
        proposal.file or "—",
        "",
        f"## {_('Before')}",
        "```",
        proposal.original,
        "```",
        "",
        f"## {_('After')}",
        "```",
        proposal.replacement,
        "```",
    ]
    return "\n".join(parts)


def _try_parse_usable(
    text: str,
    *,
    ctx: dict[str, str],
    target_path: str | None,
    finish_reason: str | None,
) -> tuple[FixProposal | None, str | None]:
    """
    Return (proposal, error_key).
    error_key is set when the reply should not be shown as a usable fix.
    """
    if _looks_truncated(text, finish_reason):
        return None, "truncated"
    if _looks_like_draft(text) and parse_fix_proposal(text) is None:
        return None, "bad_patch"
    proposal = parse_fix_proposal(text, default_file=ctx.get("file_member", ""))
    if proposal is None:
        return None, "bad_patch"
    if len(proposal.original) > _MAX_SNIPPET_CHARS * 2:
        return None, "bad_patch"
    if not _proposal_in_publication(proposal, target_path=target_path, ctx=ctx):
        return None, "no_match_in_file"
    if _looks_like_draft(text):
        # Parsed OK from a noisy reply — still accept if match is unique
        pass
    return proposal, None


def propose_fix(
    issue: Issue,
    result: CheckResult | None = None,
    *,
    target_path: str | None = None,
    cancel_event: threading.Event | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> FixResult:
    from ..publication import classify_publication
    from .context import kind_allows_excerpt
    from .session import ProviderError

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _status(message: str) -> None:
        if status_callback is None:
            return
        try:
            status_callback(message)
        except Exception:
            logger.debug("Fix status callback failed", exc_info=True)

    path_for_gate = target_path or (result.target_path if result else None)
    if not path_for_gate:
        return FixResult(ok=False, error_key="wrong_format")
    try:
        kind = classify_publication(Path(path_for_gate)).value
    except Exception:
        kind = ""
    if not kind_allows_excerpt(kind):
        return FixResult(ok=False, error_key="wrong_format")

    if _cancelled():
        return FixResult(ok=False, error_key="cancelled")

    if not litellm_available():
        logger.error("Fix requested but litellm is not installed")
        return FixResult(ok=False, error_key="no_litellm")

    _status(_("Checking AI credentials…"))
    ok, err = ensure_credentials_ready()
    if not ok:
        logger.warning("Fix credentials not ready: %s", err)
        return FixResult(ok=False, error_key=err or "no_key")
    if _cancelled():
        return FixResult(ok=False, error_key="cancelled")

    ctx = gather_issue_context(issue, result, target_path=target_path)
    member_kind = fix_member_kind(ctx.get("file_member"))
    ctx["member_kind"] = member_kind
    try:
        session = ExplainSession.create()
    except RuntimeError as e:
        return FixResult(ok=False, error_key=str(e) or "no_key")

    _status(_("Checking AI connection…"))
    conn_ok, conn_err, conn_detail = session.check_connection(cancel_event=cancel_event)
    if not conn_ok:
        return FixResult(
            ok=False,
            error_key=conn_err or "network",
            text=conn_detail,
            session=session,
        )
    if _cancelled():
        return FixResult(ok=False, error_key="cancelled", session=session)

    _status(_("Proposing fix…"))
    logger.info(
        "Fix request starting model=%s code=%s member_kind=%s",
        session.model,
        issue.code,
        member_kind,
    )
    try:
        text = session.ask(
            system=build_fix_system_prompt(),
            user=build_fix_user_prompt(ctx),
            max_tokens=_FIX_MAX_TOKENS,
        )
    except ProviderError as e:
        return FixResult(
            ok=False,
            error_key=e.error_key,
            text=e.detail,
            session=session,
        )
    except RuntimeError as e:
        return FixResult(ok=False, error_key=str(e) or "no_key", session=session)
    except Exception as e:
        logger.exception("Fix provider error")
        return FixResult(
            ok=False, error_key="provider_error", text=str(e), session=session
        )

    if _cancelled():
        return FixResult(ok=False, error_key="cancelled", session=session)

    if not (text or "").strip():
        return FixResult(ok=False, error_key="empty_response", session=session)

    proposal, err_key = _try_parse_usable(
        text,
        ctx=ctx,
        target_path=path_for_gate,
        finish_reason=session.last_finish_reason,
    )

    # One automatic repair attempt for truncated / unusable replies
    if proposal is None:
        if _cancelled():
            return FixResult(ok=False, error_key="cancelled", session=session)
        _status(_("Proposing fix…"))
        try:
            text = session.followup(
                _repair_user_prompt(reason=err_key, member_kind=member_kind),
                max_tokens=_FIX_MAX_TOKENS,
            )
        except ProviderError as e:
            return FixResult(
                ok=False,
                error_key=e.error_key,
                text=e.detail,
                session=session,
            )
        except Exception as e:
            logger.exception("Fix repair provider error")
            return FixResult(
                ok=False,
                error_key="provider_error",
                text=str(e),
                session=session,
            )
        if not (text or "").strip():
            return FixResult(ok=False, error_key=err_key or "bad_patch", session=session)
        proposal, err_key = _try_parse_usable(
            text,
            ctx=ctx,
            target_path=path_for_gate,
            finish_reason=session.last_finish_reason,
        )

    if proposal is None:
        # Do not dump raw model drafts into the UI.
        return FixResult(ok=False, error_key=err_key or "bad_patch", session=session)

    preview = format_fix_preview(proposal)
    logger.info("Fix request completed model=%s", session.model)
    return FixResult(ok=True, text=preview, proposal=proposal, session=session)


def apply_proposed_fix(
    proposal: FixProposal,
    target_path: str | Path,
) -> ApplyResult:
    return apply_text_replacement(
        Path(target_path),
        proposal.file,
        proposal.original,
        proposal.replacement,
        backup=True,
    )


def _members_match(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True  # no file constraint
    na = a.replace("\\", "/").lstrip("/")
    nb = b.replace("\\", "/").lstrip("/")
    if na == nb:
        return True
    if Path(na).name == Path(nb).name:
        return True
    return na.endswith(nb) or nb.endswith(na)


def issue_still_present(before: Issue, result: CheckResult) -> bool:
    """
    True when the issue that Fix with AI targeted still appears after re-check.

    Matches on code + message (location line numbers may shift). When both
    sides name a file member, that member must also agree.
    """
    if not before.code and not before.message:
        return False
    before_member, _before_line = parse_issue_location(before.location)
    for issue in result.issues:
        if before.code and issue.code != before.code:
            continue
        if before.source and issue.source and before.source != issue.source:
            continue
        if before.message and issue.message != before.message:
            continue
        member, _line = parse_issue_location(issue.location)
        if before_member and member and not _members_match(before_member, member):
            continue
        return True
    return False


def _problem_total(result: CheckResult) -> int:
    return int(result.fatals) + int(result.errors) + int(result.warnings)


def _issue_fingerprint(issue: Issue) -> tuple[str, str, str, str, str]:
    """Stable-ish identity ignoring line/column shifts within a member."""
    member, _line = parse_issue_location(issue.location)
    member_key = (member or "").replace("\\", "/").lstrip("/").lower()
    return (
        (issue.source or "").strip().lower(),
        (issue.code or "").strip(),
        (issue.message or "").strip(),
        member_key,
        issue.severity.value,
    )


def _is_ace_source(source: str) -> bool:
    return "ace" in (source or "").strip().lower()


def _is_epubcheck_source(source: str) -> bool:
    return "epubcheck" in (source or "").strip().lower()


def new_epubcheck_errors(
    before: CheckResult,
    after: CheckResult,
) -> list[Issue]:
    """EPUBCheck fatal/error issues present after the fix but not before."""
    from ..models import Severity

    before_keys = {
        _issue_fingerprint(i)
        for i in before.issues
        if _is_epubcheck_source(i.source)
        and i.severity in {Severity.FATAL, Severity.ERROR}
    }
    found: list[Issue] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for issue in after.issues:
        if not _is_epubcheck_source(issue.source):
            continue
        if issue.severity not in {Severity.FATAL, Severity.ERROR}:
            continue
        key = _issue_fingerprint(issue)
        if key in before_keys or key in seen:
            continue
        seen.add(key)
        found.append(issue)
    return found


@dataclass
class FixVerifyReport:
    """Outcome of comparing the pre-fix check with the post-apply re-check."""

    target_resolved: bool
    counts_reduced: bool
    before_fatals: int
    before_errors: int
    before_warnings: int
    after_fatals: int
    after_errors: int
    after_warnings: int
    fixed_ace_issue: bool
    new_epubcheck_errors: list[Issue] = field(default_factory=list)

    @property
    def has_concerns(self) -> bool:
        if not self.target_resolved:
            return True
        if not self.counts_reduced:
            return True
        if self.fixed_ace_issue and self.new_epubcheck_errors:
            return True
        return False


def evaluate_fix_outcome(
    before_issue: Issue,
    before_result: CheckResult,
    after_result: CheckResult,
) -> FixVerifyReport:
    """Compare pre/post checks for resolution, totals, and Ace→EPUBCheck side effects."""
    new_epub: list[Issue] = []
    fixed_ace = _is_ace_source(before_issue.source)
    if fixed_ace:
        new_epub = new_epubcheck_errors(before_result, after_result)
    return FixVerifyReport(
        target_resolved=not issue_still_present(before_issue, after_result),
        counts_reduced=_problem_total(after_result) < _problem_total(before_result),
        before_fatals=before_result.fatals,
        before_errors=before_result.errors,
        before_warnings=before_result.warnings,
        after_fatals=after_result.fatals,
        after_errors=after_result.errors,
        after_warnings=after_result.warnings,
        fixed_ace_issue=fixed_ace,
        new_epubcheck_errors=new_epub,
    )


@dataclass
class PendingFixVerify:
    """State handed from the issue dialog to the main window after Apply fix."""

    issue: Issue
    target_path: str
    backup_path: str
    restore_to: str
    before_result: CheckResult


def error_message_for_key(key: str | None, detail: str = "") -> str:
    from .explain import error_message_for_key as explain_error

    mapping = {
        "wrong_format": _(
            "Fix with AI is only available for EPUB and eBraille publications."
        ),
        "no_patch": _(
            "The AI did not return an applicable patch. Try Fix with AI again, "
            "or use Explain with AI."
        ),
        "bad_patch": _(
            "The AI reply was incomplete or unusable (draft text or invalid JSON). "
            "Try Fix with AI again."
        ),
        "truncated": _(
            "The AI reply was cut off before a complete patch was ready. "
            "Try Fix with AI again."
        ),
        "no_match_in_file": _(
            "The AI proposed a patch that does not match the publication file. "
            "Try Fix with AI again."
        ),
        "empty_original": _("The proposed patch has an empty original string."),
        "no_match": _(
            "Could not apply the fix: the original text was not found in the file "
            "(it may have changed)."
        ),
        "ambiguous_match": _(
            "Could not apply the fix: the original text appears more than once "
            "in the file."
        ),
        "no_target": _("The publication path is missing or no longer exists."),
        "no_member": _("Could not find the file to edit inside the publication."),
        "unsupported_target": _(
            "This publication type cannot be edited in place by CheckMate."
        ),
        "write_failed": _("Could not write the fixed publication."),
        "bad_zip": _("The publication package could not be read or rebuilt."),
    }
    if key in mapping:
        base = mapping[key]
        if detail and key in {"write_failed", "bad_zip", "provider_error"}:
            return f"{base}\n\n{detail}"
        return base
    return explain_error(key, detail=detail)
