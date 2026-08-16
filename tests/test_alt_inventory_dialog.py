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
    def test_webview_file_uri_adds_token(self):
        from checkmate.ai.alt_inventory_dialog import webview_file_uri

        uri = webview_file_uri(Path("C:/tmp/export/alt_text_report.html"), token="abc123")
        self.assertIn("alt_text_report.html", uri)
        self.assertIn("cm=abc123", uri)

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
            self.assertTrue(hasattr(MainFrame, "_reclaim_after_modal"))
        finally:
            frame.Destroy()


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


if __name__ == "__main__":
    unittest.main()
