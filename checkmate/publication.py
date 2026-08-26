"""Detect whether a path is an eBraille, EPUB, HTML, SVG, CSS, XML, PDF, or DAISY publication."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

# Peek this many bytes when classifying XML/OPF (avoid loading huge files / DTDs).
_PEEK_BYTES = 16384

DTBOOK_NS_HINTS = (
    "http://www.daisy.org/z3986/2005/dtbook",
    "application/x-dtbook+xml",
)


class PublicationKind(str, Enum):
    EBRAILLE = "ebraille"
    EPUB = "epub"
    PDF = "pdf"
    HTML = "html"
    SVG = "svg"
    CSS = "css"
    MATHML = "mathml"
    XML = "xml"
    DAISY202 = "daisy202"
    DAISY3 = "daisy3"
    DTBOOK = "dtbook"
    NIMAS = "nimas"
    UNSUPPORTED = "unsupported"


HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
SVG_SUFFIXES = {".svg"}
CSS_SUFFIXES = {".css"}
MATHML_SUFFIXES = {".mml"}
DTBOOK_SUFFIXES = {".xml"}
OPF_SUFFIXES = {".opf"}

PIPELINE_KINDS = frozenset(
    {
        PublicationKind.DAISY202,
        PublicationKind.DAISY3,
        PublicationKind.DTBOOK,
        PublicationKind.NIMAS,
    }
)
VNU_DOCUMENT_KINDS = frozenset(
    {PublicationKind.SVG, PublicationKind.CSS, PublicationKind.MATHML, PublicationKind.XML}
)


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


def _peek_text(path: Path, limit: int = _PEEK_BYTES) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    return data.decode("utf-8", errors="ignore")


def is_dtbook_xml(path: Path) -> bool:
    """True when *path* looks like a DTBook XML document (no DTD fetch)."""
    if not path.is_file():
        return False
    text = _peek_text(path).lower()
    if not text:
        return False
    if "<dtbook" in text:
        return True
    return any(hint in text for hint in DTBOOK_NS_HINTS)


def _opf_pipeline_kind(opf_path: Path) -> PublicationKind | None:
    """Return DAISY 3 or NIMAS when the OPF looks like a DAISY fileset, else None."""
    text = _peek_text(opf_path)
    if not text:
        return None
    lower = text.lower()
    has_dtbook = any(hint in lower for hint in DTBOOK_NS_HINTS)
    has_ncx = "application/x-dtbncx+xml" in lower
    has_smil = "application/smil" in lower
    has_z3986 = "z39.86" in lower or "z3986" in lower
    has_nimas = "nimas" in lower
    has_dtb_meta = "dtb:" in lower or "xmlns:dtb" in lower
    if not (has_dtbook or has_ncx or has_z3986 or has_nimas or (has_smil and has_dtb_meta)):
        return None
    # NIMAS is a DAISY 3 text fileset: DTBook + OPF, typically no SMIL/audio.
    if has_nimas and not has_smil:
        return PublicationKind.NIMAS
    if has_smil or has_ncx or has_z3986:
        return PublicationKind.DAISY3
    if has_dtbook:
        return PublicationKind.NIMAS
    return PublicationKind.DAISY3


def find_opf_for_target(path: Path) -> Path | None:
    """OPF file for a NIMAS/DAISY 3 file or folder."""
    path = path.expanduser().resolve()
    if path.is_file() and path.suffix.lower() in OPF_SUFFIXES:
        return path
    if path.is_dir():
        return find_package_document(path)
    return None


def find_dtbook_for_target(path: Path) -> Path | None:
    """DTBook XML for a standalone file or inside a fileset folder."""
    path = path.expanduser().resolve()
    if path.is_file():
        return path if is_dtbook_xml(path) else None
    if not path.is_dir():
        return None
    opf = find_package_document(path)
    if opf is not None:
        found = _dtbook_from_opf(opf)
        if found is not None:
            return found
    try:
        xmls = sorted(path.glob("*.xml"))
    except OSError:
        xmls = []
    for candidate in xmls:
        if is_dtbook_xml(candidate):
            return candidate
    return None


def _dtbook_from_opf(opf_path: Path) -> Path | None:
    """First DTBook XML referenced by a package document."""
    folder = opf_path.parent
    try:
        root = ET.parse(opf_path).getroot()
    except (OSError, ET.ParseError):
        return None
    hrefs: list[str] = []
    for elem in root.iter():
        tag = elem.tag if isinstance(elem.tag, str) else ""
        local = tag.rsplit("}", 1)[-1]
        if local != "item":
            continue
        href = (elem.attrib.get("href") or "").strip()
        if not href:
            continue
        media = (elem.attrib.get("media-type") or "").lower()
        if "dtbook" in media or href.lower().endswith(".xml"):
            hrefs.append(href)
    for href in hrefs:
        candidate = (folder / href).resolve()
        if candidate.is_file() and is_dtbook_xml(candidate):
            return candidate
    return None


def is_html_url(value: str) -> bool:
    """True for an http(s) URL CheckMate can treat as a web target."""
    text = (value or "").strip().strip('"')
    if "://" not in text:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _kind_from_url_path(url: str) -> PublicationKind:
    """Classify an http(s) URL by path suffix; default HTML."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return PublicationKind.HTML
    suffix = Path(parsed.path or "").suffix.lower()
    if suffix in SVG_SUFFIXES:
        return PublicationKind.SVG
    if suffix in CSS_SUFFIXES:
        return PublicationKind.CSS
    if suffix in MATHML_SUFFIXES:
        return PublicationKind.MATHML
    if suffix in DTBOOK_SUFFIXES:
        return PublicationKind.XML
    return PublicationKind.HTML


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
    """Classify a file or folder as a supported publication kind, or unsupported."""
    path = path.expanduser().resolve()
    suffix = path.suffix.lower()

    # Packaged / document extensions are definitive (file need not exist yet for kind).
    if suffix == ".ebrl":
        return PublicationKind.EBRAILLE
    if suffix == ".epub":
        return PublicationKind.EPUB
    if suffix == ".pdf":
        return PublicationKind.PDF
    if suffix in HTML_SUFFIXES:
        return PublicationKind.HTML
    if suffix in SVG_SUFFIXES:
        return PublicationKind.SVG
    if suffix in CSS_SUFFIXES:
        return PublicationKind.CSS
    if suffix in MATHML_SUFFIXES:
        return PublicationKind.MATHML
    if path.is_file() and suffix == ".zip":
        # Legacy packaged path: treat as eBraille (same as before)
        return PublicationKind.EBRAILLE
    if path.is_file() and suffix in DTBOOK_SUFFIXES:
        if is_dtbook_xml(path):
            return PublicationKind.DTBOOK
        from .clipboard_markup import is_mathml_xml

        if is_mathml_xml(path):
            return PublicationKind.MATHML
        return PublicationKind.XML
    if path.is_file() and suffix in OPF_SUFFIXES:
        daisy = _opf_pipeline_kind(path)
        if daisy is not None:
            return daisy

    if not path.is_dir():
        return PublicationKind.UNSUPPORTED

    # DAISY 2.02 book folder (ncc.html). Checked before OPF so plain
    # DAISY titles are not misclassified as EPUB when an unrelated .opf exists.
    if find_ncc(path) is not None:
        return PublicationKind.DAISY202

    opf = find_package_document(path)
    if opf is not None:
        daisy = _opf_pipeline_kind(opf)
        if daisy is not None:
            return daisy
        # EPUB exploded folders have META-INF/container.xml; DAISY 3/NIMAS usually
        # do not. Still treat remaining OPF packages as EPUB/eBraille.
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
        return _kind_from_url_path(text)
    try:
        return classify_publication(Path(text))
    except (OSError, ValueError):
        return PublicationKind.UNSUPPORTED


def is_pipeline_kind(kind: PublicationKind) -> bool:
    """True for kinds that run through a local DAISY Pipeline 2 webservice."""
    return kind in PIPELINE_KINDS


def is_vnu_document_kind(kind: PublicationKind) -> bool:
    """True for single-file Nu HTML Checker targets (SVG, CSS, MathML, or XML)."""
    return kind in VNU_DOCUMENT_KINDS


def is_checkable_path(path: Path) -> bool:
    """True when path exists and can be checked with an available engine."""
    path = path.expanduser().resolve()
    if not path.exists():
        return False
    kind = classify_publication(path)
    return kind != PublicationKind.UNSUPPORTED


def is_checkable_target(raw: str) -> bool:
    """True when a path-field value is an HTML/SVG/CSS URL or a checkable path."""
    text = (raw or "").strip().strip('"')
    if not text:
        return False
    if is_html_url(text):
        return True
    try:
        return is_checkable_path(Path(text))
    except (OSError, ValueError):
        return False
