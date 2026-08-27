"""Tests for branded issue-details HTML used in the dialog WebView."""

from __future__ import annotations

import unittest

from checkmate.ai.markdown_html import issue_details_markdown, issue_details_page
from checkmate.models import Issue, Severity


class IssueDetailsPageTests(unittest.TestCase):
    def _issue(self, **kwargs) -> Issue:
        defaults = dict(
            severity=Severity.ERROR,
            source="Ace",
            code="image-alt",
            location="EPUB/xhtml/ch1.xhtml#line=42",
            message="Images must have alternate text.",
        )
        defaults.update(kwargs)
        return Issue(**defaults)

    def test_escapes_message_markup(self) -> None:
        issue = self._issue(message='See <script>alert(1)</script> and <b>bold</b>.')
        html = issue_details_page(issue, count=1)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", html)

    def test_severity_badge_class(self) -> None:
        html = issue_details_page(self._issue(severity=Severity.WARNING), count=1)
        self.assertIn('class="sev sev-warning"', html)
        self.assertIn("Warning", html)

    def test_occurrences_when_count_gt_one(self) -> None:
        html = issue_details_page(self._issue(), count=3)
        self.assertIn("3", html)
        # Occurrences label appears in the meta grid.
        self.assertIn("issue-meta", html)

    def test_help_section_from_ace_fields(self) -> None:
        html = issue_details_page(
            self._issue(
                source="Ace",
                help_title="Page List",
                help_text="Provide accessible page break labels.",
                help_url="http://kb.daisy.org/publishing/docs/navigation/pagelist.html",
            ),
            count=1,
        )
        self.assertIn("Help", html)
        self.assertIn("Page List", html)
        self.assertIn("Provide accessible page break labels.", html)
        self.assertIn(
            "https://kb.daisy.org/publishing/docs/navigation/pagelist.html", html
        )
        self.assertIn("<a href=", html)

    def test_help_falls_back_to_kb_map(self) -> None:
        html = issue_details_page(
            self._issue(source="Ace", code="pagebreak-label"), count=1
        )
        self.assertIn("Help", html)
        self.assertIn("pagelist.html", html)

        html = issue_details_page(
            self._issue(impact="serious", source="Ace"), count=1
        )
        self.assertIn("Serious", html)
        self.assertIn("Impact", html)

    def test_impact_omitted_when_empty(self) -> None:
        html = issue_details_page(self._issue(impact=""), count=1)
        self.assertNotIn(">Impact<", html)

    def test_ruleset_shown_when_present(self) -> None:
        html = issue_details_page(
            self._issue(ruleset="WCAG 2.0 A", source="Ace"), count=1
        )
        self.assertIn("Ruleset", html)
        self.assertIn("WCAG 2.0 A", html)
        md = issue_details_markdown(
            self._issue(ruleset="EPUB", source="Ace"), count=1
        )
        self.assertIn("## Ruleset", md)
        self.assertIn("EPUB", md)

    def test_ruleset_omitted_when_empty(self) -> None:
        html = issue_details_page(self._issue(ruleset=""), count=1)
        self.assertNotIn(">Ruleset<", html)

    def test_generic_document_title_not_code(self) -> None:
        html = issue_details_page(self._issue(code="aria-required-children"), count=1)
        self.assertIn("<title>", html)
        # Document title must stay generic for screen readers (dialog chrome
        # carries the code separately).
        title_start = html.index("<title>") + len("<title>")
        title_end = html.index("</title>")
        title = html[title_start:title_end]
        self.assertNotIn("aria-required-children", title)

    def test_brand_paper_token_present(self) -> None:
        html = issue_details_page(self._issue(), count=1)
        self.assertIn("--paper: #eef5fb", html)
        self.assertIn("field-block", html)

    def test_code_in_own_block_meta_has_severity_source(self) -> None:
        html = issue_details_page(
            self._issue(code="image-alt", impact="serious", source="Ace"), count=1
        )
        self.assertIn('class="field-block code-block"', html)
        # Code lives outside the meta grid so severity/impact/source share a row.
        code_idx = html.index("code-block")
        meta_idx = html.index('class="issue-meta"')
        self.assertLess(code_idx, meta_idx)
        meta_section = html[meta_idx : html.index("</section>", meta_idx)]
        self.assertIn("Severity", meta_section)
        self.assertIn("Impact", meta_section)
        self.assertIn("Source", meta_section)
        self.assertNotIn(">Code<", meta_section)
        self.assertIn("image-alt", html)

    def test_markdown_helper_still_lists_fields(self) -> None:
        md = issue_details_markdown(self._issue(), count=2)
        self.assertIn("image-alt", md)
        self.assertIn("2", md)

    def test_snippet_shown_when_present(self) -> None:
        issue = self._issue(snippet="<mo>-</mo>")
        html = issue_details_page(issue, count=1)
        self.assertIn(">Snippet<", html)
        self.assertIn("&lt;mo&gt;-&lt;/mo&gt;", html)
        md = issue_details_markdown(issue, count=1)
        self.assertIn("## Snippet", md)
        self.assertIn("<mo>-</mo>", md)

    def test_snippet_omitted_when_empty(self) -> None:
        html = issue_details_page(self._issue(snippet=""), count=1)
        self.assertNotIn(">Snippet<", html)


class IssueDetailFollowupI18nTests(unittest.TestCase):
    def test_fix_followup_does_not_shadow_gettext(self) -> None:
        import inspect

        from checkmate.main import IssueDetailDialog

        fix_src = inspect.getsource(IssueDetailDialog._build_fix_followup)
        explain_src = inspect.getsource(IssueDetailDialog._build_explain_followup)
        self.assertNotIn(", _ =", fix_src)
        self.assertNotIn(", _ =", explain_src)
        self.assertIn('_("Apply fix and validate")', fix_src)


class IssueDetailCloseTests(unittest.TestCase):
    def test_close_ends_modal_without_waiting_for_idle(self) -> None:
        import inspect

        from checkmate.main import IssueDetailDialog

        close_src = inspect.getsource(IssueDetailDialog._on_close_dialog)
        self.assertIn("_ensure_end_modal", close_src)
        ensure_src = inspect.getsource(IssueDetailDialog._ensure_end_modal)
        self.assertIn("EndModal", ensure_src)
        self.assertIn("parent.Enable(True)", ensure_src)


if __name__ == "__main__":
    unittest.main()
