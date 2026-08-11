"""Build a Fido-style alt-text export folder from EPUB/PDF/eBraille."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from checkmate.doc_images.export import AltTextExportResult, export_document_alt_text

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], bool]

_SUPPORTED = {".epub", ".ebrl", ".pdf"}


@dataclass(frozen=True)
class _PubFingerprint:
    resolved: str
    mtime_ns: int
    size: int


@dataclass
class _CachedExport:
    fingerprint: _PubFingerprint
    export_path: Path


# Last successful export per resolved publication path.
_EXPORT_CACHE: dict[str, _CachedExport] = {}


def supports_alt_export_path(path: Path | str) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix.lower() in _SUPPORTED


def _fingerprint(path: Path) -> _PubFingerprint:
    resolved = path.expanduser().resolve()
    st = resolved.stat()
    return _PubFingerprint(
        resolved=str(resolved),
        mtime_ns=int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
        size=int(st.st_size),
    )


def _export_folder_usable(folder: Path, *, require_context: bool = True) -> bool:
    """True when *folder* still looks like a complete alt-text export."""
    if not folder.is_dir():
        return False
    from checkmate.ai.alt_export import export_csv_has_context, find_export_csv

    if find_export_csv(folder) is None:
        return False
    images = folder / "images"
    # Normal exports always create images/; require it so we don't reuse junk.
    if not images.is_dir():
        return False
    # Prefer exports that include surrounding text (invalidate older caches).
    if require_context and not export_csv_has_context(folder):
        return False
    return True


def get_cached_alt_export(path: Path | str) -> Path | None:
    """Return a prior export folder for *path* if the file is unchanged."""
    src = Path(path)
    if not supports_alt_export_path(src):
        return None
    try:
        fp = _fingerprint(src)
    except OSError:
        return None
    entry = _EXPORT_CACHE.get(fp.resolved)
    if entry is None:
        return None
    if entry.fingerprint != fp:
        _EXPORT_CACHE.pop(fp.resolved, None)
        return None
    if not _export_folder_usable(entry.export_path):
        _EXPORT_CACHE.pop(fp.resolved, None)
        return None
    return entry.export_path


def remember_alt_export(path: Path | str, export_path: Path | str) -> None:
    """Record *export_path* as the current export for *path*."""
    src = Path(path)
    out = Path(export_path)
    if not supports_alt_export_path(src) or not _export_folder_usable(out):
        return
    try:
        fp = _fingerprint(src)
    except OSError:
        return
    _EXPORT_CACHE[fp.resolved] = _CachedExport(fingerprint=fp, export_path=out)


def clear_alt_export_cache(path: Path | str | None = None) -> None:
    """Drop cached exports (one publication, or all)."""
    if path is None:
        _EXPORT_CACHE.clear()
        return
    try:
        key = str(Path(path).expanduser().resolve())
    except OSError:
        return
    _EXPORT_CACHE.pop(key, None)


def build_alt_export_from_document(
    path: Path | str,
    *,
    dest_parent: Path | str | None = None,
    temp_dir: Path | str | None = None,
    progress_callback: ProgressCallback | None = None,
    write_html: bool = True,
    use_cache: bool = True,
) -> AltTextExportResult:
    """Export images + CSV (+ HTML) from a packaged publication.

    Writes under *dest_parent* (default: system temp ``checkmate/alt_exports``).
    When *use_cache* is True and a matching prior export still exists, returns
    that folder without re-extracting.
    """
    src = Path(path)
    if not supports_alt_export_path(src):
        raise ValueError(
            f"Alt-text export needs a packaged .epub, .ebrl, or .pdf (got {src.suffix!r})."
        )

    if use_cache:
        cached = get_cached_alt_export(src)
        if cached is not None:
            logger.debug("Reusing cached alt-text export for %s -> %s", src, cached)
            from checkmate.ai.alt_export import load_alt_export

            try:
                export = load_alt_export(cached)
                counts = export.counts()
            except Exception:
                clear_alt_export_cache(src)
            else:
                return AltTextExportResult(
                    export_path=cached,
                    csv_path=export.csv_path or (cached / "alt_text_export.csv"),
                    html_path=(
                        (cached / "alt_text_report.html")
                        if (cached / "alt_text_report.html").is_file()
                        else None
                    ),
                    stats={
                        "total": counts["total"],
                        "exported": counts["total"],
                        "with_alt_text": counts["with_alt"],
                        "decorative": counts["decorative"],
                        "no_alt_text": counts["missing"],
                        "errors": 0,
                        "cancelled": False,
                        "cached": True,
                    },
                    cancelled=False,
                )

    if dest_parent is None:
        dest = Path(tempfile.gettempdir()) / "checkmate" / "alt_exports"
        dest.mkdir(parents=True, exist_ok=True)
    else:
        dest = Path(dest_parent)
        dest.mkdir(parents=True, exist_ok=True)

    td = temp_dir
    if td is None:
        td = Path(tempfile.gettempdir()) / "checkmate" / "doc_images"
        Path(td).mkdir(parents=True, exist_ok=True)

    result = export_document_alt_text(
        src,
        dest,
        temp_dir=td,
        write_html=write_html,
        include_classification=True,
        include_context=True,
        progress_callback=progress_callback,
    )
    if not result.cancelled:
        remember_alt_export(src, result.export_path)
    return result
