"""Format code for reading: indentation and line breaks only, never content.

Markup (XHTML, HTML, OPF, NCX, SVG, SMIL, other XML) puts each block element
on its own line, indented by nesting. Whitespace is only changed where it
cannot affect rendering: between elements of element-only content. Running
text and anything with inline children (``<p>…<em>…</em>…</p>``) is kept
exactly as written, as are ``pre``, ``script``, ``style``, ``textarea`` and
SVG text. Tags, attributes, comments, and text are copied verbatim.

CSS gets one declaration per line, indented inside ``{ }``. Strings,
comments, and ``url(…)`` contents are left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

INDENT = "  "

MARKUP_SUFFIXES = frozenset(
    {".xhtml", ".html", ".htm", ".opf", ".ncx", ".xml", ".svg", ".smil", ".mml", ".pls"}
)
CSS_SUFFIXES = frozenset({".css"})
_HTML_SUFFIXES = frozenset({".xhtml", ".html", ".htm"})

# Whitespace between these and their siblings can show on the page.
_INLINE_HTML = frozenset(
    {
        "a", "abbr", "audio", "b", "bdi", "bdo", "br", "button", "canvas", "cite",
        "code", "data", "del", "dfn", "em", "embed", "i", "iframe", "img", "input",
        "ins", "kbd", "label", "map", "mark", "math", "meter", "object", "output",
        "picture", "progress", "q", "ruby", "rb", "rp", "rt", "rtc", "s", "samp",
        "select", "small", "span", "strong", "sub", "sup", "svg", "time", "u",
        "var", "video", "wbr",
    }
)
# Content kept exactly as written, whatever it holds.
_PRESERVE = frozenset({"pre", "script", "style", "textarea", "text", "tspan", "textpath", "listing"})
# Content read as raw text (may contain "<" that is not markup).
_RAW_TEXT = frozenset({"script", "style"})
_HTML_VOID = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }
)

_TOKEN = re.compile(
    r"""
    (?P<comment><!--.*?-->)
  | (?P<cdata><!\[CDATA\[.*?\]\]>)
  | (?P<doctype><!DOCTYPE(?:[^\[>]|\[.*?\])*>)
  | (?P<pi><\?.*?\?>)
  | (?P<end></\s*(?P<endname>[A-Za-z_][\w:.\-]*)\s*>)
  | (?P<start><(?P<name>[A-Za-z_][\w:.\-]*)(?:[^>"']|"[^"]*"|'[^']*')*?(?P<selfclose>/?)>)
    """,
    re.S | re.X | re.I,
)
_XML_SPACE_PRESERVE = re.compile(r"""xml:space\s*=\s*["']preserve["']""")


class FormatError(ValueError):
    """The markup is not well formed enough to format safely.

    *kind* is ``unmatched_end`` (an end tag with no start tag) or
    ``unclosed`` (a start tag never closed); *name* and *line* say where.
    """

    def __init__(self, kind: str, name: str, line: int) -> None:
        super().__init__(f"{kind}: {name} on line {line}")
        self.kind = kind
        self.name = name
        self.line = line


@dataclass
class _Element:
    name: str
    start: str
    offset: int
    children: list = field(default_factory=list)
    end: str = ""

    @property
    def local(self) -> str:
        return self.name.rsplit(":", 1)[-1].lower()


@dataclass
class _Text:
    raw: str


@dataclass
class _Other:
    """Comment, processing instruction, DOCTYPE, or CDATA section."""

    raw: str
    is_cdata: bool = False


def format_code(text: str, member: str) -> str | None:
    """Formatted *text*, or None when files like *member* have no formatter."""
    suffix = Path(member).suffix.lower()
    if suffix in CSS_SUFFIXES:
        return format_css(text)
    if suffix in MARKUP_SUFFIXES:
        return format_markup(text, html=suffix in _HTML_SUFFIXES)
    return None


def can_format(member: str) -> bool:
    return Path(member).suffix.lower() in MARKUP_SUFFIXES | CSS_SUFFIXES


def map_offset(old: str, new: str, offset: int) -> int:
    """Offset in *new* of the character at *offset* in *old*, when only whitespace changed.

    A caret on a visible character stays on it; a caret on whitespace goes
    just after the visible character before it.
    """
    seen = sum(1 for ch in old[:offset] if not ch.isspace())
    on_visible = offset < len(old) and not old[offset].isspace()
    if seen == 0 and not on_visible:
        return 0
    for index, ch in enumerate(new):
        if not ch.isspace():
            if on_visible and seen == 0:
                return index
            seen -= 1
            if seen == 0 and not on_visible:
                return index + 1
    return len(new)


# --- Markup ---


def _parse(text: str, *, html: bool) -> list:
    root = _Element("", "", 0)
    stack = [root]
    pos = 0

    def line_at(offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    while pos < len(text):
        m = _TOKEN.search(text, pos)
        if m is None:
            stack[-1].children.append(_Text(text[pos:]))
            break
        if m.start() > pos:
            stack[-1].children.append(_Text(text[pos : m.start()]))
        pos = m.end()
        kind = m.lastgroup
        if kind in ("comment", "doctype", "pi", "cdata"):
            stack[-1].children.append(_Other(m.group(), is_cdata=kind == "cdata"))
        elif m.group("start") is not None:
            element = _Element(m.group("name"), m.group(), m.start())
            stack[-1].children.append(element)
            if m.group("selfclose") or (html and element.local in _HTML_VOID):
                continue
            if element.local in _RAW_TEXT:
                close = re.compile(r"</\s*" + re.escape(element.name) + r"\s*>", re.I)
                found = close.search(text, pos)
                if found is None:
                    raise FormatError("unclosed", element.name, line_at(element.offset))
                if found.start() > pos:
                    element.children.append(_Text(text[pos : found.start()]))
                element.end = found.group()
                pos = found.end()
                continue
            stack.append(element)
        else:
            name = m.group("endname")
            if len(stack) == 1 or stack[-1].name.lower() != name.lower():
                if html and name.lower() in _HTML_VOID:
                    # <br></br> style: attach the stray end tag to its void element.
                    stack[-1].children.append(_Other(m.group()))
                    continue
                raise FormatError("unmatched_end", name, line_at(m.start()))
            stack.pop().end = m.group()
    if len(stack) > 1:
        raise FormatError("unclosed", stack[-1].name, line_at(stack[-1].offset))
    return root.children


def _verbatim(node) -> str:
    if isinstance(node, _Element):
        return node.start + "".join(_verbatim(c) for c in node.children) + node.end
    return node.raw


def _lays_out(element: _Element, *, html: bool) -> bool:
    """True when the element's children may each go on their own line."""
    if element.local in _PRESERVE or _XML_SPACE_PRESERVE.search(element.start):
        return False
    if not element.children:
        return False
    for child in element.children:
        if isinstance(child, _Text) and child.raw.strip():
            return False
        if isinstance(child, _Other) and child.is_cdata:
            return False
        if html and isinstance(child, _Element) and child.local in _INLINE_HTML:
            return False
    return any(isinstance(child, (_Element, _Other)) for child in element.children)


def _emit(nodes: list, depth: int, out: list[str], *, html: bool) -> None:
    pad = INDENT * depth
    for node in nodes:
        if isinstance(node, _Text):
            if node.raw.strip():
                out.append(pad + node.raw.strip())
            continue
        if isinstance(node, _Element) and _lays_out(node, html=html):
            out.append(pad + node.start)
            _emit(node.children, depth + 1, out, html=html)
            out.append(pad + node.end)
        else:
            out.append(pad + _verbatim(node))


def format_markup(text: str, *, html: bool = False) -> str:
    """Indent element-only content; keep text, inline markup, and tags verbatim."""
    out: list[str] = []
    _emit(_parse(text, html=html), 0, out, html=html)
    return "\n".join(out) + "\n"


# --- CSS ---


def format_css(text: str) -> str:
    """One declaration per line, rules indented by nesting."""
    lines: list[str] = []
    # (verbatim, text): strings and comments keep their inner whitespace.
    buf: list[tuple[bool, str]] = []
    depth = 0
    parens = 0
    i = 0
    n = len(text)

    def pending() -> bool:
        return any(verbatim or part.strip() for verbatim, part in buf)

    def flush(suffix: str = "") -> None:
        pieces: list[str] = []
        plain: list[str] = []
        for verbatim, part in buf:
            if verbatim:
                pieces.append(re.sub(r"\s+", " ", "".join(plain)))
                plain.clear()
                pieces.append(part)
            else:
                plain.append(part)
        pieces.append(re.sub(r"\s+", " ", "".join(plain)))
        content = "".join(pieces).strip()
        buf.clear()
        if content or suffix:
            lines.append(INDENT * depth + content + suffix)

    def literal_end(start: int) -> int:
        if text.startswith("/*", start):
            end = text.find("*/", start + 2)
            return n if end < 0 else end + 2
        quote = text[start]
        end = start + 1
        while end < n and text[end] != quote:
            end += 2 if text[end] == "\\" else 1
        return min(end + 1, n)

    while i < n:
        ch = text[i]
        if text.startswith("/*", i):
            end = literal_end(i)
            if pending():
                buf.append((True, text[i:end]))
            else:
                buf.clear()
                lines.append(INDENT * depth + text[i:end])
            i = end
            continue
        if ch in "\"'":
            end = literal_end(i)
            buf.append((True, text[i:end]))
            i = end
            continue
        if ch == "(":
            parens += 1
        elif ch == ")":
            parens = max(0, parens - 1)
        elif parens == 0 and ch == "{":
            flush(" {" if pending() else "{")
            depth += 1
            i += 1
            continue
        elif parens == 0 and ch == ";":
            flush(";")
            i += 1
            continue
        elif parens == 0 and ch == "}":
            flush()
            depth = max(0, depth - 1)
            lines.append(INDENT * depth + "}")
            if depth == 0:
                lines.append("")
            i += 1
            continue
        buf.append((False, ch))
        i += 1
    flush()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"
