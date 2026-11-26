"""Packaging an exploded eBraille folder into a .ebrl file."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from checkmate.epub_package import create_epub
from checkmate.publication import PublicationKind, classify_publication

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:uuid:test</dc:identifier>
    <dc:title>Test</dc:title>
    <dc:language>en</dc:language>
    <dc:format>eBraille 1.0</dc:format>
  </metadata>
  <manifest>
    <item id="vol1" href="ebraille/vol1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="vol1"/></spine>
</package>
"""


def _make_ebraille_folder(root: Path, *, with_mimetype: bool) -> Path:
    folder = root / "book"
    (folder / "META-INF").mkdir(parents=True)
    (folder / "ebraille").mkdir()
    (folder / "META-INF" / "container.xml").write_text(_CONTAINER, encoding="utf-8")
    (folder / "package.opf").write_text(_OPF, encoding="utf-8")
    (folder / "ebraille" / "vol1.xhtml").write_text("<p>⠓⠊</p>", encoding="utf-8")
    # CheckMate edit leftovers must not end up in the package.
    (folder / "ebraille" / "vol1.xhtml.bak").write_text("old", encoding="utf-8")
    if with_mimetype:
        (folder / "mimetype").write_text("application/epub+zip", encoding="utf-8")
    return folder


class PackageEbrailleTests(unittest.TestCase):
    def test_folder_is_classified_as_ebraille(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _make_ebraille_folder(Path(tmp), with_mimetype=True)
            self.assertEqual(classify_publication(folder), PublicationKind.EBRAILLE)

    def test_packages_to_valid_ebrl(self) -> None:
        for with_mimetype in (True, False):
            with self.subTest(with_mimetype=with_mimetype):
                with tempfile.TemporaryDirectory() as tmp:
                    folder = _make_ebraille_folder(Path(tmp), with_mimetype=with_mimetype)
                    out = Path(tmp) / "book.ebrl"
                    create_epub(folder, out)

                    self.assertEqual(classify_publication(out), PublicationKind.EBRAILLE)
                    with zipfile.ZipFile(out) as zf:
                        first = zf.infolist()[0]
                        self.assertEqual(first.filename, "mimetype")
                        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
                        self.assertEqual(zf.read("mimetype"), b"application/epub+zip")
                        names = set(zf.namelist())
                    self.assertIn("package.opf", names)
                    self.assertIn("META-INF/container.xml", names)
                    self.assertIn("ebraille/vol1.xhtml", names)
                    self.assertNotIn("ebraille/vol1.xhtml.bak", names)


if __name__ == "__main__":
    unittest.main()
