"""Headless alt-text export (CSV + images + HTML).

Used by Fido Image Utility and by CheckMate (via sibling import or vendored copy).
"""
from __future__ import annotations

import csv
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("fido")

ProgressCallback = Callable[[int, int, str], bool]
# progress(current_1based, total, message) -> False to cancel


@dataclass
class AltTextExportResult:
    export_path: Path
    csv_path: Path
    html_path: Path | None
    stats: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False


_DEFAULT_STATUS = {
    "decorative": "Decorative",
    "has_alt": "Has Alt Text",
    "no_alt": "No Alt Text",
    "error": "Error",
    "unclassified": "Unclassified",
}

# Cap surrounding text written to CSV / HTML (backends may already truncate).
_DEFAULT_CONTEXT_MAX_CHARS = 2500


def _context_char_cap() -> int:
    try:
        from checkmate.doc_images.api import _context_params

        return max(500, int(_context_params()[2]))
    except Exception:
        return _DEFAULT_CONTEXT_MAX_CHARS


def _backend_context(backend: Any, index: int, *, max_chars: int) -> str:
    """Best-effort surrounding text from the document backend."""
    try:
        raw = backend.get_context(index)
    except Exception as e:
        logger.debug("get_context(%s) failed: %s", index, e)
        return ""
    text = (raw or "").strip() if isinstance(raw, str) else str(raw or "").strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def _format_size(file_size: int) -> str:
    if file_size >= 1024 * 1024:
        return f"{file_size / (1024 * 1024):.2f} MB"
    if file_size >= 1024:
        return f"{file_size / 1024:.1f} KB"
    return f"{file_size} bytes"


def _image_dimensions(path: str) -> str:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return f"{img.width}x{img.height}"
    except Exception:
        return "Unknown"


_THUMB_MAX_EDGE = 160
_THUMB_JPEG_QUALITY = 55


def _write_jpeg_thumb(src: str | Path, dest: Path) -> bool:
    """Write a small JPEG card preview of *src*. Returns True on success."""
    try:
        import fitz  # type: ignore

        pix = fitz.Pixmap(str(src))
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        longest = max(pix.width, pix.height) or 1
        if longest > _THUMB_MAX_EDGE:
            scale = _THUMB_MAX_EDGE / float(longest)
            pix = fitz.Pixmap(
                pix,
                max(1, int(round(pix.width * scale))),
                max(1, int(round(pix.height * scale))),
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pix.tobytes("jpeg", jpg_quality=_THUMB_JPEG_QUALITY))
        return dest.is_file() and dest.stat().st_size > 0
    except Exception:
        logger.debug("Thumb encode failed for %s", src, exc_info=True)
        return False


def _image_classification(path: str, unclassified_label: str) -> str:
    try:
        from checkmate.doc_images._fido_xmp import display_for_ui, read_classification

        return display_for_ui(read_classification(path))
    except Exception:
        return unclassified_label


def export_alt_text_report(
    backend: Any,
    dest_parent: str | Path,
    *,
    document_name: str = "",
    write_html: bool = True,
    include_classification: bool = True,
    include_context: bool = True,
    progress_callback: ProgressCallback | None = None,
    status_labels: dict[str, str] | None = None,
    exported_by: str = "CheckMate",
) -> AltTextExportResult:
    """Export all images from *backend* to an AltText_Export_* folder.

    Writes ``alt_text_export.csv``, ``images/`` (full previews), ``thumbs/``
    (card-sized JPEGs), and optionally ``alt_text_report.html``. Does not
    require wx.

    When *include_context* is True, each row gets a ``Context`` column from
    ``backend.get_context(index)`` (empty when unsupported).

    *exported_by* is shown in the HTML header (CheckMate vs Fido).
    """
    labels = dict(_DEFAULT_STATUS)
    if status_labels:
        labels.update(status_labels)

    total = int(backend.get_image_count() or 0)
    doc_name = (
        (document_name or "").strip()
        or (backend.get_document_display_name() or "").strip()
        or "document"
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c for c in doc_name if c.isalnum() or c in (" ", "-", "_")).strip()
    safe = (safe or "document")[:50]
    export_path = Path(dest_parent) / f"AltText_Export_{safe}_{timestamp}"
    images_folder = export_path / "images"
    thumbs_folder = export_path / "thumbs"
    export_path.mkdir(parents=True, exist_ok=True)
    images_folder.mkdir(parents=True, exist_ok=True)
    thumbs_folder.mkdir(parents=True, exist_ok=True)

    stats = {
        "total": total,
        "exported": 0,
        "with_alt_text": 0,
        "decorative": 0,
        "no_alt_text": 0,
        "errors": 0,
        "with_context": 0,
        "cancelled": False,
    }
    export_data: list[dict[str, Any]] = []
    context_cap = _context_char_cap() if include_context else 0

    for i in range(total):
        if progress_callback is not None:
            msg = f"Exporting image {i + 1} of {total}..."
            if progress_callback(i + 1, total, msg) is False:
                stats["cancelled"] = True
                break
        result = backend.load_image(i)
        context = (
            _backend_context(backend, i, max_chars=context_cap)
            if include_context
            else ""
        )
        if context:
            stats["with_context"] += 1
        if not result:
            stats["errors"] += 1
            export_data.append(
                {
                    "index": i + 1,
                    "filename": "",
                    "alt_text": "",
                    "status": labels["error"],
                    "is_decorative": False,
                    "dimensions": "Unknown",
                    "file_size": "0 bytes",
                    "image_classification": labels["unclassified"],
                    "context": context,
                }
            )
            continue

        path = result.get("image_path") or ""
        alt = (result.get("alt_text") or "").strip()
        is_dec = bool(result.get("is_decorative", False))
        if is_dec:
            stats["decorative"] += 1
        if alt:
            stats["with_alt_text"] += 1
        elif not is_dec:
            stats["no_alt_text"] += 1
        status = (
            labels["decorative"]
            if is_dec
            else (labels["has_alt"] if alt else labels["no_alt"])
        )

        dest_filename = ""
        thumb_filename = ""
        dimensions = "Unknown"
        file_size_str = "0 bytes"
        image_classification = labels["unclassified"]
        if path and os.path.isfile(path):
            ext = os.path.splitext(path)[1] or ".png"
            dest_filename = f"image_{i + 1:04d}{ext}"
            dest_path = images_folder / dest_filename
            try:
                shutil.copy2(path, dest_path)
                stats["exported"] += 1
                file_size = dest_path.stat().st_size
                file_size_str = _format_size(file_size)
                dimensions = _image_dimensions(str(dest_path))
                if include_classification:
                    image_classification = _image_classification(
                        str(dest_path), labels["unclassified"]
                    )
                thumb_name = f"image_{i + 1:04d}.jpg"
                if _write_jpeg_thumb(dest_path, thumbs_folder / thumb_name):
                    thumb_filename = thumb_name
            except Exception as e:
                logger.debug("Export copy image %s: %s", i, e)
                stats["errors"] += 1

        export_data.append(
            {
                "index": i + 1,
                "filename": dest_filename,
                "thumb_filename": thumb_filename,
                "alt_text": alt,
                "status": status,
                "is_decorative": is_dec,
                "dimensions": dimensions,
                "file_size": file_size_str,
                "image_classification": image_classification,
                "context": context,
            }
        )

    csv_path = export_path / "alt_text_export.csv"
    pub_format = ""
    try:
        from checkmate.ai.alt_export import publication_format_from_backend

        pub_format = publication_format_from_backend(backend) or ""
    except Exception:
        pub_format = ""
    with csv_path.open("w", newline="", encoding="utf-8-sig") as csvfile:
        fieldnames = [
            "Index",
            "Filename",
            "Classification",
            "Alt Text",
            "Status",
            "Dimensions",
            "File Size",
            "Context",
        ]
        if pub_format and pub_format != "unknown":
            fieldnames.append("Format")
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for item in export_data:
            row = {
                "Index": item["index"],
                "Filename": item["filename"],
                "Classification": item.get("image_classification", ""),
                "Alt Text": item["alt_text"],
                "Status": item["status"],
                "Dimensions": item["dimensions"],
                "File Size": item["file_size"],
                "Context": item.get("context", ""),
            }
            if pub_format and pub_format != "unknown":
                row["Format"] = pub_format
            writer.writerow(row)

    html_path = None
    if write_html and not stats["cancelled"]:
        html_path = export_path / "alt_text_report.html"
        write_alt_text_html_report(
            html_path,
            doc_name=doc_name,
            export_data=export_data,
            stats=stats,
            timestamp=timestamp,
            exported_by=exported_by,
        )

    return AltTextExportResult(
        export_path=export_path,
        csv_path=csv_path,
        html_path=html_path,
        stats=stats,
        cancelled=bool(stats["cancelled"]),
    )


def open_document_backend(
    path: str | Path,
    *,
    temp_dir: str | Path | None = None,
    dialog: Any = None,
) -> Any:
    """Open an EPUB, PDF, or HTML source with the appropriate on-disc backend."""
    from checkmate.doc_images.html import HtmlOnDiscBackend, path_is_html_source

    text = str(path).strip().strip('"')
    td = str(temp_dir) if temp_dir else None
    if path_is_html_source(text):
        backend = HtmlOnDiscBackend(dialog=dialog, temp_dir=td)
        if not backend.open_document(text):
            raise RuntimeError(f"Could not open document: {text}")
        return backend

    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".epub", ".ebrl"):
        from checkmate.doc_images.epub import EpubOnDiscBackend

        backend = EpubOnDiscBackend(dialog=dialog, temp_dir=td)
    elif suffix == ".pdf":
        from checkmate.doc_images.pdf import PdfOnDiscBackend

        backend = PdfOnDiscBackend(dialog=dialog, temp_dir=td)
    else:
        raise ValueError(f"Unsupported document type for alt-text export: {suffix}")
    if not backend.open_document(str(p.resolve())):
        raise RuntimeError(f"Could not open document: {p}")
    return backend


def export_document_alt_text(
    path: str | Path,
    dest_parent: str | Path,
    *,
    temp_dir: str | Path | None = None,
    write_html: bool = True,
    include_classification: bool = True,
    include_context: bool = True,
    progress_callback: ProgressCallback | None = None,
    status_labels: dict[str, str] | None = None,
    exported_by: str = "CheckMate",
) -> AltTextExportResult:
    """Open *path* (EPUB/PDF/HTML), export alt-text report, close backend."""
    backend = open_document_backend(path, temp_dir=temp_dir)
    try:
        display = Path(path).name if not str(path).lower().startswith("http") else str(path)
        return export_alt_text_report(
            backend,
            dest_parent,
            document_name=display,
            write_html=write_html,
            include_classification=include_classification,
            include_context=include_context,
            progress_callback=progress_callback,
            status_labels=status_labels,
            exported_by=exported_by,
        )
    finally:
        try:
            backend.close()
        except Exception:
            logger.debug("backend.close failed", exc_info=True)


def write_alt_text_html_report(
    html_path: str | Path | None,
    *,
    doc_name: str,
    export_data: list[dict[str, Any]],
    stats: dict[str, Any],
    timestamp: str,
    images_rel_dir: str = "images/",
    exported_by: str = "CheckMate",
) -> str:
    """Build the interactive HTML alt-text inventory report.

    When *html_path* is set, also write that file (browser / folder view).
    """
    import html as html_module

    try:
        dt = datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
        formatted_date = dt.strftime("%B %d, %Y at %H:%M:%S")
    except Exception:
        formatted_date = timestamp
    exporter = (exported_by or "CheckMate").strip() or "CheckMate"

    cards: list[str] = []
    for item in export_data:
        is_dec = item.get("is_decorative")
        alt = item.get("alt_text") or ""
        if is_dec:
            status_key = "decorative"
            status_label = "Decorative"
            card_class = "decorative"
            badge_class = "decorative"
        elif alt:
            status_key = "has-alt"
            status_label = "Has Alt Text"
            card_class = "has-alt"
            badge_class = "has-alt"
        else:
            status_key = "no-alt"
            status_label = "Missing Alt Text"
            card_class = "no-alt"
            badge_class = "no-alt"
        fname = item.get("filename") or ""
        thumb_name = (item.get("thumb_filename") or "").strip()
        img_src = (item.get("img_src") or "").strip()
        preview_href = (item.get("preview_href") or "").strip()
        raw_full = (item.get("full_src") or "").strip()
        full_src = raw_full or (
            "" if preview_href else (images_rel_dir + fname if fname else "")
        )
        thumb_src = img_src or (f"thumbs/{thumb_name}" if thumb_name else full_src)
        if not fname:
            img_tag = "<p>No image</p>"
        elif preview_href and not raw_full:
            # In-app SetPage: a real <a> so Edge fires NAVIGATING. Do not put a
            # relative images/ path in data-full-src (no file:// base URL).
            href = html_module.escape(preview_href, quote=True)
            img_tag = (
                f'<a class="thumb-link" href="{href}">'
                f'<img src="{html_module.escape(thumb_src)}" alt="" '
                f'loading="lazy" title="Click to enlarge"></a>'
            )
        else:
            preview_attr = (
                f' data-preview="{html_module.escape(preview_href, quote=True)}"'
                if preview_href
                else ""
            )
            img_tag = (
                f'<img src="{html_module.escape(thumb_src)}" '
                f'data-full-src="{html_module.escape(full_src)}" '
                f'{preview_attr} '
                f'alt="" loading="lazy" title="Click to enlarge" '
                f'onclick="openModal(this)">'
            )
        alt_display = (
            html_module.escape(alt)
            if alt
            else ("<em>Decorative image</em>" if is_dec else "<em>No alt text</em>")
        )
        alt_class = "" if alt else "empty"
        ctx = (item.get("context") or "").strip()
        ctx_block = ""
        if ctx:
            ctx_block = (
                "<h3>Surrounding text:</h3>"
                f'<div class="context">{html_module.escape(ctx)}</div>'
            )
        classification = str(item.get("image_classification") or "").strip()
        try:
            from checkmate.i18n import _

            unclassified_label = _("Unclassified")
        except Exception:
            unclassified_label = "Unclassified"
        if (
            not classification
            or classification.lower() == "unclassified"
            or classification == unclassified_label
        ):
            classification_html = ""
        else:
            classification_html = (
                '<p class="classification"><strong>Classification:</strong> '
                f"{html_module.escape(classification)}</p>"
            )
        cards.append(
            f"""
        <div class="image-card {card_class}" data-status="{status_key}" data-alt="{html_module.escape(alt.lower())}">
            <div class="image-container">
                <span class="image-number">#{item.get("index", "")}</span>
                <span class="status-badge {badge_class}">{status_label}</span>
                {img_tag}
            </div>
            <div class="card-content">
                {classification_html}
                <h3>Alt Text:</h3>
                <div class="alt-text {alt_class}">{alt_display}</div>
                {ctx_block}
                <div class="meta-info">
                    <span class="filename-display">{html_module.escape(fname)}</span>
                    <span>{html_module.escape(str(item.get("dimensions", "")))}</span>
                    <span>{html_module.escape(str(item.get("file_size", "")))}</span>
                </div>
            </div>
        </div>"""
        )

    try:
        from checkmate.ui_appearance import (
            html_color_scheme,
            html_root_class,
            wrap_os_dark_css,
        )

        color_scheme = html_color_scheme()
        root_class = html_root_class()
        extra_css = wrap_os_dark_css(
            """
        body { background: #0f172a; color: #e2e8f0; }
        .stat-box, .filters, .image-card { background: #1e293b; }
        .stat-box .label, .meta-info, .classification { color: #94a3b8; }
        .alt-text { background: #0f172a; color: #e2e8f0; }
        .context { background: #1e293b; color: #cbd5e1; }
        .image-container { background: #0f172a; }
        #search-box { background: #0f172a; color: #e2e8f0; border-color: #334155; }
"""
        )
    except Exception:
        color_scheme = "light dark"
        root_class = "checkmate-theme-system"
        extra_css = ""

    html_content = f"""<!DOCTYPE html>
<html lang="en" class="{html_module.escape(root_class)}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="{html_module.escape(color_scheme)}">
    <title>Alt Text Report - {html_module.escape(doc_name)}</title>
    <style>
        body {{ font-family: system-ui, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; color: #333; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; padding: 24px; border-radius: 10px; margin-bottom: 16px; }}
        .stats-container {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }}
        .stat-box {{ background: #fff; padding: 16px; border-radius: 10px; min-width: 120px; text-align: center; box-shadow: 0 2px 5px rgba(0,0,0,.08); }}
        .stat-box .number {{ font-size: 1.8em; font-weight: 700; color: #667eea; }}
        .stat-box.success .number {{ color: #27ae60; }}
        .stat-box.danger .number {{ color: #e74c3c; }}
        .filters {{ background: #fff; padding: 16px; border-radius: 10px; margin-bottom: 16px; }}
        .image-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
        .image-card {{ background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,.08); }}
        .image-card.hidden {{ display: none; }}
        .image-card.decorative {{ border-left: 4px solid #3498db; }}
        .image-card.has-alt {{ border-left: 4px solid #27ae60; }}
        .image-card.no-alt {{ border-left: 4px solid #e74c3c; }}
        .image-container {{ position: relative; background: #f0f0f0; min-height: 180px; display: flex; align-items: center; justify-content: center; padding: 10px; }}
        .image-container a.thumb-link {{ display: flex; align-items: center; justify-content: center; width: 100%; text-decoration: none; color: inherit; }}
        .image-container img {{ max-width: 100%; max-height: 220px; object-fit: contain; cursor: pointer; }}
        .image-number, .status-badge {{ position: absolute; top: 10px; padding: 4px 8px; border-radius: 4px; font-size: .8em; color: #fff; }}
        .image-number {{ left: 10px; background: rgba(0,0,0,.7); }}
        .status-badge {{ right: 10px; font-weight: 700; }}
        .status-badge.decorative {{ background: #3498db; }}
        .status-badge.has-alt {{ background: #27ae60; }}
        .status-badge.no-alt {{ background: #e74c3c; }}
        .card-content {{ padding: 12px; }}
        .alt-text {{ background: #f8f9fa; padding: 10px; border-radius: 6px; min-height: 48px; white-space: pre-wrap; }}
        .alt-text.empty {{ color: #e74c3c; }}
        .context {{ background: #f0f4f8; padding: 10px; border-radius: 6px; margin-top: 8px; max-height: 140px; overflow: auto; white-space: pre-wrap; font-size: .9em; color: #444; }}
        .meta-info {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; color: #666; font-size: .85em; }}
        .modal {{ display: none; position: fixed; z-index: 99; inset: 0; background: rgba(0,0,0,.85); }}
        .modal-content {{ max-width: 90%; max-height: 90%; margin: 5vh auto; display: block; }}
        .modal-close {{ position: absolute; top: 16px; right: 28px; color: #fff; font-size: 2em; cursor: pointer; }}
"""
    html_content = html_content + extra_css + f"""
    </style>
</head>
<body>
    <div class="header">
        <h1>Alt Text Report</h1>
        <p><strong>Document:</strong> {html_module.escape(doc_name)}</p>
        <p><strong>Generated:</strong> {html_module.escape(formatted_date)}</p>
        <p><strong>Exported by:</strong> {html_module.escape(exporter)}</p>
    </div>
    <div class="stats-container">
        <div class="stat-box"><div class="number">{stats.get("total", 0)}</div><div class="label">Total Images</div></div>
        <div class="stat-box success"><div class="number">{stats.get("with_alt_text", 0)}</div><div class="label">With Alt Text</div></div>
        <div class="stat-box"><div class="number">{stats.get("decorative", 0)}</div><div class="label">Decorative</div></div>
        <div class="stat-box danger"><div class="number">{stats.get("no_alt_text", 0)}</div><div class="label">Missing Alt Text</div></div>
    </div>
    <div class="filters">
        <strong>Filter:</strong>
        <label><input type="checkbox" id="filter-all" checked onchange="filterImages()"> All</label>
        <label><input type="checkbox" id="filter-has-alt" checked onchange="filterImages()"> Has Alt Text</label>
        <label><input type="checkbox" id="filter-decorative" checked onchange="filterImages()"> Decorative</label>
        <label><input type="checkbox" id="filter-no-alt" checked onchange="filterImages()"> Missing Alt Text</label>
        <input type="text" id="search-box" placeholder="Search alt text..." oninput="filterImages()">
    </div>
    <div class="image-grid">
        {"".join(cards)}
    </div>
    <div id="imageModal" class="modal" onclick="closeModal()">
        <span class="modal-close" onclick="closeModal()">&times;</span>
        <img class="modal-content" id="modalImage" onclick="event.stopPropagation()">
    </div>
    <script>
        function openModal(img) {{
            const modal = document.getElementById('imageModal');
            const modalImg = document.getElementById('modalImage');
            const full = img.getAttribute('data-full-src') || '';
            if (full) {{
                modal.style.display = 'block';
                modalImg.src = full;
                return;
            }}
            const preview = img.getAttribute('data-preview');
            if (preview) {{
                window.location.href = preview;
                return;
            }}
            modal.style.display = 'block';
            modalImg.src = img.src;
        }}
        function closeModal() {{
            document.getElementById('imageModal').style.display = 'none';
        }}
        function filterImages() {{
            const filterAll = document.getElementById('filter-all').checked;
            const filterHasAlt = document.getElementById('filter-has-alt').checked;
            const filterDecorative = document.getElementById('filter-decorative').checked;
            const filterNoAlt = document.getElementById('filter-no-alt').checked;
            const searchText = document.getElementById('search-box').value.toLowerCase();
            document.querySelectorAll('.image-card').forEach(card => {{
                const status = card.dataset.status;
                const altText = card.dataset.alt || '';
                const showByFilter = filterAll ||
                    (filterHasAlt && status === 'has-alt') ||
                    (filterDecorative && status === 'decorative') ||
                    (filterNoAlt && status === 'no-alt');
                const showBySearch = !searchText || altText.includes(searchText);
                card.classList.toggle('hidden', !(showByFilter && showBySearch));
            }});
        }}
        document.getElementById('filter-all').addEventListener('change', function() {{
            if (this.checked) {{
                document.getElementById('filter-has-alt').checked = true;
                document.getElementById('filter-decorative').checked = true;
                document.getElementById('filter-no-alt').checked = true;
            }}
            filterImages();
        }});
    </script>
</body>
</html>
"""
    if html_path is not None:
        Path(html_path).write_text(html_content, encoding="utf-8")
    return html_content
