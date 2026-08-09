"""Tests for Ace / EPUBCheck → authoritative Learn more links."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from checkmate.ai.ace_kb_map import kb_resource_for_ace_code, normalize_kb_url
from checkmate.ai.epubcheck_kb_map import (
    EPUBCHECK_MESSAGES_URL,
    kb_resource_for_epubcheck_code,
    normalize_epubcheck_code,
)
from checkmate.ai.explain import build_system_prompt, build_user_prompt
from checkmate.ai.fix import build_fix_user_prompt
from checkmate.ai.resources import (
    authoritative_guidance_for_explain,
    authoritative_guidance_for_fix,
    kb_article_body_for_prompt,
    primary_kb_resource,
    resources_for_issue,
    resources_prompt_block,
)
from checkmate.ace_check import _issues_from_ace_report, ruleset_label_from_tags
from checkmate.models import Issue, Severity


class AceRulesetLabelTests(unittest.TestCase):
    def test_maps_common_tags(self) -> None:
        self.assertEqual(ruleset_label_from_tags(["wcag2a"]), "WCAG 2.0 A")
        self.assertEqual(ruleset_label_from_tags(["wcag2aaa"]), "WCAG 2.0 AAA")
        self.assertEqual(ruleset_label_from_tags(["EPUB"]), "EPUB")
        self.assertEqual(
            ruleset_label_from_tags(["best-practice"]), "Best Practice"
        )

    def test_skips_axe_category_noise(self) -> None:
        self.assertEqual(
            ruleset_label_from_tags(["wcag2aa", "cat.keyboard", "wcag144"]),
            "WCAG 2.0 AA",
        )

    def test_parse_report_includes_ruleset(self) -> None:
        data = {
            "assertions": [
                {
                    "earl:testSubject": {"url": "EPUB/xhtml/ch1.xhtml"},
                    "assertions": [
                        {
                            "earl:result": {
                                "earl:outcome": "fail",
                                "dct:description": "missing alt",
                            },
                            "earl:test": {
                                "dct:title": "image-alt",
                                "earl:impact": "critical",
                                "rulesetTags": ["wcag2a", "cat.text-alternatives"],
                            },
                        }
                    ],
                }
            ]
        }
        issues = _issues_from_ace_report(data)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].ruleset, "WCAG 2.0 A")
        self.assertEqual(issues[0].impact, "critical")


class AceKbMapTests(unittest.TestCase):
    def test_normalize_http_to_https(self) -> None:
        self.assertEqual(
            normalize_kb_url("http://kb.daisy.org/publishing/docs/html/images.html"),
            "https://kb.daisy.org/publishing/docs/html/images.html",
        )

    def test_axe_rule_maps_to_article(self) -> None:
        title, url = kb_resource_for_ace_code("image-alt")  # type: ignore[misc]
        self.assertIn("Images", title)
        self.assertEqual(url, "https://kb.daisy.org/publishing/docs/html/images.html")

    def test_epub_ace_rule_maps(self) -> None:
        title, url = kb_resource_for_ace_code("metadata-accessmode")  # type: ignore[misc]
        self.assertIn("Schema.org", title)
        self.assertTrue(url.endswith("docs/metadata/schema.org/index.html"))


class EpubcheckKbMapTests(unittest.TestCase):
    def test_normalize_hyphen_and_case(self) -> None:
        self.assertEqual(normalize_epubcheck_code("opf-049"), "OPF_049")
        self.assertEqual(normalize_epubcheck_code("ACC_001"), "ACC_001")

    def test_acc_maps_to_daisy_kb(self) -> None:
        title, url = kb_resource_for_epubcheck_code("ACC-001")  # type: ignore[misc]
        self.assertIn("Images", title)
        self.assertTrue(url.endswith("docs/html/images.html"))

    def test_unmapped_code_has_no_kb_article(self) -> None:
        self.assertIsNone(kb_resource_for_epubcheck_code("OPF-049"))


class ResourcesForIssueTests(unittest.TestCase):
    def test_specific_kb_is_first_for_ace(self) -> None:
        issue = Issue(
            Severity.ERROR,
            "document-title",
            "Document does not have a non-empty title",
            source="Ace",
        )
        resources = resources_for_issue(issue)
        self.assertGreaterEqual(len(resources), 2)
        self.assertEqual(
            resources[0][1],
            "https://kb.daisy.org/publishing/docs/html/title.html",
        )
        urls = [u for _t, u in resources]
        self.assertIn("https://kb.daisy.org/publishing/", urls)

    def test_help_url_from_ace_report_wins(self) -> None:
        issue = Issue(
            Severity.ERROR,
            "landmark-unique",
            "Landmarks must be unique",
            source="Ace",
            help_url="http://kb.daisy.org/publishing/docs/html/landmarks.html",
            help_title="Landmarks",
        )
        resources = resources_for_issue(issue)
        self.assertEqual(
            resources[0][1],
            "https://kb.daisy.org/publishing/docs/html/landmarks.html",
        )
        self.assertIn("Landmarks", resources[0][0])

    def test_prompt_lists_specific_first(self) -> None:
        issue = Issue(Severity.ERROR, "image-alt", "missing alt", source="Ace")
        block = resources_prompt_block(issue)
        self.assertIn("docs/html/images.html", block)
        self.assertLess(
            block.index("docs/html/images.html"),
            block.index("https://kb.daisy.org/publishing/\n")
            if "https://kb.daisy.org/publishing/\n" in block
            else block.index("https://kb.daisy.org/publishing/"),
        )

    def test_epubcheck_acc_lists_kb_then_messages(self) -> None:
        issue = Issue(
            Severity.USAGE,
            "ACC-001",
            'img has no alt attribute',
            source="EPUBCheck",
        )
        resources = resources_for_issue(issue)
        self.assertTrue(resources[0][1].endswith("docs/html/images.html"))
        urls = [u for _t, u in resources]
        self.assertIn(EPUBCHECK_MESSAGES_URL, urls)
        self.assertLess(urls.index(resources[0][1]), urls.index(EPUBCHECK_MESSAGES_URL))

    def test_epubcheck_structural_primary_is_messages_catalog(self) -> None:
        issue = Issue(
            Severity.ERROR,
            "OPF-049",
            'Item id "x" was not found in the manifest.',
            source="EPUBCheck",
        )
        resources = resources_for_issue(issue)
        self.assertEqual(resources[0][1], EPUBCHECK_MESSAGES_URL)
        primary = primary_kb_resource(issue)
        self.assertEqual(primary, ("EPUBCheck message reference", EPUBCHECK_MESSAGES_URL))


class AuthoritativeGuidanceTests(unittest.TestCase):
    def test_explain_guidance_names_primary_article(self) -> None:
        issue = Issue(Severity.ERROR, "image-alt", "missing alt", source="Ace")
        primary = primary_kb_resource(issue)
        self.assertIsNotNone(primary)
        assert primary is not None
        block = authoritative_guidance_for_explain(issue)
        self.assertIn("AUTHORITATIVE GUIDANCE", block)
        self.assertIn(primary[1], block)
        self.assertIn("Align", block)

    def test_explain_system_prompt_includes_guidance(self) -> None:
        issue = Issue(Severity.ERROR, "document-title", "empty title", source="Ace")
        prompt = build_system_prompt(issue)
        self.assertIn("AUTHORITATIVE GUIDANCE", prompt)
        self.assertIn("docs/html/title.html", prompt)

    def test_fix_guidance_prefers_kb_but_defers_to_file_text(self) -> None:
        issue = Issue(Severity.ERROR, "image-alt", "missing alt", source="Ace")
        block = authoritative_guidance_for_fix(issue)
        self.assertIn("AUTHORITATIVE GUIDANCE", block)
        self.assertIn("docs/html/images.html", block)
        self.assertIn("Exact file text", block)
        user = build_fix_user_prompt(
            {"code": "image-alt", "message": "missing alt", "member_kind": "html"},
            issue=issue,
        )
        self.assertIn("AUTHORITATIVE GUIDANCE", user)

    def test_epubcheck_explain_uses_messages_or_kb(self) -> None:
        acc = Issue(Severity.USAGE, "ACC_001", "no alt", source="EPUBCheck")
        prompt = build_system_prompt(acc)
        self.assertIn("AUTHORITATIVE GUIDANCE", prompt)
        self.assertIn("docs/html/images.html", prompt)

        opf = Issue(Severity.ERROR, "OPF-049", "missing id", source="EPUBCheck")
        prompt_opf = build_system_prompt(opf)
        self.assertIn(EPUBCHECK_MESSAGES_URL, prompt_opf)
        self.assertIn("AUTHORITATIVE GUIDANCE", prompt_opf)

    def test_non_checker_specific_fallback(self) -> None:
        issue = Issue(Severity.ERROR, "CUSTOM", "x", source="OtherTool")
        self.assertIsNone(primary_kb_resource(issue))
        self.assertEqual(authoritative_guidance_for_fix(issue), "")
        guidance = authoritative_guidance_for_explain(issue)
        self.assertIn("Do not invent conformance requirements", guidance)
        self.assertNotIn("Primary reference", guidance)


class KbArticleBodyPromptTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        issue = Issue(Severity.ERROR, "image-alt", "missing alt", source="Ace")
        with mock.patch(
            "checkmate.ai.resources.ai_send_kb_article_body", return_value=False
        ):
            self.assertEqual(kb_article_body_for_prompt(issue), "")

    def test_ace_includes_cached_body_when_enabled(self) -> None:
        issue = Issue(Severity.ERROR, "image-alt", "missing alt", source="Ace")
        slim = (
            '<!DOCTYPE html><html lang="en" data-cm-kb="article"><body>'
            '<div id="cm-kb-body"><h1>Images</h1>'
            "<p>Provide a text alternative for every image.</p>"
            "</div></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            en_path = root / "en" / "docs" / "html" / "images.html"
            en_path.parent.mkdir(parents=True)
            en_path.write_text(slim, encoding="utf-8")

            with (
                mock.patch(
                    "checkmate.ai.resources.ai_send_kb_article_body",
                    return_value=True,
                ),
                mock.patch(
                    "checkmate.kb.store.kb_dir",
                    return_value=root,
                ),
                mock.patch(
                    "checkmate.kb.fetch.kb_dir",
                    return_value=root,
                ),
                mock.patch(
                    "checkmate.kb.fetch.ensure_article_cached",
                    return_value=True,
                ),
            ):
                body = kb_article_body_for_prompt(issue)
        self.assertIn("Provide a text alternative", body)
        self.assertNotIn("<h1>", body)

        user = build_user_prompt(
            {"severity": "Error", "code": "image-alt", "message": "missing alt"},
            kb_body=body,
        )
        self.assertIn("Knowledge Base article body", user)
        self.assertIn("Provide a text alternative", user)

        fix_user = build_fix_user_prompt(
            {"code": "image-alt", "message": "missing alt", "member_kind": "html"},
            issue=issue,
            kb_body=body,
        )
        self.assertIn("Knowledge Base article body", fix_user)
        self.assertIn("do not copy markup from here", fix_user)

    def test_epubcheck_catalog_primary_has_no_body(self) -> None:
        issue = Issue(
            Severity.ERROR,
            "OPF-049",
            'Item id "x" was not found in the manifest.',
            source="EPUBCheck",
        )
        with mock.patch(
            "checkmate.ai.resources.ai_send_kb_article_body", return_value=True
        ):
            self.assertEqual(kb_article_body_for_prompt(issue), "")


class EpubAceMergeTests(unittest.TestCase):
    def test_merge_preserves_ace_impact_and_help(self) -> None:
        from checkmate.checker import _merge_epubcheck_and_ace
        from checkmate.models import CheckResult, Verdict

        epub = CheckResult(verdict=Verdict.PASSED, issues=[], tool_name="EPUBCheck")
        ace = CheckResult(
            verdict=Verdict.FAILED,
            issues=[
                Issue(
                    severity=Severity.ERROR,
                    code="pagebreak-label",
                    message="missing label",
                    source="Ace",
                    help_url="https://kb.daisy.org/publishing/docs/navigation/pagelist.html",
                    help_title="Page List",
                    help_text="Page breaks need accessible names.",
                    impact="serious",
                    ruleset="EPUB",
                )
            ],
            tool_name="Ace",
        )
        merged = _merge_epubcheck_and_ace(epub, ace)
        self.assertEqual(len(merged.issues), 1)
        issue = merged.issues[0]
        self.assertEqual(issue.impact, "serious")
        self.assertEqual(issue.help_title, "Page List")
        self.assertEqual(issue.help_text, "Page breaks need accessible names.")
        self.assertTrue(issue.help_url.endswith("pagelist.html"))
        self.assertEqual(issue.ruleset, "EPUB")


if __name__ == "__main__":
    unittest.main()
