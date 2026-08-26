"""Publication classification for HTML, SVG, CSS, DTBook, DAISY, and NIMAS."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from checkmate.publication import (
    PublicationKind,
    classify_publication,
    classify_target,
    is_checkable_path,
    is_checkable_target,
    is_dtbook_xml,
    is_html_url,
    is_pipeline_kind,
    is_vnu_document_kind,
)
from checkmate.pipeline_report import parse_pipeline_xml_report
from checkmate.report_export import report_title
from checkmate.models import CheckResult, Severity, Verdict
from checkmate.settings import VERAPDF_FLAVOURS, VERAPDF_FLAVOUR_LABELS, verapdf_flavour_label


class ClassifySvgCssTests(unittest.TestCase):
    def test_svg_and_css_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            svg = root / "icon.svg"
            css = root / "theme.css"
            svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
            css.write_text("body { color: black; }", encoding="utf-8")
            self.assertEqual(classify_publication(svg), PublicationKind.SVG)
            self.assertEqual(classify_publication(css), PublicationKind.CSS)
            self.assertTrue(is_checkable_path(svg))
            self.assertTrue(is_vnu_document_kind(PublicationKind.SVG))
            self.assertTrue(is_vnu_document_kind(PublicationKind.CSS))

    def test_svg_css_urls(self) -> None:
        self.assertEqual(
            classify_target("https://example.org/icon.svg"), PublicationKind.SVG
        )
        self.assertEqual(
            classify_target("http://127.0.0.1:8080/app.css"), PublicationKind.CSS
        )
        self.assertEqual(
            classify_target("https://example.org/page.html"), PublicationKind.HTML
        )
        self.assertTrue(is_checkable_target("https://example.org/icon.svg"))
        self.assertTrue(is_html_url("https://example.org/icon.svg"))
        self.assertEqual(
            classify_target("https://example.org/data.xml"), PublicationKind.XML
        )


class ClassifyDaisyTests(unittest.TestCase):
    def test_dtbook_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.xml"
            path.write_text(
                '<?xml version="1.0"?>\n'
                '<dtbook xmlns="http://www.daisy.org/z3986/2005/dtbook/" version="2005-3">'
                "<book/></dtbook>",
                encoding="utf-8",
            )
            self.assertTrue(is_dtbook_xml(path))
            self.assertEqual(classify_publication(path), PublicationKind.DTBOOK)
            self.assertTrue(is_pipeline_kind(PublicationKind.DTBOOK))
            self.assertTrue(is_checkable_path(path))

    def test_plain_xml_is_not_dtbook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.xml"
            path.write_text("<root><item/></root>", encoding="utf-8")
            self.assertFalse(is_dtbook_xml(path))
            self.assertEqual(classify_publication(path), PublicationKind.XML)
            self.assertTrue(is_checkable_path(path))
            self.assertTrue(is_vnu_document_kind(PublicationKind.XML))

    def test_mathml_xml_and_mml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mml = root / "eq.mml"
            xml = root / "eq.xml"
            payload = (
                '<math xmlns="http://www.w3.org/1998/Math/MathML">'
                "<mi>x</mi></math>"
            )
            mml.write_text(payload, encoding="utf-8")
            xml.write_text(payload, encoding="utf-8")
            self.assertEqual(classify_publication(mml), PublicationKind.MATHML)
            self.assertEqual(classify_publication(xml), PublicationKind.MATHML)
            self.assertTrue(is_vnu_document_kind(PublicationKind.MATHML))

    def test_daisy202_folder_not_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ncc.html").write_text("<html></html>", encoding="utf-8")
            self.assertEqual(classify_publication(root), PublicationKind.DAISY202)
            self.assertTrue(is_checkable_path(root))

    def test_nimas_opf_not_epub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            opf = root / "package.opf"
            opf.write_text(
                '<?xml version="1.0"?>\n'
                '<package xmlns="http://openebook.org/namespaces/oeb-package/1.0/">\n'
                "<metadata><dc:Format>NIMAS 1.1</dc:Format></metadata>\n"
                '<manifest><item href="book.xml" media-type="application/x-dtbook+xml"/>'
                "</manifest></package>",
                encoding="utf-8",
            )
            (root / "book.xml").write_text(
                '<dtbook xmlns="http://www.daisy.org/z3986/2005/dtbook/"/>',
                encoding="utf-8",
            )
            self.assertEqual(classify_publication(root), PublicationKind.NIMAS)
            self.assertEqual(classify_publication(opf), PublicationKind.NIMAS)

    def test_daisy3_opf_with_smil(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.opf").write_text(
                '<?xml version="1.0"?>\n'
                "<package>\n"
                "<metadata><dc:Format>ANSI/NISO Z39.86-2005</dc:Format></metadata>\n"
                "<manifest>"
                '<item href="book.xml" media-type="application/x-dtbook+xml"/>'
                '<item href="speech.smil" media-type="application/smil"/>'
                '<item href="book.ncx" media-type="application/x-dtbncx+xml"/>'
                "</manifest></package>",
                encoding="utf-8",
            )
            self.assertEqual(classify_publication(root), PublicationKind.DAISY3)

    def test_exploded_epub_still_epub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = root / "META-INF"
            meta.mkdir()
            (meta / "container.xml").write_text(
                '<?xml version="1.0"?>\n'
                '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                'media-type="application/oebps-package+xml"/></rootfiles></container>',
                encoding="utf-8",
            )
            oebps = root / "OEBPS"
            oebps.mkdir()
            (oebps / "content.opf").write_text(
                '<?xml version="1.0"?>\n'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">'
                "<metadata><dc:identifier id='uid'>id</dc:identifier></metadata>"
                "<manifest/>"
                "</package>",
                encoding="utf-8",
            )
            self.assertEqual(classify_publication(root), PublicationKind.EPUB)


class PipelineXmlReportTests(unittest.TestCase):
    def test_relaxng_error(self) -> None:
        xml = """
        <d:document-validation-report xmlns:d="http://www.daisy.org/ns/pipeline/data">
          <d:reports>
            <d:report type="relaxng">
              <d:errors>
                <d:error type="relaxng">
                  <d:desc>element book not allowed</d:desc>
                  <d:location line="3" column="12"/>
                </d:error>
              </d:errors>
            </d:report>
          </d:reports>
        </d:document-validation-report>
        """
        issues = parse_pipeline_xml_report(xml)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.ERROR)
        self.assertEqual(issues[0].code, "relaxng")
        self.assertIn("book not allowed", issues[0].message)

    def test_report_title(self) -> None:
        result = CheckResult(verdict=Verdict.PASSED, tool_name="DAISY Pipeline")
        self.assertEqual(report_title(result), "DAISY Pipeline report")


class VeraPdfFlavourTests(unittest.TestCase):
    def test_pdfa_labels(self) -> None:
        self.assertIn("1b", VERAPDF_FLAVOURS)
        self.assertIn("4f", VERAPDF_FLAVOURS)
        self.assertEqual(VERAPDF_FLAVOUR_LABELS["1b"], "PDF/A-1b")
        self.assertEqual(verapdf_flavour_label("ua2"), "PDF/UA-2")
        self.assertEqual(verapdf_flavour_label("wt1a"), "WTPDF 1.0 Accessibility")


if __name__ == "__main__":
    unittest.main()
