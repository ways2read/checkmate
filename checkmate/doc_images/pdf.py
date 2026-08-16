"""PDF on-disc document image backend."""
from __future__ import annotations

import logging
import os
import re
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
    line = f"[Fido] PDF: {text}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


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

    @staticmethod
    def _pdf_image_placement(page, img):
        """Return (bbox, transform) for a placed image in page display space.

        *bbox* is suitable for ``page.get_pixmap(clip=bbox)`` and already
        accounts for page ``/Rotate``. *transform* is the image CTM from
        ``get_image_rects(transform=True)`` / ``get_image_info`` (unrotated
        page space), or None.
        """
        import fitz

        bbox = None
        transform = None
        raw_rect = None
        xref = img[0] if isinstance(img, (tuple, list)) and img else img

        try:
            rects = page.get_image_rects(img, transform=True)
        except Exception:
            rects = None
        if rects:
            first = rects[0]
            if isinstance(first, (tuple, list)) and len(first) >= 2:
                raw_rect, transform = first[0], first[1]
            else:
                raw_rect = first

        try:
            bbox = page.get_image_bbox(img)
        except Exception:
            bbox = None

        if (bbox is None or getattr(bbox, "is_empty", False) or getattr(bbox, "is_infinite", False)) and raw_rect is not None:
            try:
                bbox = fitz.Rect(raw_rect) * page.rotation_matrix
            except Exception:
                try:
                    bbox = fitz.Rect(raw_rect)
                except Exception:
                    bbox = None

        if bbox is None or transform is None:
            try:
                infos = page.get_image_info(xrefs=True)
            except Exception:
                infos = []
            for info in infos:
                if xref is not None and info.get("xref") != xref:
                    continue
                if transform is None and info.get("transform") is not None:
                    try:
                        transform = fitz.Matrix(info["transform"])
                    except Exception:
                        transform = info["transform"]
                if (
                    (bbox is None or getattr(bbox, "is_empty", False) or getattr(bbox, "is_infinite", False))
                    and info.get("bbox")
                ):
                    try:
                        info_rect = fitz.Rect(info["bbox"])
                        bbox = info_rect * page.rotation_matrix
                    except Exception:
                        try:
                            bbox = fitz.Rect(info["bbox"])
                        except Exception:
                            pass
                break

        if bbox is not None:
            try:
                bbox = fitz.Rect(bbox)
                if bbox.is_empty or bbox.is_infinite:
                    bbox = None
            except Exception:
                bbox = None
        if bbox is not None and not PdfOnDiscBackend._pdf_rect_visible_on_page(page, bbox):
            bbox = None
        return bbox, transform

    @staticmethod
    def _pdf_clip_rect(page, bbox, transform=None):
        """Display-space clip rect for ``page.get_pixmap``, or None."""
        import fitz

        clip = None
        if bbox is not None:
            try:
                clip = fitz.Rect(bbox)
            except Exception:
                clip = None
        if (clip is None or clip.is_empty or clip.is_infinite) and transform is not None:
            try:
                clip = fitz.Rect(0, 0, 1, 1) * fitz.Matrix(transform)
                if page.rotation:
                    clip = clip * page.rotation_matrix
            except Exception:
                clip = None
        if clip is None or clip.is_empty or clip.is_infinite:
            return None
        try:
            clip = clip & page.rect
        except Exception:
            pass
        if clip.is_empty or clip.is_infinite:
            return None
        return clip

    @staticmethod
    def _pdf_rect_visible_on_page(page, bbox, min_edge: float = 4.0) -> bool:
        """True when *bbox* intersects the visible page by a usable amount."""
        import fitz
        if bbox is None:
            return False
        try:
            rect = fitz.Rect(bbox)
            if rect.is_empty or rect.is_infinite:
                return False
            visible = rect & page.rect
            return (
                not visible.is_empty
                and visible.width >= min_edge
                and visible.height >= min_edge
            )
        except Exception:
            return False

    @staticmethod
    def _pdf_overlap_score(a, b) -> float:
        """IoU of two rects; 0 if they do not overlap."""
        import fitz
        try:
            ra, rb = fitz.Rect(a), fitz.Rect(b)
            inter = ra & rb
            if inter.is_empty:
                return 0.0
            union = (ra.get_area() + rb.get_area()) - inter.get_area()
            if union <= 0:
                return 0.0
            return inter.get_area() / union
        except Exception:
            return 0.0

    # Near-full-page rasters are often a composite (photo + logo + chrome).
    # Pair compact /Figure tags to similarly sized placements first.
    # 0.70 catches photos with margins that 0.85 treated as "content".
    _PDF_PAGE_COVER_THRESHOLD = 0.70
    _PDF_FIGURE_CROP_RATIO = 0.85
    _PDF_MIN_SIZE_SIM = 0.12
    _PDF_LOGO_COVER = 0.12
    _LOGO_ALT_RE = re.compile(
        r"\b(logo|wordmark|logotype|icon)\b",
        re.I,
    )

    def _pdf_alt_looks_like_logo(self, alt: str) -> bool:
        decoded = _pdf_decode_string_value(alt or "")
        return bool(self._LOGO_ALT_RE.search(decoded))

    @staticmethod
    def _pdf_rect_area(bbox) -> float:
        import fitz
        try:
            rect = fitz.Rect(bbox)
            if rect.is_empty or rect.is_infinite:
                return 0.0
            return float(rect.get_area())
        except Exception:
            return 0.0

    def _pdf_placement_page_coverage(self, bbox, page) -> float:
        """Fraction of the visible page covered by *bbox* (0–1)."""
        if page is None or bbox is None:
            return 0.0
        try:
            page_area = self._pdf_rect_area(page.rect)
            if page_area <= 0:
                return 0.0
            import fitz
            visible = fitz.Rect(bbox) & page.rect
            return self._pdf_rect_area(visible) / page_area
        except Exception:
            return 0.0

    def _pdf_bbox_is_page_sized(self, bbox, page) -> bool:
        return self._pdf_placement_page_coverage(bbox, page) >= self._PDF_PAGE_COVER_THRESHOLD

    def _pdf_placement_match_score(self, figure_bbox, place_bbox, page=None) -> float:
        """IoU weighted by size similarity so a logo does not match a page raster."""
        iou = self._pdf_overlap_score(figure_bbox, place_bbox)
        if iou <= 0:
            return 0.0
        fig_area = self._pdf_rect_area(figure_bbox)
        place_area = self._pdf_rect_area(place_bbox)
        if fig_area <= 0 or place_area <= 0:
            return iou
        size_sim = min(fig_area, place_area) / max(fig_area, place_area)
        if size_sim < self._PDF_MIN_SIZE_SIM:
            return 0.0
        if page is not None and self._pdf_bbox_is_page_sized(place_bbox, page):
            fig_cover = self._pdf_placement_page_coverage(figure_bbox, page)
            if fig_cover < self._PDF_LOGO_COVER:
                return 0.0
        return iou * (0.25 + 0.75 * size_sim)

    def _pdf_preview_clip(self, place_bbox, figure_bbox):
        """Clip for ``page.get_pixmap``: crop a large XObject, never expand a small one.

        Figure Layout BBox is a useful crop when the XObject is larger than the
        tagged figure (full-page raster + small logo /A BBox). Using that BBox
        when it is *larger* than the placement pulls in overlaid text and other
        graphics from the page composite.
        """
        if place_bbox is None:
            return figure_bbox
        if figure_bbox is None:
            return place_bbox
        import fitz
        try:
            place = fitz.Rect(place_bbox)
            fig = fitz.Rect(figure_bbox)
        except Exception:
            return place_bbox
        if (
            self._pdf_rect_area(fig) < self._pdf_rect_area(place) * self._PDF_FIGURE_CROP_RATIO
            and self._pdf_overlap_score(fig, place) > 0
        ):
            return fig
        return place

    @staticmethod
    def _pdf_parse_rect_array(raw: str):
        nums: List[float] = []
        for tok in (raw or "").replace("[", " ").replace("]", " ").split():
            try:
                nums.append(float(tok))
            except ValueError:
                continue
        if len(nums) < 4:
            return None
        return nums[:4]

    def _pdf_layout_bbox_from_attr_xref(self, doc, attr_xref, page):
        """Layout /BBox on an attribute object, converted to PyMuPDF page space."""
        import fitz
        try:
            b_type, b_val = doc.xref_get_key(attr_xref, "BBox")
        except Exception:
            return None
        if b_type != "array":
            return None
        nums = self._pdf_parse_rect_array(b_val or "")
        if not nums:
            return None
        try:
            pdf_rect = fitz.Rect(nums[0], nums[1], nums[2], nums[3])
            display = pdf_rect * page.transformation_matrix
            if display.is_empty or display.is_infinite:
                return None
            return display
        except Exception:
            return None

    def _pdf_figure_layout_bbox(self, doc, struct_xref, page):
        """Screen-reader highlight rect for a /Figure (/A Layout BBox), or None."""
        import fitz
        try:
            a_type, a_val = doc.xref_get_key(struct_xref, "A")
        except Exception:
            a_type, a_val = "null", None
        candidates = []
        if a_type == "xref" and a_val:
            try:
                candidates.append(int(str(a_val).split()[0]))
            except (TypeError, ValueError):
                pass
        elif a_type == "array" and a_val:
            import re
            for ref in re.findall(r"(\d+)\s+\d+\s+R", a_val):
                try:
                    candidates.append(int(ref))
                except ValueError:
                    pass
        for ax in candidates:
            bbox = self._pdf_layout_bbox_from_attr_xref(doc, ax, page)
            if bbox is not None and self._pdf_rect_visible_on_page(page, bbox):
                return bbox
        try:
            b_type, b_val = doc.xref_get_key(struct_xref, "BBox")
            if b_type == "array":
                nums = self._pdf_parse_rect_array(b_val or "")
                if nums:
                    display = fitz.Rect(nums[0], nums[1], nums[2], nums[3]) * page.transformation_matrix
                    if self._pdf_rect_visible_on_page(page, display):
                        return display
        except Exception:
            pass
        return None

    @staticmethod
    def _pdf_valid_xref(xref) -> bool:
        try:
            return int(xref) > 0
        except (TypeError, ValueError):
            return False

    def _pdf_resolve_placement_xref(self, page, xref, bbox):
        """Return a usable image XObject xref, or None.

        ``get_image_info(xrefs=True)`` sometimes reports xref 0 (inline images
        or Form-XObject draws). Isolated preview then fails with ``bad xref``
        and falls back to a page clip that includes overlays.
        """
        if self._pdf_valid_xref(xref):
            return int(xref)
        if bbox is None:
            return None
        best_xref = None
        best_score = 0.0
        try:
            images = page.get_images(full=True) or []
        except Exception:
            images = []
        for img in images:
            img_xref = img[0] if img else None
            if not self._pdf_valid_xref(img_xref):
                continue
            try:
                place, _transform = self._pdf_image_placement(page, img)
            except Exception:
                continue
            if place is None:
                continue
            score = self._pdf_placement_match_score(bbox, place, page)
            if score > best_score:
                best_score = score
                best_xref = int(img_xref)
        return best_xref if best_score > 0 else None

    def _pdf_page_placements(self, page) -> List[tuple]:
        """On-page image placements: (xref, display_bbox, transform)."""
        import fitz
        placements: List[tuple] = []
        seen_xrefs: set[int] = set()
        try:
            infos = page.get_image_info(xrefs=True) or []
        except Exception:
            infos = []
        for info in infos:
            raw_xref = info.get("xref")
            if not info.get("bbox"):
                continue
            try:
                raw = fitz.Rect(info["bbox"])
                bbox = raw * page.rotation_matrix if page.rotation else raw
                transform = (
                    fitz.Matrix(info["transform"])
                    if info.get("transform") is not None
                    else None
                )
            except Exception:
                continue
            if not self._pdf_rect_visible_on_page(page, bbox):
                continue
            xref = self._pdf_resolve_placement_xref(page, raw_xref, bbox)
            if xref is None:
                logger.debug(
                    "PdfOnDiscBackend: skipped placement with unusable xref=%r bbox=%s",
                    raw_xref,
                    bbox,
                )
                continue
            placements.append((xref, bbox, transform))
            seen_xrefs.add(int(xref))
        # get_image_info often lists the page raster and omits a small logo
        # XObject (or reports xref 0). Merge get_images() so the logo stays
        # in the pairing queue.
        try:
            images = page.get_images(full=True) or []
        except Exception:
            images = []
        for img in images:
            try:
                xref = img[0] if img else None
                if not self._pdf_valid_xref(xref) or int(xref) in seen_xrefs:
                    continue
                bbox, transform = self._pdf_image_placement(page, img)
                if bbox is None:
                    continue
                placements.append((int(xref), bbox, transform))
                seen_xrefs.add(int(xref))
            except Exception:
                self._pdf_match_stats["bbox_failures"] = (
                    int(self._pdf_match_stats.get("bbox_failures", 0)) + 1
                )
        return placements

    def _pdf_pick_placement_index(
        self, queue: list, figure_bbox, page=None, alt_text: str = ""
    ) -> Optional[int]:
        """Choose a page-image placement for a /Figure.

        A small figure that sits inside a full-page raster has a non-zero IoU
        with that backdrop. Prefer a similarly sized placement, and do not
        fall back to queue[0] when that slot is a near-full-page composite.

        When the tagged alt names a logo/icon and a compact XObject remains,
        take that even if /A BBox is the photo (common tagging error).
        """
        if not queue:
            return None

        def _is_backdrop(index: int) -> bool:
            return self._pdf_bbox_is_page_sized(queue[index][1], page)

        compact = [i for i in range(len(queue)) if not _is_backdrop(i)]
        fallback = compact[0] if compact else 0

        if compact and self._pdf_alt_looks_like_logo(alt_text):
            return min(compact, key=lambda i: self._pdf_rect_area(queue[i][1]))

        if figure_bbox is None or self._pdf_bbox_is_page_sized(figure_bbox, page):
            return fallback

        best_i = None
        best_score = 0.0
        for i, (_xref, bbox, _transform) in enumerate(queue):
            score = self._pdf_placement_match_score(figure_bbox, bbox, page)
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is not None and best_score > 0:
            return best_i
        return fallback

    @staticmethod
    def _pdf_matrix_ortho_rotation(transform) -> Optional[int]:
        """Axis-aligned rotation encoded by an image CTM, or None if skewed."""
        if transform is None:
            return 0
        import math
        import fitz

        try:
            matrix = fitz.Matrix(transform)
        except Exception:
            return None
        angle = math.degrees(math.atan2(matrix.b, matrix.a))
        if angle < 0:
            angle += 360
        snapped = int(round(angle / 90.0)) * 90 % 360
        residual = min(abs(angle - snapped), abs(angle - snapped - 360), abs(angle - snapped + 360))
        if residual > 8:
            return None
        return snapped

    def _pdf_display_insert_rotation(self, page, transform) -> Optional[int]:
        """Rotation to apply when drawing the raw XObject into a display-space rect.

        ``atan2(b, a)`` of the image CTM is the +x axis in y-down page space.
        PyMuPDF ``insert_image(..., rotate=)`` uses the opposite convention
        (``insert_image(rotate=90)`` yields CTM angle 270).
        """
        ctm_rot = self._pdf_matrix_ortho_rotation(transform)
        if ctm_rot is None:
            return None
        insert_rot = (360 - ctm_rot) % 360
        return (insert_rot + int(getattr(page, "rotation", 0) or 0)) % 360

    @staticmethod
    def _pdf_contain_rect(dest, src_w: float, src_h: float):
        """Largest rectangle inside *dest* that keeps *src* aspect ratio."""
        import fitz

        dest = fitz.Rect(dest)
        if src_w <= 0 or src_h <= 0 or dest.is_empty or dest.is_infinite:
            return dest
        scale = min(dest.width / src_w, dest.height / src_h)
        rw = src_w * scale
        rh = src_h * scale
        x0 = dest.x0 + (dest.width - rw) / 2.0
        y0 = dest.y0 + (dest.height - rh) / 2.0
        return fitz.Rect(x0, y0, x0 + rw, y0 + rh)

    def _pdf_pixmap_rotated(self, pix, rot: int):
        """Return *pix* rotated clockwise by 0/90/180/270 without stretching."""
        import fitz

        rot = int(rot) % 360
        if rot == 0 or pix is None:
            return pix
        if rot not in (90, 180, 270):
            return pix
        src_w, src_h = pix.width, pix.height
        dest_w, dest_h = (src_h, src_w) if rot in (90, 270) else (src_w, src_h)
        try:
            stream = pix.tobytes("png")
        except Exception:
            return pix
        tmp = fitz.open()
        try:
            page = tmp.new_page(width=dest_w, height=dest_h)
            page.insert_image(
                page.rect,
                stream=stream,
                rotate=rot,
                keep_proportion=True,
            )
            out = page.get_pixmap(alpha=False)
            rgb = self._pdf_pixmap_to_rgb(out)
            return rgb if rgb is not None else out
        except Exception:
            return pix
        finally:
            try:
                tmp.close()
            except Exception:
                pass

    def _pdf_extract_xobject_pixmap(self, doc, xref: int):
        """Raw XObject as RGB/gray pixmap with /SMask applied, or None if blank."""
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
                pix = self._pdf_pixmap_to_rgb(pix)
                mask = fitz.Pixmap(doc, smask)
                if mask.n - mask.alpha != 1:
                    mask = fitz.Pixmap(fitz.csGRAY, mask)
                try:
                    pix = fitz.Pixmap(pix, mask)
                except Exception:
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

        if self._pdf_pixmap_is_mostly_blank(pix):
            return None
        if pix.n - pix.alpha != 3 and pix.n - pix.alpha != 1:
            pix = self._pdf_pixmap_to_rgb(pix)
        return pix

    def _pdf_render_xobject_only_png(
        self,
        doc,
        xref: int,
        page_index: int,
        bbox,
        transform=None,
        place_bbox=None,
        zoom: float = 2.0,
    ) -> Optional[bytes]:
        """Rasterize only the tagged XObject, in on-page orientation.

        Draws the extracted bitmap onto a blank page with the placement
        rotation (CTM + ``/Rotate``). Overlaid text and neighboring images
        are omitted so the vision model sees the tagged item, not the page
        composite. Skewed CTMs and blank/mask-only XObjects return None so
        the caller can fall back to a page clip.
        """
        import fitz

        if page_index is None:
            return None
        try:
            page = doc[page_index]
        except Exception:
            return None
        place = place_bbox if place_bbox is not None else bbox
        if not self._pdf_valid_xref(xref):
            xref = self._pdf_resolve_placement_xref(page, xref, place)
            if xref is None:
                return None
        place_clip = self._pdf_clip_rect(page, place, transform=transform)
        if place_clip is None:
            return None
        rot = self._pdf_display_insert_rotation(page, transform)
        if rot is None:
            return None
        raw = self._pdf_extract_xobject_pixmap(doc, xref)
        if raw is None:
            return None
        oriented = self._pdf_pixmap_rotated(raw, rot)
        if oriented is None:
            return None
        try:
            stream = oriented.tobytes("png")
        except Exception:
            return None
        # Rotate in pixel space first. insert_image(rotate=…, keep_proportion=False)
        # stretches the *unrotated* bitmap into the already-rotated display
        # rect and squashes photos (and can turn a full-page raster into a
        # flattened "logo").
        fit = self._pdf_contain_rect(
            place_clip, oriented.width, oriented.height
        )
        scratch = fitz.open()
        try:
            scratch_page = scratch.new_page(
                width=page.rect.width, height=page.rect.height
            )
            scratch_page.insert_image(
                fit,
                stream=stream,
                rotate=0,
                keep_proportion=True,
            )
            figure_clip = self._pdf_clip_rect(page, bbox, transform=transform)
            render_clip = fit
            if (
                figure_clip is not None
                and self._pdf_rect_area(figure_clip)
                < self._pdf_rect_area(place_clip) * self._PDF_FIGURE_CROP_RATIO
                and self._pdf_overlap_score(figure_clip, fit) > 0
            ):
                inter = fitz.Rect(figure_clip) & fitz.Rect(fit)
                if not inter.is_empty:
                    render_clip = inter
            pix = scratch_page.get_pixmap(
                matrix=fitz.Matrix(zoom, zoom), clip=render_clip, alpha=False
            )
            pix = self._pdf_pixmap_to_rgb(pix)
            if pix is None or self._pdf_pixmap_is_mostly_blank(pix):
                return None
            return pix.tobytes("png")
        except Exception as e:
            logger.debug(
                "PdfOnDiscBackend isolated XObject render failed xref=%s: %s",
                xref,
                e,
            )
            return None
        finally:
            try:
                scratch.close()
            except Exception:
                pass

    def _pdf_render_bbox_png(
        self, doc, page_index: int, bbox, zoom: float = 2.0, transform=None
    ) -> Optional[bytes]:
        """Rasterize the page clip so the PNG matches Acrobat/Preview (CTM + /Rotate)."""
        import fitz
        if page_index is None:
            return None
        try:
            page = doc[page_index]
            clip = self._pdf_clip_rect(page, bbox, transform=transform)
            if clip is None:
                return None
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            pix = self._pdf_pixmap_to_rgb(pix)
            return pix.tobytes("png")
        except Exception as e:
            logger.debug("PdfOnDiscBackend bbox render failed page %s: %s", page_index, e)
            return None

    def _pdf_page_backdrop_xrefs(self, page) -> set:
        """Image XRefs whose on-page placement covers most of the page."""
        found: set[int] = set()
        try:
            infos = page.get_image_info(xrefs=True) or []
        except Exception:
            infos = []
        for info in infos:
            xref = info.get("xref")
            if not self._pdf_valid_xref(xref) or not info.get("bbox"):
                continue
            if self._pdf_bbox_is_page_sized(info["bbox"], page):
                found.add(int(xref))
        try:
            for img in page.get_images(full=True) or []:
                xref = img[0] if img else None
                if not self._pdf_valid_xref(xref) or int(xref) in found:
                    continue
                bbox, _transform = self._pdf_image_placement(page, img)
                if bbox is not None and self._pdf_bbox_is_page_sized(bbox, page):
                    found.add(int(xref))
        except Exception:
            pass
        return found

    def _pdf_render_clip_hiding_backdrops(
        self, doc, page_index: int, clip_bbox, zoom: float = 2.0
    ) -> Optional[bytes]:
        """Page clip with full-page rasters removed (vector logo over a photo)."""
        import fitz

        if page_index is None or clip_bbox is None:
            return None
        tmp = fitz.open()
        try:
            tmp.insert_pdf(doc, from_page=int(page_index), to_page=int(page_index))
            tpage = tmp[0]
            for info in tpage.get_image_info(xrefs=True) or []:
                xref = info.get("xref")
                if not self._pdf_valid_xref(xref) or not info.get("bbox"):
                    continue
                if not self._pdf_bbox_is_page_sized(info["bbox"], tpage):
                    continue
                try:
                    tpage.delete_image(int(xref))
                except Exception:
                    continue
            clip = self._pdf_clip_rect(tpage, clip_bbox)
            if clip is None:
                return None
            pix = tpage.get_pixmap(
                matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False
            )
            pix = self._pdf_pixmap_to_rgb(pix)
            if pix is None or self._pdf_pixmap_is_mostly_blank(pix):
                return None
            return pix.tobytes("png")
        except Exception as e:
            logger.debug(
                "PdfOnDiscBackend overlay-only clip failed page %s: %s",
                page_index,
                e,
            )
            return None
        finally:
            try:
                tmp.close()
            except Exception:
                pass

    def _pdf_clip_is_compact(self, clip_bbox, page) -> bool:
        return clip_bbox is not None and not self._pdf_bbox_is_page_sized(clip_bbox, page)

    def _pdf_preview_png_bytes(
        self,
        doc,
        xref: int,
        page_index=None,
        bbox=None,
        transform=None,
        place_bbox=None,
    ) -> Optional[bytes]:
        """PNG of the tagged image in on-page orientation, without page chrome.

        A compact /Figure (logo) is often only vector art sitting on a
        full-page photo. Isolated render of that photo is the boy, not the
        logo. Hide page-sized rasters and clip to the figure instead.

        Otherwise prefer an isolated XObject render (CTM + ``/Rotate``).
        Fall back to a page clip, then the raw XObject bitmap.
        """
        try:
            page = doc[page_index] if page_index is not None else None
        except Exception:
            page = None
        clip_bbox = bbox if bbox is not None else place_bbox
        if (
            page is not None
            and self._pdf_clip_is_compact(clip_bbox, page)
            and (
                not self._pdf_valid_xref(xref)
                or int(xref) in self._pdf_page_backdrop_xrefs(page)
            )
        ):
            try:
                overlay = self._pdf_render_clip_hiding_backdrops(
                    doc, page_index, clip_bbox
                )
                if overlay:
                    return overlay
            except Exception as e:
                logger.debug(
                    "PdfOnDiscBackend overlay preview failed xref=%s: %s", xref, e
                )
        try:
            isolated = self._pdf_render_xobject_only_png(
                doc,
                xref,
                page_index,
                bbox,
                transform=transform,
                place_bbox=place_bbox,
            )
            if isolated:
                return isolated
        except Exception as e:
            logger.debug(
                "PdfOnDiscBackend isolated preview failed xref=%s: %s", xref, e
            )
        try:
            rendered = self._pdf_render_bbox_png(
                doc, page_index, bbox, transform=transform
            )
            if rendered:
                return rendered
        except Exception as e:
            logger.debug(
                "PdfOnDiscBackend on-page preview failed xref=%s: %s", xref, e
            )
        try:
            return self._pdf_xref_to_png_bytes(
                doc, xref, page_index=page_index, bbox=bbox, transform=transform
            )
        except Exception as e:
            logger.warning(
                "PdfOnDiscBackend XObject extract failed xref=%s: %s",
                xref,
                e,
                exc_info=True,
            )
            return None

    def _pdf_xref_to_png_bytes(
        self, doc, xref: int, page_index=None, bbox=None, transform=None
    ) -> bytes:
        """
        Build PNG bytes from the raw PDF image XObject (embedded bitmap).

        Ignores placement CTM / page rotation — use ``_pdf_preview_png_bytes``
        for UI and AI. Handles CMYK and soft masks (/SMask). Charts often store
        ink in a mask over a white/empty base — without applying the mask the
        extract is a blank white rectangle. Falls back to rendering the figure
        bbox when the extracted pixmap is still blank.
        """
        pix = self._pdf_extract_xobject_pixmap(doc, xref)
        if pix is None:
            rendered = self._pdf_render_bbox_png(
                doc, page_index, bbox, transform=transform
            )
            if rendered:
                return rendered
            import fitz

            pix = self._pdf_pixmap_to_rgb(fitz.Pixmap(doc, xref))
        return pix.tobytes("png")

    def _pdf_log_image_record(self, index: int, rec: dict) -> None:
        page = int(rec.get("page_index", 0)) + 1
        img_xref = rec.get("image_xref")
        struct_xref = rec.get("struct_xref")
        alt_raw = rec.get("alt_text") or ""
        bbox = rec.get("bbox")
        try:
            clip = f"{bbox.width:.0f}x{bbox.height:.0f}"
        except Exception:
            clip = "?"
        _pdf_backend_log(
            "Image %d: page %d, figure=%s, image_xref=%s, clip=%s, existing alt=%r (decoded: %s)",
            index + 1,
            page,
            struct_xref if struct_xref is not None else "none",
            img_xref,
            clip,
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
                q = self._pdf_page_placements(page)
                page_image_queues[i] = q
                self._pdf_match_stats["page_image_counts"][i] = len(q)
                if q:
                    _pdf_backend_log(
                        "Page %d: %d on-page raster image(s) (pairing uses /Figure Layout BBox overlap)",
                        i + 1,
                        len(q),
                    )
            results = []
            self._pdf_walk_figures(doc, root_xref, page_xref_map, page_image_queues, results, None)
            for (
                struct_xref,
                page_index,
                image_xref,
                bbox,
                transform,
                existing_alt,
                place_bbox,
            ) in results:
                self._images_data.append({
                    "struct_xref": struct_xref,
                    "page_index": page_index,
                    "image_xref": image_xref,
                    "bbox": bbox,
                    "place_bbox": place_bbox,
                    "transform": transform,
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
                    "Hint: skipped figures usually mean structure-tree order != drawing order on the page"
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
                for image_xref, bbox, transform in self._pdf_page_placements(page):
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
                        "place_bbox": bbox,
                        "transform": transform,
                        "alt_text": existing_alt,
                    })

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
                        existing_alt = _pdf_decode_string_value(alt_val)
                except Exception:
                    pass
                queue = page_image_queues.get(page_index)
                page = doc[page_index] if 0 <= page_index < len(doc) else None
                figure_bbox = (
                    self._pdf_figure_layout_bbox(doc, xref, page) if page is not None else None
                )
                pick = self._pdf_pick_placement_index(
                    queue or [], figure_bbox, page, alt_text=existing_alt
                )
                if queue is not None and pick is not None:
                    img_xref, place_bbox, transform = queue.pop(pick)
                    clip_bbox = self._pdf_preview_clip(place_bbox, figure_bbox)
                    results.append(
                        (
                            xref,
                            page_index,
                            img_xref,
                            clip_bbox,
                            transform,
                            existing_alt,
                            place_bbox,
                        )
                    )
                    logger.debug(
                        "PdfOnDiscBackend: paired /Figure xref %s page %d → image xref %s "
                        "(placement %s, clip %s)",
                        xref,
                        page_index + 1,
                        img_xref,
                        place_bbox,
                        clip_bbox,
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
        image_bytes = self._pdf_preview_png_bytes(
            doc,
            rec["image_xref"],
            page_index=rec.get("page_index"),
            bbox=rec.get("bbox"),
            transform=rec.get("transform"),
            place_bbox=rec.get("place_bbox"),
        )
        if not image_bytes:
            _pdf_backend_log(
                "Could not extract preview bitmap for image %d (xref %s)",
                index + 1,
                rec.get("image_xref"),
            )
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
