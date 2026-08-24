"""AI Image Sniff Test dialog: follow-up paint, WebView URL matching, close ids."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from checkmate.ai.markdown_html import (
    _WEBVIEW_TAB_EXIT_JS,
    append_followup_markdown,
    compose_sniff_chat_markdown,
    conversation_turns_from_qa,
    conversation_turns_from_report_md,
    followup_markdown_suffix,
    html_report_with_chat,
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

    def test_compose_prefers_live_suffix_over_stored_qa(self):
        stored = append_followup_markdown(
            "", heading="Follow-up", question="Old question?", answer="Old."
        )
        live = append_followup_markdown(
            "Old summary.",
            heading="Follow-up",
            question="New question?",
            answer="New.",
        )
        merged = compose_sniff_chat_markdown("Fresh synthesis.", stored, live)
        self.assertTrue(merged.startswith("Fresh synthesis."))
        self.assertIn("New question?", merged)
        self.assertNotIn("Old question?", merged)

    def test_compose_restores_stored_qa_when_live_is_empty(self):
        stored = append_followup_markdown(
            "", heading="Follow-up", question="Saved question?", answer="Saved."
        )
        merged = compose_sniff_chat_markdown("Synthesis.", stored, "")
        self.assertTrue(merged.startswith("Synthesis."))
        self.assertIn("Saved question?", merged)
        self.assertIn("Saved.", merged)


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

        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog
        from checkmate.ai.fido_image_report import ImageReport, ImageReportImage

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        tmp = Path(tempfile.mkdtemp())
        html = tmp / "alt_text_report.html"
        html.write_text("<html></html>", encoding="utf-8")
        (tmp / "image_report.json").write_text(
            '{"document":"Demo.pdf","publication_format":"pdf","images":[]}',
            encoding="utf-8",
        )
        first = ImageReport(
            folder=tmp,
            document_name="Demo.pdf",
            publication_format="pdf",
            images=[
                ImageReportImage(index=1, filename="a.png", alt_text="A cat."),
            ],
            synthesis_markdown="Initial summary.",
        )
        dlg = None
        try:
            dlg = AltTextReportDialog(frame, folder=tmp, html_path=html)
            dlg._sniff_paint = lambda **_k: None  # type: ignore[method-assign]
            dlg._realize_sniff_view = lambda: None  # type: ignore[method-assign]
            dlg._select_page = lambda *_a, **_k: None  # type: ignore[method-assign]
            dlg.apply_sniff_result(first)
            dlg._sniff_synthesis_md = append_followup_markdown(
                first.synthesis_markdown,
                heading="Follow-up",
                question="Is the logo decorative?",
                answer="Yes.",
            )
            more = ImageReport(
                folder=tmp,
                document_name="Demo.pdf",
                publication_format="pdf",
                images=list(first.images),
                synthesis_markdown="Updated summary after more images.",
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
            html_doc = dlg._sniff_current_html(scroll_followup=False)
            self.assertIn("Is the logo decorative?", html_doc)
            self.assertIn("And the chart?", html_doc)
        finally:
            if dlg is not None:
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pass
            frame.Destroy()

    def test_apply_result_restores_saved_qa(self):
        import json

        import wx

        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog
        from checkmate.ai.fido_image_report import ImageReport, ImageReportImage

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        tmp = Path(tempfile.mkdtemp())
        html = tmp / "alt_text_report.html"
        html.write_text("<html></html>", encoding="utf-8")
        (tmp / "image_report.json").write_text(
            '{"document":"Demo.pdf","publication_format":"pdf","images":[]}',
            encoding="utf-8",
        )
        qa = append_followup_markdown(
            "",
            heading="Follow-up",
            question="Is this decorative?",
            answer="Yes.",
        )
        report = ImageReport(
            folder=tmp,
            document_name="Demo.pdf",
            publication_format="pdf",
            images=[
                ImageReportImage(index=1, filename="a.png", alt_text="A cat."),
            ],
            synthesis_markdown="Summary of the report.",
            qa_markdown=qa,
        )
        dlg = None
        try:
            dlg = AltTextReportDialog(frame, folder=tmp, html_path=html)
            dlg._sniff_paint = lambda **_k: None  # type: ignore[method-assign]
            dlg._realize_sniff_view = lambda: None  # type: ignore[method-assign]
            dlg._select_page = lambda *_a, **_k: None  # type: ignore[method-assign]
            dlg.apply_sniff_result(report)
            self.assertIn("Summary of the report.", dlg._sniff_synthesis_md)
            self.assertIn("Is this decorative?", dlg._sniff_synthesis_md)
            saved = json.loads(
                (tmp / "image_report.json").read_text(encoding="utf-8")
            )
            self.assertIn("Is this decorative?", saved.get("qa_markdown", ""))
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
            self.assertNotIn(dlg._PAGE_SNIFF, dlg._page_keys)
            self.assertIsNone(dlg._notebook)
        finally:
            if dlg is not None:
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pass
            frame.Destroy()


class ConversationTurnsTests(unittest.TestCase):
    def test_plain_synthesis_is_one_note(self):
        turns = conversation_turns_from_qa("The logo is decorative.")
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0][0], "note")
        self.assertIn("decorative", turns[0][2])

    def test_followup_bubbles_are_user_then_assistant(self):
        md = append_followup_markdown(
            "Summary.",
            heading="Follow-up",
            question="Is the logo decorative?",
            answer="Yes.",
        )
        turns = conversation_turns_from_report_md(md)
        kinds = [t[0] for t in turns]
        self.assertIn("note", kinds)
        self.assertIn("user", kinds)
        self.assertIn("assistant", kinds)
        user = [t for t in turns if t[0] == "user"][0]
        self.assertIn("logo", user[2])
        assistant = [t for t in turns if t[0] == "assistant"][0]
        self.assertIn("Yes.", assistant[2])

    def test_two_followups_each_have_answers(self):
        md = "First synthesis."
        md = append_followup_markdown(
            md, heading="Follow-up", question="Is the logo decorative?", answer="Yes."
        )
        md = append_followup_markdown(
            md, heading="Follow-up", question="What about page 2?", answer="Needs alt."
        )
        turns = conversation_turns_from_report_md(md)
        assistants = [t[2] for t in turns if t[0] == "assistant"]
        self.assertEqual(len(assistants), 2)
        self.assertTrue(any("Yes." in text for text in assistants))
        self.assertTrue(any("Needs alt." in text for text in assistants))

    def test_assistant_turn_keeps_list_markup(self):
        md = append_followup_markdown(
            "Summary.",
            heading="Follow-up",
            question="Tips?",
            answer="- one\n- two",
        )
        turns = conversation_turns_from_report_md(md)
        assistant = [t for t in turns if t[0] == "assistant"][0]
        self.assertIn("<li>", assistant[3].lower())


class HtmlReportChatTests(unittest.TestCase):
    def test_inserts_section_before_filters(self):
        html_doc = '<div class="filters">F</div></body></html>'
        out = html_report_with_chat(html_doc, "Is the logo decorative?", include=True)
        self.assertIn("qa-section", out)
        self.assertIn("decorative", out.lower())
        self.assertLess(out.lower().find("qa-section"), out.lower().find("filters"))

    def test_strips_section_when_unchecked(self):
        html_doc = (
            '<section class="synthesis qa-section"><h2>Q</h2><p>chat</p></section>'
            '<div class="filters">F</div>'
        )
        out = html_report_with_chat(html_doc, "ignored", include=False)
        self.assertNotIn("qa-section", out)
        self.assertNotIn("chat", out)
        self.assertIn("filters", out)

    def test_empty_chat_does_not_insert(self):
        html_doc = '<div class="filters">F</div>'
        out = html_report_with_chat(html_doc, "  ", include=True)
        self.assertNotIn("qa-section", out)


class TabExitJsTests(unittest.TestCase):
    def test_guard_is_on_document_not_window(self):
        self.assertIn("document.__cmTabExitWired", _WEBVIEW_TAB_EXIT_JS)
        self.assertNotIn("window.__cmTabExitWired", _WEBVIEW_TAB_EXIT_JS)
        self.assertIn("checkmate://close", _WEBVIEW_TAB_EXIT_JS)


if __name__ == "__main__":
    unittest.main()
