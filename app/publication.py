"""Detect whether a path is an eBraille or EPUB publication."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path


class PublicationKind(str, Enum):
    EBRAILLE = "ebraille"
    EPUB = "epub"
    UNSUPPORTED = "unsupported"


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


def classify_publication(path: Path) -> PublicationKind:
    """Classify a file or folder as eBraille, EPUB, or unsupported."""
    path = path.expanduser().resolve()
    suffix = path.suffix.lower()

    # Packaged extensions are definitive (file need not exist yet for kind).
    if suffix == ".ebrl":
        return PublicationKind.EBRAILLE
    if suffix == ".epub":
        return PublicationKind.EPUB
    if path.is_file() and suffix == ".zip":
        # Legacy packaged path: treat as eBraille (same as before)
        return PublicationKind.EBRAILLE

    if not path.is_dir():
        return PublicationKind.UNSUPPORTED

    opf = find_package_document(path)
    if opf is None:
        return PublicationKind.UNSUPPORTED
    if _opf_has_ebraille_format(opf):
        return PublicationKind.EBRAILLE
    return PublicationKind.EPUB


def is_checkable_path(path: Path) -> bool:
    """True when path exists and classifies as eBraille or EPUB."""
    path = path.expanduser().resolve()
    if not path.exists():
        return False
    return classify_publication(path) != PublicationKind.UNSUPPORTED
