"""Render AI markdown replies as HTML for WebView and the browser."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Issue

try:
    import markdown as _markdown
except ImportError:
    _markdown = None  # type: ignore

# wx.html.HtmlWindow supports only a small HTML subset (no real CSS).
_CODE_BLOCK_RE = re.compile(
    r"<pre><code(?:\s+[^>]*)?>(.*?)</code></pre>",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_CODE_RE = re.compile(
    r"<code(?:\s+[^>]*)?>(.*?)</code>",
    re.IGNORECASE | re.DOTALL,
)

# Soft-wrap width for in-dialog HtmlWindow (avoids horizontal scroll).
_SOFT_WRAP_COLS = 72


def _soft_wrap_plain(text: str, width: int = _SOFT_WRAP_COLS) -> str:
    """Insert breaks so long unbroken runs fit the dialog width."""
    out: list[str] = []
    for line in text.splitlines() or [""]:
        if len(line) <= width:
            out.append(line)
            continue
        # Prefer breaking on spaces; otherwise hard-break.
        remaining = line
        while len(remaining) > width:
            chunk = remaining[:width]
            space = chunk.rfind(" ")
            if space >= width // 3:
                out.append(remaining[: space + 1].rstrip())
                remaining = remaining[space + 1 :]
            else:
                out.append(remaining[:width])
                remaining = remaining[width:]
        if remaining:
            out.append(remaining)
    return "\n".join(out)


def _html_escape_preserve(text: str) -> str:
    return html.escape(text, quote=False)


def _code_block_to_wrapped_html(inner_html_escaped: str) -> str:
    """
    Turn fenced-code inner HTML (already entity-escaped) into wrapping markup.

    ``<pre>`` does not wrap in HtmlWindow and causes horizontal scrolling.
    """
    # Unescape only to re-wrap as plain lines, then escape again for <br> form.
    plain = html.unescape(inner_html_escaped)
    if plain.endswith("\n"):
        plain = plain[:-1]
    wrapped = _soft_wrap_plain(plain)
    lines = [_html_escape_preserve(line) for line in wrapped.split("\n")]
    body = "<br>".join(lines) if lines else ""
    return (
        '<table border="0" cellpadding="8" cellspacing="0" width="100%" '
        'bgcolor="#f3f3f3">'
        "<tr><td>"
        '<font face="Consolas, Courier New, monospace" size="2" color="#111111">'
        f"{body}"
        "</font>"
        "</td></tr></table>"
    )


def _wrap_code_block_dialog(match: re.Match[str]) -> str:
    return _code_block_to_wrapped_html(match.group(1))


def _wrap_inline_code(match: re.Match[str]) -> str:
    inner = match.group(1)
    return (
        '<font face="Consolas, Courier New, monospace" size="2" color="#111111">'
        f"<b>{inner}</b>"
        "</font>"
    )


def _style_code_for_dialog(fragment: str) -> str:
    styled = _CODE_BLOCK_RE.sub(_wrap_code_block_dialog, fragment)
    return _INLINE_CODE_RE.sub(_wrap_inline_code, styled)


def _markdown_fragment(raw: str) -> str:
    if _markdown is not None:
        try:
            return _markdown.markdown(
                raw,
                extensions=[
                    "fenced_code",
                    "sane_lists",
                    "nl2br",
                    "tables",
                ],
            )
        except Exception:
            pass
    escaped = html.escape(raw)
    paragraphs = escaped.split("\n\n")
    return "".join(
        f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip()
    )


# Bare http(s) URLs in text (AI often writes "Title: https://…").
_BARE_URL_RE = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)
_SKIP_LINKIFY_TAGS = frozenset({"a", "code", "pre", "script", "style"})


def _trim_url_trail(url: str) -> tuple[str, str]:
    """Split trailing punctuation that is usually not part of the URL."""
    trail = ""
    while url and url[-1] in ".,;:!?)]}>'\"":
        # Keep balanced ')' if it looks like part of the path (rare); strip common cases.
        if url[-1] == ")" and url.count("(") >= url.count(")"):
            break
        trail = url[-1] + trail
        url = url[:-1]
    return url, trail


def _linkify_text(text: str) -> str:
    parts: list[str] = []
    last = 0
    for match in _BARE_URL_RE.finditer(text):
        parts.append(text[last : match.start()])
        raw = match.group(1)
        url, trail = _trim_url_trail(raw)
        if url:
            safe = html.escape(url, quote=True)
            parts.append(f'<a href="{safe}">{html.escape(url)}</a>{html.escape(trail)}')
        else:
            parts.append(html.escape(raw))
        last = match.end()
    parts.append(text[last:])
    return "".join(parts)


def linkify_html(fragment: str) -> str:
    """
    Turn bare http(s) URLs into anchors, skipping text inside a/code/pre tags.

    Markdown already converts ``[text](url)``; models often emit plain URLs.
    """
    tokens = re.split(r"(<[^>]+>)", fragment or "")
    out: list[str] = []
    skip_depth = 0
    for tok in tokens:
        if tok.startswith("<"):
            m = re.match(r"</?\s*([a-zA-Z0-9]+)", tok)
            if m:
                tag = m.group(1).lower()
                if tag in _SKIP_LINKIFY_TAGS:
                    if tok.startswith("</"):
                        skip_depth = max(0, skip_depth - 1)
                    elif not tok.endswith("/>"):
                        skip_depth += 1
            out.append(tok)
            continue
        if skip_depth:
            out.append(tok)
        else:
            out.append(_linkify_text(tok))
    return "".join(out)


def markdown_to_body_html(text: str, *, for_dialog: bool = True) -> str:
    """Convert markdown to an HTML fragment (no outer document)."""
    fragment = _markdown_fragment(text or "")
    if for_dialog:
        return _style_code_for_dialog(fragment)
    return linkify_html(fragment)


def markdown_to_page(text: str, *, plain: bool = False) -> str:
    """
    Full HTML page suitable for ``HtmlWindow.SetPage`` (limited HTML subset).

    When ``plain`` is True, treat ``text`` as preformatted error/status text.
    """
    if plain:
        wrapped = _soft_wrap_plain(text or "")
        lines = [_html_escape_preserve(line) for line in wrapped.split("\n")]
        body = (
            '<font face="Consolas, Courier New, monospace" size="2">'
            + "<br>".join(lines)
            + "</font>"
        )
    else:
        body = markdown_to_body_html(text, for_dialog=True)
    return (
        "<html><head><meta charset='utf-8'></head>"
        "<body bgcolor='#ffffff' text='#111111' link='#0645ad'>"
        f"{body}"
        "</body></html>"
    )


def markdown_to_browser_page(
    text: str,
    *,
    title: str = "CheckMate",
    plain: bool = False,
) -> str:
    """Full HTML document for viewing/saving in a real browser (CSS allowed)."""
    safe_title = html.escape(title or "CheckMate")
    if plain:
        body = f"<pre class='plain'>{html.escape(text or '')}</pre>"
    else:
        body = markdown_to_body_html(text or "", for_dialog=False)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
  html, body {{
    height: 100%;
    margin: 0;
  }}
  body {{
    font-family: system-ui, Segoe UI, sans-serif;
    line-height: 1.45;
    max-width: 52rem;
    margin: 0 auto;
    padding: 1rem 1.25rem 2rem;
    color: #111;
    background: #fff;
    overflow-wrap: anywhere;
    word-wrap: break-word;
    box-sizing: border-box;
  }}
  h1, h2, h3 {{ line-height: 1.25; }}
  pre, code {{
    font-family: Consolas, "Courier New", monospace;
    font-size: 0.92em;
  }}
  pre {{
    background: #f3f3f3;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 0.85rem 1rem;
    overflow-x: auto;
    white-space: pre-wrap;
  }}
  code {{
    background: #f3f3f3;
    padding: 0.1em 0.35em;
    border-radius: 3px;
  }}
  pre code {{
    background: transparent;
    padding: 0;
  }}
  a {{ color: #0645ad; }}
  hr {{ border: none; border-top: 1px solid #ccc; margin: 1.5rem 0; }}
  .plain {{ white-space: pre-wrap; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def append_followup_markdown(
    previous: str,
    *,
    heading: str,
    question: str,
    answer: str,
) -> str:
    """Append a follow-up Q&A block to accumulated markdown."""
    prev = (previous or "").rstrip()
    block = f"\n\n---\n\n### {heading}\n\n**{question}**\n\n{answer}"
    if prev:
        return prev + block
    return block.lstrip()


def explanation_filename_stem(issue_code: str) -> str:
    raw = (issue_code or "explanation").strip() or "explanation"
    safe = re.sub(r"[^\w.\-]+", "_", raw, flags=re.UNICODE)
    return (safe[:60] or "explanation").strip("._") or "explanation"


def ai_disclaimer_markdown() -> str:
    """Localized markdown banner for AI-generated explanations (H2 to match sections)."""
    from ..i18n import _

    return (
        f"## {_('Note')}\n\n"
        f"{_('This explanation was generated by AI and may contain mistakes!')}\n"
    )


def with_ai_disclaimer(markdown_text: str) -> str:
    """Prepend the AI disclaimer once at the start of an explanation."""
    from ..i18n import _

    body = (markdown_text or "").lstrip()
    banner = ai_disclaimer_markdown().rstrip() + "\n\n"
    note_h2 = f"## {_('Note')}"
    if body.startswith(note_h2) or body.startswith(f"# {_('Note')}"):
        # Normalize a leftover H1 note from earlier builds.
        if body.startswith(f"# {_('Note')}") and not body.startswith(note_h2):
            body = note_h2 + body[len(f"# {_('Note')}") :]
        return body
    if not body:
        return banner.rstrip() + "\n"
    return banner + body


def issue_details_markdown(issue: "Issue", *, count: int = 1) -> str:
    """Issue fields as level-2 markdown sections (same info as the details pane)."""
    from ..i18n import _

    none = _("(none)")
    parts = [
        f"## {_('Severity')}\n\n{issue.severity.label}\n",
        f"## {_('Source')}\n\n{issue.source or '—'}\n",
        f"## {_('Code')}\n\n{issue.code or '—'}\n",
    ]
    if count > 1:
        parts.append(f"## {_('Occurrences')}\n\n{count}\n")
    parts.extend(
        [
            f"## {_('Location')}\n\n{issue.location or none}\n",
            f"## {_('Message')}\n\n{issue.message or none}\n",
        ]
    )
    return "\n".join(parts)


def export_explanation_markdown(
    issue: "Issue",
    explanation: str,
    *,
    count: int = 1,
) -> str:
    """
    Full markdown for View/Save: H1, issue details (H2), then explanation
    (which already includes the ## Note disclaimer when not an error).
    """
    from ..i18n import _

    title = _("AI explanation")
    code = (issue.code or "").strip()
    if code:
        title = f"{title} — {code}"
    blocks = [
        f"# {title}\n",
        issue_details_markdown(issue, count=count).rstrip(),
        "",
    ]
    body = (explanation or "").strip()
    if body:
        blocks.append(body)
        blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"
