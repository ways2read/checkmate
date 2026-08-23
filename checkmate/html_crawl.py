"""Bounded same-site HTML crawl and localhost static server for local files."""

from __future__ import annotations

import html.parser
import ssl
import threading
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

from .publication import HTML_SUFFIXES, is_html_url

ProgressCallback = Callable[[str], None]

DEFAULT_CRAWL_CAP = 25
_FETCH_TIMEOUT_S = 20.0
_SKIP_SCHEMES = {"mailto", "javascript", "data", "blob", "tel", "sms"}
_SKIP_SUFFIXES = {
    ".pdf",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".xml",
    ".mp3",
    ".mp4",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
}


class _HrefParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value.strip())


def normalize_page_url(url: str) -> str:
    """Strip fragments; keep query. Empty on failure."""
    text = (url or "").strip()
    if not text:
        return ""
    try:
        cleaned, _frag = urldefrag(text)
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"}:
            return ""
        return parsed.geturl()
    except ValueError:
        return ""


def https_to_http_url(url: str) -> str:
    """Same URL with http://, or empty when the input is not https."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        return ""
    return parsed._replace(scheme="http").geturl()


def is_tls_handshake_failure(exc: BaseException | None) -> bool:
    """True when *exc* is a failed TLS handshake (not an HTTP-level error)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        reason = getattr(current, "reason", None)
        if isinstance(reason, ssl.SSLError):
            return True
        text = str(current).lower()
        if any(
            needle in text
            for needle in (
                "ssl:",
                "sslv",
                "tlsv",
                "certificate verify failed",
                "ssl syscall",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _probe_url(url: str, *, timeout: float = 12.0) -> None:
    """Open *url* and read a few bytes. HTTPError means TLS/TCP succeeded."""
    request = Request(
        url,
        headers={"User-Agent": "CheckMate/1.0 (HTML checker)"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        response.read(256)


def prefer_working_page_url(url: str) -> tuple[str, str]:
    """If HTTPS cannot complete a TLS handshake, use HTTP on the same host.

    Returns ``(url_to_open, note)``. *note* is empty when the URL is unchanged.
    Localhost and plain HTTP are left as-is.
    """
    start = normalize_page_url(url) or (url or "").strip()
    parsed = urlparse(start)
    if parsed.scheme != "https" or not parsed.hostname:
        return start, ""
    host = (parsed.hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return start, ""
    try:
        _probe_url(start)
        return start, ""
    except HTTPError:
        # TLS worked; the server returned an HTTP status.
        return start, ""
    except (OSError, URLError, TimeoutError, ValueError, ssl.SSLError) as exc:
        if not is_tls_handshake_failure(exc):
            return start, ""
    http_url = https_to_http_url(start)
    if not http_url:
        return start, ""
    try:
        _probe_url(http_url)
    except HTTPError:
        pass
    except (OSError, URLError, TimeoutError, ValueError, ssl.SSLError):
        return start, ""
    note = (
        f"HTTPS is not available on this host (TLS handshake failed). "
        f"Checked {http_url} instead of {start}."
    )
    return http_url, note


def same_origin(left: str, right: str) -> bool:
    try:
        a = urlparse(left)
        b = urlparse(right)
    except ValueError:
        return False
    return (a.scheme, a.hostname, a.port or _default_port(a.scheme)) == (
        b.scheme,
        b.hostname,
        b.port or _default_port(b.scheme),
    )


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def should_follow_href(base_url: str, href: str, *, local_root: Path | None = None) -> str | None:
    """Return an absolute same-origin page URL to crawl, or None."""
    raw = (href or "").strip()
    if not raw or raw.startswith("#"):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme.lower() in _SKIP_SCHEMES:
        return None
    joined = urljoin(base_url, raw)
    target = normalize_page_url(joined)
    if not target or not same_origin(base_url, target):
        return None
    path = urlparse(target).path.lower()
    for suffix in _SKIP_SUFFIXES:
        if path.endswith(suffix):
            return None
    if local_root is not None:
        local = url_to_local_path(target, local_root)
        if local is None:
            return None
    return target


def url_to_local_path(page_url: str, root: Path) -> Path | None:
    """Map a localhost crawl URL back onto *root*; None if it would escape."""
    try:
        parsed = urlparse(page_url)
        rel = parsed.path.lstrip("/")
        candidate = (root / rel).resolve() if rel else root.resolve()
        candidate.relative_to(root.resolve())
        return candidate
    except (ValueError, OSError):
        return None


def extract_hrefs(html_text: str) -> list[str]:
    parser = _HrefParser()
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception:
        return []
    return parser.hrefs


def fetch_html(url: str, *, timeout: float = _FETCH_TIMEOUT_S) -> str:
    request = Request(
        url,
        headers={"User-Agent": "CheckMate/1.0 (HTML checker)"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = "utf-8"
        ctype = response.headers.get_content_charset()
        if ctype:
            charset = ctype
        return raw.decode(charset, errors="replace")


class LocalHtmlServer:
    """Serve a directory on 127.0.0.1 for file://-safe axe/vnu scans."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> str:
        root = self.root

        class _Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)

            def log_message(self, _format: str, *_args) -> None:
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="checkmate-html-http"
        )
        self._thread.start()
        return self.origin

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None

    def __enter__(self) -> LocalHtmlServer:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


def first_html_file(folder: Path) -> Path | None:
    """Prefer index.* at the folder root, then any HTML file in the tree."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        return None
    for name in ("index.html", "index.htm", "index.xhtml"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    try:
        for child in sorted(folder.iterdir()):
            if child.is_file() and child.suffix.lower() in HTML_SUFFIXES:
                return child
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix.lower() in HTML_SUFFIXES:
                return path
    except OSError:
        return None
    return None


def local_start_url(path: Path, origin: str) -> str:
    """URL for a local HTML file or folder under a running LocalHtmlServer."""
    path = path.expanduser().resolve()
    root = path.parent if path.is_file() else path
    target = path if path.is_file() else first_html_file(path)
    if target is None:
        return f"{origin}/"
    try:
        rel = target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = target.name
    return f"{origin}/{rel}" if rel else f"{origin}/"


def prepare_html_target(target: str) -> tuple[str, Path | None, LocalHtmlServer | None]:
    """Return (start_url, local_root_or_none, server_or_none).

    Caller must ``stop()`` the server when not None.
    """
    text = (target or "").strip().strip('"')
    if is_html_url(text):
        return normalize_page_url(text) or text, None, None
    path = Path(text).expanduser().resolve()
    if path.is_file():
        root = path.parent
        server = LocalHtmlServer(root)
        origin = server.start()
        return local_start_url(path, origin), root, server
    if path.is_dir():
        server = LocalHtmlServer(path)
        origin = server.start()
        return local_start_url(path, origin), path, server
    raise FileNotFoundError(text)


def pages_for_html_check(
    start_url: str,
    *,
    follow_links: bool,
    cap: int = DEFAULT_CRAWL_CAP,
    local_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> list[str]:
    """Start page only, or a bounded same-origin crawl when *follow_links*."""
    start = normalize_page_url(start_url) or start_url
    if not follow_links:
        return [start]
    pages = crawl_html_pages(
        start, cap=cap, local_root=local_root, progress=progress
    )
    return pages or [start]


def crawl_html_pages(
    start_url: str,
    *,
    cap: int = DEFAULT_CRAWL_CAP,
    local_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> list[str]:
    """BFS same-origin crawl. Returns unique page URLs, start first."""
    start = normalize_page_url(start_url) or start_url
    cap = max(1, int(cap))
    seen: set[str] = set()
    ordered: list[str] = []
    queue: deque[str] = deque([start])
    while queue and len(ordered) < cap:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
        if progress:
            progress(f"Finding linked pages… {len(ordered)}")
        try:
            body = fetch_html(url)
        except (OSError, URLError, HTTPError, TimeoutError, ValueError):
            continue
        for href in extract_hrefs(body):
            nxt = should_follow_href(url, href, local_root=local_root)
            if nxt and nxt not in seen:
                queue.append(nxt)
    return ordered
