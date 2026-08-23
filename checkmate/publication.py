"""Detect whether a path is an eBraille, EPUB, HTML, or (secret) DAISY publication."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse


class PublicationKind(str, Enum):
    EBRAILLE = "ebraille"
    EPUB = "epub"
    PDF = "pdf"
    HTML = "html"
    DAISY202 = "daisy202"
    UNSUPPORTED = "unsupported"


HTML_SUFFIXES = {".html", ".htm", ".xhtml"}


def find_ncc(folder: Path) -> Path | None:
    """Return ncc.html in a DAISY 2.02 folder, if present."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        return None
    for name in ("ncc.html", "NCC.HTML", "Ncc.html"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    try:
        for child in folder.iterdir():
            if child.is_file() and child.name.lower() == "ncc.html":
                return child
    except OSError:
        return None
    return None


def _opf_has_ebraille_format(opf_path: Path) -> bool:
    """True if package metadata dc:format contains case-sensitive 'eBraille'."""
    try:
        root = ET.parse(opf_path).getroot()
    except (OSError, ET.ParseError):
        try:
            return "eBraille" in opf_path.read_text(encoding="utf-8")
        except OSError:
            return False

    for elem in root.iter():
        tag = elem.tag if isinstance(elem.tag, str) else ""
        local = tag.rsplit("}", 1)[-1]
        if local != "format":
            continue
        value = "".join(elem.itertext()).strip()
        if "eBraille" in value:
            return True
    return False


def _rootfile_from_container(container_xml: Path) -> Path | None:
    """Resolve the package document path from META-INF/container.xml."""
    try:
        root = ET.parse(container_xml).getroot()
    except (OSError, ET.ParseError):
        return None

    for elem in root.iter():
        tag = elem.tag if isinstance(elem.tag, str) else ""
        local = tag.rsplit("}", 1)[-1]
        if local != "rootfile":
            continue
        full_path = elem.attrib.get("full-path") or elem.attrib.get("fullPath")
        if not full_path:
            continue
        candidate = (container_xml.parent.parent / full_path).resolve()
        if candidate.is_file():
            return candidate
    return None


def find_package_document(folder: Path) -> Path | None:
    """Locate the OPF for an exploded publication folder."""
    folder = folder.resolve()
    preferred = folder / "package.opf"
    if preferred.is_file():
        return preferred

    container = folder / "META-INF" / "container.xml"
    if container.is_file():
        return _rootfile_from_container(container)

    opfs = sorted(folder.glob("*.opf"))
    if len(opfs) == 1 and opfs[0].is_file():
        return opfs[0]
    return None


def is_html_url(value: str) -> bool:
    """True for an http(s) URL CheckMate can treat as an HTML target."""
    text = (value or "").strip().strip('"')
    if "://" not in text:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def folder_has_html(folder: Path, *, max_files: int = 400) -> bool:
    """True when *folder* contains at least one HTML file (bounded walk)."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        return False
    seen = 0
    try:
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            seen += 1
            if path.suffix.lower() in HTML_SUFFIXES:
                return True
            if seen >= max_files:
                break
    except OSError:
        return False
    return False


def classify_publication(path: Path) -> PublicationKind:
    """Classify a file or folder as eBraille, EPUB, PDF, HTML, DAISY 2.02, or unsupported."""
    path = path.expanduser().resolve()
    suffix = path.suffix.lower()

    # Packaged extensions are definitive (file need not exist yet for kind).
    if suffix == ".ebrl":
        return PublicationKind.EBRAILLE
    if suffix == ".epub":
        return PublicationKind.EPUB
    if suffix == ".pdf":
        return PublicationKind.PDF
    if suffix in HTML_SUFFIXES:
        return PublicationKind.HTML
    if path.is_file() and suffix == ".zip":
        # Legacy packaged path: treat as eBraille (same as before)
        return PublicationKind.EBRAILLE

    if not path.is_dir():
        return PublicationKind.UNSUPPORTED

    # Secret: DAISY 2.02 book folder (ncc.html). Checked before OPF so plain
    # DAISY titles are not misclassified as EPUB when an unrelated .opf exists.
    if find_ncc(path) is not None:
        return PublicationKind.DAISY202

    opf = find_package_document(path)
    if opf is not None:
        if _opf_has_ebraille_format(opf):
            return PublicationKind.EBRAILLE
        return PublicationKind.EPUB
    if folder_has_html(path):
        return PublicationKind.HTML
    return PublicationKind.UNSUPPORTED


def classify_target(raw: str) -> PublicationKind:
    """Classify a path-field value (filesystem path or http(s) URL)."""
    text = (raw or "").strip().strip('"')
    if not text:
        return PublicationKind.UNSUPPORTED
    if is_html_url(text):
        return PublicationKind.HTML
    try:
        return classify_publication(Path(text))
    except (OSError, ValueError):
        return PublicationKind.UNSUPPORTED


def is_checkable_path(path: Path) -> bool:
    """True when path exists and can be checked with an available engine."""
    path = path.expanduser().resolve()
    if not path.exists():
        return False
    kind = classify_publication(path)
    if kind == PublicationKind.UNSUPPORTED:
        return False
    if kind == PublicationKind.DAISY202:
        # Secret feature: only when a local Pipeline webservice is usable.
        from .pipeline_client import pipeline_usable

        return pipeline_usable()
    return True


def is_checkable_target(raw: str) -> bool:
    """True when a path-field value is an HTML URL or a checkable path."""
    text = (raw or "").strip().strip('"')
    if not text:
        return False
    if is_html_url(text):
        return True
    try:
        return is_checkable_path(Path(text))
    except (OSError, ValueError):
        return False
