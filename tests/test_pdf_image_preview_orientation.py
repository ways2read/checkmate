"""PDF image previews must match on-page appearance (CTM / page rotation).

Raw XObject bitmaps are often stored rotated; Acrobat applies the placement
transform at draw time. Vision/health checks must see the placed orientation
or they false-flag ``likely_wrong_orientation``.
"""
from __future__ import annotations

import os
import tempfile
import unittest

import fitz

from checkmate.doc_images.pdf import PdfOnDiscBackend


def _landscape_red_blue_png() -> bytes:
    """80x40 RGB: red on the left half, blue on the right half."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 40), False)
    pix.set_rect(fitz.IRect(0, 0, 40, 40), (255, 0, 0))
    pix.set_rect(fitz.IRect(40, 0, 80, 40), (0, 0, 255))
    return pix.tobytes("png")


def _write_pdf(path: str, *, rotate_image: int = 0, page_rotation: int = 0) -> None:
    png = _landscape_red_blue_png()
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    if rotate_image:
        rect = fitz.Rect(100, 50, 140, 130)
    else:
        rect = fitz.Rect(100, 50, 180, 90)
    page.insert_image(rect, stream=png, rotate=rotate_image)
    if page_rotation:
        page.set_rotation(page_rotation)
    doc.save(path)
    doc.close()


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


def _is_red(rgb) -> bool:
    return rgb[0] > 180 and rgb[2] < 80


def _is_blue(rgb) -> bool:
    return rgb[2] > 180 and rgb[0] < 80


class PdfImagePreviewOrientationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.preview_dir = os.path.join(self.tmp, "preview")
        os.makedirs(self.preview_dir, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _open_backend(self, pdf_path: str) -> PdfOnDiscBackend:
        backend = PdfOnDiscBackend(temp_dir=self.preview_dir)
        self.assertTrue(backend.open_document(pdf_path))
        return backend

    def test_rotated_xobject_preview_matches_page_not_raw(self):
        pdf_path = os.path.join(self.tmp, "rotated_image.pdf")
        _write_pdf(pdf_path, rotate_image=90)

        raw_doc = fitz.open(pdf_path)
        raw = fitz.Pixmap(raw_doc, raw_doc[0].get_images()[0][0])
        self.assertGreater(raw.width, raw.height)
        self.assertGreater(raw.pixel(2, 2)[0], 180)
        self.assertGreater(raw.pixel(raw.width - 3, 2)[2], 180)
        raw_doc.close()

        backend = self._open_backend(pdf_path)
        rec = backend._images_data[0]
        self.assertIsNotNone(rec.get("transform"))
        self.assertLess(rec["bbox"].width, rec["bbox"].height)

        loaded = backend.load_image(0)
        self.assertTrue(loaded and os.path.isfile(loaded["image_path"]))
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertGreater(preview.height, preview.width)

        top = _mean_rgb(preview, (2, 2, preview.width - 2, max(3, preview.height // 6)))
        bottom = _mean_rgb(
            preview,
            (
                2,
                preview.height - max(3, preview.height // 6),
                preview.width - 2,
                preview.height - 2,
            ),
        )
        self.assertTrue(_is_blue(top), top)
        self.assertTrue(_is_red(bottom), bottom)
        backend.close()

    def test_unrotated_image_preview_keeps_native_orientation(self):
        pdf_path = os.path.join(self.tmp, "unrotated_image.pdf")
        _write_pdf(pdf_path, rotate_image=0)

        backend = self._open_backend(pdf_path)
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertGreater(preview.width, preview.height)
        left = _mean_rgb(preview, (2, 2, max(3, preview.width // 6), preview.height - 2))
        right = _mean_rgb(
            preview,
            (
                preview.width - max(3, preview.width // 6),
                2,
                preview.width - 2,
                preview.height - 2,
            ),
        )
        self.assertTrue(_is_red(left), left)
        self.assertTrue(_is_blue(right), right)
        backend.close()

    def test_page_rotation_preview_matches_display_bbox(self):
        pdf_path = os.path.join(self.tmp, "page_rotated.pdf")
        _write_pdf(pdf_path, rotate_image=0, page_rotation=90)

        backend = self._open_backend(pdf_path)
        rec = backend._images_data[0]
        self.assertLess(rec["bbox"].width, rec["bbox"].height)

        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertGreater(preview.height, preview.width)

        raw_doc = fitz.open(pdf_path)
        raw = fitz.Pixmap(raw_doc, rec["image_xref"])
        self.assertGreater(raw.width, raw.height)
        raw_doc.close()
        backend.close()

    def test_preview_stays_oriented_when_page_clip_unavailable(self):
        pdf_path = os.path.join(self.tmp, "fallback.pdf")
        _write_pdf(pdf_path, rotate_image=90)
        backend = self._open_backend(pdf_path)

        backend._pdf_render_bbox_png = lambda *args, **kwargs: None
        loaded = backend.load_image(0)
        self.assertTrue(loaded and os.path.isfile(loaded["image_path"]))
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertGreater(preview.height, preview.width)
        top = _mean_rgb(preview, (2, 2, preview.width - 2, max(3, preview.height // 6)))
        self.assertTrue(_is_blue(top), top)
        backend.close()

    def test_preview_falls_back_to_raw_when_isolated_and_clip_fail(self):
        pdf_path = os.path.join(self.tmp, "raw_fallback.pdf")
        _write_pdf(pdf_path, rotate_image=90)
        backend = self._open_backend(pdf_path)

        backend._pdf_render_xobject_only_png = lambda *args, **kwargs: None
        backend._pdf_render_bbox_png = lambda *args, **kwargs: None
        loaded = backend.load_image(0)
        self.assertTrue(loaded and os.path.isfile(loaded["image_path"]))
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertGreater(preview.width, preview.height)
        backend.close()

    def test_preview_omits_overlays_and_neighbor_images(self):
        pdf_path = os.path.join(self.tmp, "overlays.pdf")
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        red = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 80), False)
        red.set_rect(red.irect, (220, 20, 20))
        green = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 80), False)
        green.set_rect(green.irect, (20, 200, 20))
        page.insert_image(fitz.Rect(20, 20, 140, 140), stream=red.tobytes("png"))
        page.insert_image(fitz.Rect(80, 80, 200, 200), stream=green.tobytes("png"))
        page.insert_text((28, 70), "HELLO", fontsize=28, color=(1, 1, 0))
        doc.save(pdf_path)
        doc.close()

        backend = self._open_backend(pdf_path)
        self.assertGreaterEqual(backend.get_image_count(), 1)
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        center = _mean_rgb(
            preview,
            (
                preview.width // 3,
                preview.height // 3,
                preview.width * 2 // 3,
                preview.height * 2 // 3,
            ),
        )
        self.assertTrue(_is_red(center), center)
        self.assertLess(center[1], 80, "green neighbor / yellow text must not tint the tagged image")
        backend.close()


    def test_isolated_preview_does_not_squash_into_mismatched_rect(self):
        """A 90° landscape image must stay portrait, even in a wide placement."""
        pdf_path = os.path.join(self.tmp, "squish.pdf")
        png = _landscape_red_blue_png()
        doc = fitz.open()
        page = doc.new_page(width=400, height=200)
        page.insert_image(fitz.Rect(10, 10, 390, 50), stream=png, rotate=90)
        doc.save(pdf_path)
        doc.close()

        backend = self._open_backend(pdf_path)
        loaded = backend.load_image(0)
        preview = fitz.Pixmap(loaded["image_path"])
        self.assertGreater(preview.height, preview.width)
        ratio = preview.height / float(preview.width)
        self.assertGreater(ratio, 1.5)
        self.assertLess(ratio, 2.6)
        top = _mean_rgb(preview, (2, 2, preview.width - 2, max(3, preview.height // 6)))
        self.assertTrue(_is_blue(top), top)
        backend.close()


class PdfIsolatedRenderRotationTests(unittest.TestCase):
    def test_contain_rect_keeps_source_aspect(self):
        dest = fitz.Rect(0, 0, 200, 50)
        fit = PdfOnDiscBackend._pdf_contain_rect(dest, 40, 80)
        self.assertAlmostEqual(fit.width / fit.height, 0.5, places=3)
        self.assertLessEqual(fit.width, dest.width + 1e-6)
        self.assertLessEqual(fit.height, dest.height + 1e-6)
    def test_insert_image_90_maps_to_insert_rotate_90(self):
        """PyMuPDF stores insert_image(rotate=90) as CTM angle 270."""
        png = _landscape_red_blue_png()
        doc = fitz.open()
        page = doc.new_page(width=300, height=400)
        page.insert_image(fitz.Rect(100, 50, 140, 130), stream=png, rotate=90)
        info = page.get_image_info(xrefs=True)[0]
        backend = PdfOnDiscBackend()
        self.assertEqual(backend._pdf_matrix_ortho_rotation(info["transform"]), 270)
        self.assertEqual(backend._pdf_display_insert_rotation(page, info["transform"]), 90)
        doc.close()

    def test_skewed_ctm_declines_isolated_render(self):
        backend = PdfOnDiscBackend()
        self.assertIsNone(backend._pdf_matrix_ortho_rotation((1, 0.4, 0.2, 1, 0, 0)))
