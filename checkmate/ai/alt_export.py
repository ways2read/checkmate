"""Load a Fido alt-text export folder (CSV + images)."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CSV_NAME = "alt_text_export.csv"
IMAGES_DIR = "images"

# Fido CSV uses "Alt Text" (with a space).
_ALT_KEYS = ("Alt Text", "Alt Text", "alt_text", "alt")
_STATUS_KEYS = ("Status", "status")
_CLASS_KEYS = ("Classification", "classification")
_FILE_KEYS = ("Filename", "filename", "File", "file")
_INDEX_KEYS = ("Index", "index")
_DIM_KEYS = ("Dimensions", "dimensions")
_SIZE_KEYS = ("File Size", "FileSize", "file_size")
_CONTEXT_KEYS = ("Context", "context", "Surrounding Text", "surrounding_text")
_FORMAT_KEYS = ("Format", "Publication Format", "publication_format")

PUBLICATION_FORMATS = frozenset(
    {"pdf", "epub", "ebrl", "docx", "pptx", "html", "idml", "folder", "unknown"}
)

_FORMAT_ALIASES = {
    "doc": "docx",
    "word": "docx",
    "htm": "html",
    "xhtml": "html",
    "powerpoint": "pptx",
    "ppt": "pptx",
    "indesign": "idml",
    "ebraille": "ebrl",
}


def normalize_publication_format(value: str | None) -> str:
    """Return a canonical format key, or ``unknown``."""
    raw = (value or "").strip().lower()
    if not raw:
        return "unknown"
    raw = raw.split(".", 1)[-1] if raw.startswith(".") else raw
    raw = _FORMAT_ALIASES.get(raw, raw)
    if raw in PUBLICATION_FORMATS:
        return raw
    return "unknown"


def publication_format_label(value: str | None) -> str:
    """Short display name for prompts (``PDF``, ``EPUB``, …)."""
    key = normalize_publication_format(value)
    return {
        "pdf": "PDF",
        "epub": "EPUB",
        "ebrl": "eBraille",
        "docx": "Word (DOCX)",
        "pptx": "PowerPoint (PPTX)",
        "html": "HTML",
        "idml": "InDesign (IDML)",
        "folder": "image folder",
        "unknown": "unknown",
    }.get(key, "unknown")


def infer_publication_format(
    *,
    explicit: str = "",
    document_name: str = "",
    folder: Path | str | None = None,
    backend: object | None = None,
) -> str:
    """Best-effort publication format from an explicit value, backend, or filename."""
    for candidate in (explicit, publication_format_from_backend(backend)):
        key = normalize_publication_format(candidate)
        if key != "unknown":
            return key
    for name in (document_name, str(folder) if folder is not None else ""):
        if not name:
            continue
        stem = Path(str(name)).name
        ext = Path(stem).suffix.lower().lstrip(".")
        key = normalize_publication_format(ext)
        if key != "unknown":
            return key
        lower = stem.lower()
        for token in ("ebrl", "epub", "pdf", "docx", "pptx", "idml"):
            if f".{token}" in lower or lower.endswith(token):
                return token
    return "unknown"


def publication_format_from_backend(backend: object | None) -> str:
    """Infer format from a document-image backend instance."""
    if backend is None:
        return "unknown"
    cls = type(backend).__name__.lower()
    hints = (
        ("pdf", "pdf"),
        ("ebrl", "ebrl"),
        ("ebraille", "ebrl"),
        ("epub", "epub"),
        ("docx", "docx"),
        ("word", "docx"),
        ("pptx", "pptx"),
        ("powerpoint", "pptx"),
        ("idml", "idml"),
        ("html", "html"),
        ("folder", "folder"),
    )
    for needle, key in hints:
        if needle in cls:
            return key
    for attr in ("_document_path", "document_path", "_source_path", "source"):
        path = getattr(backend, attr, None)
        if isinstance(path, str) and path.strip():
            key = infer_publication_format(document_name=path)
            if key != "unknown":
                return key
    return "unknown"


@dataclass(frozen=True)
class AltExportImage:
    index: int
    filename: str
    classification: str
    alt_text: str
    status: str
    dimensions: str = ""
    file_size: str = ""
    context: str = ""
    image_path: Path | None = None

    @property
    def status_norm(self) -> str:
        return (self.status or "").strip().lower()

    @property
    def is_decorative(self) -> bool:
        return self.status_norm == "decorative"

    @property
    def has_alt_status(self) -> bool:
        s = self.status_norm
        return s in {"has alt text", "has_alt_text", "has alt"}

    @property
    def alt_stripped(self) -> str:
        return (self.alt_text or "").strip()

    @property
    def context_stripped(self) -> str:
        return (self.context or "").strip()


@dataclass
class AltExport:
    folder: Path
    document_name: str = ""
    images: list[AltExportImage] = field(default_factory=list)
    csv_path: Path | None = None
    publication_format: str = "unknown"

    @property
    def total(self) -> int:
        return len(self.images)

    def counts(self) -> dict[str, int]:
        with_alt = sum(1 for im in self.images if im.has_alt_status)
        decorative = sum(1 for im in self.images if im.is_decorative)
        missing = sum(
            1
            for im in self.images
            if not im.is_decorative and not im.alt_stripped
        )
        return {
            "total": self.total,
            "with_alt": with_alt,
            "decorative": decorative,
            "missing": missing,
        }


def _pick(row: dict[str, str], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        if key in row and row[key] is not None:
            return str(row[key])
    # Case-insensitive fallback
    lower = {k.lower(): v for k, v in row.items()}
    for key in keys:
        v = lower.get(key.lower())
        if v is not None:
            return str(v)
    return default


def _parse_index(raw: str, fallback: int) -> int:
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        return int(text)
    except ValueError:
        return fallback


def find_export_csv(folder: Path) -> Path | None:
    """Return the CSV path inside *folder*, if present."""
    direct = folder / CSV_NAME
    if direct.is_file():
        return direct
    # Allow a single *.csv in the folder
    csvs = sorted(folder.glob("*.csv"))
    if len(csvs) == 1:
        return csvs[0]
    for name in ("alt_text_export.csv", "alt-text-export.csv", "alttext.csv"):
        p = folder / name
        if p.is_file():
            return p
    return None


def export_csv_has_context(folder: Path | str) -> bool:
    """True when the export CSV includes a Context column."""
    csv_path = find_export_csv(Path(folder))
    if csv_path is None:
        return False
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            names = reader.fieldnames or []
    except OSError:
        return False
    lower = {str(n).strip().lower() for n in names}
    return bool(lower & {k.lower() for k in _CONTEXT_KEYS})


def resolve_image_path(folder: Path, filename: str) -> Path | None:
    name = (filename or "").strip()
    if not name:
        return None
    candidates = [
        folder / IMAGES_DIR / name,
        folder / name,
        folder / "Images" / name,
    ]
    for path in candidates:
        if path.is_file():
            return path
    # Case-insensitive search in images/
    images = folder / IMAGES_DIR
    if images.is_dir():
        lower = name.lower()
        for child in images.iterdir():
            if child.is_file() and child.name.lower() == lower:
                return child
    return None


def load_alt_export(folder: Path | str) -> AltExport:
    """Load and validate a Fido alt-text export directory.

    Raises:
        FileNotFoundError: folder or CSV missing
        ValueError: CSV unreadable / empty
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Export folder not found: {root}")

    csv_path = find_export_csv(root)
    if csv_path is None:
        raise FileNotFoundError(
            f"No alt-text CSV found in {root} (expected {CSV_NAME})"
        )

    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {csv_path}")
        rows = list(reader)

    if not rows:
        raise ValueError(f"CSV has no image rows: {csv_path}")

    images: list[AltExportImage] = []
    missing_files = 0
    csv_format = ""
    for i, row in enumerate(rows, start=1):
        filename = _pick(row, _FILE_KEYS).strip()
        index = _parse_index(_pick(row, _INDEX_KEYS), i)
        path = resolve_image_path(root, filename) if filename else None
        if filename and path is None:
            missing_files += 1
        if not csv_format:
            csv_format = _pick(row, _FORMAT_KEYS).strip()
        images.append(
            AltExportImage(
                index=index,
                filename=filename or f"row_{i}",
                classification=_pick(row, _CLASS_KEYS).strip(),
                alt_text=_pick(row, _ALT_KEYS),
                status=_pick(row, _STATUS_KEYS).strip(),
                dimensions=_pick(row, _DIM_KEYS).strip(),
                file_size=_pick(row, _SIZE_KEYS).strip(),
                context=_pick(row, _CONTEXT_KEYS),
                image_path=path,
            )
        )

    if missing_files:
        logger.warning(
            "Alt export: %s image file(s) referenced in CSV but not found under %s",
            missing_files,
            root,
        )

    doc_name = ""
    html = root / "alt_text_report.html"
    html_format = ""
    if html.is_file():
        try:
            text = html.read_text(encoding="utf-8", errors="replace")
            # <strong>Document:</strong> name
            import re

            m = re.search(
                r"Document:</strong>\s*([^<]+)",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                doc_name = m.group(1).strip()
            m = re.search(
                r"Format:</strong>\s*([^<]+)",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                html_format = m.group(1).strip()
        except OSError:
            pass
    if not doc_name:
        # Folder name often embeds the publication stem
        doc_name = root.name

    publication_format = infer_publication_format(
        explicit=csv_format or html_format,
        document_name=doc_name,
        folder=root,
    )

    return AltExport(
        folder=root,
        document_name=doc_name,
        images=images,
        csv_path=csv_path,
        publication_format=publication_format,
    )


def ensure_alt_report_html(
    folder: Path | str,
    *,
    exported_by: str = "CheckMate",
) -> Path:
    """Return path to ``alt_text_report.html``, regenerating from CSV when needed.

    Regenerates when the file is missing or still branded as another product
    (e.g. a cached Fido-labelled report opened in CheckMate).
    """
    from datetime import datetime

    from checkmate.doc_images.export import write_alt_text_html_report

    root = Path(folder).expanduser().resolve()
    html_path = root / "alt_text_report.html"
    exporter = (exported_by or "CheckMate").strip() or "CheckMate"
    if html_path.is_file():
        try:
            existing = html_path.read_text(encoding="utf-8", errors="replace")
            marker = f"Exported by:</strong> {exporter}"
            if marker in existing:
                return html_path
        except OSError:
            pass

    export = load_alt_export(root)
    counts = export.counts()
    export_data = []
    for im in export.images:
        thumb_filename = ""
        if im.filename:
            stem = Path(im.filename).stem
            if (root / "thumbs" / f"{stem}.jpg").is_file():
                thumb_filename = f"{stem}.jpg"
        export_data.append(
            {
                "index": im.index,
                "filename": im.filename,
                "thumb_filename": thumb_filename,
                "alt_text": im.alt_text,
                "status": im.status,
                "is_decorative": im.is_decorative,
                "dimensions": im.dimensions,
                "file_size": im.file_size,
                "image_classification": im.classification or "Unclassified",
                "context": im.context or "",
            }
        )
    stats = {
        "total": counts["total"],
        "with_alt_text": counts["with_alt"],
        "decorative": counts["decorative"],
        "no_alt_text": counts["missing"],
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    write_alt_text_html_report(
        html_path,
        doc_name=export.document_name or root.name,
        export_data=export_data,
        stats=stats,
        timestamp=timestamp,
        exported_by=exporter,
    )
    return html_path


def inventory_webview_html(folder: Path | str, *, exported_by: str = "CheckMate") -> str:
    """Self-contained inventory HTML for in-app ``SetPage`` (data-URI thumbs).

    Edge WebView2 often paints a blank document for a second ``LoadURL(file://)``
    in the same process. ``SetPage`` with embedded thumbs matches the Knowledge
    Base viewer. Click-to-enlarge uses ``https://checkmate.invalid/preview/<index>``
    so Edge fires a real navigation (custom ``checkmate://`` is often dropped
    after ``SetPage``). Do not embed full-size data-URIs — that bloated
    ``SetPage`` and painted a blank view.
    """
    from datetime import datetime

    from checkmate.ai.alt_inventory_dialog import preview_href_for_index
    from checkmate.ai.alt_report import _thumb_data_uri
    from checkmate.doc_images.export import write_alt_text_html_report

    root = Path(folder).expanduser().resolve()
    export = load_alt_export(root)
    counts = export.counts()
    export_data = []
    for im in export.images:
        thumb_path = None
        if im.filename:
            stem = Path(im.filename).stem
            sidecar = root / "thumbs" / f"{stem}.jpg"
            if sidecar.is_file():
                thumb_path = sidecar
            elif im.image_path is not None and im.image_path.is_file():
                thumb_path = im.image_path
        export_data.append(
            {
                "index": im.index,
                "filename": im.filename,
                "thumb_filename": "",
                "img_src": _thumb_data_uri(thumb_path),
                "preview_href": preview_href_for_index(im.index),
                "full_src": "",
                "alt_text": im.alt_text,
                "status": im.status,
                "is_decorative": im.is_decorative,
                "dimensions": im.dimensions,
                "file_size": im.file_size,
                "image_classification": im.classification or "Unclassified",
                "context": im.context or "",
            }
        )
    stats = {
        "total": counts["total"],
        "with_alt_text": counts["with_alt"],
        "decorative": counts["decorative"],
        "no_alt_text": counts["missing"],
    }
    return write_alt_text_html_report(
        None,
        doc_name=export.document_name or root.name,
        export_data=export_data,
        stats=stats,
        timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
        exported_by=(exported_by or "CheckMate").strip() or "CheckMate",
    )
