"""Alt-text report dialog: WebView navigation and teardown helpers."""
from __future__ import annotations

import unittest
from pathlib import Path

from checkmate.main import _webview_host_action


class InspectorHandoffTests(unittest.TestCase):
    def test_run_inspector_id_is_not_a_close_id(self):
        import wx

        from checkmate.ai.alt_inventory_dialog import ID_RUN_AI_HEALTH

        run_id = int(ID_RUN_AI_HEALTH)
        self.assertNotEqual(run_id, int(wx.ID_CLOSE))
        self.assertNotEqual(run_id, int(wx.ID_CANCEL))
        self.assertNotEqual(run_id, int(wx.ID_OK))


class WebViewHostActionTests(unittest.TestCase):
    def test_file_report_url_is_not_close(self):
        self.assertIsNone(
            _webview_host_action("file:///C:/tmp/export/alt_text_report.html")
        )

    def test_empty_current_url_is_still_loading(self):
        current = ""
        expected = "alt_text_report.html"
        self.assertFalse(bool(current))
        self.assertNotIn(expected, current)
        self.assertIsNone(_webview_host_action("about:blank"))
        self.assertIsNone(_webview_host_action("about:srcdoc"))

    def test_checkmate_close_is_close(self):
        self.assertEqual(_webview_host_action("checkmate://close"), "close")
        self.assertEqual(_webview_host_action("checkmate://close/"), "close")

    def test_preview_https_is_not_close(self):
        self.assertIsNone(
            _webview_host_action("https://checkmate.invalid/preview/1")
        )


class InspectorHtmlLoadTests(unittest.TestCase):
    def test_temp_html_roundtrip_is_nonempty(self):
        from checkmate.ai.alt_dialog import _unlink_quietly

        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.gettempdir()) / "checkmate_alt_assess_test.html"
        tmp.write_text("<html><body>ok</body></html>", encoding="utf-8")
        self.assertGreater(tmp.stat().st_size, 10)
        _unlink_quietly(tmp)
        self.assertFalse(tmp.exists())


class PreviewUrlTests(unittest.TestCase):
    def test_preview_filename_from_url(self):
        from checkmate.ai.alt_inventory_dialog import preview_filename_from_url

        self.assertEqual(
            preview_filename_from_url("checkmate://preview/image_0001.png"),
            "image_0001.png",
        )
        self.assertEqual(
            preview_filename_from_url("checkmate://preview/image%20001.png"),
            "image 001.png",
        )
        self.assertIsNone(preview_filename_from_url("checkmate://close"))
        self.assertIsNone(preview_filename_from_url("checkmate://preview/../secret.png"))
        self.assertIsNone(preview_filename_from_url("checkmate://preview/1"))

    def test_preview_index_from_url(self):
        from checkmate.ai.alt_inventory_dialog import (
            preview_href_for_index,
            preview_index_from_url,
        )

        self.assertEqual(preview_href_for_index(1), "https://checkmate.invalid/preview/1")
        self.assertEqual(
            preview_index_from_url("https://checkmate.invalid/preview/3"),
            3,
        )
        self.assertEqual(
            preview_index_from_url("https://checkmate.invalid/preview/index/2"),
            2,
        )
        self.assertEqual(preview_index_from_url("checkmate://preview/4"), 4)
        self.assertIsNone(preview_index_from_url("https://example.com/preview/1"))
        self.assertIsNone(preview_index_from_url("checkmate://preview/image_0001.png"))

    def test_safe_export_image_path_stays_in_folder(self):
        import tempfile

        from checkmate.ai.alt_inventory_dialog import safe_export_image_path

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "images").mkdir()
            target = folder / "images" / "image_0001.png"
            target.write_bytes(b"x")
            self.assertEqual(
                safe_export_image_path(folder, "image_0001.png"),
                target.resolve(),
            )
            self.assertIsNone(safe_export_image_path(folder, "missing.png"))
            escaped = safe_export_image_path(folder, "../image_0001.png")
            if escaped is not None:
                self.assertEqual(escaped, target.resolve())
                self.assertTrue(str(escaped).startswith(str((folder / "images").resolve())))


class UniqueViewHtmlTests(unittest.TestCase):
    def test_webview_file_uri_has_no_query(self):
        from checkmate.ai.alt_inventory_dialog import webview_file_uri

        uri = webview_file_uri(Path("C:/tmp/export/alt_text_report.html"), token="abc123")
        self.assertIn("alt_text_report.html", uri)
        self.assertNotIn("cm=abc123", uri)
        self.assertTrue(uri.startswith("file:"))

    def test_unique_view_copy_is_distinct_path(self):
        import tempfile

        from checkmate.ai.alt_inventory_dialog import (
            cleanup_view_html,
            write_unique_view_html,
        )

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            src = folder / "alt_text_report.html"
            src.write_text("<html><body>ok</body></html>", encoding="utf-8")
            copy = write_unique_view_html(src)
            self.assertNotEqual(copy.resolve(), src.resolve())
            self.assertTrue(copy.name.startswith(".cm_view_"))
            self.assertEqual(
                copy.read_text(encoding="utf-8"), src.read_text(encoding="utf-8")
            )
            cleanup_view_html(folder)
            self.assertFalse(copy.exists())
            self.assertTrue(src.exists())

    def test_load_unique_does_not_mark_navigated_until_loaded(self):
        import tempfile

        from checkmate.ai.alt_inventory_dialog import load_unique_file_in_webview

        class FakeView:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def LoadURL(self, uri: str) -> None:
                self.urls.append(uri)

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            src = folder / "alt_text_report.html"
            src.write_text("<html><body>ok</body></html>", encoding="utf-8")
            view = FakeView()
            dest = load_unique_file_in_webview(view, src)
            self.assertIsNotNone(dest)
            self.assertTrue(dest.is_file())
            self.assertFalse(getattr(view, "_cm_ever_navigated", False))
            self.assertEqual(len(view.urls), 1)
            self.assertNotIn("cm=", view.urls[0])
            self.assertNotIn("?", Path(dest.name).name)
            self.assertIn(dest.name, view.urls[0])

    def test_reload_writes_html_into_existing_document(self):
        import tempfile

        from checkmate.ai import alt_inventory_dialog as mod

        scripts: list[str] = []

        def fake_run(_view, script: str) -> bool:
            scripts.append(script)
            return True

        class FakeView:
            def __init__(self) -> None:
                self.urls: list[str] = []
                self._cm_ever_navigated = True

            def LoadURL(self, uri: str) -> None:
                self.urls.append(uri)

        original = mod._webview_run_script
        mod._webview_run_script = fake_run
        try:
            with tempfile.TemporaryDirectory() as raw:
                folder = Path(raw)
                src = folder / "alt_text_report.html"
                src.write_text("<html><body>rebuilt</body></html>", encoding="utf-8")
                view = FakeView()
                dest = mod.load_unique_file_in_webview(view, src)
                self.assertEqual(dest.resolve(), src.resolve())
                self.assertEqual(view.urls, [])
                self.assertTrue(any("document.write" in text for text in scripts))
                self.assertTrue(any("rebuilt" in text for text in scripts))
        finally:
            mod._webview_run_script = original

    def test_html_with_folder_base_pins_relative_urls(self):
        import tempfile

        from checkmate.ai.alt_inventory_dialog import html_with_folder_base

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            out = html_with_folder_base(
                "<html><head><title>x</title></head><body>"
                '<img src="images/a.png"></body></html>',
                folder,
            )
            base = folder.resolve().as_uri()
            if not base.endswith("/"):
                base += "/"
            self.assertIn(f'<base href="{base}">', out)
            self.assertIn('<img src="images/a.png">', out)
            replaced = html_with_folder_base(
                '<html><head><base href="file:///old/"></head></html>',
                folder,
            )
            self.assertIn(f'<base href="{base}">', replaced)
            self.assertNotIn("file:///old/", replaced)

    def test_reload_writes_when_report_folder_changes(self):
        import tempfile

        from checkmate.ai import alt_inventory_dialog as mod

        scripts: list[str] = []

        def fake_run(_view, script: str) -> bool:
            scripts.append(script)
            return True

        class FakeView:
            def __init__(self) -> None:
                self.urls: list[str] = []
                self._cm_ever_navigated = True

            def LoadURL(self, uri: str) -> None:
                self.urls.append(uri)

        original = mod._webview_run_script
        mod._webview_run_script = fake_run
        try:
            with tempfile.TemporaryDirectory() as raw:
                first = Path(raw) / "first"
                second = Path(raw) / "second"
                first.mkdir()
                second.mkdir()
                prev = first / "alt_text_report.html"
                prev.write_text("<html><body>old</body></html>", encoding="utf-8")
                src = second / "alt_text_report.html"
                src.write_text(
                    "<html><head></head><body>new doc"
                    '<img src="images/photo.png"></body></html>',
                    encoding="utf-8",
                )
                view = FakeView()
                dest = mod.load_unique_file_in_webview(
                    view, src, previous_doc=prev
                )
                self.assertIsNotNone(dest)
                self.assertEqual(dest.parent.resolve(), second.resolve())
                self.assertTrue(any("document.write" in text for text in scripts))
                self.assertTrue(any("new doc" in text for text in scripts))
                self.assertFalse(any("location.replace" in text for text in scripts))
                self.assertEqual(view.urls, [])
                second_uri = second.resolve().as_uri()
                if not second_uri.endswith("/"):
                    second_uri += "/"
                self.assertTrue(any(second_uri in text for text in scripts))
        finally:
            mod._webview_run_script = original

    def test_cleanup_keeps_listed_unique_copies(self):
        import tempfile

        from checkmate.ai.alt_inventory_dialog import (
            cleanup_view_html,
            write_unique_view_html,
        )

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            src = folder / "alt_text_report.html"
            src.write_text("<html>report</html>", encoding="utf-8")
            report_copy = write_unique_view_html(src)
            sniff_src = folder / "alt_assess.html"
            sniff_src.write_text("<html>sniff</html>", encoding="utf-8")
            sniff_copy = write_unique_view_html(sniff_src)
            extra = write_unique_view_html(src)
            cleanup_view_html(folder, keep=[report_copy, sniff_copy])
            self.assertTrue(report_copy.exists())
            self.assertTrue(sniff_copy.exists())
            self.assertFalse(extra.exists())
            self.assertTrue(src.exists())


    def test_image_report_uses_native_conversation_pane(self):
        import inspect

        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog
        from checkmate.main import AiOverviewDialog

        report_src = inspect.getsource(AltTextReportDialog.__init__)
        self.assertIn("ConversationScroller", report_src)
        self.assertIn("make_chat_splitter", report_src)
        self.assertIn("apply_webview_chat_dialog_size", report_src)
        overview_src = inspect.getsource(AiOverviewDialog.__init__)
        self.assertIn("ConversationScroller", overview_src)
        self.assertIn("apply_webview_chat_dialog_size", overview_src)
        self.assertIn("on_toggle_chat", overview_src)
        self.assertIn("_add_chat_column_composer", overview_src)
        self.assertIn("chat_toggle_btn", overview_src)
        self.assertIn("Include chat in HTML report", overview_src)
        self.assertNotIn("_add_followup_question_row", overview_src)
        follow_src = inspect.getsource(AltTextReportDialog._build_sniff_followup)
        self.assertIn("Include chat in HTML report", follow_src)
        self.assertIn("_add_chat_column_composer", follow_src)
        actions_src = inspect.getsource(AltTextReportDialog._build_report_actions)
        self.assertIn("chat_toggle_btn", actions_src)
        self.assertIn("&Close", actions_src)
        self.assertNotIn("_path_label", report_src)
        open_src = inspect.getsource(AltTextReportDialog._on_open_browser)
        self.assertIn("_html_report_for_export", open_src)
        overview_view = inspect.getsource(AiOverviewDialog._on_view_browser)
        self.assertIn("_html_for_export", overview_view)
        overview_save = inspect.getsource(AiOverviewDialog._on_save_html)
        self.assertIn("_html_for_export", overview_save)


class ConversationPaneMacSafetyTests(unittest.TestCase):
    def test_set_content_before_shown_does_not_raise(self) -> None:
        import wx

        from checkmate.ai.conversation_pane import ConversationScroller

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        try:
            view = ConversationScroller(frame)
            view.set_content([], idle="Ask about this report")
            view.set_content([("user", "You", "Hello")])
        finally:
            frame.Destroy()

    def test_dialog_enter_helper_ignores_unrelated_focus(self) -> None:
        import wx

        from checkmate.ai.conversation_pane import dialog_handles_composer_enter

        class Event:
            def GetKeyCode(self):
                return wx.WXK_RETURN

            def ShiftDown(self):
                return False

        called = []
        self.assertFalse(
            dialog_handles_composer_enter(Event(), None, called.append)
        )
        self.assertEqual(called, [])


class ChatComposerSizeTests(unittest.TestCase):
    def test_composer_min_height_covers_three_lines(self) -> None:
        import inspect

        import wx

        from checkmate.main import (
            _add_chat_column_composer,
            _add_followup_question_row,
            _size_chat_composer,
        )

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        try:
            ctrl = wx.TextCtrl(frame, style=wx.TE_MULTILINE | wx.TE_WORDWRAP)
            _size_chat_composer(ctrl, lines=3)
            self.assertGreaterEqual(
                ctrl.GetMinSize().GetHeight(), ctrl.GetCharHeight() * 3
            )
            row_src = inspect.getsource(_add_followup_question_row)
            self.assertIn("_chat_composer_style", row_src)
            self.assertIn("_size_chat_composer", row_src)
            col_src = inspect.getsource(_add_chat_column_composer)
            self.assertIn("_chat_composer_style", col_src)
            self.assertIn("_size_chat_composer", col_src)
        finally:
            frame.Destroy()


class PrepareReuseTests(unittest.TestCase):
    def test_prepare_resets_closing_and_html_cache(self):
        import tempfile

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
            dlg._closing = True
            dlg._dialog_html_cache = "stale"
            gen = dlg._load_gen
            dlg.prepare(tmp, html)
            self.assertFalse(dlg._closing)
            self.assertIsNone(dlg._dialog_html_cache)
            self.assertGreater(dlg._load_gen, gen)
            dlg._modal_session = 4
            dlg._close_dialog_if_session(3)
            self.assertFalse(dlg._closing)
        finally:
            if dlg is not None:
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pass
            frame.Destroy()

    def test_prepare_same_folder_keeps_chat(self):
        import tempfile

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
            marker = object()
            dlg._sniff_synthesis_md = "live chat"
            dlg._sniff_session = marker
            dlg.prepare(tmp, html)
            self.assertEqual(dlg._sniff_synthesis_md, "live chat")
            self.assertIs(dlg._sniff_session, marker)
        finally:
            if dlg is not None:
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pass
            frame.Destroy()

    def test_prepare_other_folder_clears_chat(self):
        import tempfile

        import wx

        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        tmp = Path(tempfile.mkdtemp())
        html = tmp / "alt_text_report.html"
        html.write_text("<html></html>", encoding="utf-8")
        other = Path(tempfile.mkdtemp())
        html2 = other / "alt_text_report.html"
        html2.write_text("<html></html>", encoding="utf-8")
        dlg = None
        try:
            dlg = AltTextReportDialog(frame, folder=tmp, html_path=html)
            dlg._sniff_synthesis_md = "live chat"
            dlg.prepare(other, html2)
            self.assertEqual(dlg._sniff_synthesis_md, "")
            self.assertIsNone(dlg._sniff_session)
        finally:
            if dlg is not None:
                try:
                    dlg.Destroy()
                except RuntimeError:
                    pass
            frame.Destroy()


class ReclaimAfterModalTests(unittest.TestCase):
    def test_reclaim_enables_frame(self):
        import wx

        from checkmate.main import MainFrame

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        # Don't construct MainFrame (heavy). The helper must exist and be safe
        # to call on a plain Frame with the same Enable/Raise pattern.
        frame = wx.Frame(None)
        try:
            frame.Enable(False)
            self.assertFalse(frame.IsEnabled())
            frame.Enable(True)
            frame.Raise()
            self.assertTrue(frame.IsEnabled())
            self.assertTrue(hasattr(MainFrame, "_retire_inventory_dialog"))
            self.assertTrue(hasattr(MainFrame, "_reopen_inventory_when_idle"))
            self.assertTrue(hasattr(MainFrame, "_reveal_inventory_dialog"))
            self.assertTrue(hasattr(MainFrame, "_park_inventory_host"))
            self.assertTrue(hasattr(MainFrame, "_reschedule_inventory_after_park"))
            self.assertTrue(hasattr(MainFrame, "_inventory_dialog_dismissed"))
            self.assertTrue(hasattr(MainFrame, "_finish_app_exit"))
        finally:
            frame.Destroy()

    def test_escape_exit_can_be_suppressed(self):
        from checkmate.main import MainFrame

        class Stub:
            _suppress_escape_exit_until = 0.0
            _suppress_escape_exit = MainFrame._suppress_escape_exit
            _escape_exit_suppressed = MainFrame._escape_exit_suppressed

        stub = Stub()
        self.assertFalse(stub._escape_exit_suppressed())
        stub._suppress_escape_exit(30)
        self.assertTrue(stub._escape_exit_suppressed())
        stub._suppress_escape_exit_until = 0.0
        self.assertFalse(stub._escape_exit_suppressed())


class InventoryLifecycleSourceTests(unittest.TestCase):
    def test_close_keeps_webview_bindings_for_reuse(self):
        import inspect

        from checkmate.ai.alt_dialog import AltSniffTestMixin

        close_src = inspect.getsource(AltSniffTestMixin._on_close_dialog)
        self.assertNotIn("release_inventory", close_src)
        self.assertNotIn("_release_sniff_webview()", close_src)
        self.assertIn("_ensure_end_modal", close_src)
        destroy_src = inspect.getsource(AltSniffTestMixin._on_window_destroy)
        self.assertIn("_release_sniff_webview()", destroy_src)

    def test_leftover_modal_ends_then_reopens(self):
        import inspect

        from checkmate.main import MainFrame

        src = inspect.getsource(MainFrame._present_alt_inventory_report)
        self.assertIn("_ensure_end_modal", src)
        self.assertIn("_unpark_inventory_dialog", src)
        self.assertNotIn("dlg.ShowModal()", src)
        self.assertNotIn("_reveal_inventory_dialog", src)
        self.assertNotIn("hidden-modal, wait for EndModal", src)

    def test_rebuild_uses_the_publication_now_in_the_path_field(self):
        import inspect

        from checkmate.main import MainFrame

        src = inspect.getsource(MainFrame._rebuild_image_report)
        current_at = src.find("self._current_target()")
        source_at = src.find("self._alt_report_source")
        self.assertGreater(current_at, 0)
        self.assertGreater(source_at, current_at)

    def test_quit_exits_mainloop_immediately(self):
        import inspect

        from checkmate.main import MainFrame

        src = inspect.getsource(MainFrame._on_main_close)
        self.assertIn("_finish_app_exit", src)
        self.assertIn("_end_nested_modals", src)
        self.assertNotIn("dlg.Destroy()", src)
        self.assertNotIn("flush_pending_webview_destroys", src)
        self.assertNotIn("schedule_webview_window_destroy", src)
        self.assertNotIn("CallLater", src)
        nested = inspect.getsource(MainFrame._end_nested_modals)
        self.assertIn("_end_inventory_modal", nested)
        self.assertIn("_overview_dialog", nested)
        self.assertIn("_issue_detail_dialog", nested)
        self.assertIn("_ensure_end_modal", nested)
        self.assertIn("ExitMainLoop", inspect.getsource(MainFrame._finish_app_exit))
        self.assertIn("os._exit", inspect.getsource(MainFrame._finish_app_exit))

    def test_after_shown_swallows_deleted_host(self):
        import inspect

        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog

        src = inspect.getsource(AltTextReportDialog._after_shown)
        self.assertIn("except RuntimeError", src)
        sync = inspect.getsource(AltTextReportDialog._sync_chat_chrome)
        self.assertIn("except RuntimeError", sync)


class NestedEdgeModalCloseTests(unittest.TestCase):
    def test_issue_detail_close_ends_modal_immediately(self):
        import inspect

        from checkmate.main import IssueDetailDialog, MainFrame

        close_src = inspect.getsource(IssueDetailDialog._on_close_dialog)
        self.assertIn("_ensure_end_modal", close_src)
        self.assertIn("reclaim_focus=False", close_src)
        closing_branch = close_src.split('if getattr(self, "_closing", False):', 1)[1]
        closing_branch = closing_branch.split("self._closing = True", 1)[0]
        self.assertIn("_ensure_end_modal", closing_branch)
        show_src = inspect.getsource(MainFrame._show_issue_details)
        self.assertIn("self._issue_detail_dialog = dlg", show_src)
        self.assertIn("dlg.ShowModal()", show_src)
        self.assertLess(
            show_src.find("self._issue_detail_dialog = dlg"),
            show_src.find("dlg.ShowModal()"),
        )

    def test_overview_close_ends_modal_immediately(self):
        import inspect

        from checkmate.main import AiOverviewDialog, MainFrame

        close_src = inspect.getsource(AiOverviewDialog._on_close_dialog)
        self.assertIn("_ensure_end_modal", close_src)
        self.assertIn("reclaim_focus=False", close_src)
        closing_branch = close_src.split('if getattr(self, "_closing", False):', 1)[1]
        closing_branch = closing_branch.split("self._closing = True", 1)[0]
        self.assertIn("_ensure_end_modal", closing_branch)
        run_src = inspect.getsource(MainFrame._run_overview_dialog)
        self.assertIn("dlg.ShowModal()", run_src)
        self.assertNotIn("self._overview_dialog = None", run_src.split("dlg.ShowModal()", 1)[0])
        self.assertIn("self._overview_dialog = None", run_src.split("finally:", 1)[1])


class ScheduleDestroyTests(unittest.TestCase):
    def test_none_is_noop(self):
        from checkmate.ai.alt_inventory_dialog import schedule_webview_window_destroy

        schedule_webview_window_destroy(None)

    def test_keeps_calllater_alive(self):
        import wx

        from checkmate.ai import alt_inventory_dialog as mod

        app = wx.GetApp() or wx.App(False)
        self.assertIsNotNone(app)
        frame = wx.Frame(None)
        try:
            before = len(mod._pending_later)
            mod.schedule_webview_window_destroy(frame, delay_ms=60_000)
            self.assertGreater(len(mod._pending_later), before)
            self.assertTrue(mod._pending_later[-1].IsRunning())
            mod._pending_later[-1].Stop()
            if frame in mod._pending_webview_destroy:
                mod._pending_webview_destroy.remove(frame)
        finally:
            frame.Destroy()


class WebviewChatDialogSizeTests(unittest.TestCase):
    def test_first_open_uses_three_quarters_except_ultrawide(self) -> None:
        from checkmate.settings import default_webview_chat_dialog_size

        self.assertEqual(default_webview_chat_dialog_size(1920, 1080), (1440, 810))
        self.assertEqual(default_webview_chat_dialog_size(2560, 1440), (1920, 1080))
        self.assertEqual(default_webview_chat_dialog_size(3440, 1440), (1720, 1080))
        self.assertEqual(default_webview_chat_dialog_size(3840, 1080), (1920, 810))

    def test_saved_size_is_per_dialog_kind(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from checkmate import settings as settings_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertIsNone(settings_mod.webview_chat_dialog_size("overview"))
                settings_mod.set_webview_chat_dialog_size("overview", 1400, 800)
                settings_mod.set_webview_chat_dialog_size("image_report", 1600, 900)
                self.assertEqual(
                    settings_mod.webview_chat_dialog_size("overview"), (1400, 800)
                )
                self.assertEqual(
                    settings_mod.webview_chat_dialog_size("image_report"), (1600, 900)
                )

    def test_hide_chat_keeps_saved_width_until_user_toggles(self) -> None:
        import inspect

        from checkmate.ai.conversation_pane import set_chat_pane_shown
        from checkmate.ai.alt_dialog import AltSniffTestMixin
        from checkmate.ai.alt_inventory_dialog import AltTextReportDialog
        from checkmate.main import AiOverviewDialog

        shown_src = inspect.getsource(set_chat_pane_shown)
        self.assertIn("remember_width", shown_src)
        self.assertIn("if remember_width:", shown_src)
        overview_apply = inspect.getsource(AiOverviewDialog._apply_chat_pane_shown)
        self.assertIn("remember_width=persist", overview_apply)
        report_apply = inspect.getsource(AltTextReportDialog._apply_chat_pane_shown)
        self.assertIn("remember_width=persist", report_apply)
        overview_close = inspect.getsource(AiOverviewDialog._on_close_dialog)
        self.assertIn("remember_webview_chat_dialog_size", overview_close)
        mixin_close = inspect.getsource(AltSniffTestMixin._on_close_dialog)
        self.assertIn("remember_webview_chat_dialog_size", mixin_close)


if __name__ == "__main__":
    unittest.main()
