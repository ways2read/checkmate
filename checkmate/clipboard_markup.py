"""Clipboard / snippet markup: detect kind and prepare it for Nu or HTML check."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

MATHML_NS = "http://www.w3.org/1998/Math/MathML"
SVG_NS = "http://www.w3.org/2000/svg"
CLIPBOARD_STEM = "clipboard-check"

_BODY_MARK = "<!--CHECKMATE-SNIPPET-->"

_HTML_WRAPPER = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Clipboard HTML</title>
</head>
<body>
<main>
{_BODY_MARK}
</main>
</body>
</html>
"""

_MATHML_WRAPPER = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Clipboard MathML</title>
</head>
<body>
<main>
{_BODY_MARK}
</main>
</body>
</html>
"""

# Page-level axe rules that are noise for a wrapped clipboard snippet.
SNIPPET_AXE_CODES = frozenset(
    {
        "document-title",
        "html-has-lang",
        "html-lang-valid",
        "page-has-heading-one",
        "region",
        "landmark-one-main",
        "bypass",
        "skip-link",
        "meta-viewport",
    }
)

_SVG_WRAPPER = f"""\
<svg xmlns="{SVG_NS}">
{_BODY_MARK}
</svg>
"""

_HTML_TAG_RE = re.compile(
    r"<(?:!DOCTYPE\s+html|html|head|body|div|span|p|section|article|"
    r"header|footer|main|nav|ul|ol|li|table|thead|tbody|tr|td|th|"
    r"h[1-6]|img|a|br|hr|input|button|form|label|textarea|select|"
    r"figure|figcaption|aside|details|summary|picture|source|video|"
    r"audio|canvas|script|style|meta|link|title)\b",
    re.IGNORECASE,
)
_CSS_HINT_RE = re.compile(
    r"(@(?:charset|import|media|font-face|supports|keyframes|layer)\b)|"
    r"(^|[\s}>])([.#]?[A-Za-z][\w-]*)\s*\{",
    re.MULTILINE,
)
_MARKUP_TAG_RE = re.compile(r"</?[A-Za-z_:]")


class ClipboardKind(str, Enum):
    HTML = "html"
    CSS = "css"
    XML = "xml"
    SVG = "svg"
    MATHML = "mathml"
    UNKNOWN = "unknown"


CLIPBOARD_KIND_LABELS: dict[ClipboardKind, str] = {
    ClipboardKind.HTML: "HTML",
    ClipboardKind.CSS: "CSS",
    ClipboardKind.XML: "XML",
    ClipboardKind.SVG: "SVG",
    ClipboardKind.MATHML: "MathML",
}

_KIND_SUFFIX: dict[ClipboardKind, str] = {
    ClipboardKind.HTML: ".html",
    ClipboardKind.CSS: ".css",
    ClipboardKind.XML: ".xml",
    ClipboardKind.SVG: ".svg",
    ClipboardKind.MATHML: ".mml",
}

CHOOSABLE_KINDS: tuple[ClipboardKind, ...] = (
    ClipboardKind.HTML,
    ClipboardKind.CSS,
    ClipboardKind.XML,
    ClipboardKind.SVG,
    ClipboardKind.MATHML,
)


def looks_like_mathml(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if "<math" in low:
        return True
    return MATHML_NS.lower() in low


def is_mathml_xml(path: Path) -> bool:
    """True when *path* looks like a MathML XML document (no DTD fetch)."""
    if not path.is_file():
        return False
    try:
        data = path.read_bytes()[:8192]
    except OSError:
        return False
    return looks_like_mathml(data.decode("utf-8", errors="ignore"))


def _is_complete_html(text: str) -> bool:
    """True when the paste looks like a full page, not a snippet or skeleton.

    A bare ``<html><body>…`` wrapper (no doctype, or no head/title) is treated
    as a snippet so Check clipboard… does not fail on missing page chrome.
    """
    raw = (text or "").lstrip()
    if not raw:
        return False
    low = raw[:12000].lower()
    if low.startswith("<!doctype html"):
        return True
    if not re.match(r"<html(\s|>|/>)", low):
        return False
    return bool(re.search(r"<head\b", low) and re.search(r"<title\b", low))


def html_snippet_content(text: str) -> str:
    """Inner markup to wrap: body contents, else html contents, else the text."""
    raw = (text or "").strip()
    if not raw:
        return raw
    body = re.search(r"<body\b[^>]*>(.*)</body\s*>", raw, flags=re.IGNORECASE | re.DOTALL)
    if body:
        return body.group(1).strip() or raw
    opened = re.search(r"<body\b[^>]*>", raw, flags=re.IGNORECASE)
    if opened:
        rest = raw[opened.end() :]
        rest = re.sub(r"</body\s*>\s*</html\s*>\s*$", "", rest, flags=re.IGNORECASE)
        rest = re.sub(r"</html\s*>\s*$", "", rest, flags=re.IGNORECASE)
        return rest.strip() or raw
    html = re.search(r"<html\b[^>]*>(.*)</html\s*>", raw, flags=re.IGNORECASE | re.DOTALL)
    if html:
        inner = html.group(1).strip()
        inner = re.sub(
            r"<head\b[^>]*>.*?</head\s*>",
            "",
            inner,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        return inner or raw
    return raw


def clipboard_document_is_snippet(document: str) -> bool:
    return _BODY_MARK in (document or "")


def _insert_snippet(wrapper: str, snippet: str) -> str:
    """Keep the snippet marker so we can detect wrapping later."""
    if _BODY_MARK not in wrapper:
        return snippet
    return wrapper.replace(_BODY_MARK, _BODY_MARK + "\n" + snippet, 1)


def _is_xml_mathml_document(text: str) -> bool:
    start = text.lstrip()
    low = start.lower()
    if low.startswith("<?xml"):
        return "<math" in low
    if not low.startswith("<math"):
        return False
    head = low[:500]
    return "math/mathml" in head or "xmlns" in head


def _looks_like_css(text: str) -> bool:
    raw = text.strip()
    if not raw:
        return False
    if _MARKUP_TAG_RE.search(raw):
        return False
    if "{" not in raw:
        return bool(re.match(r"@(charset|import)\b", raw, re.IGNORECASE))
    return bool(_CSS_HINT_RE.search(raw))


def _strip_xml_prolog(text: str) -> str:
    rest = text.lstrip()
    rest = re.sub(r"^<\?xml[^?]*\?>\s*", "", rest, count=1, flags=re.IGNORECASE)
    rest = re.sub(r"^<!DOCTYPE[^>]*>\s*", "", rest, count=1, flags=re.IGNORECASE)
    return rest.lstrip()


def _looks_like_svg(text: str) -> bool:
    rest = _strip_xml_prolog(text).lower()
    return rest.startswith("<svg")


def _markup_start(text: str) -> str:
    return (text or "").lstrip("\ufeff \t\n\r")


def looks_like_checkmate_report(text: str) -> bool:
    """True when the clipboard is a CheckMate results report, not source markup."""
    head = _markup_start(text)[:500]
    if not head:
        return False
    if re.match(
        r"(nu html checker|epubcheck|ace by daisy|ace |verapdf|ebraille checker)"
        r".{0,80}\breport\b",
        head,
        flags=re.IGNORECASE,
    ):
        return True
    if re.match(r"(web page|publication):", head, flags=re.IGNORECASE):
        return True
    if re.match(
        r"(failed|passed|passed with warnings|error)\s+[—–-]",
        head,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def _looks_like_html(text: str) -> bool:
    if _is_complete_html(text):
        return True
    start = _markup_start(text)
    # Prose that mentions tags (e.g. a CheckMate report) is not markup.
    if not start.startswith("<"):
        return False
    return bool(_HTML_TAG_RE.match(start) or _HTML_TAG_RE.search(start[:2000]))


def _looks_like_xml(text: str) -> bool:
    raw = text.lstrip()
    if not raw.startswith("<"):
        return False
    low = raw[:200].lower()
    if low.startswith("<?xml") or low.startswith("<!doctype"):
        return True
    return bool(_MARKUP_TAG_RE.match(raw))


def _is_mathml_document(text: str) -> bool:
    rest = _strip_xml_prolog(text).lower()
    if rest.startswith("<math"):
        return True
    return MATHML_NS.lower() in rest[:800] and rest.startswith("<") and "<math" in rest


def extract_cf_html_fragment(text: str) -> str:
    """Return the fragment from a Windows CF_HTML payload, or *text* unchanged."""
    raw = text or ""
    start_m = re.search(r"StartFragment:(\d+)", raw)
    end_m = re.search(r"EndFragment:(\d+)", raw)
    if start_m and end_m:
        try:
            start = int(start_m.group(1))
            end = int(end_m.group(1))
        except ValueError:
            start = end = -1
        if 0 <= start < end <= len(raw):
            frag = raw[start:end].strip()
            if frag:
                return frag
    marked = re.search(
        r"<!--StartFragment-->(.*)<!--EndFragment-->",
        raw,
        re.DOTALL | re.IGNORECASE,
    )
    if marked:
        return marked.group(1).strip()
    return raw.strip()


def prefer_clipboard_payload(plain: str, html: str = "") -> str:
    """Prefer plain text when it is recognizable markup; otherwise HTML clipboard."""
    plain_text = extract_cf_html_fragment(plain or "").strip()
    html_text = extract_cf_html_fragment(html or "").strip()
    if plain_text and detect_clipboard_kind(plain_text) != ClipboardKind.UNKNOWN:
        return plain_text
    if html_text:
        return html_text
    return plain_text


def detect_clipboard_kind(text: str) -> ClipboardKind:
    """Best-effort kind for clipboard text. ``UNKNOWN`` when it is not obvious."""
    raw = (text or "").strip()
    if not raw:
        return ClipboardKind.UNKNOWN
    if looks_like_checkmate_report(raw):
        return ClipboardKind.UNKNOWN
    if _is_complete_html(raw):
        return ClipboardKind.HTML
    if _looks_like_css(raw):
        return ClipboardKind.CSS
    if _looks_like_svg(raw):
        return ClipboardKind.SVG
    if _is_mathml_document(raw):
        return ClipboardKind.MATHML
    if _looks_like_html(raw):
        return ClipboardKind.HTML
    if _looks_like_xml(raw):
        return ClipboardKind.XML
    return ClipboardKind.UNKNOWN


def prepare_clipboard_document(text: str, kind: ClipboardKind) -> str:
    """Normalize clipboard text into a document Nu (or HTML check) can open.

    HTML and MathML fragments (including a standalone ``<math>`` root, with or
    without xmlns / XML prolog) are wrapped in a complete page so Nu reports
    problems in the snippet, not missing page chrome or 'math not allowed as
    root'. Skeleton ``<html><body>`` documents are wrapped the same way.
    """
    raw = (text or "").strip()
    if kind == ClipboardKind.HTML:
        if clipboard_document_is_snippet(raw) or _is_complete_html(raw):
            return raw
        return _insert_snippet(_HTML_WRAPPER, html_snippet_content(raw))
    if kind == ClipboardKind.MATHML:
        if clipboard_document_is_snippet(raw) or _is_complete_html(raw):
            return raw
        inner = _strip_xml_prolog(raw) if _is_mathml_document(raw) else html_snippet_content(raw)
        return _insert_snippet(_MATHML_WRAPPER, inner or raw)
    if kind == ClipboardKind.SVG:
        low = raw.lstrip().lower()
        if low.startswith("<svg") or low.startswith("<?xml"):
            return raw
        return _insert_snippet(_SVG_WRAPPER, raw)
    return raw


def vnu_args_for_kind(kind: ClipboardKind, document: str = "") -> list[str]:
    """Nu extra args for a clipboard/document kind."""
    if kind == ClipboardKind.CSS:
        return ["--css"]
    if kind == ClipboardKind.SVG:
        return ["--svg"]
    if kind == ClipboardKind.XML:
        return ["--xml"]
    if kind == ClipboardKind.MATHML:
        # Standalone ``<math>`` (even with xmlns) is wrapped as an HTML snippet
        # for Nu. Only unwrapped XML MathML files still use ``--xml``.
        if document and clipboard_document_is_snippet(document):
            return ["--html"]
        if document and _is_xml_mathml_document(document):
            return ["--xml"]
        return ["--html"]
    if kind == ClipboardKind.HTML:
        return ["--html"]
    return []


def clipboard_snapshot_path(kind: ClipboardKind) -> Path:
    from .paths import app_data_dir

    suffix = _KIND_SUFFIX.get(kind, ".txt")
    return app_data_dir() / f"{CLIPBOARD_STEM}{suffix}"


def is_clipboard_snapshot_path(path: Path) -> bool:
    name = path.name.lower()
    if ".orig." in name:
        return False
    return name.startswith(CLIPBOARD_STEM) or name == "clipboard-mathml.mml"


def is_clipboard_snippet_path(path: Path) -> bool:
    """True when *path* is a CheckMate clipboard snapshot wrapped as a snippet."""
    try:
        resolved = path.expanduser()
    except OSError:
        return False
    if not is_clipboard_snapshot_path(resolved):
        return False
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return clipboard_document_is_snippet(text)


def clipboard_snapshot_files(root: Path | None = None) -> list[Path]:
    """Snapshot files under *root* (default: CheckMate app-data)."""
    from .paths import app_data_dir

    base = root if root is not None else app_data_dir()
    found: list[Path] = []
    seen: set[Path] = set()
    names = [f"{CLIPBOARD_STEM}{suffix}" for suffix in _KIND_SUFFIX.values()]
    names.append("clipboard-mathml.mml")
    for name in names:
        path = base / name
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        found.append(path)
    return found


def resolve_clipboard_snapshot(
    preferred: Path | None = None,
    *,
    root: Path | None = None,
) -> Path | None:
    """Return *preferred* when it is a snapshot file, else the newest snapshot."""
    if preferred is not None:
        try:
            candidate = preferred.expanduser()
            if candidate.is_file() and is_clipboard_snapshot_path(candidate):
                return candidate
        except OSError:
            pass
    files = clipboard_snapshot_files(root)
    if not files:
        return None

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return max(files, key=_mtime)


def clipboard_source_path(snapshot: Path) -> Path:
    """Sidecar file with the original clipboard text for *snapshot*."""
    return snapshot.with_name(f"{snapshot.stem}.orig{snapshot.suffix}")


def extract_clipboard_view_text(document: str) -> str:
    """Unwrap a snippet document for display; otherwise return *document*."""
    raw = document or ""
    if _BODY_MARK not in raw:
        return raw
    after = raw.split(_BODY_MARK, 1)[1]
    after = re.sub(
        r"</main>\s*</body>\s*</html>\s*$", "", after, flags=re.IGNORECASE
    )
    after = re.sub(r"</svg>\s*$", "", after, flags=re.IGNORECASE)
    return after.strip()


def clipboard_view_text(snapshot: Path) -> str:
    """Markup to show the user: original clipboard text, not the check wrapper."""
    source = clipboard_source_path(snapshot)
    try:
        if source.is_file():
            return source.read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        document = snapshot.read_text(encoding="utf-8")
    except OSError:
        return ""
    return extract_clipboard_view_text(document)


def save_clipboard_snapshot(text: str, kind: ClipboardKind) -> Path:
    """Write a prepared snapshot and return its path (overwrites the last one)."""
    raw = (text or "").strip()
    document = prepare_clipboard_document(raw, kind)
    path = clipboard_snapshot_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    orig = clipboard_source_path(path)
    orig.write_text(raw + ("\n" if raw and not raw.endswith("\n") else ""), encoding="utf-8")
    return path
