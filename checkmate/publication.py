"""Detect whether a path is an eBraille, EPUB, or (secret) DAISY publication."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path


class PublicationKind(str, Enum):
    EBRAILLE = "ebraille"
    EPUB = "epub"
    PDF = "pdf"
    DAISY202 = "daisy202"
    UNSUPPORTED = "unsupported"


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


def classify_publication(path: Path) -> PublicationKind:
    """Classify a file or folder as eBraille, EPUB, PDF, DAISY 2.02, or unsupported."""
    path = path.expanduser().resolve()
    suffix = path.suffix.lower()

    # Packaged extensions are definitive (file need not exist yet for kind).
    if suffix == ".ebrl":
        return PublicationKind.EBRAILLE
    if suffix == ".epub":
        return PublicationKind.EPUB
    if suffix == ".pdf":
        return PublicationKind.PDF
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
    if opf is None:
        return PublicationKind.UNSUPPORTED
    if _opf_has_ebraille_format(opf):
        return PublicationKind.EBRAILLE
    return PublicationKind.EPUB


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


def same_publication_path(left: Path, right: Path) -> bool:
    """True when both paths refer to the same filesystem location."""
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return (
            left.expanduser().absolute() == right.expanduser().absolute()
        )


def copy_publication(source: Path, destination: Path) -> Path:
    """
    Copy a packaged publication file or exploded folder to *destination*.

    The original is left unchanged. Returns *destination*. Raises
    ``FileNotFoundError``, ``FileExistsError``, or ``ValueError`` on failure.
    """
    source = Path(source).expanduser()
    destination = Path(destination).expanduser()
    if not source.exists():
        raise FileNotFoundError(str(source))
    if same_publication_path(source, destination):
        raise ValueError("source and destination are the same path")

    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    if source.is_dir():
        if destination.exists():
            raise FileExistsError(str(destination))
        shutil.copytree(source, destination)
        return destination

    raise ValueError(f"not a file or folder: {source}")
