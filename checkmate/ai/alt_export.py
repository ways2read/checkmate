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
    for i, row in enumerate(rows, start=1):
        filename = _pick(row, _FILE_KEYS).strip()
        index = _parse_index(_pick(row, _INDEX_KEYS), i)
        path = resolve_image_path(root, filename) if filename else None
        if filename and path is None:
            missing_files += 1
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
        except OSError:
            pass
    if not doc_name:
        # Folder name often embeds the publication stem
        doc_name = root.name

    return AltExport(
        folder=root,
        document_name=doc_name,
        images=images,
        csv_path=csv_path,
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
    export_data = [
        {
            "index": im.index,
            "filename": im.filename,
            "alt_text": im.alt_text,
            "status": im.status,
            "is_decorative": im.is_decorative,
            "dimensions": im.dimensions,
            "file_size": im.file_size,
            "image_classification": im.classification or "Unclassified",
            "context": im.context or "",
        }
        for im in export.images
    ]
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
