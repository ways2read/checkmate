"""Working copies of packaged EPUB files for external editors."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from checkmate.package_working_copy import WorkingCopies
from checkmate.workspace_files import open_source


def _make_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        zf.writestr("META-INF/container.xml", "<container/>", compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("EPUB/ch1.xhtml", "<p>one</p>\r\n", compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("EPUB/ch2.xhtml", "<p>two</p>\r\n", compress_type=zipfile.ZIP_DEFLATED)


def _touch_later(path: Path, data: bytes) -> None:
    """Write as an editor would, with a clearly newer modification time."""
    path.write_bytes(data)
    later = time.time() + 5
    os.utime(path, (later, later))


class WorkingCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.epub = Path(self._tmp.name) / "book.epub"
        _make_epub(self.epub)
        self.copies = WorkingCopies()
        self.addCleanup(self.copies.remove_all)
        self.copy = self.copies.for_source(open_source(self.epub))

    def _member(self, name: str) -> bytes:
        with zipfile.ZipFile(self.epub) as zf:
            return zf.read(name)

    def test_extracts_only_the_member_opened_into_a_folder_named_after_the_package(self) -> None:
        path = self.copy.file_for("EPUB/ch1.xhtml")
        self.assertEqual(path.read_bytes(), b"<p>one</p>\r\n")
        self.assertEqual(path.parent.parent.name, "book.epub")
        self.assertFalse((self.copy.folder / "EPUB" / "ch2.xhtml").exists())

    def test_nothing_to_write_back_until_the_editor_saves(self) -> None:
        self.copy.file_for("EPUB/ch1.xhtml")
        self.assertIsNone(self.copy.write_back({}))
        self.assertFalse(self.copies.has_pending())

    def test_write_back_replaces_the_member_and_backs_up_once(self) -> None:
        path = self.copy.file_for("EPUB/ch1.xhtml")
        _touch_later(path, b"<p>ONE</p>\r\n")
        backups: dict[str, str] = {}
        written = self.copy.write_back(backups)
        self.assertEqual(written.members, ["EPUB/ch1.xhtml"])
        self.assertEqual(self._member("EPUB/ch1.xhtml"), b"<p>ONE</p>\r\n")
        self.assertEqual(self._member("EPUB/ch2.xhtml"), b"<p>two</p>\r\n")
        with zipfile.ZipFile(self.epub) as zf:
            first = zf.infolist()[0]
            self.assertEqual((first.filename, first.compress_type), ("mimetype", zipfile.ZIP_STORED))
        backup = Path(written.outcome.backup_path)
        with zipfile.ZipFile(backup) as zf:
            self.assertEqual(zf.read("EPUB/ch1.xhtml"), b"<p>one</p>\r\n")
        _touch_later(path, b"<p>ONE!</p>\r\n")
        self.assertEqual(self.copy.write_back(backups).outcome.backup_path, str(backup))
        self.assertIsNone(self.copy.write_back(backups))

    def test_saving_identical_bytes_does_not_rewrite_the_package(self) -> None:
        path = self.copy.file_for("EPUB/ch1.xhtml")
        before = self.epub.stat().st_mtime_ns
        _touch_later(path, b"<p>one</p>\r\n")
        self.assertIsNone(self.copy.write_back({}))
        self.assertEqual(self.epub.stat().st_mtime_ns, before)

    def test_refresh_follows_package_changes_but_never_overwrites_editor_saves(self) -> None:
        ch1 = self.copy.file_for("EPUB/ch1.xhtml")
        ch2 = self.copy.file_for("EPUB/ch2.xhtml")
        from checkmate.workspace_files import rewrite_package_members

        # CheckMate changed both (Edit files, Apply fix); the editor saved ch2.
        rewrite_package_members(self.epub, {"EPUB/ch1.xhtml": b"A", "EPUB/ch2.xhtml": b"B"})
        _touch_later(ch2, b"<p>mine</p>")
        self.copies.refresh_unchanged()
        self.assertEqual(ch1.read_bytes(), b"A")
        self.assertEqual(ch2.read_bytes(), b"<p>mine</p>")
        self.assertEqual(self.copy.changed(), ["EPUB/ch2.xhtml"])

    def test_remove_all_deletes_the_temporary_folder(self) -> None:
        self.copy.file_for("EPUB/ch1.xhtml")
        folder = self.copy.folder.parent
        self.copies.remove_all()
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
