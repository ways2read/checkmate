"""Tests for multi-patch Fix all like this helpers."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from checkmate.ai.context import issues_matching_seed
from checkmate.ai.fix import (
    BatchFixProposal,
    FixProposal,
    apply_proposed_fixes,
    evaluate_fix_outcome,
    format_batch_fix_preview,
    parse_batch_fix_proposal,
    parse_extra_backups,
)
from checkmate.edit_log import log_batch_fix_applied
from checkmate.epub_package import apply_text_replacements
from checkmate.models import CheckResult, Issue, Severity, Verdict


def _issue(
    code: str,
    *,
    location: str,
    source: str = "Ace",
    message: str = "Missing alt",
) -> Issue:
    return Issue(
        Severity.ERROR,
        code,
        message,
        location=location,
        source=source,
    )


class BatchFixParseTests(unittest.TestCase):
    def test_parse_patches_array(self) -> None:
        text = """## Proposed fix
Fix alts in two files.

```json
{
  "patches": [
    {"file": "OEBPS/a.xhtml", "original": "<img src=\\"a.png\\"/>", "replacement": "<img src=\\"a.png\\" alt=\\"A\\"/>"},
    {"file": "OEBPS/b.xhtml", "original": "<img src=\\"b.png\\"/>", "replacement": "<img src=\\"b.png\\" alt=\\"B\\"/>"}
  ],
  "skipped": ["c.xhtml: ambiguous"]
}
```
"""
        batch = parse_batch_fix_proposal(text)
        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertEqual(len(batch.patches), 2)
        self.assertEqual(batch.patches[0].file, "OEBPS/a.xhtml")
        self.assertEqual(batch.skipped, ["c.xhtml: ambiguous"])
        self.assertIn("Fix alts", batch.rationale)

    def test_parse_single_object_as_one_patch(self) -> None:
        text = """## Proposed fix
One fix.

```json
{"file": "x.xhtml", "original": "aa", "replacement": "bb"}
```
"""
        batch = parse_batch_fix_proposal(text)
        self.assertIsNotNone(batch)
        assert batch is not None
        self.assertEqual(len(batch.patches), 1)
        self.assertEqual(batch.patches[0].original, "aa")

    def test_format_preview_groups_by_file(self) -> None:
        batch = BatchFixProposal(
            rationale="Do the thing.",
            patches=[
                FixProposal("a.xhtml", "x", "y"),
                FixProposal("a.xhtml", "p", "q"),
                FixProposal("b.xhtml", "m", "n"),
            ],
            matched_issue_count=3,
        )
        preview = format_batch_fix_preview(batch)
        self.assertIn("a.xhtml", preview)
        self.assertIn("b.xhtml", preview)
        self.assertIn("```\nx\n```", preview)

    def test_parse_extra_backups(self) -> None:
        self.assertEqual(parse_extra_backups(""), [])
        self.assertEqual(
            parse_extra_backups("extra_backups=/tmp/a.bak|/tmp/a;/tmp/b.bak|/tmp/b"),
            [("/tmp/a.bak", "/tmp/a"), ("/tmp/b.bak", "/tmp/b")],
        )


class IssuesMatchingSeedTests(unittest.TestCase):
    def test_groups_by_code_keeps_distinct_locations(self) -> None:
        seed = _issue("image-alt", location="OEBPS/a.xhtml:10")
        result = CheckResult(
            verdict=Verdict.FAILED,
            issues=[
                seed,
                _issue("image-alt", location="OEBPS/a.xhtml:20"),
                _issue("image-alt", location="OEBPS/b.xhtml:5"),
                _issue("other", location="OEBPS/c.xhtml:1"),
            ],
        )
        matched = issues_matching_seed(seed, result)
        self.assertEqual(len(matched), 3)
        self.assertEqual(matched[0].location, seed.location)


class MultiPatchApplyTests(unittest.TestCase):
    def test_apply_two_members_exploded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "book"
            oebps = root / "OEBPS"
            oebps.mkdir(parents=True)
            a = oebps / "a.xhtml"
            b = oebps / "b.xhtml"
            a.write_text('<img src="a.png"/>\n', encoding="utf-8")
            b.write_text('<img src="b.png"/>\n', encoding="utf-8")
            out = apply_text_replacements(
                root,
                [
                    ("OEBPS/a.xhtml", '<img src="a.png"/>', '<img src="a.png" alt="A"/>'),
                    ("OEBPS/b.xhtml", '<img src="b.png"/>', '<img src="b.png" alt="B"/>'),
                ],
            )
            self.assertTrue(out.ok, out.error_key)
            self.assertTrue(out.backup_path)
            self.assertIn("extra_backups=", out.detail)
            self.assertIn('alt="A"', a.read_text(encoding="utf-8"))
            self.assertIn('alt="B"', b.read_text(encoding="utf-8"))
            self.assertTrue(Path(out.backup_path).is_file())

    def test_apply_proposed_fixes_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            meta = work / "META-INF"
            oebps = work / "OEBPS"
            meta.mkdir(parents=True)
            oebps.mkdir()
            (meta / "container.xml").write_text(
                '<?xml version="1.0"?>\n'
                "<container><rootfiles>"
                '<rootfile full-path="OEBPS/content.opf"/>'
                "</rootfiles></container>\n",
                encoding="utf-8",
            )
            (oebps / "content.opf").write_text(
                '<?xml version="1.0"?>\n<package><metadata/>'
                "<manifest/><spine/></package>\n",
                encoding="utf-8",
            )
            (oebps / "c1.xhtml").write_text(
                '<p class="x">one</p>\n<p class="y">two</p>\n',
                encoding="utf-8",
            )
            epub = root / "book.epub"
            with zipfile.ZipFile(epub, "w") as zf:
                zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                for path in work.rglob("*"):
                    if path.is_file():
                        zf.write(path, path.relative_to(work).as_posix())

            patches = [
                FixProposal(
                    "OEBPS/c1.xhtml",
                    '<p class="x">one</p>',
                    '<p class="x">ONE</p>',
                ),
                FixProposal(
                    "OEBPS/c1.xhtml",
                    '<p class="y">two</p>',
                    '<p class="y">TWO</p>',
                ),
            ]
            out = apply_proposed_fixes(patches, epub)
            self.assertTrue(out.ok, f"{out.error_key}: {out.detail}")
            self.assertTrue(Path(out.backup_path).is_file())
            with zipfile.ZipFile(epub) as zf:
                body = zf.read("OEBPS/c1.xhtml").decode("utf-8")
            self.assertIn("ONE", body)
            self.assertIn("TWO", body)


class BatchEvaluateAndLogTests(unittest.TestCase):
    def test_evaluate_batch_cleared(self) -> None:
        seed = _issue("image-alt", location="a.xhtml:1")
        before = CheckResult(
            verdict=Verdict.FAILED,
            issues=[seed, _issue("image-alt", location="b.xhtml:1")],
            errors=2,
        )
        after = CheckResult(verdict=Verdict.PASSED, issues=[], errors=0)
        report = evaluate_fix_outcome(seed, before, after, batch_mode=True)
        self.assertTrue(report.target_resolved)
        self.assertEqual(report.matched_before, 2)
        self.assertEqual(report.matched_after, 0)
        self.assertFalse(report.has_concerns)

    def test_log_batch_fix_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub = Path(tmp) / "demo.epub"
            epub.write_bytes(b"PK")
            bak = Path(tmp) / "demo.epub.bak"
            bak.write_bytes(b"PK")
            issue = _issue("image-alt", location="OEBPS/a.xhtml")
            log_path = log_batch_fix_applied(
                target_path=epub,
                issue=issue,
                backup_path=str(bak),
                patches=[
                    ("OEBPS/a.xhtml", "<img/>", '<img alt="a"/>'),
                    ("OEBPS/b.xhtml", "<img/>", '<img alt="b"/>'),
                ],
                rationale="Add alts.",
                matched_issue_count=2,
            )
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("AI batch fix applied", text)
            self.assertIn("**Patches:** 2", text)
            self.assertIn("Add alts.", text)


if __name__ == "__main__":
    unittest.main()
