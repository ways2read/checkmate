"""Build a Fido-style alt-text export folder from EPUB/PDF/eBraille/HTML."""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from checkmate.doc_images.export import AltTextExportResult, export_document_alt_text

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], bool]

_SUPPORTED = {".epub", ".ebrl", ".pdf"}

# Bump when preview extraction or export layout changes so stale folders
# are not reused after an upgrade (any packaged format).
CACHE_FORMAT = 7
_MANIFEST_NAME = "export_manifest.json"
_INDEX_NAME = "index.json"
_MAX_CACHED_PUBLICATIONS = 12


@dataclass(frozen=True)
class _PubFingerprint:
    resolved: str
    mtime_ns: int
    size: int
    extra: str = ""


@dataclass
class _CachedExport:
    fingerprint: _PubFingerprint
    export_path: Path
    format: int = CACHE_FORMAT
    saved_at: float = 0.0


# Last successful export per resolved publication path (also mirrored on disk).
_EXPORT_CACHE: dict[str, _CachedExport] = {}
_INDEX_LOADED = False


def supports_alt_export_path(path: Path | str) -> bool:
    from checkmate.doc_images.html import path_is_html_source
    from checkmate.publication import is_html_url

    text = str(path).strip().strip('"')
    if not text:
        return False
    if is_html_url(text) or path_is_html_source(text):
        return True
    try:
        p = Path(text)
        return p.is_file() and p.suffix.lower() in _SUPPORTED
    except OSError:
        return False


def _html_cache_extra(text: str) -> str:
    try:
        from checkmate.html_check import last_html_session

        session = last_html_session()
    except Exception:
        return ""
    if session is None:
        return ""
    if session.target.strip().strip('"') != text:
        return ""
    return f"{session.crawl_cap}:{session.page_hash}"


def alt_export_cache_dir() -> Path:
    """Durable folder for export caches (survives restart; not %TEMP%)."""
    from checkmate.paths import app_data_dir

    path = app_data_dir() / "alt_exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    return alt_export_cache_dir() / _INDEX_NAME


def _fingerprint(path: Path | str) -> _PubFingerprint:
    from checkmate.publication import is_html_url

    text = str(path).strip().strip('"')
    extra = _html_cache_extra(text)
    if is_html_url(text):
        return _PubFingerprint(resolved=text, mtime_ns=0, size=0, extra=extra)
    resolved = Path(text).expanduser().resolve()
    st = resolved.stat()
    return _PubFingerprint(
        resolved=str(resolved),
        mtime_ns=int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
        size=int(st.st_size),
        extra=extra,
    )


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_export_manifest(folder: Path, fingerprint: _PubFingerprint) -> None:
    _atomic_write_json(
        folder / _MANIFEST_NAME,
        {
            "format": CACHE_FORMAT,
            "source": fingerprint.resolved,
            "mtime_ns": fingerprint.mtime_ns,
            "size": fingerprint.size,
            "extra": fingerprint.extra,
        },
    )


def read_export_manifest(folder: Path) -> dict[str, Any] | None:
    path = folder / _MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _export_folder_usable(folder: Path, *, require_context: bool = True) -> bool:
    """True when *folder* still looks like a complete, current alt-text export."""
    if not folder.is_dir():
        return False
    manifest = read_export_manifest(folder)
    if manifest is None:
        return False
    try:
        fmt = int(manifest.get("format") or 0)
    except (TypeError, ValueError):
        return False
    if fmt < CACHE_FORMAT:
        return False
    from checkmate.ai.alt_export import export_csv_has_context, find_export_csv

    if find_export_csv(folder) is None:
        return False
    images = folder / "images"
    if not images.is_dir():
        return False
    if require_context and not export_csv_has_context(folder):
        return False
    return True


def _entry_from_index_payload(payload: dict[str, Any]) -> _CachedExport | None:
    try:
        export_path = Path(str(payload.get("export_path") or ""))
        fp = _PubFingerprint(
            resolved=str(payload.get("resolved") or ""),
            mtime_ns=int(payload.get("mtime_ns") or 0),
            size=int(payload.get("size") or 0),
            extra=str(payload.get("extra") or ""),
        )
        fmt = int(payload.get("format") or 0)
        saved_at = float(payload.get("saved_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if not fp.resolved or not export_path.parts:
        return None
    if fmt < CACHE_FORMAT:
        return None
    return _CachedExport(
        fingerprint=fp, export_path=export_path, format=fmt, saved_at=saved_at
    )


def _load_index_into_memory() -> None:
    path = _index_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Could not read alt-export cache index", exc_info=True)
        return
    if not isinstance(data, dict):
        return
    try:
        if int(data.get("format") or 0) < CACHE_FORMAT:
            return
    except (TypeError, ValueError):
        return
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return
    for key, raw in entries.items():
        if not isinstance(key, str) or not isinstance(raw, dict):
            continue
        entry = _entry_from_index_payload(raw)
        if entry is None:
            continue
        _EXPORT_CACHE[key] = entry


def _ensure_index_loaded() -> None:
    global _INDEX_LOADED
    if _INDEX_LOADED:
        return
    _INDEX_LOADED = True
    _load_index_into_memory()


def _save_index() -> None:
    entries: dict[str, Any] = {}
    for key, entry in _EXPORT_CACHE.items():
        entries[key] = {
            "resolved": entry.fingerprint.resolved,
            "mtime_ns": entry.fingerprint.mtime_ns,
            "size": entry.fingerprint.size,
            "extra": entry.fingerprint.extra,
            "export_path": str(entry.export_path),
            "format": entry.format,
            "saved_at": entry.saved_at,
        }
    try:
        _atomic_write_json(
            _index_path(),
            {"format": CACHE_FORMAT, "entries": entries},
        )
    except OSError:
        logger.debug("Could not write alt-export cache index", exc_info=True)


def _prune_managed_folder(folder: Path) -> None:
    try:
        cache_dir = alt_export_cache_dir().resolve()
        resolved = folder.resolve()
    except OSError:
        return
    if resolved == cache_dir or not _is_under(resolved, cache_dir):
        return
    if resolved.name == _INDEX_NAME:
        return
    shutil.rmtree(resolved, ignore_errors=True)


def _evict_oldest_if_needed() -> None:
    if len(_EXPORT_CACHE) <= _MAX_CACHED_PUBLICATIONS:
        return
    ordered = sorted(
        _EXPORT_CACHE.items(),
        key=lambda item: item[1].saved_at or 0.0,
    )
    overflow = len(_EXPORT_CACHE) - _MAX_CACHED_PUBLICATIONS
    for key, entry in ordered[:overflow]:
        _EXPORT_CACHE.pop(key, None)
        _prune_managed_folder(entry.export_path)


def get_cached_alt_export(path: Path | str) -> Path | None:
    """Return a prior export folder for *path* if the file is unchanged."""
    if not supports_alt_export_path(path):
        return None
    try:
        fp = _fingerprint(path)
    except OSError:
        return None
    _ensure_index_loaded()
    entry = _EXPORT_CACHE.get(fp.resolved)
    if entry is None:
        return None
    if entry.fingerprint != fp or entry.format < CACHE_FORMAT:
        _EXPORT_CACHE.pop(fp.resolved, None)
        _save_index()
        return None
    if not _export_folder_usable(entry.export_path):
        _EXPORT_CACHE.pop(fp.resolved, None)
        _save_index()
        return None
    return entry.export_path


def remember_alt_export(path: Path | str, export_path: Path | str) -> None:
    """Record *export_path* as the current export for *path* (memory + disk)."""
    if not supports_alt_export_path(path):
        return
    try:
        fp = _fingerprint(path)
        out = Path(export_path).expanduser().resolve()
    except OSError:
        return
    try:
        write_export_manifest(out, fp)
    except OSError:
        logger.debug("Could not write export manifest for %s", out, exc_info=True)
        return
    if not _export_folder_usable(out):
        return
    _ensure_index_loaded()
    previous = _EXPORT_CACHE.get(fp.resolved)
    if (
        previous is not None
        and previous.export_path.resolve() != out
    ):
        _prune_managed_folder(previous.export_path)
    _EXPORT_CACHE[fp.resolved] = _CachedExport(
        fingerprint=fp,
        export_path=out,
        format=CACHE_FORMAT,
        saved_at=time.time(),
    )
    _evict_oldest_if_needed()
    _save_index()


def reset_alt_export_cache_state() -> None:
    """Forget the in-memory index so the next lookup reloads from disk."""
    global _INDEX_LOADED
    _EXPORT_CACHE.clear()
    _INDEX_LOADED = False


def clear_alt_export_cache(path: Path | str | None = None) -> None:
    """Drop cached export mappings (one publication, or all).

    Does not delete export folders; replacement and LRU eviction prune
    managed folders under ``alt_export_cache_dir()``.
    """
    _ensure_index_loaded()
    if path is None:
        _EXPORT_CACHE.clear()
        _save_index()
        return
    try:
        from checkmate.publication import is_html_url

        text = str(path).strip().strip('"')
        key = text if is_html_url(text) else str(Path(text).expanduser().resolve())
    except OSError:
        return
    _EXPORT_CACHE.pop(key, None)
    _save_index()


def build_alt_export_from_document(
    path: Path | str,
    *,
    dest_parent: Path | str | None = None,
    temp_dir: Path | str | None = None,
    progress_callback: ProgressCallback | None = None,
    write_html: bool = True,
    use_cache: bool = True,
) -> AltTextExportResult:
    """Export images + CSV (+ HTML) from a packaged publication or HTML source.

    Writes under *dest_parent* (default: app-data ``alt_exports``).
    When *use_cache* is True and a matching prior export still exists, returns
    that folder without re-extracting.
    """
    src = str(path).strip().strip('"')
    if not supports_alt_export_path(src):
        raise ValueError(
            "Alt-text export needs a packaged .epub, .ebrl, or .pdf, "
            f"or an HTML file/folder/URL (got {src!r})."
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
        dest = alt_export_cache_dir()
    else:
        dest = Path(dest_parent)
        dest.mkdir(parents=True, exist_ok=True)

    td = temp_dir
    if td is None:
        import tempfile

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
