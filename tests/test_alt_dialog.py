"""AI Image Sniff Test dialog: follow-up paint, WebView URL matching, close ids."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from checkmate.ai.markdown_html import (
    append_followup_markdown,
    followup_markdown_suffix,
    merge_followup_suffix,
    split_followup_markdown,
)


class FollowupSuffixTests(unittest.TestCase):
    def test_two_questions_keep_both(self):
        md = "First synthesis."
        md = append_followup_markdown(
            md, heading="Follow-up", question="Is the logo decorative?", answer="Yes."
        )
        md = append_followup_markdown(
            md, heading="Follow-up", question="What about page 2?", answer="Needs alt."
        )
        self.assertIn("Is the logo decorative?", md)
        self.assertIn("What about page 2?", md)
        self.assertEqual(md.count('id="cm-latest-followup"'), 1)
        self.assertEqual(md.count('class="chat-bubble chat-user"'), 2)

    def test_suffix_extract_and_merge_onto_new_synthesis(self):
        md = append_followup_markdown(
            "Old summary.",
            heading="Follow-up",
            question="First question?",
            answer="First answer.",
        )
        suffix = followup_markdown_suffix(md)
        self.assertIn("First question?", suffix)
        merged = merge_followup_suffix("New summary after assessing more.", suffix)
        self.assertTrue(merged.startswith("New summary after assessing more."))
        self.assertIn("First question?", merged)
        self.assertIn("First answer.", merged)
        twice = merge_followup_suffix(merged, suffix)
        self.assertEqual(twice.count('class="chat-bubble chat-user"'), 1)

    def test_empty_suffix_is_noop(self):
        self.assertEqual(followup_markdown_suffix("No questions here."), "")
        self.assertEqual(merge_followup_suffix("Synth.", ""), "Synth.")

    def test_split_followup_markdown(self):
        md = append_followup_markdown(
            "Initial summary.",
            heading="Follow-up",
            question="Is the logo decorative?",
            answer="Yes.",
        )
        synth, follow = split_followup_markdown(md)
        self.assertEqual(synth, "Initial summary.")
        self.assertIn("Is the logo decorative?", follow)
        self.assertIn('class="chat-bubble chat-user"', follow)
        self.assertNotIn("Is the logo decorative?", synth)


class WebViewUrlMatchTests(unittest.TestCase):
    def test_stale_copy_does_not_match_current(self):
        try:
            from checkmate.ai.alt_dialog import webview_url_matches_html
        except ImportError:
            self.skipTest("wxPython is not installed")

        current = Path("/tmp/export/.cm_view_bbb.html")
        self.assertTrue(
            webview_url_matches_html(
                "file:///tmp/export/.cm_view_bbb.html?cm=abc", current
            )
        )
        self.assertFalse(
            webview_url_matches_html(
                "file:///tmp/export/.cm_view_aaa.html?cm=old", current
            )
        )
        self.assertFalse(webview_url_matches_html("about:blank", current))
        self.assertFalse(webview_url_matches_html("", current))
        self.assertFalse(webview_url_matches_html("about:blank", None))


class ApplyResultPreservesFollowupsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            import wx  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("wxPython is not installed")

    def test_apply_result_keeps_previous_questions(self):
        import wx

        from checkmate.ai.alt_assess import AltAssessResult, AltImageAssessment
        from checkmate.ai.alt_export import AltExport, AltExportImage
        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        tmp = Path(tempfile.mkdtemp())
        html = tmp / "alt_text_report.html"
        html.write_text("<html></html>", encoding="utf-8")
        img = tmp / "a.png"
        img.write_bytes(b"x")
        export = AltExport(
            folder=tmp,
            document_name="Demo.pdf",
            publication_format="pdf",
            images=[
                AltExportImage(
                    index=1,
                    filename="a.png",
                    classification="",
                    alt_text="A cat.",
                    status="Has Alt Text",
                    image_path=img,
                ),
            ],
        )
        assessment = AltImageAssessment(
            index=1,
            filename="a.png",
            verdict="ok",
            reason="Describes the photo.",
            issues=[],
        )
        first = AltAssessResult(
            ok=True,
            text="Initial summary.",
            export=export,
            assessments=[assessment],
        )
        dlg = None
        try:
            dlg = AltTextReportDialog(frame, folder=tmp, html_path=html)
            dlg._sniff_paint = lambda **_k: None  # type: ignore[method-assign]
            dlg._realize_sniff_view = lambda: None  # type: ignore[method-assign]
            dlg._select_page = lambda *_a, **_k: None  # type: ignore[method-assign]
            dlg.apply_sniff_result(first)
            dlg._sniff_synthesis_md = append_followup_markdown(
                first.text,
                heading="Follow-up",
                question="Is the logo decorative?",
                answer="Yes.",
            )
            more = AltAssessResult(
                ok=True,
                text="Updated summary after more images.",
                export=export,
                assessments=[assessment],
            )
            dlg.apply_sniff_result(more)
            self.assertIn("Updated summary after more images.", dlg._sniff_synthesis_md)
            self.assertIn("Is the logo decorative?", dlg._sniff_synthesis_md)
            dlg._sniff_synthesis_md = append_followup_markdown(
                dlg._sniff_synthesis_md,
                heading="Follow-up",
                question="And the chart?",
                answer="Needs a better alt.",
            )
            self.assertIn("Is the logo decorative?", dlg._sniff_synthesis_md)
            self.assertIn("And the chart?", dlg._sniff_synthesis_md)
            dlg._sniff_result.text = dlg._sniff_synthesis_md
            html_doc = dlg._sniff_current_html(scroll_followup=False)
            self.assertIn('id="cm-followups"', html_doc)
            self.assertIn('id="cm-latest-followup"', html_doc)
            self.assertIn("Is the logo decorative?", html_doc)
            self.assertIn("And the chart?", html_doc)
        finally:
            if dlg is not None:
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pass
            frame.Destroy()

    def test_escape_id_is_none(self):
        import wx

        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        tmp = Path(tempfile.mkdtemp())
        html = tmp / "alt_text_report.html"
        html.write_text("<html></html>", encoding="utf-8")
        dlg = None
        try:
            dlg = AltTextReportDialog(frame, folder=tmp, html_path=html)
            self.assertEqual(int(dlg.GetEscapeId()), int(wx.ID_NONE))
            self.assertEqual(int(dlg.GetAffirmativeId()), int(wx.ID_NONE))
            self.assertEqual(dlg._page_keys[0], dlg._PAGE_REPORT)
            if dlg._show_sniff:
                self.assertIn(dlg._PAGE_SNIFF, dlg._page_keys)
        finally:
            if dlg is not None:
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pass
            frame.Destroy()


if __name__ == "__main__":
    unittest.main()
