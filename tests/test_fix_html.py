"""Fix with AI for HTML, SVG, CSS, and MathML on disk (not web URLs or clipboard)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from checkmate.ai.context import (
    fix_allowed_for_result,
    gather_issue_context,
    parse_issue_location,
)
from checkmate.ai.explain import build_user_prompt
from checkmate.ai.fix import build_fix_user_prompt, error_message_for_key
from checkmate.clipboard_markup import CLIPBOARD_STEM
from checkmate.epub_package import apply_text_replacements, read_member_text
from checkmate.models import CheckResult, Issue, Severity, Verdict


class HtmlLocationParseTests(unittest.TestCase):
    def test_localhost_url_with_line_and_column(self) -> None:
        self.assertEqual(
            parse_issue_location("http://127.0.0.1:53757/index.html:12:3"),
            ("index.html", 12),
        )

    def test_localhost_url_does_not_treat_port_as_line(self) -> None:
        self.assertEqual(
            parse_issue_location("http://127.0.0.1:8080/about.html"),
            ("about.html", None),
        )

    def test_axe_localhost_selector_location(self) -> None:
        self.assertEqual(
            parse_issue_location(
                "http://127.0.0.1:9/docs/ch.html · html img · <img src=\"a.png\">"
            ),
            ("docs/ch.html", None),
        )

    def test_windows_path_line_and_column(self) -> None:
        member, line = parse_issue_location(r"C:\site\index.html:8:1")
        self.assertTrue(member.replace("\\", "/").endswith("site/index.html"))
        self.assertEqual(line, 8)


class HtmlFixGateTests(unittest.TestCase):
    def test_local_html_file_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "index.html"
            page.write_text("<p>x</p>", encoding="utf-8")
            result = CheckResult(
                verdict=Verdict.FAILED,
                target_path=str(page),
            )
            self.assertTrue(fix_allowed_for_result(result))

    def test_html_folder_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<p>x</p>", encoding="utf-8")
            result = CheckResult(verdict=Verdict.FAILED, target_path=str(root))
            self.assertTrue(fix_allowed_for_result(result))

    def test_web_url_is_not_allowed(self) -> None:
        result = CheckResult(
            verdict=Verdict.FAILED,
            target_path="https://example.com/page.html",
        )
        self.assertFalse(fix_allowed_for_result(result))

    def test_clipboard_snapshot_is_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / f"{CLIPBOARD_STEM}.html"
            snap.write_text("<p>x</p>", encoding="utf-8")
            result = CheckResult(verdict=Verdict.FAILED, target_path=str(snap))
            self.assertFalse(fix_allowed_for_result(result))

    def test_wrong_format_message_mentions_html(self) -> None:
        text = error_message_for_key("wrong_format")
        self.assertIn("HTML", text)
        self.assertIn("SVG", text)
        self.assertIn("disk", text.lower())


class HtmlApplyAndContextTests(unittest.TestCase):
    def test_apply_edits_html_file_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "index.html"
            page.write_text('<img src="a.png">\n', encoding="utf-8")
            out = apply_text_replacements(
                page,
                [
                    (
                        "http://127.0.0.1:9/index.html",
                        '<img src="a.png">',
                        '<img src="a.png" alt="A">',
                    )
                ],
            )
            self.assertTrue(out.ok, out.error_key)
            self.assertTrue(out.backup_path)
            self.assertIn('alt="A"', page.read_text(encoding="utf-8"))
            self.assertTrue(Path(out.backup_path).is_file())

    def test_read_sibling_under_opened_html_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root / "index.html"
            about = root / "about.html"
            index.write_text("<p>home</p>", encoding="utf-8")
            about.write_text("<p>about</p>", encoding="utf-8")
            name, text = read_member_text(index, "http://127.0.0.1:9/about.html")
            self.assertEqual(name, "about.html")
            self.assertIn("about", text or "")

    def test_gather_excerpt_from_localhost_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "index.html"
            page.write_text(
                "<html><body><img src='a.png'></body></html>\n",
                encoding="utf-8",
            )
            issue = Issue(
                Severity.ERROR,
                "image-alt",
                "Images must have alternate text.",
                location="http://127.0.0.1:53757/index.html · html img",
                source="axe",
                snippet='<img src="a.png">',
            )
            result = CheckResult(
                verdict=Verdict.FAILED,
                tool_name="axe",
                target_path=str(page),
                issues=[issue],
            )
            with mock.patch(
                "checkmate.ai.context.send_file_context_enabled",
                return_value=True,
            ):
                ctx = gather_issue_context(issue, result, target_path=str(page))
            self.assertEqual(ctx.get("file_member"), "index.html")
            self.assertIn("img", ctx.get("file_excerpt_raw") or "")
            self.assertEqual(ctx.get("snippet"), '<img src="a.png">')
            explain = build_user_prompt(ctx)
            self.assertIn("Flagged markup", explain)
            self.assertIn('<img src="a.png">', explain)
            fix = build_fix_user_prompt(ctx, issue=issue)
            self.assertIn("Flagged markup", fix)
            self.assertNotIn("CROSS-FILE FIXES", fix)
            self.assertNotIn("Related package document", fix)


class SvgCssMathmlFixTests(unittest.TestCase):
    def _allowed(self, name: str, body: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_text(body, encoding="utf-8")
            result = CheckResult(verdict=Verdict.FAILED, target_path=str(path))
            self.assertTrue(fix_allowed_for_result(result), name)

    def test_local_svg_css_mathml_are_allowed(self) -> None:
        self._allowed("icon.svg", '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        self._allowed("theme.css", "p { color: red; }")
        self._allowed("eq.mml", '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>')

    def test_remote_svg_url_is_not_allowed(self) -> None:
        result = CheckResult(
            verdict=Verdict.FAILED,
            target_path="https://example.com/icon.svg",
        )
        self.assertFalse(fix_allowed_for_result(result))

    def test_xml_file_stays_explain_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.xml"
            path.write_text("<root/>", encoding="utf-8")
            result = CheckResult(verdict=Verdict.FAILED, target_path=str(path))
            self.assertFalse(fix_allowed_for_result(result))

    def test_clipboard_svg_snapshot_is_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / f"{CLIPBOARD_STEM}.svg"
            snap.write_text("<svg></svg>", encoding="utf-8")
            result = CheckResult(verdict=Verdict.FAILED, target_path=str(snap))
            self.assertFalse(fix_allowed_for_result(result))

    def test_apply_edits_svg_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            icon = Path(tmp) / "icon.svg"
            icon.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><title></title></svg>\n',
                encoding="utf-8",
            )
            out = apply_text_replacements(
                icon,
                [("icon.svg", "<title></title>", "<title>Logo</title>")],
            )
            self.assertTrue(out.ok, out.error_key)
            self.assertIn("<title>Logo</title>", icon.read_text(encoding="utf-8"))

    def test_gather_svg_excerpt_and_no_opf_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            icon = Path(tmp) / "icon.svg"
            icon.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><title></title></svg>\n',
                encoding="utf-8",
            )
            issue = Issue(
                Severity.ERROR,
                "error",
                "Element title is missing a child",
                location=f"{icon}:1:40",
                source="Nu HTML Checker",
            )
            result = CheckResult(
                verdict=Verdict.FAILED,
                tool_name="Nu HTML Checker",
                target_path=str(icon),
                issues=[issue],
            )
            with mock.patch(
                "checkmate.ai.context.send_file_context_enabled",
                return_value=True,
            ):
                ctx = gather_issue_context(issue, result, target_path=str(icon))
            self.assertEqual(ctx.get("publication_kind"), "svg")
            self.assertEqual(ctx.get("file_member"), "icon.svg")
            self.assertIn("<title>", ctx.get("file_excerpt_raw") or "")
            prompt = build_fix_user_prompt(ctx, issue=issue)
            self.assertIn("FILE TYPE: SVG document", prompt)
            self.assertNotIn("CROSS-FILE FIXES", prompt)

    def test_parse_css_and_mathml_line_locations(self) -> None:
        self.assertEqual(parse_issue_location("theme.css:10:1"), ("theme.css", 10))
        self.assertEqual(parse_issue_location("eq.mml:3:2"), ("eq.mml", 3))


if __name__ == "__main__":
    unittest.main()
