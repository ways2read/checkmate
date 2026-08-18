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

def _html_root_class() -> str:
    try:
        from ..ui_appearance import html_root_class

        return html_root_class()
    except Exception:
        return "checkmate-theme-system"


def _html_color_scheme() -> str:
    try:
        from ..ui_appearance import html_color_scheme

        return html_color_scheme()
    except Exception:
        return "light dark"


_AI_DARK_ROOT_TOKENS = """
      :root {
        --ink: #f1f5f9;
        --muted: #94a3b8;
        --paper: #0f172a;
        --card: #1e293b;
        --line: #334155;
        --line-strong: #64748b;
        --focus: #2dd4bf;
        --focus-ring: #0f766e;
        --link: #5eead4;
        --link-visited: #99f6e4;
        --note-fg: #fed7aa;
        --note-bg: #7c2d12;
        --note-border: #c2410c;
        --fix-fg: #bbf7d0;
        --fix-border: #166534;
        --chat-user-bg: #1e3a5f;
        --chat-user-fg: #eff6ff;
        --chat-user-border: #3b82f6;
        --code-bg: #0f172a;
        --shadow: 0 1px 3px rgb(0 0 0 / 35%);
      }
"""

_ISSUE_DARK_ROOT_TOKENS = """
      :root {
        --fatal-fg: #fecaca;
        --fatal-bg: #7f1d1d;
        --error-fg: #fecaca;
        --error-bg: #7f1d1d;
        --warning-fg: #fed7aa;
        --warning-bg: #7c2d12;
        --info-fg: #bfdbfe;
        --info-bg: #1e3a8a;
        --usage-fg: #cbd5e1;
        --usage-bg: #334155;
      }
"""

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


def _ai_browser_css() -> str:
    """Shared look with the checker HTML report (lighter, prose-focused)."""
    try:
        from ..ui_appearance import wrap_os_dark_css
    except Exception:
        def wrap_os_dark_css(inner: str) -> str:
            return f"@media (prefers-color-scheme: dark) {{\n{inner}\n}}\n"

    return (
        """
    :root {
      --ink: #0f172a;
      --muted: #475569;
      --paper: #eef5fb;
      --card: #ffffff;
      --line: #c9d8e8;
      --line-strong: #8aa0b8;
      --focus: #0f766e;
      --focus-ring: #5eead4;
      --link: #0f766e;
      --link-visited: #115e59;
      --note-fg: #9a3412;
      --note-bg: #ffedd5;
      --note-border: #fdba74;
      --fix-fg: #14532d;
      --fix-border: #86efac;
      --chat-user-bg: #dbeafe;
      --chat-user-fg: #0f172a;
      --chat-user-border: #93c5fd;
      --code-bg: #e8f0f8;
      --radius: 0.5rem;
      --font: "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
      --mono: ui-monospace, "Cascadia Code", "Consolas", "Liberation Mono", monospace;
      --shadow: 0 1px 2px rgb(15 23 42 / 8%);
    }
"""
        + wrap_os_dark_css(_AI_DARK_ROOT_TOKENS)
        + """
    * { box-sizing: border-box; }
    html {
      margin: 0;
      min-height: 100%;
      overflow-y: auto;
    }
    body {
      margin: 0;
      min-height: 100%;
      font-family: var(--font);
      line-height: 1.55;
      color: var(--ink);
      background: var(--paper);
      overflow-wrap: anywhere;
      word-wrap: break-word;
    }
    :focus-visible {
      outline: 3px solid var(--focus-ring);
      outline-offset: 2px;
    }
    main {
      max-width: 46rem;
      margin: 0 auto;
      padding: 1.35rem clamp(1rem, 3vw, 1.5rem) 2.25rem;
    }
    .doc-header {
      margin: 0 0 1.35rem;
      padding: 1rem 1.1rem 1.1rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .doc-eyebrow {
      margin: 0 0 0.4rem;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .doc-header h1 {
      margin: 0;
      font-size: clamp(1.3rem, 2.6vw, 1.7rem);
      font-weight: 700;
      letter-spacing: -0.015em;
      line-height: 1.25;
    }
    .issue-meta {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
      gap: 0.65rem 1rem;
      margin: 0 0 1.35rem;
      padding: 0.9rem 1rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .issue-meta .meta-item {
      min-width: 0;
    }
    .issue-meta h2 {
      margin: 0 0 0.2rem;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      border: 0;
      padding: 0;
    }
    .issue-meta p {
      margin: 0;
      font-size: 0.98rem;
      font-weight: 600;
      line-height: 1.35;
    }
    .doc-meta {
      margin: -0.5rem 0 1.25rem;
      padding: 0.75rem 1rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.45;
      box-shadow: var(--shadow);
    }
    .doc-meta p {
      margin: 0.2rem 0;
    }
    .doc-meta p:first-child { margin-top: 0; }
    .doc-meta p:last-child { margin-bottom: 0; }
    h2 {
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      line-height: 1.3;
      margin: 1.65rem 0 0.55rem;
      padding-bottom: 0.3rem;
      border-bottom: 1px solid var(--line);
    }
    h3 {
      font-size: 1.02rem;
      font-weight: 700;
      line-height: 1.35;
      margin: 1.35rem 0 0.45rem;
    }
    p, ul, ol { margin: 0.65rem 0; }
    ul, ol { padding-left: 1.35rem; }
    li { margin: 0.25rem 0; }
    a {
      color: var(--link);
      text-underline-offset: 0.15em;
    }
    a:visited { color: var(--link-visited); }
    hr {
      border: none;
      border-top: 1px solid var(--line);
      margin: 1.6rem 0;
    }
    blockquote {
      margin: 0.9rem 0;
      padding: 0.35rem 0 0.35rem 0.9rem;
      border-left: 3px solid var(--line-strong);
      color: var(--muted);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin: 0.9rem 0;
      font-size: 0.95rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      overflow: hidden;
    }
    th, td {
      text-align: left;
      padding: 0.5rem 0.7rem;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      background: color-mix(in srgb, var(--paper) 70%, var(--card));
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--muted);
    }
    tr:last-child td { border-bottom: 0; }
    pre, code {
      font-family: var(--mono);
      font-size: 0.9em;
    }
    pre {
      background: var(--code-bg);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 0.85rem 1rem;
      overflow-x: auto;
      white-space: pre-wrap;
      line-height: 1.45;
    }
    code {
      background: color-mix(in srgb, var(--code-bg) 80%, var(--line));
      padding: 0.12em 0.38em;
      border-radius: 0.3rem;
    }
    pre code {
      background: transparent;
      padding: 0;
    }
    .ai-note {
      margin: 1.15rem 0 1.4rem;
      padding: 0.85rem 1rem;
      background: var(--note-bg);
      color: var(--note-fg);
      border: 1px solid var(--note-border);
      border-radius: var(--radius);
    }
    .ai-note h2 {
      margin: 0 0 0.35rem;
      padding: 0;
      border: 0;
      font-size: 0.95rem;
      color: inherit;
    }
    .ai-note p {
      margin: 0;
    }
    .ai-placeholder {
      margin: 2.75rem auto 1.5rem;
      max-width: 28rem;
      padding: 1.35rem 1.4rem 1.45rem;
      text-align: center;
      background: var(--card);
      color: var(--muted);
      border: 1px dashed var(--line-strong);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .ai-placeholder h2 {
      margin: 0 0 0.45rem;
      padding: 0;
      border: 0;
      font-size: 1.05rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: var(--ink);
    }
    .ai-placeholder p {
      margin: 0;
      font-size: 0.98rem;
      line-height: 1.5;
    }
    h2.ai-fix-heading {
      color: var(--fix-fg);
      border-bottom-color: var(--fix-border);
    }
    .chat-bubble.chat-user {
      display: block;
      box-sizing: border-box;
      margin: 1.35rem 0 0.9rem auto;
      max-width: min(34rem, 94%);
      padding: 0.7rem 0.95rem 0.8rem;
      background: var(--chat-user-bg);
      color: var(--chat-user-fg);
      border: 1px solid var(--chat-user-border);
      border-radius: 1.1rem 1.1rem 0.3rem 1.1rem;
      box-shadow: var(--shadow);
      line-height: 1.45;
      scroll-margin-top: 0.75rem;
    }
    .chat-bubble.chat-user:focus {
      outline: 3px solid var(--focus-ring);
      outline-offset: 2px;
    }
    .chat-bubble.chat-user .chat-user-label {
      display: block;
      margin: 0 0 0.3rem;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      opacity: 0.75;
    }
    .chat-bubble.chat-user p {
      margin: 0;
      font-weight: 550;
      overflow-wrap: anywhere;
    }
    .plain {
      white-space: pre-wrap;
      font-family: var(--mono);
      font-size: 0.9rem;
      margin: 0;
      padding: 0.9rem 1rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
    }
    footer.doc-footer {
      margin-top: 2rem;
      padding-top: 0.85rem;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 0.85rem;
    }
    @media print {
      body { background: #fff; color: #000; }
      .doc-header, .issue-meta, .doc-meta, .ai-note, .ai-placeholder, pre, table {
        box-shadow: none;
        break-inside: avoid;
      }
    }
    """
    )


def _structure_ai_browser_body(fragment: str) -> str:
    """Light structural polish: title header, issue meta card, note callout."""
    from ..i18n import _

    if not fragment or not fragment.strip():
        return fragment

    detail_labels = {
        _("Severity"),
        _("Source"),
        _("Code"),
        _("Occurrences"),
        _("Location"),
        _("Message"),
    }
    note_label = _("Note")
    fix_label = _("Proposed fix")
    placeholder_label = _("AI assistance")

    out = fragment

    # Promote the document title.
    out = re.sub(
        r"<h1(\s[^>]*)?>(.*?)</h1>",
        (
            r'<header class="doc-header">'
            r'<p class="doc-eyebrow">CheckMate</p>'
            r"<h1\1>\2</h1>"
            r"</header>"
        ),
        out,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Group leading issue-detail h2+p pairs into a compact meta card.
    pair_re = re.compile(
        r"<h2(\s[^>]*)?>\s*(.*?)\s*</h2>\s*<p(\s[^>]*)?>(.*?)</p>\s*",
        re.IGNORECASE | re.DOTALL,
    )
    header_end = out.find("</header>")
    scan_at = header_end + len("</header>") if header_end != -1 else 0
    # Skip whitespace after header.
    while scan_at < len(out) and out[scan_at].isspace():
        scan_at += 1

    meta_chunks: list[str] = []
    cursor = scan_at
    while True:
        m = pair_re.match(out, cursor)
        if not m:
            break
        label = html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if label not in detail_labels:
            break
        meta_chunks.append(
            f'<div class="meta-item"><h2{m.group(1) or ""}>{m.group(2)}</h2>'
            f"<p{m.group(3) or ''}>{m.group(4)}</p></div>"
        )
        cursor = m.end()

    if meta_chunks:
        card = '<div class="issue-meta">\n' + "\n".join(meta_chunks) + "\n</div>\n"
        out = out[:scan_at] + card + out[cursor:]

    # Overview-style plain meta paragraphs right under the title.
    if '<div class="issue-meta">' not in out:
        meta_p_re = re.compile(r"(?:<p(\s[^>]*)?>.*?</p>\s*)+", re.IGNORECASE | re.DOTALL)
        header_end = out.find("</header>")
        if header_end != -1:
            start = header_end + len("</header>")
            while start < len(out) and out[start].isspace():
                start += 1
            m = meta_p_re.match(out, start)
            if m:
                # Only wrap when the next block is a heading or note (overview shape),
                # and paragraphs look like "Label: value" metadata lines.
                block = m.group(0)
                plain_paras = re.findall(
                    r"<p(?:\s[^>]*)?>(.*?)</p>", block, flags=re.IGNORECASE | re.DOTALL
                )
                texts = [
                    html.unescape(re.sub(r"<[^>]+>", "", p)).strip() for p in plain_paras
                ]
                if texts and all(":" in t for t in texts):
                    out = (
                        out[:start]
                        + f'<div class="doc-meta">{block.rstrip()}</div>\n'
                        + out[m.end() :]
                    )

    # AI disclaimer callout.
    note_esc = re.escape(html.escape(note_label, quote=False))
    out = re.sub(
        rf"<h2(\s[^>]*)?>\s*{note_esc}\s*</h2>\s*<p(\s[^>]*)?>(.*?)</p>",
        (
            r'<aside class="ai-note" role="note">'
            rf"<h2\1>{html.escape(note_label)}</h2>"
            r"<p\2>\3</p>"
            r"</aside>"
        ),
        out,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Empty-state placeholder (before Explain / Fix / Overview has content).
    placeholder_esc = re.escape(html.escape(placeholder_label, quote=False))
    out = re.sub(
        rf"<h2(\s[^>]*)?>\s*{placeholder_esc}\s*</h2>\s*<p(\s[^>]*)?>(.*?)</p>",
        (
            r'<aside class="ai-placeholder" role="status" aria-live="polite">'
            rf"<h2\1>{html.escape(placeholder_label)}</h2>"
            r"<p\2>\3</p>"
            r"</aside>"
        ),
        out,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Mark the proposed-fix heading for a subtle accent.
    fix_esc = re.escape(html.escape(fix_label, quote=False))
    out = re.sub(
        rf"<h2(\s[^>]*)?>\s*{fix_esc}\s*</h2>",
        rf'<h2 class="ai-fix-heading">{html.escape(fix_label)}</h2>',
        out,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return out


def markdown_to_browser_page(
    text: str,
    *,
    title: str = "CheckMate",
    plain: bool = False,
    tab_exit: bool = False,
) -> str:
    """
    Full HTML document for viewing/saving in a real browser (CSS allowed).

    When ``tab_exit`` is True (in-dialog WebView), include a script that moves
    focus out of the page to the host dialog: Tab after the last link,
    Shift+Tab before the first, or Ctrl+Tab / Ctrl+Shift+Tab anytime.
    """
    from ..i18n import _, get_language, get_text_direction

    safe_title = html.escape(title or "CheckMate")
    if plain:
        body = f"<pre class='plain'>{html.escape(text or '')}</pre>"
    else:
        body = _structure_ai_browser_body(
            markdown_to_body_html(text or "", for_dialog=False)
        )
    tab_script = _WEBVIEW_TAB_EXIT_SCRIPT if tab_exit else ""
    reveal_script = _LATEST_FOLLOWUP_REVEAL_SCRIPT
    body_attrs = ' tabindex="-1"' if tab_exit else ""
    footer = ""
    if not tab_exit:
        footer = f'<footer class="doc-footer">{html.escape(_("Generated by CheckMate"))}</footer>'
    lang = html.escape(get_language())
    direction = html.escape(get_text_direction())
    return f"""<!DOCTYPE html>
<html lang="{lang}" dir="{direction}" class="{_html_root_class()}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="{_html_color_scheme()}">
<title>{safe_title}</title>
<style>{_ai_browser_css()}
</style>
</head>
<body{body_attrs}>
<main>
{body}
{footer}
</main>
{reveal_script}
{tab_script}
</body>
</html>
"""


# Custom scheme handled by the dialog WebView NAVIGATING handler (vetoed).
# Raw JS for WebView.RunScript (no <script> wrapper).
_WEBVIEW_TAB_EXIT_JS = """
(function () {
  if (window.__cmTabExitWired) return;
  window.__cmTabExitWired = true;
  function focusables() {
    return Array.prototype.slice.call(document.querySelectorAll(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )).filter(function (el) {
      if (el.disabled) return false;
      if (el.getAttribute('aria-hidden') === 'true') return false;
      var rects = el.getClientRects();
      return rects && rects.length > 0;
    });
  }
  function leave(prev) {
    window.location.href = prev
      ? 'checkmate://focus-prev'
      : 'checkmate://focus-next';
  }
  function closeDialog() {
    window.location.href = 'checkmate://close';
  }
  document.addEventListener('keydown', function (e) {
    // Escape never reaches wx CHAR_HOOK once Edge owns the document HWND.
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      // Prefer closing an in-page lightbox (alt report) before the host dialog.
      var modal = document.getElementById('imageModal');
      if (modal && modal.style.display === 'block') {
        modal.style.display = 'none';
        return;
      }
      closeDialog();
      return;
    }
    // Ctrl+PgUp / Ctrl+PgDn also stay inside Edge unless we forward them.
    if (e.ctrlKey && !e.altKey && !e.metaKey &&
        (e.key === 'PageUp' || e.key === 'PageDown')) {
      e.preventDefault();
      e.stopPropagation();
      window.location.href = e.key === 'PageUp'
        ? 'checkmate://page-prev'
        : 'checkmate://page-next';
      return;
    }
    if (e.key !== 'Tab') return;
    // Always-available escape hatch while inside the WebView document.
    if (e.ctrlKey) {
      e.preventDefault();
      leave(!!e.shiftKey);
      return;
    }
    var list = focusables();
    var active = document.activeElement;
    var onChrome = !active
      || active === document.body
      || active === document.documentElement
      || (active && active.id === 'cm-latest-followup');
    if (!list.length) {
      e.preventDefault();
      leave(!!e.shiftKey);
      return;
    }
    if (!e.shiftKey && active === list[list.length - 1]) {
      e.preventDefault();
      leave(false);
      return;
    }
    if (e.shiftKey && (active === list[0] || onChrome)) {
      e.preventDefault();
      leave(true);
    }
  }, true);
})();
""".strip()

_WEBVIEW_TAB_EXIT_SCRIPT = f"<script>\n{_WEBVIEW_TAB_EXIT_JS}\n</script>"


# Shared scroll for #cm-latest-followup. Do not el.focus() in the sniff-test
# dialog: moving keyboard focus into Edge WebView2 after LoadURL/SetPage can
# leave the host modal unable to quit on Windows.
_LATEST_FOLLOWUP_SCROLL_JS = """
    var el = document.getElementById('cm-latest-followup');
    if (el) {
      try {
        // Prefer scrolling the document so tall pages remain scrollable after
        // WebView SetPage (height:100% layouts can clip follow-ups otherwise).
        var top = 0;
        try {
          var rect = el.getBoundingClientRect();
          top = (window.pageYOffset || document.documentElement.scrollTop || 0)
            + rect.top - 12;
        } catch (e0) {
          top = el.offsetTop || 0;
        }
        if (top < 0) top = 0;
        window.scrollTo(0, top);
        if (document.documentElement) {
          document.documentElement.scrollTop = top;
        }
        if (document.body) {
          document.body.scrollTop = top;
        }
        el.scrollIntoView({ block: 'start', inline: 'nearest', behavior: 'auto' });
      } catch (e) {
        try { el.scrollIntoView(true); } catch (e2) {}
      }
    }
""".strip()

# Host RunScript: scroll only (no Edge focus).
_WEBVIEW_SCROLL_LATEST_FOLLOWUP_JS = (
    "(function () {\n  try {\n    "
    + _LATEST_FOLLOWUP_SCROLL_JS
    + "\n    return document.getElementById('cm-latest-followup') ? 'scrolled' : 'none';"
    + "\n  } catch (e5) { return 'err'; }\n})();"
)

# After SetPage, put the newest follow-up question at the top of the viewport
# and move accessibility focus onto it when present.
_LATEST_FOLLOWUP_REVEAL_SCRIPT = f"""
<script>
(function () {{
  function revealLatestFollowup() {{
    try {{
{_LATEST_FOLLOWUP_SCROLL_JS}
    }} catch (e5) {{}}
    try {{
      var focusEl = document.getElementById('cm-latest-followup');
      if (!focusEl) return;
      if (!(focusEl.tabIndex < 0)) {{ focusEl.tabIndex = -1; }}
      focusEl.focus({{ preventScroll: true }});
    }} catch (e3) {{
      try {{
        var focusEl2 = document.getElementById('cm-latest-followup');
        if (focusEl2) focusEl2.focus();
      }} catch (e4) {{}}
    }}
  }}
  function schedule() {{
    setTimeout(revealLatestFollowup, 0);
    setTimeout(revealLatestFollowup, 50);
    setTimeout(revealLatestFollowup, 200);
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', schedule);
  }} else {{
    schedule();
  }}
  window.addEventListener('load', schedule);
}})();
</script>
""".strip()

# Dialog path: scroll on load without stealing Win32 focus into Edge.
_LATEST_FOLLOWUP_SCROLL_SCRIPT = f"""
<script>
(function () {{
  function scrollLatestFollowup() {{
    try {{
{_LATEST_FOLLOWUP_SCROLL_JS}
    }} catch (e5) {{}}
  }}
  function schedule() {{
    setTimeout(scrollLatestFollowup, 0);
    setTimeout(scrollLatestFollowup, 50);
    setTimeout(scrollLatestFollowup, 200);
    setTimeout(scrollLatestFollowup, 500);
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', schedule);
  }} else {{
    schedule();
  }}
  window.addEventListener('load', schedule);
}})();
</script>
""".strip()


def append_followup_markdown(
    previous: str,
    *,
    heading: str,
    question: str,
    answer: str,
) -> str:
    """Append a follow-up Q&A block to accumulated markdown.

    The HTML view renders the question as a chat bubble (see CSS in
    ``markdown_to_browser_page``). ``heading`` is accepted for call-site
    compatibility but is not shown in the document.

    The newest question bubble gets ``id="cm-latest-followup"`` so the HTML
    view can scroll and move accessibility focus to it after reload.
    """
    prev = (previous or "").rstrip()
    # Only the newest bubble should be the scroll/focus target.
    prev = re.sub(r'\s+id="cm-latest-followup"', "", prev)
    q_plain = (question or "").strip()
    q = html.escape(q_plain, quote=False)
    from ..i18n import _

    label = _("You asked")
    label_esc = html.escape(label, quote=False)
    aria = html.escape(f"{label}: {q_plain}", quote=True)
    # Raw HTML survives markdown → HTML and is styled in markdown_to_browser_page.
    # ``heading`` is intentionally unused (kept so existing callers need no change).
    block = (
        f"\n\n---\n\n"
        f'<div id="cm-latest-followup" class="chat-bubble chat-user" '
        f'role="note" tabindex="-1" aria-label="{aria}">'
        f'<span class="chat-user-label">{label_esc}</span>'
        f"<p>{q}</p>"
        f"</div>\n\n"
        f"{answer}"
    )
    if prev:
        return prev + block
    return block.lstrip()


_FOLLOWUP_BUBBLE_MARKER = 'class="chat-bubble chat-user"'


def followup_markdown_suffix(md: str) -> str:
    """Return the follow-up Q&A suffix (from the first chat bubble), or ``""``.

    Used so “Assess more…” can replace the synthesis while keeping earlier
    questions in the report.
    """
    text = md or ""
    idx = text.find(_FOLLOWUP_BUBBLE_MARKER)
    if idx < 0:
        return ""
    sep = text.rfind("\n\n---\n\n", 0, idx)
    if sep >= 0:
        return text[sep:]
    div = text.rfind("<div", 0, idx)
    if div >= 0:
        return text[div:]
    return ""


def merge_followup_suffix(synthesis: str, suffix: str) -> str:
    """Append a previously extracted follow-up suffix onto new synthesis text."""
    base = (synthesis or "").rstrip()
    extra = suffix or ""
    if not extra.strip():
        return base
    if _FOLLOWUP_BUBBLE_MARKER in base:
        return base
    extra = extra if extra.startswith("\n") else "\n\n" + extra.lstrip()
    if not base:
        return extra.lstrip()
    return base + extra


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


def ai_idle_placeholder_markdown() -> str:
    """Localized empty-state copy for the AI output pane before a reply arrives."""
    from ..i18n import _

    return (
        f"## {_('AI assistance')}\n\n"
        f"{_('AI-generated responses will be shown here.')}\n"
    )


def ai_idle_placeholder_page(*, title: str, tab_exit: bool = True) -> str:
    """Styled HTML placeholder page for an idle AI WebView."""
    return markdown_to_browser_page(
        ai_idle_placeholder_markdown(),
        title=title,
        plain=False,
        tab_exit=tab_exit,
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
        f"## {_('Code')}\n\n{issue.code or '—'}\n",
        f"## {_('Severity')}\n\n{issue.severity.label}\n",
    ]
    impact = (getattr(issue, "impact", "") or "").strip()
    if impact:
        parts.append(f"## {_('Impact')}\n\n{impact.title()}\n")
    ruleset = (getattr(issue, "ruleset", "") or "").strip()
    if ruleset:
        parts.append(f"## {_('Ruleset')}\n\n{ruleset}\n")
    parts.append(f"## {_('Source')}\n\n{issue.source or '—'}\n")
    if count > 1:
        parts.append(f"## {_('Occurrences')}\n\n{count}\n")
    parts.extend(
        [
            f"## {_('Location')}\n\n{issue.location or none}\n",
            f"## {_('Message')}\n\n{issue.message or none}\n",
        ]
    )
    help_title, help_text, help_url = _issue_help_fields(issue)
    if help_title or help_text or help_url:
        help_bits: list[str] = [f"## {_('Help')}\n"]
        if help_title:
            help_bits.append(f"**{help_title}**\n")
        if help_text:
            help_bits.append(f"{help_text}\n")
        if help_url:
            help_bits.append(f"{help_url}\n")
        parts.append("\n".join(help_bits))
    return "\n".join(parts)


def _issue_help_fields(issue: "Issue") -> tuple[str, str, str]:
    """
    Title, remediation text, and URL for the details Help section.

    Prefers Ace (or other checker) help fields; falls back to the mapped
    Knowledge Base article when present.
    """
    from .ace_kb_map import normalize_kb_url
    from .resources import primary_kb_resource

    title = (getattr(issue, "help_title", "") or "").strip()
    text = (getattr(issue, "help_text", "") or "").strip()
    url = normalize_kb_url((getattr(issue, "help_url", "") or "").strip())
    if title or text or url:
        return title, text, url
    kb = primary_kb_resource(issue)
    if kb:
        return (kb[0] or "").strip(), "", normalize_kb_url(kb[1] or "")
    return "", "", ""


def _issue_details_dialog_css() -> str:
    """Brand tokens from the AI page, tightened for the issue-details WebView."""
    try:
        from ..ui_appearance import wrap_os_dark_css
    except Exception:
        def wrap_os_dark_css(inner: str) -> str:
            return f"@media (prefers-color-scheme: dark) {{\n{inner}\n}}\n"

    return (
        _ai_browser_css()
        + """
    :root {
      --fatal-fg: #7f1d1d;
      --fatal-bg: #fef2f2;
      --error-fg: #991b1b;
      --error-bg: #fef2f2;
      --warning-fg: #9a3412;
      --warning-bg: #fff7ed;
      --info-fg: #1e3a8a;
      --info-bg: #eff6ff;
      --usage-fg: #334155;
      --usage-bg: #e8eef5;
    }
"""
        + wrap_os_dark_css(_ISSUE_DARK_ROOT_TOKENS)
        + """
    main {
      max-width: none;
      margin: 0;
      padding: 0.7rem 0.8rem 0.9rem;
    }
    .issue-meta {
      margin-bottom: 0.75rem;
      padding: 0.7rem 0.8rem;
      grid-template-columns: repeat(auto-fit, minmax(6.5rem, 1fr));
    }
    .issue-meta h2 {
      font-size: 0.7rem;
    }
    .issue-meta p {
      font-size: 0.95rem;
      font-weight: 600;
    }
    .code-block .code-text {
      display: inline-block;
      font-size: 1.05rem;
      line-height: 1.35;
      padding: 0.15rem 0;
    }
    .sev {
      display: inline-block;
      font-weight: 700;
      font-size: 0.78rem;
      line-height: 1.2;
      padding: 0.22rem 0.55rem;
      border-radius: 999px;
      border: 1px solid transparent;
      letter-spacing: 0.01em;
    }
    .sev-fatal {
      color: var(--fatal-fg);
      background: var(--fatal-bg);
      border-color: color-mix(in srgb, var(--fatal-fg) 30%, transparent);
    }
    .sev-error {
      color: var(--error-fg);
      background: var(--error-bg);
      border-color: color-mix(in srgb, var(--error-fg) 30%, transparent);
    }
    .sev-warning {
      color: var(--warning-fg);
      background: var(--warning-bg);
      border-color: color-mix(in srgb, var(--warning-fg) 30%, transparent);
    }
    .sev-info {
      color: var(--info-fg);
      background: var(--info-bg);
      border-color: color-mix(in srgb, var(--info-fg) 30%, transparent);
    }
    .sev-usage, .sev-unknown {
      color: var(--usage-fg);
      background: var(--usage-bg);
      border-color: color-mix(in srgb, var(--usage-fg) 30%, transparent);
    }
    .field-block {
      margin: 0 0 0.7rem;
      padding: 0.7rem 0.8rem;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .field-block:last-child { margin-bottom: 0; }
    .field-block h2 {
      margin: 0 0 0.35rem;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      border: 0;
      padding: 0;
    }
    .field-value {
      margin: 0;
      font-size: 0.95rem;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-wrap: break-word;
    }
    .field-value a {
      font-weight: 600;
    }
    .help-title {
      margin: 0 0 0.35rem;
      font-size: 1rem;
      font-weight: 700;
      line-height: 1.35;
    }
    .code-text {
      font-family: var(--mono);
      font-size: 0.92em;
      font-weight: 600;
    }
"""
    )


def issue_details_page(
    issue: "Issue",
    *,
    count: int = 1,
    tab_exit: bool = True,
) -> str:
    """
    Compact branded HTML for the in-dialog issue-details WebView.

    Checker fields are HTML-escaped (not markdown) so raw markup in messages
    cannot inject structure. Works for all checker types via the Issue model.
    """
    from ..i18n import _, get_language, get_text_direction
    from ..models import Severity

    none = _("(none)")
    sev = issue.severity if isinstance(issue.severity, Severity) else Severity.UNKNOWN
    sev_class = {
        Severity.FATAL: "fatal",
        Severity.ERROR: "error",
        Severity.WARNING: "warning",
        Severity.INFO: "info",
        Severity.USAGE: "usage",
        Severity.UNKNOWN: "unknown",
    }.get(sev, "unknown")

    def esc(text: str) -> str:
        return html.escape(text or "", quote=False)

    meta_items = [
        (
            _("Severity"),
            f'<p><span class="sev sev-{sev_class}">{esc(sev.label)}</span></p>',
        ),
    ]
    impact = (getattr(issue, "impact", "") or "").strip()
    if impact:
        meta_items.append((_("Impact"), f"<p>{esc(impact.title())}</p>"))
    ruleset = (getattr(issue, "ruleset", "") or "").strip()
    if ruleset:
        meta_items.append((_("Ruleset"), f"<p>{esc(ruleset)}</p>"))
    meta_items.append((_("Source"), f"<p>{esc(issue.source or '—')}</p>"))
    if count > 1:
        meta_items.append((_("Occurrences"), f"<p>{int(count)}</p>"))

    meta_html = "".join(
        f'<div class="meta-item"><h2>{esc(label)}</h2>{value}</div>'
        for label, value in meta_items
    )
    code_html = f"""
<section class="field-block code-block">
<h2>{esc(_("Code"))}</h2>
<p class="field-value"><code class="code-text">{esc(issue.code or "—")}</code></p>
</section>
"""
    location = esc(issue.location or none)
    message = esc(issue.message or none)
    help_title, help_text, help_url = _issue_help_fields(issue)
    help_html = ""
    if help_title or help_text or help_url:
        bits: list[str] = []
        if help_title:
            bits.append(f'<p class="help-title">{esc(help_title)}</p>')
        if help_text:
            bits.append(f'<p class="field-value">{esc(help_text)}</p>')
        if help_url:
            safe_href = html.escape(help_url, quote=True)
            bits.append(
                f'<p class="field-value"><a href="{safe_href}">{esc(help_url)}</a></p>'
            )
        help_html = f"""
<section class="field-block">
<h2>{esc(_("Help"))}</h2>
{"".join(bits)}
</section>
"""
    title = html.escape(_("Issue details"), quote=False)
    tab_script = _WEBVIEW_TAB_EXIT_SCRIPT if tab_exit else ""
    body_attrs = ' tabindex="-1"' if tab_exit else ""
    lang = html.escape(get_language())
    direction = html.escape(get_text_direction())
    return f"""<!DOCTYPE html>
<html lang="{lang}" dir="{direction}" class="{_html_root_class()}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="{_html_color_scheme()}">
  <title>{title}</title>
<style>{_issue_details_dialog_css()}
</style>
</head>
<body{body_attrs}>
<main>
{code_html}
<section class="issue-meta" aria-label="{title}">
{meta_html}
</section>
<section class="field-block">
<h2>{esc(_("Location"))}</h2>
<p class="field-value">{location}</p>
</section>
<section class="field-block">
<h2>{esc(_("Message"))}</h2>
<p class="field-value">{message}</p>
</section>
{help_html}
</main>
{tab_script}
</body>
</html>
"""


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
