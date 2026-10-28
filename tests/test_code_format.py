"""Format code: whitespace-only, idempotent, and safe for rendered text."""

from __future__ import annotations

import re
import time
import unittest

from checkmate.code_format import (
    FormatError,
    can_format,
    format_code,
    format_css,
    format_markup,
    map_offset,
)


def _no_space(text: str) -> str:
    return re.sub(r"\s", "", text)


XHTML = (
    '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html>'
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
    "<head><title>T</title><style>p > a { color: red }</style></head>"
    '<body><section epub:type="chapter"><h1>One</h1>'
    "<p>Hello <em>big</em><strong>world</strong>.</p>"
    '<ul><li><a href="a.xhtml">A</a></li><li>B</li></ul>'
    "<pre>  keep\n   this</pre>"
    '<div><img src="x.png" alt=""/><img src="y.png" alt=""/></div>'
    "<!-- note --></section><script>if (a<b && c>d) {}</script></body></html>"
)


class MarkupTests(unittest.TestCase):
    def test_blocks_on_their_own_lines(self) -> None:
        out = format_markup(XHTML, html=True)
        self.assertIn("\n  <head>\n    <title>T</title>\n", out)
        self.assertIn('\n    <section epub:type="chapter">\n      <h1>One</h1>\n', out)
        self.assertIn("\n      <!-- note -->\n", out)

    def test_only_whitespace_changes_and_is_idempotent(self) -> None:
        out = format_markup(XHTML, html=True)
        self.assertEqual(_no_space(out), _no_space(XHTML))
        self.assertEqual(format_markup(out, html=True), out)

    def test_running_text_and_inline_siblings_kept_exactly(self) -> None:
        out = format_markup(XHTML, html=True)
        self.assertIn("<p>Hello <em>big</em><strong>world</strong>.</p>", out)
        self.assertIn('<li><a href="a.xhtml">A</a></li>', out)
        # Adjacent inline images would gain a visible gap if split.
        self.assertIn('<div><img src="x.png" alt=""/><img src="y.png" alt=""/></div>', out)

    def test_preformatted_and_raw_text_kept_exactly(self) -> None:
        out = format_markup(XHTML, html=True)
        self.assertIn("<pre>  keep\n   this</pre>", out)
        self.assertIn("<style>p > a { color: red }</style>", out)
        self.assertIn("<script>if (a<b && c>d) {}</script>", out)

    def test_xml_space_preserve_and_svg_text(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg"><g xml:space="preserve"><a/><b/></g>'
            "<text><tspan>A</tspan><tspan>B</tspan></text></svg>"
        )
        out = format_markup(svg)
        self.assertIn('<g xml:space="preserve"><a/><b/></g>', out)
        self.assertIn("<text><tspan>A</tspan><tspan>B</tspan></text>", out)

    def test_data_xml_lays_out_every_element(self) -> None:
        opf = '<package><metadata><dc:title>Book</dc:title></metadata><spine><itemref idref="a"/></spine></package>'
        self.assertEqual(
            format_markup(opf),
            "<package>\n  <metadata>\n    <dc:title>Book</dc:title>\n  </metadata>\n"
            '  <spine>\n    <itemref idref="a"/>\n  </spine>\n</package>\n',
        )

    def test_html_void_elements(self) -> None:
        out = format_markup('<html><head><meta charset="utf-8"><link rel="x"></head></html>', html=True)
        self.assertIn('    <meta charset="utf-8">\n    <link rel="x">\n', out)

    def test_malformed_markup_raises_with_location(self) -> None:
        with self.assertRaises(FormatError) as ctx:
            format_markup("<a>\n<b></a>")
        self.assertEqual((ctx.exception.kind, ctx.exception.name, ctx.exception.line), ("unmatched_end", "a", 2))
        with self.assertRaises(FormatError) as ctx:
            format_markup("<a>\n<b>")
        self.assertEqual((ctx.exception.kind, ctx.exception.name, ctx.exception.line), ("unclosed", "b", 2))

    def test_large_file_is_fast(self) -> None:
        big = "<html><body>" + "<div><p>Para <em>x</em></p></div>" * 40000 + "</body></html>"
        start = time.monotonic()
        format_markup(big, html=True)
        self.assertLess(time.monotonic() - start, 5.0)


class CssTests(unittest.TestCase):
    CSS = (
        '/* base */ h1,h2{color:red;font-family:"Times  New Roman" , serif}'
        "@media print{p{margin:0;background:url(data:image/png;base64,AAA=)}}"
        '.a::before{content:"a ; b { }"}'
    )

    def test_layout(self) -> None:
        self.assertEqual(
            format_css(self.CSS),
            "/* base */\nh1,h2 {\n  color:red;\n  font-family:\"Times  New Roman\" , serif\n}\n\n"
            "@media print {\n  p {\n    margin:0;\n    background:url(data:image/png;base64,AAA=)\n  }\n}\n\n"
            '.a::before {\n  content:"a ; b { }"\n}\n',
        )

    def test_only_whitespace_changes_and_is_idempotent(self) -> None:
        out = format_css(self.CSS)
        self.assertEqual(_no_space(out), _no_space(self.CSS))
        self.assertEqual(format_css(out), out)


class MapOffsetTests(unittest.TestCase):
    def test_caret_stays_on_its_character(self) -> None:
        old = "<p>Hello <em>big</em> world</p><div><b>x</b></div>"
        new = format_markup(old)
        for offset, ch in enumerate(old):
            mapped = map_offset(old, new, offset)
            if not ch.isspace():
                self.assertEqual(new[mapped], ch)
            self.assertEqual(_no_space(new[:mapped]), _no_space(old[:offset]))
        self.assertEqual(_no_space(new[: map_offset(old, new, len(old))]), _no_space(old))


class DispatchTests(unittest.TestCase):
    def test_by_suffix(self) -> None:
        self.assertTrue(can_format("EPUB/a.XHTML"))
        self.assertTrue(can_format("EPUB/style.css"))
        self.assertFalse(can_format("EPUB/app.js"))
        self.assertIsNone(format_code("x", "EPUB/app.js"))
        self.assertEqual(format_code("a{b:c}", "s.css"), "a {\n  b:c\n}\n")


if __name__ == "__main__":
    unittest.main()
