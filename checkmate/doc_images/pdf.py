"""PDF on-disc document image backend."""
from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

from checkmate.doc_images.api import (
    CAP_EXTENDED_DESCRIPTION,
    DocumentImageBackend,
    _cap_context_string,
    _context_params,
    load_image_result,
)

logger = logging.getLogger("fido")

def _pdf_backend_log(message: str, *args, level: int = logging.INFO) -> None:
    """Console + fido logger line for PDF image-backend diagnostics."""
    try:
        text = message % args if args else message
    except (TypeError, ValueError):
        text = message + (" " + " ".join(str(a) for a in args) if args else "")
    logger.log(level, "[PdfOnDiscBackend] %s", text)
    print(f"[Fido] PDF: {text}")


def _pdf_decode_string_value(raw: str) -> str:
    """Best-effort decode of a PDF string object value for logging/display."""
    if not raw:
        return ""
    text = raw.strip()
    if text.startswith("(") and text.endswith(")"):
        inner = text[1:-1]
        return (
            inner.replace("\\r", "\r")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\\(", "(")
            .replace("\\)", ")")
            .replace("\\\\", "\\")
        )
    if text.startswith("<") and text.endswith(">"):
        hex_body = text[1:-1].strip()
        if hex_body.upper().startswith("FEFF"):
            try:
                return bytes.fromhex(hex_body[4:]).decode("utf-16-be", errors="replace")
            except Exception:
                return text
        try:
            return bytes.fromhex(hex_body).decode("latin-1", errors="replace")
        except Exception:
            return text
    return text


def _pdf_alt_preview(raw: str, max_len: int = 60) -> str:
    decoded = _pdf_decode_string_value(raw)
    if not decoded:
        return "(empty)"
    one_line = decoded.replace("\r", " ").replace("\n", " ")
    if len(one_line) > max_len:
        return one_line[: max_len - 1] + "…"
    return one_line


class PdfOnDiscBackend(DocumentImageBackend):
    """Backend for tagged PDF on disc. /Figure /Alt via PyMuPDF. No decorative."""

    def __init__(self, dialog: Any = None, temp_dir: str | None = None):
        super().__init__(dialog, temp_dir=temp_dir)
        self._capabilities[CAP_EXTENDED_DESCRIPTION] = False  # PDF images: extended description not supported
        self._document_path = ""
        self._doc: Any = None
        self._images_data: List[dict] = []
        self._pdf_is_tagged = False
        self._pdf_match_stats: dict = {}

    def _pdf_target_label(self, rec: dict) -> str:
        if rec.get("struct_xref") is not None:
            return f"/Figure struct xref {rec['struct_xref']}"
        return f"image XObject xref {rec.get('image_xref')} (untagged — alt may not reach assistive tech)"

    @staticmethod
    def _pdf_pixmap_to_rgb(pix):
        """Convert gray/CMYK/other non-RGB pixmaps so PNG export works."""
        import fitz
        if pix is None:
            return None
        # n includes alpha; PNG needs gray or RGB samples (not CMYK / DeviceN).
        if pix.n - pix.alpha == 3:
            return pix
        return fitz.Pixmap(fitz.csRGB, pix)

    @staticmethod
    def _pdf_pixmap_is_mostly_blank(pix, white_ratio: float = 0.98) -> bool:
        """True if pixmap is empty or almost entirely white/transparent (common for mask bases / chart backgrounds)."""
        if pix is None or pix.width < 1 or pix.height < 1:
            return True
        try:
            samples = pix.samples
            if not samples:
                return True
            n = pix.n
            total = pix.width * pix.height
            if total <= 0:
                return True
            # Sample up to ~4k pixels for speed on large images
            step = max(1, total // 4000)
            blank = 0
            checked = 0
            for i in range(0, total, step):
                off = i * n
                if off + n > len(samples):
                    break
                checked += 1
                if pix.alpha and samples[off + n - 1] == 0:
                    blank += 1
                    continue
                # Treat near-white RGB/gray as blank
                if n - pix.alpha == 1:
                    if samples[off] >= 250:
                        blank += 1
                else:
                    r, g, b = samples[off], samples[off + 1], samples[off + 2]
                    if r >= 250 and g >= 250 and b >= 250:
                        blank += 1
            if checked == 0:
                return True
            return (blank / checked) >= white_ratio
        except Exception:
            return False

    def _pdf_render_bbox_png(self, doc, page_index: int, bbox, zoom: float = 2.0) -> Optional[bytes]:
        """Rasterize a page clip (fallback when the XObject alone is a white rect / stencil)."""
        import fitz
        if page_index is None or bbox is None:
            return None
        try:
            page = doc[page_index]
            clip = fitz.Rect(bbox)
            if clip.is_empty or clip.is_infinite:
                return None
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            return pix.tobytes("png")
        except Exception as e:
            logger.debug("PdfOnDiscBackend bbox render failed page %s: %s", page_index, e)
            return None

    def _pdf_xref_to_png_bytes(self, doc, xref: int, page_index=None, bbox=None) -> bytes:
        """
        Build PNG bytes for a PDF image XObject.
        Handles CMYK and soft masks (/SMask). Charts often store ink in a mask over a white/empty base —
        without applying the mask the preview is a blank white rectangle.
        Falls back to rendering the figure bbox when the extracted pixmap is still blank.
        """
        import fitz

        smask = 0
        try:
            info = doc.extract_image(xref)
            if info:
                smask = int(info.get("smask") or 0)
        except Exception:
            info = None

        pix = fitz.Pixmap(doc, xref)

        if smask > 0:
            try:
                # Convert base to RGB/gray before combining with mask (set_alpha / Pixmap(pix, mask)
                # require gray or RGB, not CMYK).
                pix = self._pdf_pixmap_to_rgb(pix)
                mask = fitz.Pixmap(doc, smask)
                if mask.n - mask.alpha != 1:
                    # Unexpected mask shape — try converting to gray
                    mask = fitz.Pixmap(fitz.csGRAY, mask)
                try:
                    # Modern constructor: image + mask → pixmap with alpha
                    pix = fitz.Pixmap(pix, mask)
                except Exception:
                    # Older API fallback
                    if not pix.alpha:
                        pix = fitz.Pixmap(pix, 1)
                    pix.set_alpha(mask.samples)
            except Exception as e:
                logger.debug(
                    "PdfOnDiscBackend: soft-mask apply failed xref=%s smask=%s: %s",
                    xref,
                    smask,
                    e,
                )
        else:
            pix = self._pdf_pixmap_to_rgb(pix)

        # Stencil / mask-only XObjects (no colorspace) and white chart backgrounds: show page clip instead
        if self._pdf_pixmap_is_mostly_blank(pix):
            rendered = self._pdf_render_bbox_png(doc, page_index, bbox)
            if rendered:
                return rendered

        if pix.n - pix.alpha != 3 and pix.n - pix.alpha != 1:
            pix = self._pdf_pixmap_to_rgb(pix)
        return pix.tobytes("png")

    def _pdf_log_image_record(self, index: int, rec: dict) -> None:
        page = int(rec.get("page_index", 0)) + 1
        img_xref = rec.get("image_xref")
        struct_xref = rec.get("struct_xref")
        alt_raw = rec.get("alt_text") or ""
        _pdf_backend_log(
            "Image %d: page %d, figure=%s, image_xref=%s, existing alt=%r (decoded: %s)",
            index + 1,
            page,
            struct_xref if struct_xref is not None else "none",
            img_xref,
            alt_raw[:80] if alt_raw else "",
            _pdf_alt_preview(alt_raw),
        )

    def get_document_display_name(self) -> str:
        return os.path.basename(self._document_path) if self._document_path else ""

    def open_document(self, source: Optional[str]) -> bool:
        if not source or not os.path.isfile(source):
            logger.info("[PdfOnDiscBackend] open_document: no source or file not found: %s", source)
            print(f"[Fido] PDF open: no file or path missing: {source!r}")
            return False
        try:
            import fitz
        except ImportError as e:
            logger.info("[PdfOnDiscBackend] open_document: PyMuPDF (fitz) not installed: %s", e)
            print("[Fido] PDF open: PyMuPDF (fitz) is not installed. Install with: pip install pymupdf")
            return False
        from collections import deque
        self._document_path = os.path.realpath(source)
        self._images_data = []
        try:
            doc = fitz.open(self._document_path)
        except Exception as e:
            logger.debug("PdfOnDiscBackend open: %s", e, exc_info=True)
            logger.info("[PdfOnDiscBackend] open_document: fitz.open failed: %s", e)
            print(f"[Fido] PDF open: could not open file: {e}")
            return False
        from checkmate.doc_images.pdf_struct_utils import pdf_has_struct_tree, pdf_struct_tree_root_xref

        self._pdf_is_tagged = pdf_has_struct_tree(doc)
        self._pdf_match_stats = {
            "figures_matched": 0,
            "figures_skipped": 0,
            "page_image_counts": {},
            "bbox_failures": 0,
        }

        if self._pdf_is_tagged:
            _pdf_backend_log("Tagged PDF detected (StructTreeRoot present)")
            # Tagged PDF: walk structure tree for /Figure elements
            root_xref = pdf_struct_tree_root_xref(doc)
            if root_xref is None:
                doc.close()
                _pdf_backend_log("Failed to read StructTreeRoot xref — cannot enumerate /Figure elements")
                return False
            page_xref_map = {doc[i].xref: i for i in range(len(doc))}
            page_image_queues = {}
            for i in range(len(doc)):
                page = doc[i]
                q = deque()
                for img in page.get_images(full=True):
                    try:
                        bbox = page.get_image_bbox(img)
                        q.append((img[0], bbox))
                    except Exception as e:
                        self._pdf_match_stats["bbox_failures"] += 1
                        logger.debug(
                            "PdfOnDiscBackend: get_image_bbox failed page %d image xref %s: %s",
                            i + 1,
                            img[0] if img else "?",
                            e,
                        )
                page_image_queues[i] = q
                self._pdf_match_stats["page_image_counts"][i] = len(q)
                if q:
                    _pdf_backend_log(
                        "Page %d: %d raster image(s) on page (pairing order = structure-tree /Figure order)",
                        i + 1,
                        len(q),
                    )
            results = []
            self._pdf_walk_figures(doc, root_xref, page_xref_map, page_image_queues, results, None)
            for struct_xref, page_index, image_xref, bbox, existing_alt in results:
                self._images_data.append({
                    "struct_xref": struct_xref,
                    "page_index": page_index,
                    "image_xref": image_xref,
                    "bbox": bbox,
                    "alt_text": existing_alt,
                })
                self._pdf_match_stats["figures_matched"] += 1
            unmatched_images = sum(len(q) for q in page_image_queues.values())
            skipped = self._pdf_match_stats["figures_skipped"]
            matched = self._pdf_match_stats["figures_matched"]
            _pdf_backend_log(
                "Tagged PDF pairing summary: %d /Figure matched, %d /Figure skipped (no page image left), "
                "%d page image(s) unused after pairing, %d bbox read failure(s)",
                matched,
                skipped,
                unmatched_images,
                self._pdf_match_stats["bbox_failures"],
            )
            if skipped:
                _pdf_backend_log(
                    "Hint: skipped figures usually mean structure-tree order ≠ drawing order on the page"
                )
            if unmatched_images:
                _pdf_backend_log(
                    "Hint: unused page images may indicate extra bitmaps not referenced by /Figure tags"
                )
        else:
            # Untagged PDF: collect all images from all pages (no structure tree)
            _pdf_backend_log(
                "Untagged PDF (no StructTreeRoot) — listing raster images by page; "
                "alt text will be written to image XObjects (may not be read by assistive tech)"
            )
            for page_index in range(len(doc)):
                page = doc[page_index]
                for img in page.get_images(full=True):
                    try:
                        bbox = page.get_image_bbox(img)
                        image_xref = img[0]
                        existing_alt = ""
                        try:
                            alt_type, alt_val = doc.xref_get_key(image_xref, "Alt")
                            if alt_type == "string":
                                existing_alt = alt_val
                        except Exception as e:
                            logger.debug(
                                "PdfOnDiscBackend: read Alt on image xref %s failed: %s",
                                image_xref,
                                e,
                            )
                        self._images_data.append({
                            "struct_xref": None,
                            "page_index": page_index,
                            "image_xref": image_xref,
                            "bbox": bbox,
                            "alt_text": existing_alt,
                        })
                    except Exception as e:
                        logger.debug(
                            "PdfOnDiscBackend: untagged page %d image skipped: %s",
                            page_index + 1,
                            e,
                        )

        self._doc = doc
        _pdf_backend_log("Opened %s — %d image(s) in utility list", self._document_path, len(self._images_data))
        for idx, rec in enumerate(self._images_data):
            self._pdf_log_image_record(idx, rec)
        return True

    def _pdf_walk_figures(self, doc, xref, page_xref_map, page_image_queues, results, inherited_page_xref):
        from checkmate.doc_images.pdf_struct_utils import pdf_get_page_xref_for_struct

        current_page = pdf_get_page_xref_for_struct(doc, xref, inherited_page_xref)
        try:
            s_type, s_val = doc.xref_get_key(xref, "S")
            if s_type == "name" and s_val == "/Figure":
                page_index = page_xref_map.get(current_page, 0) if current_page else 0
                existing_alt = ""
                try:
                    alt_type, alt_val = doc.xref_get_key(xref, "Alt")
                    if alt_type == "string":
                        existing_alt = alt_val
                except Exception:
                    pass
                queue = page_image_queues.get(page_index)
                if queue:
                    img_xref, bbox = queue.popleft()
                    results.append((xref, page_index, img_xref, bbox, existing_alt))
                    logger.debug(
                        "PdfOnDiscBackend: paired /Figure xref %s page %d → image xref %s",
                        xref,
                        page_index + 1,
                        img_xref,
                    )
                else:
                    self._pdf_match_stats["figures_skipped"] = (
                        int(self._pdf_match_stats.get("figures_skipped", 0)) + 1
                    )
                    _pdf_backend_log(
                        "/Figure struct xref %d on page %d: no remaining page image in pairing queue — figure skipped",
                        xref,
                        page_index + 1,
                    )
                for cx in self._pdf_get_child_xrefs(doc, xref):
                    self._pdf_walk_figures(doc, cx, page_xref_map, page_image_queues, results, current_page)
                return
        except Exception:
            pass
        for cx in self._pdf_get_child_xrefs(doc, xref):
            self._pdf_walk_figures(doc, cx, page_xref_map, page_image_queues, results, current_page)

    def _pdf_get_child_xrefs(self, doc, parent_xref):
        from checkmate.doc_images.pdf_struct_utils import pdf_get_child_struct_xrefs

        return pdf_get_child_struct_xrefs(doc, parent_xref)

    def close(self) -> None:
        if self._doc:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None

    def save_document(self) -> bool:
        if not self._doc or not self._document_path:
            _pdf_backend_log("save_document: nothing to save (document not open)", level=logging.WARNING)
            return True
        import fitz
        import tempfile

        path = self._document_path
        try:
            _pdf_backend_log("Saving PDF in place: %s", path)
            # Saving over the opened file requires incremental mode (PyMuPDF).
            try:
                self._doc.save(
                    path,
                    incremental=True,
                    encryption=fitz.PDF_ENCRYPT_KEEP,
                )
            except Exception as incr_err:
                # Some PDFs (or hosts like OneDrive) reject incremental; rewrite via temp beside the file.
                _pdf_backend_log(
                    "Incremental save failed (%s); rewriting via temp file",
                    incr_err,
                )
                tmp_path = None
                try:
                    fd, tmp_path = tempfile.mkstemp(
                        suffix=".pdf",
                        prefix=".fido_pdf_save_",
                        dir=os.path.dirname(path) or None,
                    )
                    os.close(fd)
                    self._doc.save(tmp_path, garbage=0, deflate=True)
                    self._doc.close()
                    self._doc = None
                    os.replace(tmp_path, path)
                    tmp_path = None
                    self._doc = fitz.open(path)
                finally:
                    if tmp_path and os.path.isfile(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
            _pdf_backend_log("Save completed successfully")
            return True
        except Exception as e:
            logger.warning("PdfOnDiscBackend save failed: %s", e, exc_info=True)
            _pdf_backend_log(
                "Save FAILED for %s: %s (file locked, read-only, or invalid after edits?)",
                path,
                e,
            )
            return False

    def get_image_count(self) -> int:
        return len(self._images_data)

    def load_image(self, index: int) -> Optional[dict]:
        if index < 0 or index >= len(self._images_data):
            return None
        rec = self._images_data[index]
        doc = self._doc
        try:
            image_bytes = self._pdf_xref_to_png_bytes(
                doc,
                rec["image_xref"],
                page_index=rec.get("page_index"),
                bbox=rec.get("bbox"),
            )
        except Exception as e:
            logger.warning(
                "PdfOnDiscBackend extract failed index %d image_xref %s: %s",
                index,
                rec.get("image_xref"),
                e,
                exc_info=True,
            )
            _pdf_backend_log(
                "Could not extract preview bitmap for image %d (xref %s): %s",
                index + 1,
                rec.get("image_xref"),
                e,
            )
            # Last resort: page clip when XObject extract fails entirely
            image_bytes = self._pdf_render_bbox_png(
                doc, rec.get("page_index"), rec.get("bbox")
            )
            if not image_bytes:
                return load_image_result("", rec["alt_text"], False)
        path = os.path.join(self._resolve_temp_dir(), f"pdf_image_{index}.png")
        try:
            with open(path, "wb") as f:
                f.write(image_bytes)
        except Exception as e:
            _pdf_backend_log(
                "Could not write PNG preview for image %d: %s",
                index + 1,
                e,
            )
            return load_image_result("", rec["alt_text"], False)
        return load_image_result(path, rec["alt_text"], False)

    def set_alt_text(self, index: int, text: str) -> bool:
        if index < 0 or index >= len(self._images_data):
            _pdf_backend_log("set_alt_text: invalid index %d", index, level=logging.WARNING)
            return False
        import fitz
        rec = self._images_data[index]
        target = self._pdf_target_label(rec)
        preview = (text or "").replace("\n", " ")[:80]
        if len(text or "") > 80:
            preview += "…"
        _pdf_backend_log(
            "Writing alt text to image %d (%s), %d chars: %r",
            index + 1,
            target,
            len(text or ""),
            preview,
        )
        try:
            get_pdf_str = getattr(fitz, "get_pdf_str", None)
            if get_pdf_str:
                pdf_val = get_pdf_str(text)
                encoding_note = "fitz.get_pdf_str"
            else:
                raw = (text or "").encode("utf-16-be")
                pdf_val = "<" + "FEFF" + raw.hex().upper() + ">"
                encoding_note = "UTF-16BE hex literal"
            xref = rec["struct_xref"] if rec.get("struct_xref") is not None else rec["image_xref"]
            self._doc.xref_set_key(xref, "Alt", pdf_val)
            rec["alt_text"] = text
            _pdf_backend_log(
                "Alt text write OK for image %d → xref %s (%s)",
                index + 1,
                xref,
                encoding_note,
            )
            return True
        except Exception as e:
            logger.warning(
                "PdfOnDiscBackend set_alt_text failed index %d xref %s: %s",
                index,
                rec.get("struct_xref") or rec.get("image_xref"),
                e,
                exc_info=True,
            )
            _pdf_backend_log(
                "Alt text write FAILED for image %d (%s): %s",
                index + 1,
                target,
                e,
            )
            return False

    def sync_embedded_image_from_path(self, index: int, path: str) -> bool:
        if index < 0 or index >= len(self._images_data) or not path or not os.path.isfile(path):
            return False
        rec = self._images_data[index]
        xref = rec.get("image_xref")
        if xref is None or self._doc is None:
            return False
        try:
            with open(path, "rb") as f:
                data = f.read()
            self._doc.update_stream(xref, data)
            return True
        except Exception as e:
            logger.debug("PdfOnDiscBackend sync_embedded_image_from_path: %s", e)
            _pdf_backend_log(
                "Could not replace embedded image %d (xref %s) from %s: %s",
                index + 1,
                xref,
                path,
                e,
            )
            return False

    def set_decorative(self, index: int, is_decorative: bool) -> bool:
        if is_decorative:
            return self.set_alt_text(index, "")
        return True

    def get_context(self, index: int) -> str:
        if index < 0 or index >= len(self._images_data):
            return ""
        import fitz
        rec = self._images_data[index]
        try:
            context_size, max_blocks_to_scan, max_chars = _context_params()

            page = self._doc[rec["page_index"]]
            blocks = page.get_text("blocks")
            figure_bbox = rec["bbox"]
            if not isinstance(figure_bbox, fitz.Rect):
                figure_bbox = fitz.Rect(figure_bbox) if figure_bbox is not None else fitz.Rect()

            # Score all text blocks by distance to the image (closest first). Include empty blocks
            # so we can skip them when counting (empty paragraphs don't count toward context_size).
            candidates = []
            for b in blocks:
                if len(b) < 6 or b[6] != 0:
                    continue
                block_rect = fitz.Rect(b[:4])
                text = (b[4] or "").strip()
                # Proximity scoring: prefer blocks that overlap horizontally
                x_overlap = max(0, min(figure_bbox.x1, block_rect.x1) - max(figure_bbox.x0, block_rect.x0))
                y_dist = min(abs(block_rect.y0 - figure_bbox.y1), abs(block_rect.y1 - figure_bbox.y0))
                score = y_dist * (0.5 if x_overlap > 0 else 1.0)
                candidates.append((score, text))
            candidates.sort(key=lambda x: x[0])

            # Take up to max_blocks_to_scan blocks; only non-empty blocks count toward context_size.
            parts = []
            for _, text in candidates[:max_blocks_to_scan]:
                if not text:
                    continue
                parts.append(text)
                if len(parts) >= context_size:
                    break

            result = "\n\n".join(parts)
            return _cap_context_string(result, max_chars)
        except Exception as e:
            logger.debug("PdfOnDiscBackend get_context: %s", e)
            return ""


# ---------------------------------------------------------------------------
# InDesign IDML on disc (zip + XML; ObjectExportOption CustomAltText)
# ---------------------------------------------------------------------------

_IDML_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".bmp", ".psd", ".eps", ".pdf",
})

# ObjectExportOption AltTextSourceType / ActualTextSourceType: modern IDML (e.g. DOM 21+) uses
# enumeration names in XML. Legacy four-char codes (sTCm) are not reliably round-tripped.
_IDML_ALT_SOURCE_CUSTOM = "SourceCustom"
_IDML_ALT_SOURCE_XML_STRUCTURE = "SourceXMLStructure"
# "Decorative Image" in Object Export Options (InDesign writes this + TagArtifact + $ID/ placeholders).
_IDML_ALT_SOURCE_DECORATIVE_IMAGE = "SourceDecorativeImage"
_IDML_PLACEHOLDER_ID = "$ID/"


def _idml_local_name(tag) -> str:
    if tag is None:
        return ""
    s = str(tag)
    return s.rsplit("}", 1)[-1] if "}" in s else s


def _idml_uri_points_to_image(uri: Optional[str]) -> bool:
    if not uri:
        return False
    u = uri.strip()
    if u.lower().startswith("file:"):
        u = u[5:]
    u = u.replace("\\", "/")
    base = os.path.basename(u)
    ext = os.path.splitext(base)[1].lower()
    return ext in _IDML_IMAGE_EXTENSIONS


def _idml_resolve_link_to_path(extracted_root: str, uri: str) -> str:
    u = (uri or "").strip()
    if u.lower().startswith("file:"):
        u = u[5:]
    u = u.replace("\\", "/").lstrip("/")
    # Relative to package root (e.g. Links/photo.jpg)
    return os.path.normpath(os.path.join(extracted_root, u.replace("/", os.sep)))


def _idml_resolve_link_uri_to_existing_file(extracted_root: str, uri: Optional[str]) -> Optional[str]:
    """
    Resolve LinkResourceURI to a readable file path.

    Handles file: URLs (absolute paths), and paths relative to the IDML package root.
    """
    if not uri:
        return None
    u = uri.strip()
    if not u or u.startswith("$ID"):
        return None
    if u.lower().startswith("file:"):
        try:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(u)
            path = unquote(parsed.path or "")
            if os.name == "nt" and path.startswith("/") and len(path) >= 3 and path[2] == ":":
                path = path[1:]
            path = os.path.normpath(path)
            if os.path.isfile(path):
                return path
        except Exception:
            logger.debug("IdmlOnDiscBackend: could not parse file URI %s", uri[:120], exc_info=True)
        u = u[5:].replace("\\", "/")
    else:
        u = u.replace("\\", "/")
    u = u.lstrip("/")
    if len(u) >= 3 and u[1] == ":" and u[0].isalpha():
        path = os.path.normpath(u.replace("/", os.sep))
        if os.path.isfile(path):
            return path
        return None
    path = os.path.normpath(os.path.join(extracted_root, u.replace("/", os.sep)))
    if os.path.isfile(path):
        return path
    return None


def _idml_is_probably_image_file(path: str) -> bool:
    """True if file starts with known raster/vector image signatures (or common types)."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    return _idml_is_image_magic(head)


def _idml_is_image_magic(data: bytes) -> bool:
    if not data or len(data) < 2:
        return False
    if data[:2] == b"\xff\xd8":
        return True
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"GIF":
        return True
    if data[:2] in (b"BM", b"II", b"MM"):
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if len(data) >= 4 and data[:4] == b"8BPS":
        return True
    if len(data) >= 4 and data[:4] == b"%PDF":
        return True
    if data[:2] == b"%!":
        return True
    return False


def _idml_ext_from_magic(data: bytes) -> str:
    if not data or len(data) < 4:
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data[:2] in (b"II", b"MM"):
        return ".tif"
    if data[:2] == b"BM":
        return ".bmp"
    if len(data) >= 4 and data[:4] == b"8BPS":
        return ".psd"
    if len(data) >= 4 and data[:4] == b"%PDF":
        return ".pdf"
    return ".png"


def _idml_ext_from_image_type_attr(image_el) -> str:
    name = (image_el.get("ImageTypeName") or "") or ""
    upper = name.upper()
    if "JPEG" in upper or "JPG" in upper:
        return ".jpg"
    if "PNG" in upper:
        return ".png"
    if "GIF" in upper:
        return ".gif"
    if "TIFF" in upper or "TIF" in upper:
        return ".tif"
    if "BMP" in upper:
        return ".bmp"
    if "PSD" in upper:
        return ".psd"
    if "PDF" in upper:
        return ".pdf"
    return ".png"


def _idml_find_link_element(image_el) -> Optional[Any]:
    for child in image_el:
        if _idml_local_name(child.tag) == "Link":
            return child
    return None


def _idml_extract_embedded_image_bytes(image_el) -> Optional[bytes]:
    """
    Embedded images: base64 in <Contents> (CDATA) under Image, sometimes <Data>.
    Multiple CDATA chunks are concatenated before decoding.
    """
    import base64
    import re

    parts: List[str] = []
    for elem in image_el.iterdescendants():
        ln = _idml_local_name(elem.tag).lower()
        if ln not in ("contents", "data", "binarydata"):
            continue
        t = (elem.text or "").strip()
        if t:
            parts.append(t)
    if not parts:
        return None
    raw = "".join(parts)
    raw = re.sub(r"\s+", "", raw)
    if len(raw) < 32:
        return None
    for pad in (0, 1, 2, 3):
        try:
            decoded = base64.b64decode(raw + "=" * pad, validate=False)
            if decoded and _idml_is_image_magic(decoded[: min(32, len(decoded))]):
                return decoded
        except Exception:
            continue
    try:
        decoded = base64.b64decode(raw, validate=False)
        if decoded and len(decoded) > 32:
            return decoded
    except Exception:
        pass
    return None


def _idml_find_object_export_option(frame) -> Optional[Any]:
    """First ObjectExportOption under a graphic frame (Rectangle, Oval, …)."""
    if frame is None:
        return None
    for el in frame.iterdescendants():
        if _idml_local_name(el.tag) == "ObjectExportOption":
            return el
    return None


def _idml_element_namespace(tag) -> Optional[str]:
    if not tag or "}" not in str(tag):
        return None
    return str(tag).split("}", 1)[0][1:]


def _idml_ensure_object_export_option(frame) -> Any:
    """Create ObjectExportOption if missing (first child of frame or of Properties)."""
    from lxml import etree

    existing = _idml_find_object_export_option(frame)
    if existing is not None:
        return existing
    ns = _idml_element_namespace(frame.tag)
    oeo_tag = "{%s}ObjectExportOption" % ns if ns else "ObjectExportOption"
    oeo = etree.Element(oeo_tag)
    props = None
    for child in frame:
        if _idml_local_name(child.tag) == "Properties":
            props = child
            break
    if props is not None:
        props.insert(0, oeo)
    else:
        frame.insert(0, oeo)
    return oeo


def _idml_normalize_alt_string(value: Optional[str]) -> str:
    """InDesign uses $ID/ as empty placeholder for custom alt/actual text."""
    if value is None:
        return ""
    t = str(value).strip()
    if not t or t == "$ID/" or t == "$ID":
        return ""
    return t


def _idml_get_alt_text_from_oeo(oeo) -> str:
    if oeo is None:
        return ""
    for key in ("CustomAltText", "customAltText"):
        v = oeo.get(key)
        if v is not None:
            return _idml_normalize_alt_string(v)
    return ""


def _idml_is_decorative_oeo(oeo) -> bool:
    """True for InDesign 'Decorative Image' + artifact tag (see IDML from InDesign export)."""
    if oeo is None:
        return False
    ap = (oeo.get("ApplyTagType") or oeo.get("applyTagType") or "").strip()
    if ap.lower() == "tagartifact":
        return True
    src = (oeo.get("AltTextSourceType") or oeo.get("altTextSourceType") or "").strip()
    return src.lower() == "sourcedecorativeimage"


def _idml_set_decorative_on_oeo(oeo, is_decorative: bool) -> None:
    """
    Decorative: match InDesign IDML — SourceDecorativeImage, TagArtifact, $ID/ placeholders.
    Not decorative: TagBasedOnObject and restore alt source when custom text exists.
    """
    if is_decorative:
        oeo.set("ApplyTagType", "TagArtifact")
        oeo.set("AltTextSourceType", _IDML_ALT_SOURCE_DECORATIVE_IMAGE)
        oeo.set("ActualTextSourceType", _IDML_ALT_SOURCE_XML_STRUCTURE)
        oeo.set("CustomAltText", _IDML_PLACEHOLDER_ID)
        oeo.set("CustomActualText", _IDML_PLACEHOLDER_ID)
    else:
        oeo.set("ApplyTagType", "TagBasedOnObject")
        alt = _idml_get_alt_text_from_oeo(oeo)
        if alt:
            oeo.set("AltTextSourceType", _IDML_ALT_SOURCE_CUSTOM)
            oeo.set("ActualTextSourceType", _IDML_ALT_SOURCE_CUSTOM)
        else:
            oeo.set("AltTextSourceType", _IDML_ALT_SOURCE_XML_STRUCTURE)
            oeo.set("ActualTextSourceType", _IDML_ALT_SOURCE_XML_STRUCTURE)
            for key in ("CustomAltText", "CustomActualText"):
                if key in oeo.attrib:
                    del oeo.attrib[key]


def _idml_set_alt_on_oeo(oeo, text: str) -> None:
    """Write alt text with alt source = Custom (matches InDesign Object Export Options)."""
    t = (text or "").strip()
    if t:
        oeo.set("AltTextSourceType", _IDML_ALT_SOURCE_CUSTOM)
        oeo.set("ActualTextSourceType", _IDML_ALT_SOURCE_CUSTOM)
        oeo.set("CustomAltText", t)
        oeo.set("ApplyTagType", "TagBasedOnObject")
    else:
        if "CustomAltText" in oeo.attrib:
            del oeo.attrib["CustomAltText"]


def _idml_inner_story_element(root: Any) -> Optional[Any]:
    """idPkg:Story wrapper has a child Story; some files use Story as root."""
    if root is None:
        return None
    if _idml_local_name(root.tag) == "Story":
        return root
    for child in root:
        if _idml_local_name(child.tag) == "Story":
            return child
    return None


def _idml_paragraph_style_ranges_in_order(story_el: Any) -> List[Any]:
    return [e for e in story_el.iter() if _idml_local_name(e.tag) == "ParagraphStyleRange"]


def _idml_plain_text_for_paragraph_range(psr: Any) -> str:
    """Plain text for one ParagraphStyleRange: Content + line breaks from Br."""
    parts: List[str] = []
    for el in psr.iter():
        ln = _idml_local_name(el.tag)
        if ln == "Content":
            if el.text:
                parts.append(el.text)
        elif ln == "Br":
            parts.append("\n")
    return "".join(parts).strip()


def _idml_nearest_paragraph_style_range(frame: Any) -> Optional[Any]:
    cur = frame
    while cur is not None:
        if _idml_local_name(cur.tag) == "ParagraphStyleRange":
            return cur
        cur = cur.getparent()
    return None


def _idml_context_from_frame(frame: Any) -> str:
    """
    Surrounding story text for vision prompts (Word-style: non-empty paragraphs
    before/after containing block). Empty for spread-only assets (no Story root).
    """
    try:
        tree_root = frame.getroottree().getroot()
    except Exception:
        return ""
    story_el = _idml_inner_story_element(tree_root)
    if story_el is None:
        return ""
    containing = _idml_nearest_paragraph_style_range(frame)
    if containing is None:
        return ""
    all_psr = _idml_paragraph_style_ranges_in_order(story_el)
    try:
        idx = all_psr.index(containing)
    except ValueError:
        return ""

    context_size, _, max_chars = _context_params()

    before_texts: List[str] = []
    i = idx - 1
    while i >= 0 and len(before_texts) < context_size:
        t = _idml_plain_text_for_paragraph_range(all_psr[i])
        if t:
            before_texts.append(t)
        i -= 1
    before_texts.reverse()

    after_texts: List[str] = []
    i = idx + 1
    while i < len(all_psr) and len(after_texts) < context_size:
        t = _idml_plain_text_for_paragraph_range(all_psr[i])
        if t:
            after_texts.append(t)
        i += 1

    cur = _idml_plain_text_for_paragraph_range(containing)
    middle: List[str] = [cur] if cur else []
    parts = before_texts + middle + after_texts
    if not parts:
        return ""
    return _cap_context_string("\n\n".join(parts), max_chars)


def _idml_build_image_record(
    xml_path: str,
    tree: Any,
    extracted_root: str,
    image_el: Any,
) -> Optional[dict]:
    """
    One Image per record: prefer linked file on disk; else embedded base64 in XML.
    """
    frame = image_el.getparent()
    if frame is None:
        return None
    link_el = _idml_find_link_element(image_el)
    oeo = _idml_find_object_export_option(frame)
    alt = _idml_get_alt_text_from_oeo(oeo)
    is_dec = _idml_is_decorative_oeo(oeo)

    linked_path = None
    if link_el is not None:
        uri = link_el.get("LinkResourceURI") or link_el.get("linkResourceURI")
        if uri and not str(uri).strip().startswith("$ID"):
            linked_path = _idml_resolve_link_uri_to_existing_file(extracted_root, uri)
            if linked_path and _idml_is_probably_image_file(linked_path):
                return {
                    "kind": "linked",
                    "xml_path": xml_path,
                    "tree": tree,
                    "frame": frame,
                    "image_el": image_el,
                    "link_el": link_el,
                    "image_path": linked_path,
                    "embedded_bytes": None,
                    "embedded_ext": None,
                    "alt_text": alt,
                    "is_decorative": is_dec,
                }
            # Known extension but wrong magic (e.g. CMYK JPEG) — still use file
            if linked_path and _idml_uri_points_to_image(uri):
                return {
                    "kind": "linked",
                    "xml_path": xml_path,
                    "tree": tree,
                    "frame": frame,
                    "image_el": image_el,
                    "link_el": link_el,
                    "image_path": linked_path,
                    "embedded_bytes": None,
                    "embedded_ext": None,
                    "alt_text": alt,
                    "is_decorative": is_dec,
                }

    emb = _idml_extract_embedded_image_bytes(image_el)
    if emb and len(emb) > 16:
        ext = _idml_ext_from_magic(emb[: min(64, len(emb))])
        typ = _idml_ext_from_image_type_attr(image_el)
        if ext == ".png" and typ != ".png":
            ext = typ
        return {
            "kind": "embedded",
            "xml_path": xml_path,
            "tree": tree,
            "frame": frame,
            "image_el": image_el,
            "link_el": link_el,
            "image_path": None,
            "embedded_bytes": emb,
            "embedded_ext": ext,
            "alt_text": alt,
            "is_decorative": is_dec,
        }

    return None
