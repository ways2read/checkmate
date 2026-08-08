"""Tests for Ace / EPUBCheck → authoritative Learn more links."""

from __future__ import annotations

import unittest

from checkmate.ai.ace_kb_map import kb_resource_for_ace_code, normalize_kb_url
from checkmate.ai.epubcheck_kb_map import (
    EPUBCHECK_MESSAGES_URL,
    kb_resource_for_epubcheck_code,
    normalize_epubcheck_code,
)
from checkmate.ai.explain import build_system_prompt
from checkmate.ai.fix import build_fix_user_prompt
from checkmate.ai.resources import (
    authoritative_guidance_for_explain,
    authoritative_guidance_for_fix,
    primary_kb_resource,
    resources_for_issue,
    resources_prompt_block,
)
from checkmate.models import Issue, Severity


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


if __name__ == "__main__":
    unittest.main()
