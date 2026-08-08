"""Tests for CheckMate publication edit changelog."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from checkmate.edit_log import (
    changelog_path_for,
    find_changelog,
    log_fix_applied,
    log_fix_reverted,
    log_fix_validation,
)
from checkmate.models import Issue, Severity


class EditLogTests(unittest.TestCase):
    def test_changelog_path_packaged_and_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub = root / "book.epub"
            epub.write_bytes(b"PK")
            self.assertEqual(
                changelog_path_for(epub),
                root / "book.epub.checkmate-changelog.md",
            )
            folder = root / "exploded"
            folder.mkdir()
            self.assertEqual(
                changelog_path_for(folder),
                folder / "checkmate-changelog.md",
            )

    def test_find_changelog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub = root / "book.epub"
            epub.write_bytes(b"PK")
            self.assertIsNone(find_changelog(epub))
            log = root / "book.epub.checkmate-changelog.md"
            log.write_text("# log\n", encoding="utf-8")
            self.assertEqual(find_changelog(epub), log)

    def test_apply_validation_revert_trail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub = root / "demo.epub"
            epub.write_bytes(b"PK")
            bak = root / "demo.epub.bak"
            bak.write_bytes(b"PK")
            issue = Issue(
                Severity.ERROR,
                "image-alt",
                "Image missing alt text",
                location="OEBPS/ch1.xhtml",
                source="Ace",
            )
            log_path = log_fix_applied(
                target_path=epub,
                issue=issue,
                member="OEBPS/ch1.xhtml",
                backup_path=str(bak),
                rationale="Add alt text.",
                original='<img src="a.png"/>',
                replacement='<img src="a.png" alt="A"/>',
            )
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("CheckMate edit changelog", text)
            self.assertIn("demo.epub.bak", text)
            self.assertIn("image-alt", text)
            self.assertIn("AI fix applied", text)

            log_fix_validation(
                target_path=epub,
                issue=issue,
                backup_path=str(bak),
                outcome="confirmed",
            )
            log_fix_reverted(
                target_path=epub,
                issue=issue,
                backup_path=str(bak),
                restore_to=str(epub),
            )
            text2 = log_path.read_text(encoding="utf-8")
            self.assertIn("AI fix validation", text2)
            self.assertIn("confirmed", text2)
            self.assertIn("AI fix reverted", text2)


if __name__ == "__main__":
    unittest.main()
