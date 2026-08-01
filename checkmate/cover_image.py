"""Extract a cover / first-page preview for HTML reports."""

from __future__ import annotations

import base64
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from .publication import find_package_document

_MAX_EMBED_BYTES = 2_500_000  # keep HTML reports reasonable

_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


@dataclass(frozen=True)
class CoverImage:
    data: bytes
    media_type: str
    alt: str = "Cover"

    def data_uri(self) -> str:
        b64 = base64.standard_b64encode(self.data).decode("ascii")
        return f"data:{self.media_type};base64,{b64}"


def _local_name(tag: str | None) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _mime_for(path_or_name: str, declared: str | None = None) -> str:
    if declared and declared.strip() and declared.strip() != "image/*":
        return declared.strip()
    suffix = Path(path_or_name).suffix.lower()
    return _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


def _parse_opf_cover_href(opf_bytes: bytes) -> tuple[str, str | None] | None:
    """Return (href, media-type) for the package cover image, if any."""
    try:
        root = ET.fromstring(opf_bytes)
    except ET.ParseError:
        return None

    items: dict[str, tuple[str, str | None]] = {}
    cover_id: str | None = None

    for elem in root.iter():
        local = _local_name(elem.tag)
        if local == "item":
            item_id = elem.attrib.get("id")
            href = elem.attrib.get("href")
            if not item_id or not href:
                continue
            media = elem.attrib.get("media-type")
            props = (elem.attrib.get("properties") or "").split()
            items[item_id] = (href, media)
            if "cover-image" in props:
                return href, media
        elif local == "meta":
            name = (elem.attrib.get("name") or "").strip().lower()
            if name == "cover":
                cover_id = (elem.attrib.get("content") or "").strip() or None

    if cover_id and cover_id in items:
        return items[cover_id]
    return None


def _read_zip_member(zf: zipfile.ZipFile, href: str, opf_inner: str) -> bytes | None:
    # Resolve href relative to the OPF directory inside the zip.
    opf_dir = str(Path(opf_inner).parent).replace("\\", "/")
    if opf_dir in (".", ""):
        candidate = href.lstrip("/")
    else:
        candidate = f"{opf_dir}/{href.lstrip('/')}"
    # Normalize .. segments lightly
    parts: list[str] = []
    for part in candidate.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    name = "/".join(parts)
    # Zip namelist may use different separators / case
    names = {n.replace("\\", "/"): n for n in zf.namelist()}
    if name in names:
        return zf.read(names[name])
    lower = {k.lower(): v for k, v in names.items()}
    if name.lower() in lower:
        return zf.read(lower[name.lower()])
    return None


def _cover_from_package_zip(path: Path) -> CoverImage | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = {n.replace("\\", "/"): n for n in zf.namelist()}
            container_key = None
            for key in ("META-INF/container.xml", "meta-inf/container.xml"):
                if key in names:
                    container_key = names[key]
                    break
            if container_key is None:
                lower = {k.lower(): v for k, v in names.items()}
                container_key = lower.get("meta-inf/container.xml")
            if container_key is None:
                return None
            container = ET.fromstring(zf.read(container_key))
            opf_inner = None
            for elem in container.iter():
                if _local_name(elem.tag) != "rootfile":
                    continue
                opf_inner = elem.attrib.get("full-path") or elem.attrib.get("fullPath")
                if opf_inner:
                    break
            if not opf_inner:
                return None
            opf_inner = opf_inner.replace("\\", "/")
            if opf_inner not in names:
                lower = {k.lower(): v for k, v in names.items()}
                real = lower.get(opf_inner.lower())
                if not real:
                    return None
                opf_inner = real.replace("\\", "/")
            cover = _parse_opf_cover_href(zf.read(names.get(opf_inner, opf_inner)))
            if cover is None:
                return None
            href, media = cover
            data = _read_zip_member(zf, href, opf_inner)
            if not data or len(data) > _MAX_EMBED_BYTES:
                return None
            return CoverImage(data=data, media_type=_mime_for(href, media))
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError):
        return None


def _cover_from_exploded(folder: Path) -> CoverImage | None:
    opf = find_package_document(folder)
    if opf is None:
        return None
    try:
        cover = _parse_opf_cover_href(opf.read_bytes())
    except OSError:
        return None
    if cover is None:
        return None
    href, media = cover
    image_path = (opf.parent / href).resolve()
    try:
        # Stay inside the publication folder.
        image_path.relative_to(folder.resolve())
    except ValueError:
        return None
    if not image_path.is_file():
        return None
    try:
        data = image_path.read_bytes()
    except OSError:
        return None
    if not data or len(data) > _MAX_EMBED_BYTES:
        return None
    return CoverImage(data=data, media_type=_mime_for(str(image_path), media))


def _cover_from_pdf(path: Path) -> CoverImage | None:
    """Render the first page of a PDF with PyMuPDF (fitz)."""
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(path)
        try:
            if doc.page_count < 1:
                return None
            page = doc.load_page(0)
            # ~108 dpi-ish preview (72 * 1.5) — enough for the report sidebar
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            try:
                data = pix.tobytes("jpeg")
            finally:
                pix = None
        finally:
            doc.close()
    except Exception:  # noqa: BLE001 — cover is optional polish
        return None
    if not data or len(data) > _MAX_EMBED_BYTES:
        return None
    return CoverImage(data=data, media_type="image/jpeg", alt="First page")


def extract_cover_image(path: Path | str | None) -> CoverImage | None:
    """Best-effort cover for a publication path (EPUB/eBraille folder or PDF)."""
    if not path:
        return None
    target = Path(path).expanduser()
    try:
        target = target.resolve()
    except OSError:
        return None
    if not target.exists():
        return None

    suffix = target.suffix.lower()
    if target.is_file() and suffix == ".pdf":
        return _cover_from_pdf(target)
    if target.is_file() and suffix in {".epub", ".ebrl", ".zip"}:
        return _cover_from_package_zip(target)
    if target.is_dir():
        return _cover_from_exploded(target)
    return None
