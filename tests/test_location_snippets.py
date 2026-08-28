"""Source snippets for location-only checker issues (eBraille, EPUBCheck, …)."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from checkmate.ai.context import (
    attach_location_snippets,
    compact_source_snippet,
    gather_issue_context,
    parse_issue_location,
    parse_issue_location_parts,
)
from checkmate.ai.explain import build_user_prompt
from checkmate.ai.fix import build_fix_user_prompt
from checkmate.ai.markdown_html import issue_details_page
from checkmate.models import CheckResult, Issue, Severity, Verdict

_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>vol1</title></head>
<body>
<p class="front">Intro</p>
<h1 id="ch1">Chapter</h1>
<p>The <span class="error">flagged</span> word.</p>
</body>
</html>
"""


class LocationPartsTests(unittest.TestCase):
    def test_epubcheck_paren_includes_column(self) -> None:
        self.assertEqual(
            parse_issue_location_parts("ebraille/vol1.xhtml (61,24)"),
            ("ebraille/vol1.xhtml", 61, 24),
        )
        self.assertEqual(
            parse_issue_location("ebraille/vol1.xhtml (61,24)"),
            ("ebraille/vol1.xhtml", 61),
        )

    def test_colon_line_col(self) -> None:
        self.assertEqual(
            parse_issue_location_parts("OEBPS/ch.xhtml:8:3"),
            ("OEBPS/ch.xhtml", 8, 3),
        )

    def test_http_url_keeps_column(self) -> None:
        self.assertEqual(
            parse_issue_location_parts("http://127.0.0.1:53757/index.html:12:3"),
            ("index.html", 12, 3),
        )


class CompactSnippetTests(unittest.TestCase):
    def test_snippet_starts_at_tag_on_line(self) -> None:
        text = _XHTML
        # Line 7 is the paragraph with the span (1-based).
        snippet = compact_source_snippet(text, 7, 15)
        self.assertIn("<span class=\"error\">flagged</span>", snippet)
        self.assertNotIn("<?xml", snippet)

    def test_missing_line_is_empty(self) -> None:
        self.assertEqual(compact_source_snippet(_XHTML, None), "")
        self.assertEqual(compact_source_snippet(_XHTML, 99), "")


class AttachAndPayloadTests(unittest.TestCase):
    def _issue(self) -> Issue:
        return Issue(
            severity=Severity.ERROR,
            code="RSC-005",
            message="Error while parsing file: element \"span\" not allowed here",
            location="ebraille/vol1.xhtml (7,15)",
            source="eBraille Checker",
        )

    def _packaged(self, tmp: Path) -> Path:
        path = tmp / "book.ebrl"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ebraille/vol1.xhtml", _XHTML)
        return path

    def test_attach_fills_snippet_from_ebrl_zip(self) -> None:
        issue = self._issue()
        with tempfile.TemporaryDirectory() as tmp:
            ebrl = self._packaged(Path(tmp))
            result = CheckResult(
                verdict=Verdict.FAILED,
                issues=[issue],
                target_path=str(ebrl),
            )
            attach_location_snippets(result)
        self.assertIn("<span class=\"error\">flagged</span>", issue.snippet)
        html = issue_details_page(issue, count=1)
        self.assertIn(">Snippet<", html)
        self.assertIn("flagged", html)

    def test_explain_and_fix_prompts_include_snippet(self) -> None:
        issue = self._issue()
        with tempfile.TemporaryDirectory() as tmp:
            ebrl = self._packaged(Path(tmp))
            result = CheckResult(
                verdict=Verdict.FAILED,
                tool_name="eBraille Checker",
                issues=[issue],
                target_path=str(ebrl),
            )
            with mock.patch(
                "checkmate.ai.context.send_file_context_enabled",
                return_value=True,
            ):
                ctx = gather_issue_context(issue, result, target_path=str(ebrl))
        self.assertIn("<span class=\"error\">flagged</span>", ctx.get("snippet") or "")
        self.assertIn("flagged", ctx.get("file_excerpt_raw") or "")
        explain = build_user_prompt(ctx)
        self.assertIn("Flagged markup at the reported location", explain)
        self.assertIn("flagged", explain)
        fix = build_fix_user_prompt(ctx, issue=issue)
        self.assertIn("Flagged markup at the reported location", fix)
        self.assertIn("flagged", fix)

    def test_does_not_overwrite_existing_snippet(self) -> None:
        issue = self._issue()
        issue.snippet = "<mo>-</mo>"
        with tempfile.TemporaryDirectory() as tmp:
            ebrl = self._packaged(Path(tmp))
            result = CheckResult(
                verdict=Verdict.FAILED,
                issues=[issue],
                target_path=str(ebrl),
            )
            attach_location_snippets(result)
        self.assertEqual(issue.snippet, "<mo>-</mo>")


if __name__ == "__main__":
    unittest.main()
