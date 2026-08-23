"""HTML (file, folder, or URL) document-image backend — read-only."""

from __future__ import annotations

import base64
import html.parser
import logging
import re
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from checkmate.doc_images.api import DocumentImageBackend, load_image_result
from checkmate.html_crawl import (
    DEFAULT_CRAWL_CAP,
    LocalHtmlServer,
    fetch_html,
    pages_for_html_check,
    prepare_html_target,
)
from checkmate.publication import HTML_SUFFIXES, is_html_url

logger = logging.getLogger("fido")

_FETCH_TIMEOUT_S = 20.0
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_SKIP_SRC_PREFIXES = ("blob:", "javascript:", "mailto:")
_DATA_URI_RE = re.compile(
    r"^data:([^;,]+)?(;base64)?,(.*)$", re.IGNORECASE | re.DOTALL
)
_SKIP_TEXT_TAGS = frozenset({"script", "style", "noscript"})
_LAZY_SRC_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")


def _first_srcset_url(srcset: str) -> str:
    first = (srcset or "").split(",", 1)[0].strip()
    if not first:
        return ""
    return first.split()[0] if first.split() else ""


def _src_from_attrs(ad: dict[str, str], picture_srcset: str = "") -> str:
    for key in _LAZY_SRC_ATTRS:
        val = (ad.get(key) or "").strip()
        if val:
            return val
    srcset = _first_srcset_url(ad.get("srcset") or ad.get("data-srcset") or "")
    if srcset:
        return srcset
    return _first_srcset_url(picture_srcset)


class _ImgParser(html.parser.HTMLParser):
    """Collect img / input[type=image] / svg[role=img] from static HTML."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.records: list[dict[str, Any]] = []
        self._tokens: list[tuple[str, Any]] = []
        self._svg_depth = 0
        self._svg_attrs: dict[str, str] = {}
        self._skip_text_depth = 0
        self._figure_stack: list[dict[str, Any]] = []
        self._figcaption_depth = 0
        self._picture_srcset = ""
        self._picture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k.lower(): (v or "") for k, v in attrs}
        lower = tag.lower()
        if lower in _SKIP_TEXT_TAGS:
            self._skip_text_depth += 1
            return
        if lower == "figure":
            self._figure_stack.append({"indices": [], "caption": []})
        elif lower == "figcaption" and self._figure_stack:
            self._figcaption_depth += 1
        elif lower == "picture":
            self._picture_depth += 1
            if self._picture_depth == 1:
                self._picture_srcset = ""
        elif lower == "source" and self._picture_depth and not self._picture_srcset:
            self._picture_srcset = ad.get("srcset") or ad.get("src") or ""
        if self._skip_text_depth:
            return
        if lower == "img":
            self._add_image("img", ad, src=_src_from_attrs(ad, self._picture_srcset))
        elif lower == "input" and ad.get("type", "").lower() == "image":
            self._add_image("input", ad, src=_src_from_attrs(ad, ""))
        elif lower == "svg":
            self._svg_depth += 1
            if self._svg_depth == 1:
                self._svg_attrs = ad
                role = ad.get("role", "").lower()
                if role == "img":
                    self._add_image("svg", ad, src="")
        elif ad.get("role", "").lower() == "img" and lower not in {"img", "svg"}:
            self._add_image(
                "role-img",
                ad,
                src=_src_from_attrs(ad, "") or ad.get("href", ""),
            )

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in _SKIP_TEXT_TAGS and self._skip_text_depth:
            self._skip_text_depth -= 1
            return
        if lower == "figcaption" and self._figcaption_depth:
            self._figcaption_depth -= 1
        elif lower == "figure" and self._figure_stack:
            frame = self._figure_stack.pop()
            caption = re.sub(r"\s+", " ", " ".join(frame["caption"])).strip()
            for idx in frame["indices"]:
                if 0 <= idx < len(self.records) and caption:
                    self.records[idx]["figcaption"] = caption
        elif lower == "picture" and self._picture_depth:
            self._picture_depth -= 1
            if self._picture_depth == 0:
                self._picture_srcset = ""
        elif lower == "svg" and self._svg_depth:
            self._svg_depth -= 1
            if self._svg_depth == 0:
                role = self._svg_attrs.get("role", "").lower()
                if role != "img":
                    # Bare <svg> still counts as an image for the sniff test.
                    self._add_image("svg", self._svg_attrs, src="")
                self._svg_attrs = {}

    def handle_data(self, data: str) -> None:
        if self._skip_text_depth:
            return
        text = re.sub(r"\s+", " ", data or "")
        if self._figcaption_depth and self._figure_stack:
            self._figure_stack[-1]["caption"].append(text)
            return
        if text.strip():
            self._tokens.append(("text", text.strip()))

    def close(self) -> None:
        super().close()
        self._attach_nearby_text()

    def _add_image(self, kind: str, ad: dict[str, str], *, src: str) -> None:
        rec = self._record(kind, ad, src=src)
        self.records.append(rec)
        idx = len(self.records) - 1
        self._tokens.append(("img", idx))
        if self._figure_stack:
            self._figure_stack[-1]["indices"].append(idx)

    def _attach_nearby_text(self) -> None:
        for i, (kind, val) in enumerate(self._tokens):
            if kind != "img":
                continue
            rec = self.records[val]
            before: list[str] = []
            after: list[str] = []
            j = i - 1
            while j >= 0 and sum(len(x) for x in before) < 400:
                token_kind, token_val = self._tokens[j]
                if token_kind == "text":
                    before.append(str(token_val))
                elif token_kind == "img":
                    break
                j -= 1
            j = i + 1
            while j < len(self._tokens) and sum(len(x) for x in after) < 400:
                token_kind, token_val = self._tokens[j]
                if token_kind == "text":
                    after.append(str(token_val))
                elif token_kind == "img":
                    break
                j += 1
            bits = [*reversed(before), *after]
            caption = str(rec.get("figcaption") or "").strip()
            if caption:
                bits.insert(0, caption)
            rec["nearbyText"] = re.sub(r"\s+", " ", " ".join(bits)).strip()[:800]

    def _record(self, kind: str, ad: dict[str, str], *, src: str) -> dict[str, Any]:
        alt_present = "alt" in ad
        alt = ad.get("alt") if alt_present else None
        role = ad.get("role", "")
        aria_hidden = ad.get("aria-hidden", "").lower() == "true"
        decorative = (
            aria_hidden
            or role.lower() in {"presentation", "none"}
            or (kind in {"img", "input"} and alt_present and alt == "")
        )
        abs_src = urljoin(self.page_url, src) if src else ""
        return {
            "kind": kind,
            "src": abs_src,
            "alt": alt,
            "altPresent": alt_present,
            "role": role,
            "ariaHidden": aria_hidden,
            "ariaLabel": ad.get("aria-label", ""),
            "figcaption": "",
            "nearbyText": "",
            "selector": "",
            "pageUrl": self.page_url,
            "width": 0,
            "height": 0,
            "decorative": decorative,
        }


def collect_image_records_from_html(html_text: str, page_url: str) -> list[dict[str, Any]]:
    """Parse static HTML for img / input[type=image] / svg records."""
    parser = _ImgParser(page_url)
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception:
        logger.debug("HTML image parse failed for %s", page_url, exc_info=True)
        return []
    return parser.records


def _ext_from_mime(mime: str) -> str:
    key = (mime or "").split(";")[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
        "image/avif": ".avif",
    }.get(key, ".bin")


def _decode_data_uri(src: str, dest_dir: Path, index: int) -> str:
    match = _DATA_URI_RE.match(src.strip())
    if not match:
        return ""
    mime = match.group(1) or "application/octet-stream"
    is_b64 = bool(match.group(2))
    payload = match.group(3) or ""
    try:
        raw = base64.b64decode(payload) if is_b64 else payload.encode("utf-8")
    except Exception:
        return ""
    if len(raw) > _MAX_IMAGE_BYTES:
        return ""
    ext = _ext_from_mime(mime)
    path = dest_dir / f"html_img_{index:04d}{ext}"
    path.write_bytes(raw)
    return str(path)


def _fetch_http_image(url: str, dest_dir: Path, index: int) -> str:
    request = Request(
        url,
        headers={"User-Agent": "CheckMate/1.0 (HTML image export)"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=_FETCH_TIMEOUT_S) as response:
            mime = response.headers.get_content_type() or ""
            raw = response.read(_MAX_IMAGE_BYTES + 1)
    except (OSError, URLError, HTTPError, TimeoutError, ValueError):
        return ""
    if len(raw) > _MAX_IMAGE_BYTES:
        return ""
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif"}:
        suffix = _ext_from_mime(mime)
    path = dest_dir / f"html_img_{index:04d}{suffix or '.bin'}"
    path.write_bytes(raw)
    return str(path)


def materialize_image(
    rec: dict[str, Any],
    dest_dir: Path,
    index: int,
    *,
    local_root: Path | None = None,
) -> tuple[str, str]:
    """Write image bytes to *dest_dir*. Returns (path, status_note)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = str(rec.get("src") or "").strip()
    page_url = str(rec.get("pageUrl") or "").strip()
    if src and not urlparse(src).scheme and page_url:
        src = urljoin(page_url, src)
    if not src:
        markup = str(rec.get("svgMarkup") or "").strip()
        if markup:
            path = dest_dir / f"html_img_{index:04d}.svg"
            path.write_text(markup, encoding="utf-8")
            return str(path), ""
        if rec.get("kind") in {"svg", "role-img"}:
            return "", "inline SVG (no src)"
        return "", "missing src"
    lower = src.lower()
    if lower.startswith(_SKIP_SRC_PREFIXES):
        return "", f"skipped {src.split(':', 1)[0]} URL"
    if lower.startswith("data:"):
        path = _decode_data_uri(src, dest_dir, index)
        return (path, "") if path else ("", "could not decode data URI")
    parsed = urlparse(src)
    if parsed.scheme in {"http", "https"}:
        if local_root is not None and parsed.hostname in {"127.0.0.1", "localhost"}:
            rel = parsed.path.lstrip("/")
            candidate = (local_root / rel).resolve() if rel else local_root
            try:
                candidate.relative_to(local_root.resolve())
            except ValueError:
                candidate = None
            if candidate is not None and candidate.is_file():
                ext = candidate.suffix or ".bin"
                dest = dest_dir / f"html_img_{index:04d}{ext}"
                dest.write_bytes(candidate.read_bytes()[: _MAX_IMAGE_BYTES + 1])
                if dest.stat().st_size > _MAX_IMAGE_BYTES:
                    dest.unlink(missing_ok=True)
                    return "", "image too large"
                return str(dest), ""
        path = _fetch_http_image(src, dest_dir, index)
        return (path, "") if path else ("", "could not fetch image")
    # file path
    try:
        file_path = Path(src)
        if file_path.is_file():
            ext = file_path.suffix or ".bin"
            dest = dest_dir / f"html_img_{index:04d}{ext}"
            dest.write_bytes(file_path.read_bytes()[: _MAX_IMAGE_BYTES + 1])
            if dest.stat().st_size > _MAX_IMAGE_BYTES:
                dest.unlink(missing_ok=True)
                return "", "image too large"
            return str(dest), ""
    except OSError:
        pass
    return "", "could not read image"


def _is_decorative(rec: dict[str, Any]) -> bool:
    if rec.get("decorative"):
        return True
    role = str(rec.get("role") or "").lower()
    if role in {"presentation", "none"}:
        return True
    if rec.get("ariaHidden") in {True, "true"}:
        return True
    if rec.get("altPresent") and rec.get("alt") == "":
        return True
    return False


class HtmlOnDiscBackend(DocumentImageBackend):
    """Read-only images from crawled HTML pages (file, folder, or URL)."""

    def __init__(self, dialog: Any = None, temp_dir: str | None = None):
        super().__init__(dialog, temp_dir=temp_dir)
        self._source = ""
        self._records: list[dict[str, Any]] = []
        self._files: list[str] = []
        self._notes: list[str] = []
        self._server: LocalHtmlServer | None = None
        self._local_root: Path | None = None

    def get_document_display_name(self) -> str:
        text = self._source
        if is_html_url(text):
            try:
                return urlparse(text).netloc or text
            except ValueError:
                return text
        path = Path(text)
        return path.name or str(path)

    def open_document(self, source: Optional[str]) -> bool:
        text = (source or "").strip().strip('"')
        if not text:
            return False
        self._source = text
        self.close()
        records = self._records_from_session(text)
        pages: list[str] = []
        try:
            if records is None:
                start_url, local_root, server = prepare_html_target(text)
                self._server = server
                self._local_root = local_root
                from checkmate.settings import html_follow_links

                follow = html_follow_links()
                pages = pages_for_html_check(
                    start_url,
                    follow_links=follow,
                    cap=DEFAULT_CRAWL_CAP,
                    local_root=local_root,
                )
                records = []
                for page_url in pages:
                    try:
                        body = fetch_html(page_url)
                    except (OSError, URLError, HTTPError, TimeoutError, ValueError):
                        continue
                    records.extend(collect_image_records_from_html(body, page_url))
            else:
                self._local_root = None
                if not is_html_url(text):
                    path = Path(text)
                    if path.exists():
                        try:
                            start_url, local_root, server = prepare_html_target(text)
                            self._server = server
                            self._local_root = local_root
                        except OSError:
                            pass
        except (OSError, FileNotFoundError):
            logger.debug("Could not open HTML target %s", text, exc_info=True)
            self.close()
            return False

        dest = Path(self._resolve_temp_dir()) / "html_images"
        dest.mkdir(parents=True, exist_ok=True)
        self._records = []
        self._files = []
        self._notes = []
        for i, rec in enumerate(records):
            src = str(rec.get("src") or "")
            if src.lower().startswith("blob:"):
                continue
            path, note = materialize_image(
                rec, dest, i, local_root=self._local_root
            )
            self._records.append(rec)
            self._files.append(path)
            self._notes.append(note)
        return True

    def _records_from_session(self, source: str) -> list[dict[str, Any]] | None:
        try:
            from checkmate.html_check import last_html_session
        except Exception:
            return None
        session = last_html_session()
        if session is None:
            return None
        if session.target.strip().strip('"') != source:
            return None
        if session.images:
            return list(session.images)
        return None

    def save_document(self) -> bool:
        return True

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
                logger.debug("HTML server stop failed", exc_info=True)
            self._server = None
        self._local_root = None

    def get_image_count(self) -> int:
        return len(self._records)

    def load_image(self, index: int) -> Optional[dict]:
        if index < 0 or index >= len(self._records):
            return None
        rec = self._records[index]
        path = self._files[index] if index < len(self._files) else ""
        alt = rec.get("alt")
        if alt is None:
            alt = rec.get("ariaLabel") or ""
        return load_image_result(
            path,
            str(alt or ""),
            _is_decorative(rec),
        )

    def set_alt_text(self, index: int, text: str) -> bool:
        return False

    def set_decorative(self, index: int, is_decorative: bool) -> bool:
        return False

    def get_context(self, index: int) -> str:
        if index < 0 or index >= len(self._records):
            return ""
        rec = self._records[index]
        bits: list[str] = []
        page = str(rec.get("pageUrl") or "").strip()
        src = str(rec.get("src") or "").strip()
        name = ""
        if src and not src.lower().startswith("data:"):
            name = Path(urlparse(src).path).name or src
        if page:
            bits.append(f"Page: {page}")
        if name:
            bits.append(f"File: {name}")
        selector = str(rec.get("selector") or "").strip()
        if selector:
            bits.append(f"Selector: {selector}")
        caption = str(rec.get("figcaption") or "").strip()
        if caption:
            bits.append(caption)
        nearby = str(rec.get("nearbyText") or "").strip()
        if nearby:
            bits.append(nearby)
        note = self._notes[index] if index < len(self._notes) else ""
        if note and not self._files[index]:
            bits.append(f"Status: {note}")
        return "\n".join(bits)

    def get_alt_text(self, index: int) -> str:
        result = self.load_image(index)
        if result is None:
            return ""
        return str(result.get("alt_text") or "").strip()


def path_is_html_source(path: str | Path) -> bool:
    """True for an HTML file, HTML folder, or http(s) URL."""
    text = str(path).strip().strip('"')
    if not text:
        return False
    if is_html_url(text):
        return True
    p = Path(text)
    try:
        if p.is_file() and p.suffix.lower() in HTML_SUFFIXES:
            return True
        if p.is_dir():
            from checkmate.publication import PublicationKind, classify_publication

            return classify_publication(p) == PublicationKind.HTML
    except OSError:
        return False
    return False
