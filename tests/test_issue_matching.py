"""Following an issue across re-checks (Edit files issue strip, Fix with AI verify)."""

from __future__ import annotations

import unittest

from checkmate.ai.fix import issue_still_present, matching_issues
from checkmate.models import CheckResult, Issue, Severity, Verdict


def _issue(location: str, *, code: str = "RSC-005", message: str = "bad attribute") -> Issue:
    return Issue(Severity.ERROR, code, message, location=location, source="EPUBCheck")


def _result(*issues: Issue) -> CheckResult:
    return CheckResult(verdict=Verdict.FAILED, errors=len(issues), issues=list(issues))


class MatchingIssuesTests(unittest.TestCase):
    def test_follows_the_issue_when_its_line_moves(self) -> None:
        seed = _issue("EPUB/ch1.xhtml:12:4")
        moved = _issue("EPUB/ch1.xhtml:30:4")
        self.assertEqual(matching_issues(seed, _result(moved)), [moved])
        self.assertTrue(issue_still_present(seed, _result(moved)))

    def test_other_files_codes_and_messages_do_not_match(self) -> None:
        seed = _issue("EPUB/ch1.xhtml:12:4")
        others = _result(
            _issue("EPUB/ch2.xhtml:12:4"),
            _issue("EPUB/ch1.xhtml:12:4", code="RSC-012"),
            _issue("EPUB/ch1.xhtml:12:4", message="something else"),
        )
        self.assertEqual(matching_issues(seed, others), [])
        self.assertFalse(issue_still_present(seed, others))

    def test_counts_every_similar_issue_in_the_file(self) -> None:
        seed = _issue("EPUB/ch1.xhtml:12:4")
        result = _result(_issue("EPUB/ch1.xhtml:3:1"), _issue("EPUB/ch1.xhtml:40:2"))
        self.assertEqual(len(matching_issues(seed, result)), 2)


if __name__ == "__main__":
    unittest.main()
