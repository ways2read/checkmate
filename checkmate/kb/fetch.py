"""Fetch live KB pages from kb.daisy.org into the local store."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

from ..paths import kb_dir
from .store import (
    content_hash,
    en_file_path,
    en_relative_path_from_url,
    ja_file_path,
    ja_relative_from_en,
    load_manifest,
    online_url_for_en_rel,
    save_manifest,
)

ProgressCallback = Callable[[str], None]

USER_AGENT = "CheckMate/KB-offline (+https://daisy.org/)"
REQUEST_TIMEOUT = 45

# Marker on slim offline documents (article body only, no site chrome).
_SLIM_MARKER = 'data-cm-kb="article"'

# Shared stylesheet for English slim pages and AI translations.
ARTICLE_OFFLINE_CSS = """
body { font-family: system-ui, Segoe UI, sans-serif; line-height: 1.45; margin: 1.25rem; max-width: 52rem; color: #222; }
h1, h2, h3, h4 { line-height: 1.25; }
pre, code { white-space: pre-wrap; font-family: ui-monospace, Consolas, monospace; }
pre { background: #f5f5f5; padding: 0.75rem; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; }
th, td { border: 1px solid #ccc; padding: 0.35rem 0.5rem; text-align: left; vertical-align: top; }
figure { margin: 1rem 0; }
figcaption { font-size: 0.95rem; color: #444; }
.category { font-size: 0.9rem; color: #555; margin-bottom: 0.35rem; }
.wcag-level { white-space: nowrap; }
a { color: #0b57d0; }
.cm-kb-note { font-size: 0.9rem; color: #444; border-bottom: 1px solid #ccc; padding-bottom: 0.75rem; margin-bottom: 1rem; }
""".strip()

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_ASSET_SUFFIXES = (
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
)


class _AssetHrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        if tag == "link" and ad.get("href"):
            self.hrefs.append(ad["href"])
        elif tag == "script" and ad.get("src"):
            self.hrefs.append(ad["src"])
        elif tag == "img" and ad.get("src"):
            self.hrefs.append(ad["src"])
        elif tag == "source" and ad.get("src"):
            self.hrefs.append(ad["src"])
        elif tag == "use" and ad.get("href"):
            self.hrefs.append(ad["href"])


class _HtmlHrefParser(HTMLParser):
    """Collect ``href`` values from anchor tags."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for key, val in attrs:
            if key == "href" and val:
                self.hrefs.append(val)
                return


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    return s


def _element_outer_by_id(html: str, element_id: str) -> str | None:
    """Return the outer HTML of the first element with ``id=element_id``."""
    eid = re.escape(element_id)
    open_re = re.compile(
        rf"<(?P<tag>[a-zA-Z][\w:-]*)\b[^>]*\bid\s*=\s*['\"]{eid}['\"][^>]*>",
        re.IGNORECASE,
    )
    m = open_re.search(html)
    if not m:
        return None
    tag = m.group("tag").lower()
    start = m.start()
    end_open = m.end()
    open_token = html[start:end_open]
    if tag in _VOID_TAGS or open_token.rstrip().endswith("/>"):
        return open_token
    depth = 1
    token_re = re.compile(rf"</?{re.escape(tag)}\b[^>]*>", re.IGNORECASE)
    for tm in token_re.finditer(html, end_open):
        tok = tm.group(0)
        if tok.startswith("</"):
            depth -= 1
            if depth == 0:
                return html[start : tm.end()]
            continue
        if tok.rstrip().endswith("/>") or tag in _VOID_TAGS:
            continue
        depth += 1
    return None


def _inner_html(outer: str) -> str:
    first = outer.find(">")
    last = outer.rfind("<")
    if first < 0 or last <= first:
        return ""
    return outer[first + 1 : last].strip()


def _page_lang(html: str) -> str:
    m = re.search(r"""<html\b[^>]*\blang\s*=\s*['"]([^'"]+)['"]""", html, re.I)
    if m:
        return (m.group(1) or "en").strip() or "en"
    return "en"


def _page_title(html: str) -> str:
    m = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        # Live pages use "Topic - Accessible Publishing Knowledge Base".
        if " - " in title:
            title = title.split(" - ", 1)[0].strip()
        if title:
            return title
    for pat in (
        r"""<div\b[^>]*id=['"]page-title['"][^>]*>.*?<h2\b[^>]*>(.*?)</h2>""",
        r"<h1\b[^>]*>(.*?)</h1>",
        r"<h2\b[^>]*>(.*?)</h2>",
    ):
        m = re.search(pat, html, re.I | re.S)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            if title:
                return title
    return "Article"


def is_slim_article(html: str) -> bool:
    return _SLIM_MARKER in (html or "")


def refresh_article_offline_styles(html: str, *, extra_css: str = "") -> str:
    """
    Ensure a slim (or any) document uses the current offline article stylesheet.

    Replaces the first ``<style>`` block, or inserts one before ``</head>``.
    """
    if not html:
        return html
    css = ARTICLE_OFFLINE_CSS
    if extra_css:
        css = f"{css}\n{extra_css.strip()}"
    style_tag = f"<style>\n{css}\n</style>"
    replaced, n = re.subn(
        r"<style\b[^>]*>.*?</style>",
        style_tag,
        html,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if n:
        return replaced
    if re.search(r"</head\s*>", html, flags=re.IGNORECASE):
        return re.sub(
            r"</head\s*>",
            style_tag + "</head>",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    return style_tag + html


def extract_article_fragment(html: str) -> str:
    """
    Return the article body HTML from a live or cached KB page.

    Prefers ``#body`` (stable on kb.daisy.org), then ``#main`` without chrome,
    then ``<main>`` / ``<article>``, then ``<body>``.
    """
    if not html:
        return ""
    if is_slim_article(html):
        body = _element_outer_by_id(html, "cm-kb-body")
        if body:
            return _inner_html(body)
        m = re.search(r"<body\b[^>]*>(.*)</body>", html, re.I | re.S)
        return (m.group(1).strip() if m else html)

    body = _element_outer_by_id(html, "body")
    if body:
        return _inner_html(body)

    main = _element_outer_by_id(html, "main")
    if main:
        frag = _inner_html(main)
        for chrome_id in ("sponsor", "nav-col", "categories"):
            chrome = _element_outer_by_id(frag, chrome_id)
            if chrome:
                frag = frag.replace(chrome, "", 1)
        wrap = _element_outer_by_id(frag, "col-wrapper")
        if wrap:
            inner = _inner_html(wrap)
            body2 = _element_outer_by_id(inner, "body")
            if body2:
                return _inner_html(body2)
            return inner.strip()
        return frag.strip()

    for tag in ("main", "article"):
        m = re.search(rf"<{tag}\b[^>]*>(.*)</{tag}>", html, re.I | re.S)
        if m:
            return m.group(1).strip()

    m = re.search(r"<body\b[^>]*>(.*)</body>", html, re.I | re.S)
    return (m.group(1).strip() if m else html)


def html_to_plain_text(html: str) -> str:
    """Best-effort plain text from HTML (no full HTML parser)."""
    if not html:
        return ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)</h[1-6]>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def slim_article_document(html: str, *, lang: str | None = None) -> str:
    """
    Build a compact offline HTML document containing only the KB article.

    Drops site header, footer, search, sponsor banner, and side TOC chrome.
    Already-slim pages keep their body but refresh the shared stylesheet.
    """
    if is_slim_article(html):
        return refresh_article_offline_styles(html)
    fragment = extract_article_fragment(html)
    if not fragment:
        return html
    title = _page_title(html)
    page_lang = (lang or _page_lang(html)).strip() or "en"
    # Escape title for text content / attribute-free text node.
    title_safe = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return f"""<!DOCTYPE html>
<html lang="{page_lang}" {_SLIM_MARKER}>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title_safe}</title>
<style>
{ARTICLE_OFFLINE_CSS}
</style>
</head>
<body>
<div id="cm-kb-body">
{fragment}
</div>
</body>
</html>
"""


def _is_kb_daisy_host(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    return p.netloc.lower() == "kb.daisy.org"


def _should_fetch_asset(url: str) -> bool:
    if not _is_kb_daisy_host(url):
        return False
    path = (urlparse(url).path or "").lower()
    if not (path.endswith(_ASSET_SUFFIXES) or path.endswith("favicon.ico")):
        return False
    return (
        path.startswith("/css/")
        or path.startswith("/js/")
        or path.startswith("/graphics/")
        or path.startswith("/publishing/")
        or path.endswith("favicon.ico")
    )


def _local_path_for_asset_url(url: str) -> Path | None:
    """Map a kb.daisy.org asset URL onto the offline store."""
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return None
    if path.startswith("/publishing/"):
        rel = path[len("/publishing/") :].lstrip("/")
        if not rel:
            return None
        if rel.startswith("ja/"):
            return kb_dir() / "ja" / Path(rel[len("ja/") :])
        return kb_dir() / "en" / Path(rel)
    rel = path.lstrip("/")
    if not rel:
        return None
    return kb_dir() / "site" / Path(rel)


def _download_bytes(session: requests.Session, url: str, dest: Path) -> bytes:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.content
    dest.write_bytes(data)
    return data


def _rewrite_html_for_offline(html: str, page_file: Path, *, page_url: str = "") -> str:
    """
    Make root-relative site assets and /publishing/ links work offline.

    - ``/css|js|graphics/...`` → absolute ``file://`` URI under ``kb/site/...``
      (reliable for both LoadURL and SetPage in Edge WebView2)
    - ``/publishing/...`` and same-site relative ``.html`` links → absolute
      https URL (viewer intercepts navigations)
    """
    root = kb_dir().resolve()
    page_dir = page_file.resolve().parent
    base_url = page_url or online_url_for_en_rel(
        _guess_en_rel_from_page_file(page_file)
    )

    def site_file_uri(site_subpath: str) -> str:
        target = (root / "site" / site_subpath.lstrip("/")).resolve()
        return target.as_uri()

    html = re.sub(
        r"""\b(href|src)=(['"])/(css|js|graphics)/([^'"]+)\2""",
        lambda m: (
            f"{m.group(1)}={m.group(2)}"
            f"{site_file_uri(m.group(3) + '/' + m.group(4))}"
            f"{m.group(2)}"
        ),
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r"""\b(href|src)=(['"])/favicon\.ico\2""",
        lambda m: (
            f"{m.group(1)}={m.group(2)}{site_file_uri('favicon.ico')}{m.group(2)}"
        ),
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r"""\b(href)=(['"])(/publishing/[^'"]*)\2""",
        lambda m: (
            f"{m.group(1)}={m.group(2)}https://kb.daisy.org{m.group(3)}{m.group(2)}"
        ),
        html,
        flags=re.IGNORECASE,
    )

    # Relative / same-folder article links → absolute https for viewer intercept.
    def rewrite_rel_href(match: re.Match[str]) -> str:
        quote = match.group(2)
        href = match.group(3)
        if href.startswith(("#", "mailto:", "javascript:", "data:")):
            return match.group(0)
        if href.startswith(("http://", "https://", "//", "file:")):
            return match.group(0)
        path_only = href.split("#", 1)[0].split("?", 1)[0]
        name = path_only.rsplit("/", 1)[-1].lower()
        # Skip stylesheets, scripts, images, fonts — only HTML articles.
        if name and "." in name and not name.endswith(".html"):
            # Local relative asset (e.g. older caches): absolutize if under kb/.
            try:
                target = (page_dir / path_only).resolve()
                if root == target or root in target.parents:
                    return f"href={quote}{target.as_uri()}{quote}"
            except (OSError, ValueError):
                pass
            return match.group(0)
        abs_url = urljoin(base_url, href)
        en_rel = en_relative_path_from_url(abs_url)
        if not en_rel or not en_rel.startswith("docs/"):
            return match.group(0)
        return f"href={quote}{abs_url}{quote}"

    html = re.sub(
        r"""\b(href)=(['"])([^'"]+)\2""",
        rewrite_rel_href,
        html,
        flags=re.IGNORECASE,
    )

    # Absolutize remaining relative src= assets (img/script) under the kb root.
    def rewrite_rel_src(match: re.Match[str]) -> str:
        quote = match.group(2)
        href = match.group(3)
        if href.startswith(("#", "data:", "http://", "https://", "//", "file:")):
            return match.group(0)
        try:
            target = (page_dir / href.split("#", 1)[0].split("?", 1)[0]).resolve()
            if root == target or root in target.parents:
                return f"src={quote}{target.as_uri()}{quote}"
        except (OSError, ValueError):
            pass
        return match.group(0)

    html = re.sub(
        r"""\b(src)=(['"])([^'"]+)\2""",
        rewrite_rel_src,
        html,
        flags=re.IGNORECASE,
    )
    return html


def prepare_html_for_webview(html: str, page_file: Path) -> str:
    """
    Ensure article navigations use https (viewer intercept) and leave a clean
    document for ``SetPage``.

    Local stylesheets are inlined by ``html_document_for_display`` — Edge
    WebView2 blocks ``file://`` subresources from ``SetPage`` / often paints
    blank for ``LoadURL(file://…)``.
    """
    root = kb_dir().resolve()
    page_url = online_url_for_en_rel(_guess_en_rel_from_page_file(page_file))

    def fix_href(match: re.Match[str]) -> str:
        attr, quote, href = match.group(1), match.group(2), match.group(3)
        if href.startswith(
            ("http://", "https://", "data:", "mailto:", "javascript:", "#")
        ):
            return match.group(0)
        path_only = href.split("#", 1)[0].split("?", 1)[0]
        name = path_only.rsplit("/", 1)[-1].lower()
        if name.endswith(
            (
                ".css",
                ".js",
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".svg",
                ".webp",
                ".ico",
                ".woff",
                ".woff2",
                ".ttf",
            )
        ):
            return match.group(0)
        if href.startswith("file:"):
            local = _file_url_to_path(href)
            if local is not None and local.suffix.lower() == ".html":
                try:
                    rel = local.resolve().relative_to(root / "en").as_posix()
                    return f"{attr}={quote}{online_url_for_en_rel(rel)}{quote}"
                except ValueError:
                    return match.group(0)
            return match.group(0)
        abs_url = urljoin(page_url, href)
        en_rel = en_relative_path_from_url(abs_url)
        if en_rel and en_rel.startswith("docs/"):
            return f"{attr}={quote}{abs_url}{quote}"
        return match.group(0)

    return re.sub(
        r"""\b(href)=(['"])([^'"]+)\2""",
        fix_href,
        html,
        flags=re.IGNORECASE,
    )


def _file_url_to_path(url: str) -> Path | None:
    try:
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        parsed = urlparse(url)
        if parsed.scheme != "file":
            return None
        # Windows: url2pathname('/C:/Users/...') → 'C:\\Users\\...'
        return Path(url2pathname(unquote(parsed.path)))
    except Exception:
        return None


def _resolve_local_asset_path(href: str, page_file: Path) -> Path | None:
    """Map a href/src to a file under the KB store, if possible."""
    root = kb_dir().resolve()
    page_dir = page_file.resolve().parent
    href = (href or "").strip()
    if not href or href.startswith(("data:", "mailto:", "javascript:", "#")):
        return None
    path: Path | None = None
    if href.startswith("file:"):
        path = _file_url_to_path(href)
    elif href.startswith(("http://", "https://", "//")):
        return None
    elif href.startswith("/"):
        if href.startswith(("/css/", "/js/", "/graphics/")):
            path = (root / "site" / href.lstrip("/")).resolve()
        elif href.startswith("/publishing/"):
            rel = href[len("/publishing/") :].lstrip("/")
            if rel.startswith("ja/"):
                path = (root / "ja" / rel[len("ja/") :]).resolve()
            else:
                path = (root / "en" / rel).resolve()
        else:
            return None
    else:
        try:
            path = (page_dir / href.split("#", 1)[0].split("?", 1)[0]).resolve()
        except (OSError, ValueError):
            return None
    if path is None or not path.is_file():
        return None
    try:
        if root not in path.parents and path != root:
            return None
    except Exception:
        return None
    return path


_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}
_MAX_INLINE_IMAGE_BYTES = 1_500_000


def _inline_local_images(html: str, page_file: Path) -> str:
    """Replace local ``img src`` with ``data:`` URIs (SetPage blocks file://)."""
    import base64

    def replace_src(match: re.Match[str]) -> str:
        prefix, quote, href = match.group(1), match.group(2), match.group(3)
        path = _resolve_local_asset_path(href, page_file)
        if path is None:
            return match.group(0)
        suffix = path.suffix.lower()
        mime = _IMAGE_MIME.get(suffix)
        if not mime:
            return match.group(0)
        try:
            data = path.read_bytes()
        except OSError:
            return match.group(0)
        if len(data) > _MAX_INLINE_IMAGE_BYTES:
            return match.group(0)
        if suffix == ".svg":
            # Prefer utf-8 data URI for SVG when possible.
            try:
                text = data.decode("utf-8")
                from urllib.parse import quote as url_quote

                return (
                    f"{prefix}{quote}data:image/svg+xml;charset=utf-8,"
                    f"{url_quote(text)}{quote}"
                )
            except UnicodeDecodeError:
                pass
        b64 = base64.b64encode(data).decode("ascii")
        return f"{prefix}{quote}data:{mime};base64,{b64}{quote}"

    return re.sub(
        r"""(\bsrc=)(['"])([^'"]+)\2""",
        replace_src,
        html,
        flags=re.IGNORECASE,
    )


def _strip_sponsor_chrome(html: str) -> str:
    """
    Remove the DAISY sponsorship banner and its script from offline display.

    Dismiss uses a Secure cookie that does not persist across SetPage reloads
    in the WebView, so the banner would otherwise reappear on every article.
    """
    html = re.sub(
        r"""<\s*script\b[^>]*\bsrc\s*=\s*['"][^'"]*sponsor\.js[^'"]*['"][^>]*>\s*</\s*script\s*>""",
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r"""<\s*aside\b[^>]*\bid\s*=\s*['"]sponsor['"][^>]*>.*?</\s*aside\s*>""",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # Belt-and-suspenders if markup varies.
    html = re.sub(
        r"</head>",
        "<style type=\"text/css\">aside#sponsor{display:none!important}</style></head>",
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    return html


def _inline_local_stylesheets(html: str, page_file: Path) -> str:
    """Replace local ``<link rel=stylesheet>`` tags with inlined ``<style>``."""
    def replace_link(match: re.Match[str]) -> str:
        tag = match.group(0)
        if not re.search(r'rel\s*=\s*[\'"]stylesheet[\'"]', tag, flags=re.I):
            return tag
        hm = re.search(r"""href\s*=\s*['"]([^'"]+)['"]""", tag, flags=re.I)
        if not hm:
            return tag
        href = hm.group(1).strip()
        css_path = _resolve_local_asset_path(href, page_file)
        if css_path is None:
            return tag
        try:
            css = css_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return tag
        # Also inline local url(...) images referenced from CSS when small.
        css = _inline_css_local_urls(css, css_path)
        return f'<style type="text/css">\n/* {css_path.name} */\n{css}\n</style>'

    return re.sub(r"<\s*link\b[^>]*>", replace_link, html, flags=re.IGNORECASE)


def _inline_css_local_urls(css: str, css_file: Path) -> str:
    """Rewrite ``url(...)`` in CSS to data URIs when the file is local."""
    import base64

    css_dir = css_file.resolve().parent

    def replace_url(match: re.Match[str]) -> str:
        raw = match.group(1).strip().strip("'\"")
        if raw.startswith(("data:", "http://", "https://", "//")):
            return match.group(0)
        path = _resolve_local_asset_path(raw, css_file)
        if path is None:
            # url() paths are often relative to the CSS file.
            try:
                candidate = (css_dir / raw.split("#", 1)[0].split("?", 1)[0]).resolve()
                root = kb_dir().resolve()
                if candidate.is_file() and (
                    root in candidate.parents or candidate == root
                ):
                    path = candidate
            except (OSError, ValueError):
                path = None
        if path is None:
            return match.group(0)
        mime = _IMAGE_MIME.get(path.suffix.lower())
        if not mime:
            return match.group(0)
        try:
            data = path.read_bytes()
        except OSError:
            return match.group(0)
        if len(data) > _MAX_INLINE_IMAGE_BYTES:
            return match.group(0)
        b64 = base64.b64encode(data).decode("ascii")
        return f'url("data:{mime};base64,{b64}")'

    return re.sub(r"url\(\s*([^)]+?)\s*\)", replace_url, css, flags=re.IGNORECASE)


def html_document_for_display(page_file: Path) -> str:
    """Read a cached article and prepare a self-contained HTML string for SetPage."""
    raw = page_file.read_text(encoding="utf-8", errors="replace")
    # Always prefer article-only HTML (also upgrades older full-page caches
    # and refreshes the shared stylesheet on already-slim translations).
    html = slim_article_document(raw)
    html = refresh_article_offline_styles(html)
    html = prepare_html_for_webview(html, page_file)
    # Slim docs use inlined CSS; keep stylesheet inlining for any leftover links.
    if re.search(r"""rel\s*=\s*['"]stylesheet['"]""", html, flags=re.I):
        html = _inline_local_stylesheets(html, page_file)
    html = _inline_local_images(html, page_file)
    html = re.sub(
        r"""<\s*script\b[^>]*\bsrc\s*=\s*['"]file:[^'"]+['"][^>]*>\s*</\s*script\s*>""",
        "",
        html,
        flags=re.IGNORECASE,
    )
    return html


def _guess_en_rel_from_page_file(page_file: Path) -> str:
    try:
        resolved = page_file.resolve()
        root = kb_dir().resolve()
        rel = resolved.relative_to(root).as_posix()
    except (ValueError, OSError):
        return "docs/index.html"
    if rel.startswith("en/"):
        return rel[len("en/") :]
    if rel.startswith("ja/"):
        return f"docs/{rel[len('ja/') :]}"
    return "docs/index.html"


def linked_article_paths(html: str, *, page_url: str) -> list[str]:
    """
    English relative paths for HTML articles linked from ``html``.

    Only paths under ``docs/`` (or JA equivalents normalized to English) are
    returned. Fragments and non-HTML targets are ignored.
    """
    found: list[str] = []
    seen: set[str] = set()
    parser = _HtmlHrefParser()
    try:
        parser.feed(html)
    except Exception:
        return found
    for href in parser.hrefs:
        href = (href or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "data:")):
            continue
        path_only = href.split("#", 1)[0].split("?", 1)[0]
        name = path_only.rsplit("/", 1)[-1].lower()
        # Require HTML (or directory → index) — skip css/js/images/fonts.
        if name and "." in name and not name.endswith(".html"):
            continue
        if href.startswith("/") and not href.startswith("//"):
            abs_url = "https://kb.daisy.org" + href
        else:
            abs_url = urljoin(page_url, href)
        abs_url = abs_url.split("#", 1)[0].split("?", 1)[0]
        try:
            path = urlparse(abs_url).path or ""
        except ValueError:
            continue
        # Ignore site chrome mistakenly under /publishing/site/...
        lower_path = path.lower()
        if "/publishing/site/" in lower_path or lower_path.startswith("/site/"):
            continue
        if any(
            lower_path.startswith(prefix)
            for prefix in ("/css/", "/js/", "/graphics/")
        ):
            continue
        en_rel = en_relative_path_from_url(abs_url)
        if not en_rel or not en_rel.startswith("docs/"):
            continue
        if not en_rel.endswith(".html"):
            continue
        # Guard against docs/site/... false positives from relative asset paths.
        if en_rel.startswith("docs/site/"):
            continue
        if en_rel in seen:
            continue
        seen.add(en_rel)
        found.append(en_rel)
    return found


def _fetch_page_assets(
    session: requests.Session,
    html: str,
    page_url: str,
    *,
    progress: ProgressCallback | None,
) -> list[str]:
    assets: list[str] = []
    parser = _AssetHrefParser()
    try:
        parser.feed(html)
    except Exception:
        return assets
    for href in parser.hrefs:
        abs_url = urljoin(page_url, href)
        # Root-relative paths need an explicit host.
        if href.startswith("/") and not href.startswith("//"):
            abs_url = "https://kb.daisy.org" + href
        if not _should_fetch_asset(abs_url):
            continue
        dest = _local_path_for_asset_url(abs_url)
        if dest is None:
            continue
        try:
            if not dest.is_file():
                if progress:
                    progress(f"Downloading asset {dest.name}…")
                _download_bytes(session, abs_url, dest)
            assets.append(dest.name)
        except Exception:
            continue
    return assets


def fetch_article(
    en_rel: str,
    *,
    also_ja: bool = True,
    progress: ProgressCallback | None = None,
    session: requests.Session | None = None,
) -> dict:
    """
    Download one English article (and optional JA) plus referenced same-origin assets.

    Returns article meta ``{en_hash, ja_hash?}``.
    """
    own_session = session is None
    sess = session or _session()
    try:
        en_url = online_url_for_en_rel(en_rel, locale="en")
        if progress:
            progress(f"Downloading {en_rel}…")
        en_dest = en_file_path(en_rel)
        en_bytes = _download_bytes(sess, en_url, en_dest)
        try:
            text = en_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = en_bytes.decode("utf-8", errors="replace")

        linked = linked_article_paths(text, page_url=en_url)
        # Store article body only — smaller cache and cheaper AI translation.
        text = slim_article_document(text, lang="en")
        assets = _fetch_page_assets(sess, text, en_url, progress=progress)
        text = _rewrite_html_for_offline(text, en_dest, page_url=en_url)
        en_dest.write_text(text, encoding="utf-8")
        en_hash = content_hash(text)
        meta: dict = {
            "en_hash": en_hash,
            "assets": sorted(set(assets))[:200],
            "links": linked[:200],
        }

        if also_ja:
            ja_url = online_url_for_en_rel(en_rel, locale="ja")
            try:
                if progress:
                    progress(f"Downloading JA {ja_relative_from_en(en_rel)}…")
                ja_dest = ja_file_path(en_rel)
                ja_bytes = _download_bytes(sess, ja_url, ja_dest)
                try:
                    ja_text = ja_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    ja_text = ja_bytes.decode("utf-8", errors="replace")
                if len(ja_text) < 80 or "404" in ja_text[:200].lower():
                    try:
                        ja_dest.unlink()
                    except OSError:
                        pass
                else:
                    ja_text = slim_article_document(ja_text, lang="ja")
                    _fetch_page_assets(sess, ja_text, ja_url, progress=progress)
                    ja_text = _rewrite_html_for_offline(ja_text, ja_dest, page_url=ja_url)
                    ja_dest.write_text(ja_text, encoding="utf-8")
                    meta["ja_hash"] = content_hash(ja_text)
            except requests.HTTPError:
                pass
            except requests.RequestException:
                pass

        manifest = load_manifest()
        articles = dict(manifest.get("articles") or {})
        articles[en_rel] = meta
        manifest["articles"] = articles
        save_manifest(manifest)
        return meta
    finally:
        if own_session:
            sess.close()


def fetch_home_assets(*, progress: ProgressCallback | None = None) -> None:
    """Best-effort fetch of shared site chrome used by most pages."""
    sess = _session()
    try:
        candidates = [
            "https://kb.daisy.org/css/kb.css",
            "https://kb.daisy.org/css/prettify.css",
            "https://kb.daisy.org/css/sponsor.css",
            "https://kb.daisy.org/js/kb.js",
            "https://kb.daisy.org/js/prettify.js",
            "https://kb.daisy.org/graphics/daisy_high.jpg",
            "https://kb.daisy.org/favicon.ico",
        ]
        for url in candidates:
            dest = _local_path_for_asset_url(url)
            if dest is None or dest.is_file():
                continue
            try:
                if progress:
                    progress(f"Downloading {dest.name}…")
                _download_bytes(sess, url, dest)
            except requests.RequestException:
                continue
    finally:
        sess.close()


def ensure_article_cached(
    en_rel: str,
    *,
    also_ja: bool = True,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> bool:
    """
    Ensure shared site assets and the English article exist locally.

    If the article is already cached and ``force`` is False, only missing
    home assets are fetched. Returns True when the English file is present.
    """
    try:
        fetch_home_assets(progress=progress)
    except Exception:
        pass

    en_path = en_file_path(en_rel)
    if en_path.is_file() and not force:
        return True
    try:
        fetch_article(en_rel, also_ja=also_ja, progress=progress)
    except Exception:
        return en_path.is_file()
    return en_path.is_file()
