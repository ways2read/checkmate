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


def _ai_browser_css() -> str:
    """Shared look with the checker HTML report (lighter, prose-focused)."""
    return """
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
      --code-bg: #e8f0f8;
      --radius: 0.5rem;
      --font: "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
      --mono: ui-monospace, "Cascadia Code", "Consolas", "Liberation Mono", monospace;
      --shadow: 0 1px 2px rgb(15 23 42 / 8%);
    }
    @media (prefers-color-scheme: dark) {
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
        --code-bg: #0f172a;
        --shadow: 0 1px 3px rgb(0 0 0 / 35%);
      }
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    @media (prefers-reduced-motion: reduce) {
      html { scroll-behavior: auto; }
    }
    body {
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
    h2.ai-fix-heading {
      color: var(--fix-fg);
      border-bottom-color: var(--fix-border);
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
      .doc-header, .issue-meta, .doc-meta, .ai-note, pre, table {
        box-shadow: none;
        break-inside: avoid;
      }
    }
    """


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
    from ..i18n import _, get_language

    safe_title = html.escape(title or "CheckMate")
    if plain:
        body = f"<pre class='plain'>{html.escape(text or '')}</pre>"
    else:
        body = _structure_ai_browser_body(
            markdown_to_body_html(text or "", for_dialog=False)
        )
    tab_script = _WEBVIEW_TAB_EXIT_SCRIPT if tab_exit else ""
    body_attrs = ' tabindex="-1"' if tab_exit else ""
    footer = ""
    if not tab_exit:
        footer = f'<footer class="doc-footer">{html.escape(_("Generated by CheckMate"))}</footer>'
    return f"""<!DOCTYPE html>
<html lang="{html.escape(get_language())}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{safe_title}</title>
<style>{_ai_browser_css()}
</style>
</head>
<body{body_attrs}>
<main>
{body}
{footer}
</main>
{tab_script}
</body>
</html>
"""


# Custom scheme handled by the dialog WebView NAVIGATING handler (vetoed).
_WEBVIEW_TAB_EXIT_SCRIPT = """
<script>
(function () {
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
      closeDialog();
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
      || active === document.documentElement;
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
</script>
""".strip()


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
