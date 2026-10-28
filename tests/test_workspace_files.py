"""Tests for the Edit files workspace file helpers."""

from __future__ import annotations

import codecs
import tempfile
import unittest
import zipfile
from pathlib import Path

from checkmate.ai.context import issue_member_position
from checkmate.models import Issue, Severity
from checkmate.workspace_files import (
    READ_ONLY_PACKAGED,
    READ_ONLY_UNDECODABLE,
    decode_text,
    encode_text,
    is_text_member,
    load_text_document,
    open_source,
    resolve_member,
    revert_member,
    save_member_text,
)


def _make_folder(root: Path) -> Path:
    book = root / "book"
    (book / "META-INF").mkdir(parents=True)
    (book / "EPUB").mkdir()
    (book / "mimetype").write_bytes(b"application/epub+zip")
    (book / "META-INF" / "container.xml").write_bytes(b"<container/>")
    (book / "EPUB" / "package.opf").write_bytes(b"<package/>")
    (book / "EPUB" / "nav.xhtml").write_bytes(b"<html/>")
    (book / "EPUB" / "nav.xhtml.bak").write_bytes(b"old")
    (book / "checkmate-changelog.md").write_bytes(b"# log")
    return book


class SourceTests(unittest.TestCase):
    def test_folder_lists_files_without_checkmate_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = open_source(_make_folder(Path(tmp)))
            self.assertFalse(source.packaged)
            self.assertEqual(
                source.members,
                (
                    "EPUB/nav.xhtml",
                    "EPUB/package.opf",
                    "META-INF/container.xml",
                    "mimetype",
                ),
            )

    def test_package_lists_zip_members_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub = Path(tmp) / "book.epub"
            with zipfile.ZipFile(epub, "w") as zf:
                zf.writestr("mimetype", "application/epub+zip")
                zf.writestr("EPUB/", "")
                zf.writestr("EPUB/ch1.xhtml", "<html/>")
            source = open_source(epub)
            self.assertTrue(source.packaged)
            self.assertEqual(source.members, ("EPUB/ch1.xhtml", "mimetype"))
            doc = load_text_document(source, "EPUB/ch1.xhtml")
            self.assertEqual(doc.text, "<html/>")
            self.assertEqual(doc.read_only_reason, READ_ONLY_PACKAGED)

    def test_single_file_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_bytes(b"<p>hi</p>")
            source = open_source(page)
            self.assertEqual(source.root, page.parent.resolve())
            self.assertEqual(source.members, ("page.html",))

    def test_resolve_member_handles_checker_path_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            book = _make_folder(Path(tmp))
            source = open_source(book)
            self.assertEqual(resolve_member(source, "EPUB\\package.opf"), "EPUB/package.opf")
            # Ace reports paths relative to the OPF folder.
            self.assertEqual(resolve_member(source, "nav.xhtml"), "EPUB/nav.xhtml")
            self.assertEqual(
                resolve_member(source, str(book / "EPUB" / "nav.xhtml")),
                "EPUB/nav.xhtml",
            )
            self.assertIsNone(resolve_member(source, "missing.css"))

    def test_text_member_detection(self) -> None:
        self.assertTrue(is_text_member("EPUB/ch1.xhtml"))
        self.assertTrue(is_text_member("mimetype"))
        self.assertFalse(is_text_member("EPUB/cover.jpg"))


class TextRoundTripTests(unittest.TestCase):
    def _round_trip(self, raw: bytes) -> bytes:
        doc = decode_text(raw)
        self.assertTrue(doc.editable)
        self.assertNotIn("\r", doc.text)
        return encode_text(doc, doc.text)

    def test_crlf_utf8_round_trips_byte_for_byte(self) -> None:
        raw = "a{color:red}\r\nb{content:'é'}\r\n".encode("utf-8")
        self.assertEqual(self._round_trip(raw), raw)

    def test_bom_is_preserved(self) -> None:
        raw = codecs.BOM_UTF8 + b"<p>x</p>\n"
        self.assertEqual(self._round_trip(raw), raw)

    def test_utf16_with_bom_is_preserved(self) -> None:
        raw = codecs.BOM_UTF16_LE + "<p>é</p>\r\n".encode("utf-16-le")
        self.assertEqual(self._round_trip(raw), raw)

    def test_declared_latin1_is_used(self) -> None:
        raw = '<?xml version="1.0" encoding="ISO-8859-1"?><p>é</p>'.encode("latin-1")
        doc = decode_text(raw)
        self.assertIn("é", doc.text)
        self.assertEqual(encode_text(doc, doc.text), raw)
        with self.assertRaises(UnicodeEncodeError):
            encode_text(doc, doc.text + "€")

    def test_invalid_utf8_is_read_only(self) -> None:
        doc = decode_text(b"<p>\xff\xfe broken</p>")
        self.assertEqual(doc.read_only_reason, READ_ONLY_UNDECODABLE)

    def test_new_text_uses_file_newline_style(self) -> None:
        doc = decode_text(b"one\r\ntwo\r\n")
        self.assertEqual(encode_text(doc, "one\nnew\ntwo\n"), b"one\r\nnew\r\ntwo\r\n")


class SaveTests(unittest.TestCase):
    def test_save_backs_up_once_per_session_and_reverts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            book = _make_folder(Path(tmp))
            css = book / "EPUB" / "style.css"
            css.write_bytes(b"a{}\r\n")
            source = open_source(book)
            backups: dict[str, str] = {}
            doc = load_text_document(source, "EPUB/style.css")

            first = save_member_text(
                source, "EPUB/style.css", doc, "a{color:red}\n", session_backups=backups
            )
            self.assertTrue(first.backup_created)
            self.assertEqual(css.read_bytes(), b"a{color:red}\r\n")
            self.assertEqual(Path(first.backup_path).read_bytes(), b"a{}\r\n")

            second = save_member_text(
                source, "EPUB/style.css", doc, "a{color:blue}\n", session_backups=backups
            )
            self.assertFalse(second.backup_created)
            self.assertEqual(second.backup_path, first.backup_path)

            revert_member(source, "EPUB/style.css", backups)
            self.assertEqual(css.read_bytes(), b"a{}\r\n")

    def test_unencodable_text_does_not_touch_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.xml"
            raw = '<?xml version="1.0" encoding="ISO-8859-1"?><p/>'.encode("latin-1")
            page.write_bytes(raw)
            source = open_source(page)
            doc = load_text_document(source, "page.xml")
            backups: dict[str, str] = {}
            with self.assertRaises(UnicodeEncodeError):
                save_member_text(source, "page.xml", doc, "€", session_backups=backups)
            self.assertEqual(page.read_bytes(), raw)
            self.assertEqual(backups, {})

    def test_packaged_members_cannot_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub = Path(tmp) / "book.epub"
            with zipfile.ZipFile(epub, "w") as zf:
                zf.writestr("a.css", "a{}")
            source = open_source(epub)
            doc = load_text_document(source, "a.css")
            with self.assertRaises(PermissionError):
                save_member_text(source, "a.css", doc, "b{}", session_backups={})


class IssuePositionTests(unittest.TestCase):
    def test_epubcheck_location_with_line_and_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            book = _make_folder(Path(tmp))
            issue = Issue(
                Severity.ERROR, "RSC-005", "bad", location="EPUB/nav.xhtml (3,7)"
            )
            self.assertEqual(
                issue_member_position(book, issue), ("EPUB/nav.xhtml", 3, 7)
            )

    def test_ace_location_line_found_from_snippet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            book = _make_folder(Path(tmp))
            (book / "EPUB" / "nav.xhtml").write_text(
                "<html>\n<body>\n<nav epub:type=\"toc\">\n</nav>\n</body>\n</html>\n",
                encoding="utf-8",
            )
            issue = Issue(
                Severity.WARNING,
                "epub-type-has-matching-role",
                "role",
                location='EPUB/nav.xhtml · nav · <nav epub:type="toc">',
            )
            member, line, _col = issue_member_position(book, issue)
            self.assertEqual(member, "EPUB/nav.xhtml")
            self.assertEqual(line, 3)


if __name__ == "__main__":
    unittest.main()
