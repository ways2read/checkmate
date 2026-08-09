"""Curated learning resources by checker source."""

from __future__ import annotations

import logging
import re

from ..models import Issue
from ..settings import ai_send_kb_article_body
from .ace_kb_map import kb_resource_for_ace_code, normalize_kb_url
from .epubcheck_kb_map import (
    epubcheck_messages_resource,
    kb_resource_for_epubcheck_code,
    looks_like_epubcheck_code,
)

logger = logging.getLogger(__name__)

# Cap for optional KB article plain text in explain/fix prompts.
_MAX_KB_BODY_CHARS = 12_000

# Injected into the system prompt; models should prefer these over inventing URLs.
RESOURCE_MAP: dict[str, list[tuple[str, str]]] = {
    "Ace": [
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/publishing/",
        ),
        (
            "Ace by DAISY",
            "https://daisy.github.io/ace/",
        ),
    ],
    "EPUBCheck": [
        epubcheck_messages_resource(),
        (
            "EPUB 3 Accessibility Guidelines",
            "https://www.w3.org/publishing/a11y/",
        ),
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/publishing/",
        ),
    ],
    "eBraille Checker": [
        (
            "eBraille standard",
            "https://daisy.org/s/ebraille/",
        ),
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/publishing/",
        ),
    ],
    "veraPDF": [
        (
            "veraPDF",
            "https://verapdf.org/",
        ),
        (
            "PDF/UA",
            "https://www.pdfa.org/resource/iso-14289-pdfua/",
        ),
    ],
    "DAISY Pipeline": [
        (
            "DAISY Pipeline",
            "https://daisy.github.io/pipeline/",
        ),
        (
            "DAISY Accessible Publishing Knowledge Base",
            "https://kb.daisy.org/publishing/",
        ),
    ],
}

_DEFAULT_RESOURCES: list[tuple[str, str]] = [
    (
        "DAISY Accessible Publishing Knowledge Base",
        "https://kb.daisy.org/publishing/",
    ),
]

# Ace often appends its help URL into the issue message; use as a fallback.
_KB_URL_IN_TEXT = re.compile(
    r"https?://kb\.daisy\.org/[^\s\]\)\"'<>]+",
    re.IGNORECASE,
)


def _looks_like_ace(issue: Issue) -> bool:
    source = (issue.source or "").strip()
    if source == "Ace":
        return True
    if _looks_like_epubcheck(issue):
        return False
    code = (issue.code or "").lower()
    if code.startswith(("epub-", "metadata-", "pagebreak-")):
        return True
    if "wcag" in code or code.startswith("aria-") or "epub-image" in code:
        return True
    # Common axe rule ids Ace reports as dct:title.
    if kb_resource_for_ace_code(code):
        return True
    return False


def _looks_like_epubcheck(issue: Issue) -> bool:
    source = (issue.source or "").strip()
    if source == "EPUBCheck":
        return True
    if source == "Ace":
        return False
    return looks_like_epubcheck_code(issue.code or "")


def _ace_specific_kb(issue: Issue) -> tuple[str, str] | None:
    """Best specific KB article for an Ace issue (help URL, else rule-id map)."""
    help_url = normalize_kb_url(getattr(issue, "help_url", "") or "")
    if help_url and "kb.daisy.org" in help_url.lower():
        title = (getattr(issue, "help_title", "") or "").strip()
        if not title:
            mapped = kb_resource_for_ace_code(issue.code)
            title = mapped[0] if mapped else "DAISY Knowledge Base article"
        elif not title.lower().startswith("daisy"):
            title = f"DAISY KB: {title}"
        return title, help_url

    mapped = kb_resource_for_ace_code(issue.code)
    if mapped:
        return mapped

    # Fallback: Ace may have left a KB URL only in the message text.
    msg = issue.message or ""
    m = _KB_URL_IN_TEXT.search(msg)
    if m:
        url = normalize_kb_url(m.group(0).rstrip(".,;"))
        mapped = kb_resource_for_ace_code(issue.code)
        title = mapped[0] if mapped else "DAISY Knowledge Base article"
        return title, url
    return None


def _epubcheck_specific_resources(issue: Issue) -> list[tuple[str, str]]:
    """
    EPUBCheck Learn more / authoritative list.

    Prefer a mapped DAISY KB article when the message is accessibility-oriented,
    always include the official EPUBCheck message catalog, then general guides.
    """
    items: list[tuple[str, str]] = []
    kb = kb_resource_for_epubcheck_code(issue.code)
    if kb:
        items.append(kb)
    items.append(epubcheck_messages_resource())
    items.extend(RESOURCE_MAP["EPUBCheck"][1:])  # a11y guidelines + KB home
    return items


def _dedupe_resources(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for title, url in items:
        key = normalize_kb_url(url).rstrip("/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((title, normalize_kb_url(url)))
    return out


def resources_for_issue(issue: Issue) -> list[tuple[str, str]]:
    source = (issue.source or "").strip()

    if _looks_like_epubcheck(issue):
        return _dedupe_resources(_epubcheck_specific_resources(issue))

    if _looks_like_ace(issue):
        base = list(RESOURCE_MAP["Ace"])
        specific = _ace_specific_kb(issue)
        if specific:
            return _dedupe_resources([specific, *base])
        return _dedupe_resources(base)

    if source in RESOURCE_MAP:
        return _dedupe_resources(list(RESOURCE_MAP[source]))

    # EPUB merged runs may leave source empty; guess from code prefixes.
    code = (issue.code or "").lower()
    if code.startswith("epub") or "opf" in code or "rsc-" in code or "rsc_" in code:
        if looks_like_epubcheck_code(issue.code or ""):
            return _dedupe_resources(_epubcheck_specific_resources(issue))
        return _dedupe_resources(list(RESOURCE_MAP["EPUBCheck"]))
    return _dedupe_resources(list(_DEFAULT_RESOURCES))


def primary_kb_resource(issue: Issue) -> tuple[str, str] | None:
    """
    Most specific authoritative reference for this issue, when known.

    - Ace: rule-linked DAISY KB article (help URL or ace_kb_map)
    - EPUBCheck: mapped DAISY KB article when available, else the official
      EPUBCheck message catalog (not the generic wiki homepage)
    """
    if _looks_like_ace(issue):
        return _ace_specific_kb(issue)
    if _looks_like_epubcheck(issue):
        kb = kb_resource_for_epubcheck_code(issue.code)
        if kb:
            return kb
        return epubcheck_messages_resource()
    return None


def resources_prompt_block(issue: Issue) -> str:
    lines = [
        "Trusted resources (use only these links in Learn more):",
        "List the most specific article first when several are given.",
    ]
    for title, url in resources_for_issue(issue):
        lines.append(f"- {title}: {url}")
    return "\n".join(lines)


def authoritative_guidance_for_explain(issue: Issue) -> str:
    """System-prompt block: treat the primary reference as authoritative topic guidance."""
    primary = primary_kb_resource(issue)
    if not primary:
        return (
            "AUTHORITATIVE GUIDANCE:\n"
            "- Do not invent conformance requirements. If unsure, say what to verify.\n"
            "- Prefer concrete markup/CSS/OPF steps for EPUB and eBraille."
        )
    title, url = primary
    return (
        "AUTHORITATIVE GUIDANCE:\n"
        f"- Primary reference for this issue: [{title}]({url})\n"
        "- Align \"What this means\", \"Why it matters\", and \"How to fix\" with that "
        "reference; do not invent requirements that conflict with it.\n"
        "- If the reference and the checker message seem to disagree, prefer the "
        "reference and note the uncertainty briefly.\n"
        "- Prefer concrete markup/CSS/OPF steps for EPUB and eBraille.\n"
        "- In Learn more, list that primary reference first as a markdown link; you may "
        "add other trusted resources from the list below."
    )


def authoritative_guidance_for_fix(issue: Issue) -> str:
    """Optional user-prompt block for Fix: light steering without overriding file text."""
    primary = primary_kb_resource(issue)
    if not primary:
        return ""
    title, url = primary
    return (
        "AUTHORITATIVE GUIDANCE:\n"
        f"- Prefer the remediation approach described in: {title} — {url}\n"
        "- Still copy \"original\" and \"replacement\" exclusively from Exact file text "
        "(or Related package document text). Do not invent markup from the reference.\n"
        "- If the reference suggests a fix that cannot be applied as a unique local "
        "replace, omit the JSON block and explain why."
    )


def kb_article_body_for_prompt(issue: Issue) -> str:
    """
    Plain-text DAISY KB article body for explain/fix prompts, or empty.

    Honours ``ai_send_kb_article_body`` (default off). Uses the offline cache,
    downloading the English article on demand when missing. Only applies when
    the primary reference is a kb.daisy.org article (not the EPUBCheck catalog).
    """
    if not ai_send_kb_article_body():
        return ""
    primary = primary_kb_resource(issue)
    if not primary:
        return ""
    _title, url = primary
    if "kb.daisy.org" not in (url or "").lower():
        return ""

    from ..kb.fetch import (
        ensure_article_cached,
        extract_article_fragment,
        html_to_plain_text,
    )
    from ..kb.store import en_file_path, en_relative_path_from_url

    en_rel = en_relative_path_from_url(url)
    if not en_rel:
        return ""
    try:
        if not ensure_article_cached(en_rel, also_ja=False):
            return ""
        path = en_file_path(en_rel)
        if not path.is_file():
            return ""
        html = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("KB article body unavailable for %s", en_rel, exc_info=True)
        return ""

    fragment = extract_article_fragment(html)
    plain = html_to_plain_text(fragment or html)
    if not plain:
        return ""
    if len(plain) > _MAX_KB_BODY_CHARS:
        plain = plain[:_MAX_KB_BODY_CHARS] + "\n…"
    return plain


def kb_article_body_prompt_block(body: str, *, for_fix: bool = False) -> str:
    """Fenced prompt block for an already-loaded KB article body."""
    text = (body or "").strip()
    if not text:
        return ""
    if for_fix:
        intro = (
            "Knowledge Base article body (authoritative remediation guidance; "
            "do not copy markup from here into \"original\" / \"replacement\" — "
            "use Exact file text for those):"
        )
    else:
        intro = (
            "Knowledge Base article body (authoritative; align What this means / "
            "Why it matters / How to fix with this text):"
        )
    return f"{intro}\n```\n{text}\n```"
