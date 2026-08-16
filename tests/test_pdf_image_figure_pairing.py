"""Tagged PDF figure pairing: size-aware match and preview clip."""
from __future__ import annotations

import os
import tempfile
import unittest

import fitz

from checkmate.doc_images.pdf import PdfOnDiscBackend

_REAL_PDF = r"D:\OneDrive\Desktop\PDF2616 Turning the Tide Together_copy.pdf"
_CBM_PDF = r"D:\OneDrive\Desktop\CBM-UK-Annual-Report-2025-web-ready-Final.pdf"


def _solid_png(rgb: tuple[int, int, int], size: tuple[int, int] = (40, 40)) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, size[0], size[1]), False)
    pix.set_rect(pix.irect, rgb)
    return pix.tobytes("png")


def _new_obj(doc: fitz.Document, body: str) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, body)
    return xref


def _mark_tagged(doc: fitz.Document, struct_root: int) -> None:
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "StructTreeRoot", f"{struct_root} 0 R")
    doc.xref_set_key(catalog, "MarkInfo", "<< /Marked true >>")


def _mean_rgb(pix: fitz.Pixmap, box) -> tuple[float, float, float]:
    x0, y0, x1, y1 = box
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = min(pix.width, int(x1))
    y1 = min(pix.height, int(y1))
    r = g = b = 0
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            pr, pg, pb = pix.pixel(x, y)[:3]
            r += pr
            g += pg
            b += pb
            n += 1
    n = max(1, n)
    return r / n, g / n, b / n


class PdfFigurePairingUnitTests(unittest.TestCase):
    def setUp(self):
        self.backend = PdfOnDiscBackend()
        self.doc = fitz.open()
        self.page = self.doc.new_page(width=300, height=400)
        self.backdrop = (1, fitz.Rect(0, 0, 300, 400), None)
        self.logo = (2, fitz.Rect(20, 20, 80, 50), None)
        self.queue = [self.backdrop, self.logo]

    def tearDown(self):
        self.doc.close()

    def test_small_figure_prefers_logo_not_full_page_placement(self):
        fig = fitz.Rect(18, 18, 82, 52)
        self.assertEqual(
            self.backend._pdf_pick_placement_index(self.queue, fig, self.page),
            1,
        )

    def test_missing_figure_bbox_skips_backdrop(self):
        self.assertEqual(
            self.backend._pdf_pick_placement_index(self.queue, None, self.page),
            1,
        )

    def test_page_sized_figure_bbox_skips_backdrop(self):
        fig = fitz.Rect(0, 0, 300, 400)
        self.assertEqual(
            self.backend._pdf_pick_placement_index(self.queue, fig, self.page),
            1,
        )

    def test_off_page_figure_does_not_steal_backdrop(self):
        fig = fitz.Rect(-120, -80, -40, -20)
        self.assertEqual(
            self.backend._pdf_pick_placement_index(self.queue, fig, self.page),
            1,
        )

    def test_logo_figure_does_not_score_against_backdrop(self):
        fig = fitz.Rect(20, 20, 80, 50)
        self.assertEqual(
            self.backend._pdf_placement_match_score(fig, self.backdrop[1], self.page),
            0.0,
        )

    def test_logo_figure_without_compact_placement_uses_backdrop(self):
        queue = [self.backdrop]
        fig = fitz.Rect(20, 20, 80, 50)
        self.assertEqual(
            self.backend._pdf_pick_placement_index(queue, fig, self.page),
            0,
        )

    def test_only_backdrop_left_is_used(self):
        queue = [self.backdrop]
        fig = fitz.Rect(40, 80, 220, 280)
        self.assertEqual(
            self.backend._pdf_pick_placement_index(queue, fig, self.page),
            0,
        )

    def test_logo_alt_takes_compact_when_figure_bbox_matches_photo(self):
        fig = fitz.Rect(20, 80, 280, 320)
        self.assertEqual(
            self.backend._pdf_pick_placement_index(
                self.queue, fig, self.page, alt_text="CBM logo"
            ),
            1,
        )
        self.assertEqual(
            self.backend._pdf_pick_placement_index(
                self.queue, fig, self.page, alt_text="(CBM logo)"
            ),
            1,
        )
        self.assertEqual(
            self.backend._pdf_pick_placement_index(
                self.queue, fig, self.page, alt_text="Boy holding a football"
            ),
            0,
        )

    def test_alt_looks_like_logo(self):
        self.assertTrue(self.backend._pdf_alt_looks_like_logo("CBM logo"))
        self.assertTrue(self.backend._pdf_alt_looks_like_logo("Publisher wordmark"))
        self.assertFalse(self.backend._pdf_alt_looks_like_logo("Boy holding a football"))

    def test_preview_clip_crops_large_xobject_to_figure(self):
        place = fitz.Rect(0, 0, 300, 400)
        fig = fitz.Rect(20, 20, 120, 50)
        clip = self.backend._pdf_preview_clip(place, fig)
        self.assertLess(clip.width, 150)
        self.assertLess(clip.height, 80)

    def test_preview_clip_does_not_expand_past_placement(self):
        place = fitz.Rect(20, 20, 80, 50)
        fig = fitz.Rect(0, 0, 300, 400)
        clip = self.backend._pdf_preview_clip(place, fig)
        self.assertLess(clip.width, 100)
        self.assertLess(clip.height, 50)

    def test_xref_zero_is_not_usable(self):
        self.assertFalse(self.backend._pdf_valid_xref(0))
        self.assertFalse(self.backend._pdf_valid_xref(None))
        self.assertTrue(self.backend._pdf_valid_xref(5))


class PdfFigurePairingPdfTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.preview_dir = os.path.join(self.tmp, "preview")
        os.makedirs(self.preview_dir, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _open(self, pdf_path: str) -> PdfOnDiscBackend:
        backend = PdfOnDiscBackend(temp_dir=self.preview_dir)
        self.assertTrue(backend.open_document(pdf_path))
        return backend

    def test_figure_layout_bbox_clips_logo_not_full_page(self):
        path = os.path.join(self.tmp, "logo_full_page.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400), False)
        pix.set_rect(pix.irect, (255, 0, 0))
        pix.set_rect(fitz.IRect(20, 20, 120, 50), (0, 0, 255))
        page.insert_image(page.rect, stream=pix.tobytes("png"))
        page_xref = page.xref
        # Fitz (20,20,120,50) → PDF user space with origin at bottom-left.
        attr = _new_obj(
            doc, "<< /O /Layout /Placement /Block /BBox [ 20 350 120 380 ] >>"
        )
        fig = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {attr} 0 R /Alt (Logo) >>",
        )
        document = _new_obj(doc, f"<< /Type /StructElem /S /Document /K {fig} 0 R >>")
        root = _new_obj(doc, f"<< /Type /StructTreeRoot /K {document} 0 R >>")
        doc.update_object(
            fig,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {attr} 0 R /Alt (Logo) >>",
        )
        _mark_tagged(doc, root)
        doc.save(path)
        doc.close()

        backend = self._open(path)
        self.assertEqual(backend.get_image_count(), 1)
        rec = backend._images_data[0]
        self.assertLess(rec["bbox"].width, 150)
        self.assertLess(rec["bbox"].height, 80)
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertLess(preview.width, 280)
        self.assertLess(preview.height, 160)
        cx, cy = preview.width // 2, preview.height // 2
        r, _g, b = preview.pixel(cx, cy)[:3]
        self.assertGreater(b, 180)
        self.assertLess(r, 80)
        backend.close()

    def test_logo_figure_does_not_take_full_page_raster(self):
        """Repro: page-sized / full-page XObject + small logo, two /Figure tags.

        Greedy queue[0] pairing attached 'CBM logo' to the page raster and the
        photo alt to the leftover logo. Compact figures must take the logo.
        """
        path = os.path.join(self.tmp, "logo_and_photo.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=_solid_png((200, 80, 40), (300, 400)))
        page.insert_image(fitz.Rect(20, 20, 80, 50), stream=_solid_png((0, 0, 255), (60, 30)))
        page_xref = page.xref
        logo_attr = _new_obj(
            doc, "<< /O /Layout /Placement /Block /BBox [ 18 348 82 382 ] >>"
        )
        # Photo figure bbox is the lower two-thirds of the page (not the logo strip).
        photo_attr = _new_obj(
            doc, "<< /O /Layout /Placement /Block /BBox [ 20 20 280 300 ] >>"
        )
        fig_logo = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {logo_attr} 0 R /Alt (CBM logo) >>",
        )
        fig_photo = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {photo_attr} 0 R /Alt (Boy holding a football) >>",
        )
        document = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Document /K [ {fig_logo} 0 R {fig_photo} 0 R ] >>",
        )
        root = _new_obj(doc, f"<< /Type /StructTreeRoot /K {document} 0 R >>")
        doc.update_object(
            fig_logo,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {logo_attr} 0 R /Alt (CBM logo) >>",
        )
        doc.update_object(
            fig_photo,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {photo_attr} 0 R /Alt (Boy holding a football) >>",
        )
        _mark_tagged(doc, root)
        doc.save(path)
        doc.close()

        backend = self._open(path)
        self.assertEqual(backend.get_image_count(), 2)
        rec0, rec1 = backend._images_data
        self.assertIn("CBM logo", rec0.get("alt_text") or "")
        self.assertIn("football", rec1.get("alt_text") or "")
        self.assertLess(rec0["bbox"].width, 120, "logo clip must not be the full page")
        self.assertLess(rec0["bbox"].height, 80)
        self.assertGreater(rec1["bbox"].height, 150)
        self.assertNotEqual(rec0["image_xref"], rec1["image_xref"])

        loaded0 = backend.load_image(0)
        preview0 = fitz.Pixmap(loaded0["image_path"])
        self.assertLess(preview0.width, 200)
        self.assertLess(preview0.height, 140)
        r, _g, b = preview0.pixel(preview0.width // 2, preview0.height // 2)[:3]
        self.assertGreater(b, 180)
        self.assertLess(r, 80)
        backend.close()

    def test_page_sized_logo_bbox_still_pairs_to_logo(self):
        """When /A BBox is missing or page-sized, still prefer the compact XObject."""
        path = os.path.join(self.tmp, "page_sized_logo_bbox.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=_solid_png((200, 80, 40), (300, 400)))
        page.insert_image(fitz.Rect(20, 20, 80, 50), stream=_solid_png((0, 0, 255), (60, 30)))
        page_xref = page.xref
        fig_logo = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /Alt (CBM logo) >>",
        )
        fig_photo = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /Alt (Boy holding a football) >>",
        )
        document = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Document /K [ {fig_logo} 0 R {fig_photo} 0 R ] >>",
        )
        root = _new_obj(doc, f"<< /Type /StructTreeRoot /K {document} 0 R >>")
        doc.update_object(
            fig_logo,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /Alt (CBM logo) >>",
        )
        doc.update_object(
            fig_photo,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /Alt (Boy holding a football) >>",
        )
        _mark_tagged(doc, root)
        doc.save(path)
        doc.close()

        backend = self._open(path)
        self.assertEqual(backend.get_image_count(), 2)
        rec0, rec1 = backend._images_data
        self.assertIn("CBM logo", rec0.get("alt_text") or "")
        self.assertIn("football", rec1.get("alt_text") or "")
        self.assertLess(rec0["bbox"].width, 120)
        self.assertLess(rec0["bbox"].height, 80)
        backend.close()

    def test_logo_alt_wins_when_layout_bbox_is_the_photo(self):
        """CBM case: /Figure alt is 'logo' but /A BBox is the photo region."""
        path = os.path.join(self.tmp, "logo_alt_photo_bbox.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=_solid_png((200, 80, 40), (300, 400)))
        page.insert_image(fitz.Rect(20, 20, 80, 50), stream=_solid_png((0, 0, 255), (60, 30)))
        page_xref = page.xref
        # Fitz (20, 80, 280, 320) → PDF user space (origin bottom-left).
        shared_attr = _new_obj(
            doc, "<< /O /Layout /Placement /Block /BBox [ 20 80 280 320 ] >>"
        )
        fig_logo = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {shared_attr} 0 R /Alt (CBM logo) >>",
        )
        fig_photo = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {shared_attr} 0 R /Alt (Boy holding a football) >>",
        )
        document = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Document /K [ {fig_logo} 0 R {fig_photo} 0 R ] >>",
        )
        root = _new_obj(doc, f"<< /Type /StructTreeRoot /K {document} 0 R >>")
        doc.update_object(
            fig_logo,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {shared_attr} 0 R /Alt (CBM logo) >>",
        )
        doc.update_object(
            fig_photo,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {shared_attr} 0 R /Alt (Boy holding a football) >>",
        )
        _mark_tagged(doc, root)
        doc.save(path)
        doc.close()

        backend = self._open(path)
        self.assertEqual(backend.get_image_count(), 2)
        rec0, rec1 = backend._images_data
        self.assertIn("CBM logo", rec0.get("alt_text") or "")
        self.assertIn("football", rec1.get("alt_text") or "")
        self.assertLess(rec0["bbox"].width, 120, "logo clip must not be the photo")
        self.assertLess(rec0["bbox"].height, 80)
        self.assertGreater(rec1["bbox"].height, 150)
        self.assertNotEqual(rec0["image_xref"], rec1["image_xref"])
        loaded0 = backend.load_image(0)
        preview0 = fitz.Pixmap(loaded0["image_path"])
        r, _g, b = preview0.pixel(preview0.width // 2, preview0.height // 2)[:3]
        self.assertGreater(b, 180)
        self.assertLess(r, 80)
        backend.close()

    def test_vector_logo_over_photo_is_not_the_photo(self):
        """Logo /Figure is vector art on a full-page raster — do not export the photo."""
        path = os.path.join(self.tmp, "vector_logo_over_photo.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=_solid_png((200, 80, 40), (300, 400)))
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(20, 20, 120, 50))
        shape.finish(color=(0, 0, 1), fill=(0, 0, 1), width=0)
        shape.commit()
        page_xref = page.xref
        logo_attr = _new_obj(
            doc, "<< /O /Layout /Placement /Block /BBox [ 20 350 120 380 ] >>"
        )
        fig_logo = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {logo_attr} 0 R /Alt (CBM logo) >>",
        )
        document = _new_obj(
            doc, f"<< /Type /StructElem /S /Document /K [ {fig_logo} 0 R ] >>"
        )
        root = _new_obj(doc, f"<< /Type /StructTreeRoot /K {document} 0 R >>")
        doc.update_object(
            fig_logo,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {logo_attr} 0 R /Alt (CBM logo) >>",
        )
        _mark_tagged(doc, root)
        doc.save(path)
        doc.close()

        backend = self._open(path)
        self.assertGreaterEqual(backend.get_image_count(), 1)
        rec0 = backend._images_data[0]
        self.assertIn("logo", (rec0.get("alt_text") or "").lower())
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        found_blue = False
        found_photo_orange = False
        for y in range(0, preview.height, 3):
            for x in range(0, preview.width, 3):
                r, g, b = preview.pixel(x, y)[:3]
                if b > 180 and r < 80:
                    found_blue = True
                if r > 160 and 50 < g < 120 and b < 80:
                    found_photo_orange = True
        self.assertTrue(found_blue, "vector logo (blue) must be in the preview")
        self.assertFalse(found_photo_orange, "full-page photo must not fill the logo preview")
        backend.close()

    def test_photo_crop_of_page_raster_keeps_the_photo(self):
        """Compact /Figure that is a crop of the page photo — not a vector logo.

        Overlay-hide would delete the only raster and look blank; fall back to
        the isolated XObject so the preview stays the photo.
        """
        path = os.path.join(self.tmp, "photo_crop_of_backdrop.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=_solid_png((200, 80, 40), (300, 400)))
        page_xref = page.xref
        crop_attr = _new_obj(
            doc, "<< /O /Layout /Placement /Block /BBox [ 50 120 220 280 ] >>"
        )
        fig = _new_obj(
            doc,
            f"<< /Type /StructElem /S /Figure /P 5 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {crop_attr} 0 R /Alt (A smiling boy holding a football) >>",
        )
        document = _new_obj(
            doc, f"<< /Type /StructElem /S /Document /K [ {fig} 0 R ] >>"
        )
        root = _new_obj(doc, f"<< /Type /StructTreeRoot /K {document} 0 R >>")
        doc.update_object(
            fig,
            f"<< /Type /StructElem /S /Figure /P {document} 0 R /Pg {page_xref} 0 R "
            f"/K 0 /A {crop_attr} 0 R /Alt (A smiling boy holding a football) >>",
        )
        _mark_tagged(doc, root)
        doc.save(path)
        doc.close()

        backend = self._open(path)
        self.assertEqual(backend.get_image_count(), 1)
        rec = backend._images_data[0]
        page = backend._doc[0]
        self.assertTrue(backend._pdf_clip_is_compact(rec["bbox"], page))
        self.assertIn(int(rec["image_xref"]), backend._pdf_page_backdrop_xrefs(page))
        overlay = backend._pdf_render_clip_hiding_backdrops(
            backend._doc, rec["page_index"], rec["bbox"]
        )
        self.assertIsNone(overlay, "hiding the photo must not invent leftover chrome")
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        r, g, b = preview.pixel(preview.width // 2, preview.height // 2)[:3]
        self.assertGreater(r, 160)
        self.assertLess(b, 80)
        self.assertTrue(50 < g < 120)
        backend.close()

    def test_zero_info_xref_resolves_to_real_xobject(self):
        path = os.path.join(self.tmp, "resolve_xref.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=_solid_png((200, 80, 40), (300, 400)))
        page.insert_image(fitz.Rect(20, 20, 80, 50), stream=_solid_png((0, 0, 255), (60, 30)))
        doc.save(path)
        doc.close()

        backend = self._open(path)
        self.assertGreaterEqual(backend.get_image_count(), 2)
        for rec in backend._images_data:
            self.assertTrue(
                backend._pdf_valid_xref(rec["image_xref"]),
                rec.get("image_xref"),
            )
        page = backend._doc[0]
        logo = min(backend._images_data, key=lambda r: r["bbox"].get_area())
        resolved = backend._pdf_resolve_placement_xref(page, 0, logo["bbox"])
        self.assertEqual(resolved, logo["image_xref"])
        backend.close()

    @unittest.skipUnless(os.path.isfile(_CBM_PDF), "CBM annual report PDF not on this machine")
    def test_cbm_cover_logo_is_not_the_boy_photo(self):
        backend = self._open(_CBM_PDF)
        rec0 = backend._images_data[0]
        self.assertIn("logo", (rec0.get("alt_text") or "").lower())
        self.assertLess(rec0["bbox"].height, 250)
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        found_cbm_red = False
        for y in range(0, preview.height, 4):
            for x in range(0, preview.width, 4):
                r, g, b = preview.pixel(x, y)[:3]
                if r > 180 and g < 90 and b < 90:
                    found_cbm_red = True
                    break
            if found_cbm_red:
                break
        self.assertTrue(found_cbm_red, "cover logo preview must include CBM red artwork")
        backend.close()

    @unittest.skipUnless(os.path.isfile(_REAL_PDF), "commission PDF not on this machine")
    def test_tide_cover_logo_is_not_the_page_photo(self):
        """Same CBM pattern: banner /Figure paired to a full-page raster."""
        backend = self._open(_REAL_PDF)
        rec0 = backend._images_data[0]
        self.assertIn("logo", (rec0.get("alt_text") or "").lower())
        self.assertLess(rec0["bbox"].height, 80)
        page = backend._doc[rec0["page_index"]]
        self.assertIn(int(rec0["image_xref"]), backend._pdf_page_backdrop_xrefs(page))
        overlay = backend._pdf_render_clip_hiding_backdrops(
            backend._doc, rec0["page_index"], rec0["bbox"]
        )
        self.assertIsNotNone(overlay, "vector/composite logo must survive hiding the photo")
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertLess(preview.height / max(1, preview.width), 0.4)
        backend.close()

    @unittest.skipUnless(os.path.isfile(_REAL_PDF), "commission PDF not on this machine")
    def test_commission_pdf_logo_and_alanna_placements(self):
        backend = self._open(_REAL_PDF)
        rec0 = backend._images_data[0]
        self.assertLess(rec0["bbox"].width, 400, "logo clip must not be the full page")
        self.assertLess(rec0["bbox"].height, 80)

        alanna = [
            rec
            for rec in backend._images_data
            if "Alanna Jenkins smiling toward the camera" in (rec.get("alt_text") or "")
            and "Sean" not in (rec.get("alt_text") or "")
        ]
        group = [
            rec
            for rec in backend._images_data
            if "Alanna Jenkins and Sean McLeod" in (rec.get("alt_text") or "")
        ]
        self.assertTrue(alanna)
        self.assertTrue(group)
        self.assertNotEqual(alanna[0]["image_xref"], group[0]["image_xref"])
        self.assertNotEqual(alanna[0]["page_index"], group[0]["page_index"])
        backend.close()


if __name__ == "__main__":
    unittest.main()
