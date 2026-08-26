"""MathML quality heuristics and Explain links to the Nordic guidelines."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest import mock

from checkmate import settings as settings_mod
from checkmate.ai.explain import build_system_prompt
from checkmate.ai.resources import (
    authoritative_guidance_for_explain,
    primary_kb_resource,
    resources_for_issue,
)
from checkmate.mathml_quality import (
    MATHML_GUIDELINES_URL,
    MATHML_QUALITY_DISPLAY_NAME,
    attach_mathml_quality,
    issues_from_mathml_text,
)
from checkmate.models import CheckResult, Issue, Severity, Verdict
from checkmate.report_export import report_title


NS = "http://www.w3.org/1998/Math/MathML"


def _math(inner: str) -> str:
    return f'<math xmlns="{NS}">{inner}</math>'


class ScanRulesTests(unittest.TestCase):
    def test_mfenced(self) -> None:
        issues = issues_from_mathml_text(_math("<mfenced><mi>x</mi></mfenced>"))
        codes = {i.code for i in issues}
        self.assertIn("mathml-mfenced", codes)

    def test_hyphen_minus_in_mo(self) -> None:
        issues = issues_from_mathml_text(_math("<mi>x</mi><mo>-</mo><mi>y</mi>"))
        self.assertTrue(any(i.code == "mathml-hyphen-minus" for i in issues))

    def test_math_minus_not_flagged(self) -> None:
        issues = issues_from_mathml_text(_math("<mi>x</mi><mo>\u2212</mo><mi>y</mi>"))
        self.assertFalse(any(i.code == "mathml-hyphen-minus" for i in issues))

    def test_unicode_root(self) -> None:
        issues = issues_from_mathml_text(_math("<mi>\u221a</mi>"))
        self.assertTrue(any(i.code == "mathml-unicode-root" for i in issues))

    def test_macron(self) -> None:
        issues = issues_from_mathml_text(_math("<mi>x\u0304</mi>"))
        self.assertTrue(any(i.code == "mathml-macron" for i in issues))

    def test_empty_msup_exponent(self) -> None:
        issues = issues_from_mathml_text(_math("<msup><mi>x</mi><mi></mi></msup>"))
        empty = [i for i in issues if i.code == "mathml-empty"]
        self.assertTrue(empty)
        self.assertTrue(any("exponent" in i.message or "Empty mi" in i.message for i in empty))

    def test_clean_expression_has_no_hits(self) -> None:
        issues = issues_from_mathml_text(
            _math("<mi>x</mi><mo>\u2212</mo><msqrt><mn>2</mn></msqrt>")
        )
        self.assertEqual(issues, [])

    def test_html_fragment_still_scanned(self) -> None:
        html = (
            "<!DOCTYPE html><html><body>"
            f"{_math('<mi>x</mi><mo>-</mo><mi>y</mi>')}"
            "</body></html>"
        )
        issues = issues_from_mathml_text(html)
        self.assertTrue(any(i.code == "mathml-hyphen-minus" for i in issues))

    def test_hyphen_outside_math_not_flagged(self) -> None:
        html = (
            "<!DOCTYPE html><html><body><p>a-b</p>"
            "<math><mi>x</mi></math>"
            "</body></html>"
        )
        issues = issues_from_mathml_text(html)
        self.assertFalse(any(i.code == "mathml-hyphen-minus" for i in issues))

    def test_issues_carry_guidelines_link(self) -> None:
        issues = issues_from_mathml_text(_math("<mfenced><mi>x</mi></mfenced>"))
        self.assertTrue(issues)
        self.assertEqual(issues[0].source, MATHML_QUALITY_DISPLAY_NAME)
        self.assertEqual(issues[0].help_url, MATHML_GUIDELINES_URL)
        self.assertEqual(issues[0].help_title, "Nordic MathML Guidelines")
        self.assertEqual(issues[0].severity, Severity.WARNING)


class MergeAndExplainTests(unittest.TestCase):
    def test_merge_promotes_passed_to_warnings(self) -> None:
        result = CheckResult(
            verdict=Verdict.PASSED,
            tool_name="Nu HTML Checker",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eq.mml"
            path.write_text(_math("<mi>x</mi><mo>-</mo><mn>1</mn>"), encoding="utf-8")
            merged = attach_mathml_quality(result, str(path), enabled=True)
        self.assertEqual(merged.verdict, Verdict.PASSED_WITH_WARNINGS)
        self.assertGreater(merged.warnings, 0)
        self.assertIn("MathML quality", merged.tool_name)
        self.assertEqual(report_title(merged), "Nu HTML Checker + MathML quality report")

    def test_explain_lists_guidelines_first(self) -> None:
        issue = Issue(
            Severity.WARNING,
            "mathml-hyphen-minus",
            "Use math minus",
            source=MATHML_QUALITY_DISPLAY_NAME,
            help_url=MATHML_GUIDELINES_URL,
            help_title="Nordic MathML Guidelines",
        )
        resources = resources_for_issue(issue)
        self.assertTrue(resources)
        self.assertIn("nlbdev/mathml-guidelines", resources[0][1])
        primary = primary_kb_resource(issue)
        self.assertIsNotNone(primary)
        assert primary is not None
        self.assertEqual(primary[1], MATHML_GUIDELINES_URL)
        guidance = authoritative_guidance_for_explain(issue)
        self.assertIn("Nordic MathML Guidelines", guidance)
        self.assertIn("false-positive", guidance)
        self.assertNotIn("web page", guidance)
        prompt = build_system_prompt(issue)
        self.assertIn("MathML accessibility", prompt)
        self.assertIn("nlbdev/mathml-guidelines", prompt)


class NordicFullGuidelinesTests(unittest.TestCase):
    def test_setting_defaults_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertFalse(settings_mod.mathml_nordic_guidelines())
                settings_mod.update_settings(mathml_nordic_guidelines=True)
                self.assertTrue(settings_mod.mathml_nordic_guidelines())

    def test_attach_skips_when_disabled(self) -> None:
        result = CheckResult(verdict=Verdict.PASSED, tool_name="Nu HTML Checker")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eq.mml"
            path.write_text(_math("<mfenced><mi>x</mi></mfenced>"), encoding="utf-8")
            merged = attach_mathml_quality(result, str(path), enabled=False)
        self.assertEqual(merged.issues, [])
        self.assertEqual(merged.verdict, Verdict.PASSED)

    def test_missing_namespace(self) -> None:
        issues = issues_from_mathml_text("<math><mi>x</mi></math>")
        self.assertTrue(any(i.code == "mathml-namespace" for i in issues))

    def test_alttext(self) -> None:
        issues = issues_from_mathml_text(
            f'<math xmlns="{NS}" alttext="x"><mi>x</mi></math>'
        )
        self.assertTrue(any(i.code == "mathml-alttext" for i in issues))

    def test_outer_mrow(self) -> None:
        issues = issues_from_mathml_text(_math("<mrow><mi>x</mi></mrow>"))
        codes = {i.code for i in issues}
        self.assertIn("mathml-outer-mrow", codes)
        self.assertIn("mathml-singleton-mrow", codes)

    def test_invisible_times_and_function_apply(self) -> None:
        issues = issues_from_mathml_text(
            _math("<mn>2</mn><mi>x</mi><mi>sin</mi><mo>(</mo><mi>x</mi><mo>)</mo>")
        )
        codes = {i.code for i in issues}
        self.assertIn("mathml-invisible-times", codes)
        self.assertIn("mathml-function-apply", codes)

    def test_punct_in_mo(self) -> None:
        issues = issues_from_mathml_text(_math("<mi>x</mi><mo>=</mo><mn>5</mn><mo>.</mo>"))
        self.assertTrue(any(i.code == "mathml-punct-mo" for i in issues))

    def test_mtext_letter_and_adjacent(self) -> None:
        issues = issues_from_mathml_text(
            _math("<mtext>a</mtext><mtext>b</mtext>")
        )
        codes = {i.code for i in issues}
        self.assertIn("mathml-mtext-letter", codes)
        self.assertIn("mathml-adjacent-mtext", codes)

    def test_ocr_exponent(self) -> None:
        issues = issues_from_mathml_text(_math("<msup><mn>10</mn><mi>o</mi></msup>"))
        self.assertTrue(any(i.code == "mathml-ocr-exponent" for i in issues))

    def test_content_and_semantics(self) -> None:
        issues = issues_from_mathml_text(
            _math("<semantics><apply><plus/><ci>x</ci></apply></semantics>")
        )
        codes = {i.code for i in issues}
        self.assertIn("mathml-semantics", codes)
        self.assertIn("mathml-content", codes)

    def test_correct_invisible_times_not_flagged(self) -> None:
        issues = issues_from_mathml_text(
            _math(f"<mn>2</mn><mo>{chr(0x2062)}</mo><mi>x</mi>")
        )
        self.assertFalse(any(i.code == "mathml-invisible-times" for i in issues))


if __name__ == "__main__":
    unittest.main()
