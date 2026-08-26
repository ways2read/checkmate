"""Clipboard markup detection and wrapping for Check clipboard…"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from checkmate.clipboard_markup import (
    CLIPBOARD_STEM,
    ClipboardKind,
    clipboard_document_is_snippet,
    clipboard_snapshot_files,
    clipboard_source_path,
    clipboard_view_text,
    detect_clipboard_kind,
    extract_cf_html_fragment,
    extract_clipboard_view_text,
    html_snippet_content,
    is_clipboard_snapshot_path,
    looks_like_checkmate_report,
    prefer_clipboard_payload,
    prepare_clipboard_document,
    resolve_clipboard_snapshot,
    vnu_args_for_kind,
)


class DetectClipboardKindTests(unittest.TestCase):
    def test_css(self) -> None:
        self.assertEqual(
            detect_clipboard_kind("body { color: black; }"),
            ClipboardKind.CSS,
        )
        self.assertEqual(
            detect_clipboard_kind('@import "theme.css";'),
            ClipboardKind.CSS,
        )

    def test_html_document_not_svg(self) -> None:
        html = (
            "<!DOCTYPE html><html><body>"
            '<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'
            "</body></html>"
        )
        self.assertEqual(detect_clipboard_kind(html), ClipboardKind.HTML)

    def test_html_fragment(self) -> None:
        self.assertEqual(
            detect_clipboard_kind('<p class="lead">Hello</p>'),
            ClipboardKind.HTML,
        )

    def test_svg_document(self) -> None:
        self.assertEqual(
            detect_clipboard_kind(
                '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
            ),
            ClipboardKind.SVG,
        )

    def test_mathml_root(self) -> None:
        self.assertEqual(
            detect_clipboard_kind(
                '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
            ),
            ClipboardKind.MATHML,
        )

    def test_xml(self) -> None:
        self.assertEqual(
            detect_clipboard_kind('<?xml version="1.0"?><root><item/></root>'),
            ClipboardKind.XML,
        )

    def test_plain_text_unknown(self) -> None:
        self.assertEqual(detect_clipboard_kind("just a sentence"), ClipboardKind.UNKNOWN)

    def test_checkmate_report_is_not_html(self) -> None:
        report = (
            "Nu HTML Checker + axe report\n\n"
            "Web page: C:\\tmp\\clipboard-check.html\n"
            "Failed — 5 errors, 3 warnings — Nu HTML Checker: 3 errors\n\n"
            "Error  Nu HTML Checker  error  file.html:1:6: "
            'Start tag seen without seeing a doctype first. Expected “<!DOCTYPE html>”.\n'
            "Error  axe  document-title  http://127.0.0.1/x.html · html: "
            "Documents must have <title> element to aid in navigation\n"
        )
        self.assertTrue(looks_like_checkmate_report(report))
        self.assertEqual(detect_clipboard_kind(report), ClipboardKind.UNKNOWN)

    def test_style_block_is_html_not_css(self) -> None:
        self.assertEqual(
            detect_clipboard_kind("<style>body { color: red; }</style>"),
            ClipboardKind.HTML,
        )


class PrepareClipboardTests(unittest.TestCase):
    def test_html_fragment_wrapped(self) -> None:
        out = prepare_clipboard_document("<p>Hi</p>", ClipboardKind.HTML)
        self.assertIn("<!DOCTYPE html>", out)
        self.assertIn("<main>", out)
        self.assertIn("<p>Hi</p>", out)
        self.assertEqual(out.count("<html"), 1)
        self.assertTrue(clipboard_document_is_snippet(out))
        self.assertEqual(extract_clipboard_view_text(out), "<p>Hi</p>")

    def test_complete_html_not_rewrapped(self) -> None:
        src = "<!DOCTYPE html><html><body>x</body></html>"
        self.assertEqual(prepare_clipboard_document(src, ClipboardKind.HTML), src)

    def test_skeleton_html_body_wrapped_as_snippet(self) -> None:
        src = (
            "<html>\n<body>\n<p>Here is a simple fraction:</p>\n"
            '<math xmlns="https://www.w3.org/1998/Math/MathML/">\n'
            " <mfrac><mn>1</mn><mn>2</mn></mfrac>\n"
            "</math>\n</body>\n</html>"
        )
        out = prepare_clipboard_document(src, ClipboardKind.HTML)
        self.assertIn("<!DOCTYPE html>", out)
        self.assertIn("<main>", out)
        self.assertIn("Here is a simple fraction:", out)
        self.assertIn("https://www.w3.org/1998/Math/MathML/", out)
        self.assertEqual(out.count("<html"), 1)
        self.assertEqual(out.count("<body"), 1)
        self.assertNotIn("<html>\n<body>", out)

    def test_html_with_head_title_not_treated_as_snippet(self) -> None:
        src = (
            "<html><head><title>Demo</title></head>"
            "<body><p>Hi</p></body></html>"
        )
        self.assertEqual(prepare_clipboard_document(src, ClipboardKind.HTML), src)

    def test_mathml_fragment_wrapped_as_html(self) -> None:
        src = "<math><mi>x</mi></math>"
        out = prepare_clipboard_document(src, ClipboardKind.MATHML)
        self.assertIn("<!DOCTYPE html>", out)
        self.assertIn(src, out)
        self.assertEqual(vnu_args_for_kind(ClipboardKind.MATHML, out), ["--html"])

    def test_mathml_xml_uses_xml_flag(self) -> None:
        src = (
            '<?xml version="1.0"?>\n'
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
        )
        self.assertEqual(prepare_clipboard_document(src, ClipboardKind.MATHML), src)
        self.assertEqual(vnu_args_for_kind(ClipboardKind.MATHML, src), ["--xml"])

    def test_svg_fragment_wrapped(self) -> None:
        out = prepare_clipboard_document('<circle r="1"/>', ClipboardKind.SVG)
        self.assertIn("<svg xmlns=", out)
        self.assertIn('<circle r="1"/>', out)

    def test_css_and_xml_passthrough(self) -> None:
        css = "a { color: red; }"
        xml = "<root/>"
        self.assertEqual(prepare_clipboard_document(css, ClipboardKind.CSS), css)
        self.assertEqual(prepare_clipboard_document(xml, ClipboardKind.XML), xml)
        self.assertEqual(vnu_args_for_kind(ClipboardKind.CSS), ["--css"])
        self.assertEqual(vnu_args_for_kind(ClipboardKind.XML), ["--xml"])

    def test_html_snippet_content_extracts_body(self) -> None:
        inner = html_snippet_content(
            "<html>\n<body>\n<p>Hi</p>\n</body>\n</html>"
        )
        self.assertEqual(inner, "<p>Hi</p>")


class CfHtmlTests(unittest.TestCase):
    def test_extract_marked_fragment(self) -> None:
        raw = (
            "Version:0.9\nStartHTML:0000000\nEndHTML:0000000\n"
            "StartFragment:0000000\nEndFragment:0000000\n"
            "<html><body><!--StartFragment--><b>Hi</b><!--EndFragment--></body></html>"
        )
        self.assertEqual(extract_cf_html_fragment(raw), "<b>Hi</b>")

    def test_prefer_plain_when_detected(self) -> None:
        self.assertEqual(
            prefer_clipboard_payload("body { color: red; }", "<html><b>ignored</b></html>"),
            "body { color: red; }",
        )

    def test_prefer_html_when_plain_unknown(self) -> None:
        self.assertEqual(
            prefer_clipboard_payload("hello", "<p>from html</p>"),
            "<p>from html</p>",
        )


class SnapshotResolveTests(unittest.TestCase):
    def test_prefers_explicit_file_then_newest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / f"{CLIPBOARD_STEM}.css"
            newer = root / f"{CLIPBOARD_STEM}.html"
            older.write_text("a { color: red; }", encoding="utf-8")
            newer.write_text("<p>Hi</p>", encoding="utf-8")
            self.assertEqual(
                resolve_clipboard_snapshot(older, root=root), older
            )
            self.assertEqual(
                resolve_clipboard_snapshot(None, root=root), newer
            )
            self.assertEqual(
                resolve_clipboard_snapshot(root / "book.epub", root=root),
                newer,
            )
            self.assertEqual(len(clipboard_snapshot_files(root)), 2)
            empty = root / "empty"
            empty.mkdir()
            self.assertIsNone(resolve_clipboard_snapshot(None, root=empty))

    def test_view_text_prefers_original_not_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / f"{CLIPBOARD_STEM}.html"
            original = "<html>\n<body>\n<p>Hi</p>\n</body>\n</html>"
            snap.write_text(
                prepare_clipboard_document(original, ClipboardKind.HTML),
                encoding="utf-8",
            )
            clipboard_source_path(snap).write_text(original, encoding="utf-8")
            self.assertEqual(clipboard_view_text(snap).strip(), original)
            self.assertFalse(is_clipboard_snapshot_path(clipboard_source_path(snap)))

    def test_view_text_unwraps_when_original_missing(self) -> None:
        wrapped = prepare_clipboard_document("<p>Hi</p>", ClipboardKind.HTML)
        self.assertEqual(extract_clipboard_view_text(wrapped), "<p>Hi</p>")


if __name__ == "__main__":
    unittest.main()
