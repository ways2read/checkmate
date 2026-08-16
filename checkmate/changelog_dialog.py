"""Dialog to view a CheckMate edit changelog in a styled WebView."""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

import wx

from .i18n import _

logger = logging.getLogger(__name__)


class ChangelogDialog(wx.Dialog):
    """Show ``*.checkmate-changelog.md`` as formatted HTML in a WebView."""

    def __init__(self, parent: wx.Window, *, path: Path, markdown_text: str) -> None:
        super().__init__(
            parent,
            title=_("Edit changelog"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.SetSize((780, 640))
        self._path = Path(path)
        self._markdown = markdown_text or ""
        self._view_realized = False
        self._output_is_webview = False
        self._closing = False

        # Import helpers that live with the AI HTML viewer in main.
        from . import main as main_mod

        self._main = main_mod

        root = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(self, label=_("Edit changelog"))
        font = heading.GetFont()
        if font.IsOk():
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            heading.SetFont(font)
        root.Add(heading, 0, wx.ALL, 12)

        path_label = wx.StaticText(self, label=str(self._path))
        from .ui_appearance import secondary_text_colour

        path_label.SetForegroundColour(secondary_text_colour())
        root.Add(path_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._host = main_mod._AiHtmlHostPanel(self, name=_("Edit changelog"))
        self._host.SetMinSize((-1, 420))
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        self._loading_label = wx.StaticText(
            self._host, label=_("Loading AI view…")
        )
        host_sizer.Add(self._loading_label, 0, wx.ALL, 8)
        self._host.SetSizer(host_sizer)
        main_mod._win_clear_tab_stop(self._host)
        root.Add(self._host, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.open_browser_btn = wx.Button(self, label=_("Open in &browser"))
        self.open_folder_btn = wx.Button(self, label=_("Open &folder"))
        self.open_browser_btn.SetToolTip(_("Open a formatted HTML view in your browser"))
        self.open_folder_btn.SetToolTip(_("Reveal the changelog file in the file manager"))
        close_btn = wx.Button(self, wx.ID_CLOSE, label=_("&Close"))
        btns.Add(self.open_browser_btn, 0, wx.RIGHT, 8)
        btns.Add(self.open_folder_btn, 0, wx.RIGHT, 8)
        btns.AddStretchSpacer(1)
        btns.Add(close_btn, 0)
        root.Add(btns, 0, wx.EXPAND | wx.ALL, 12)

        self.open_browser_btn.Bind(wx.EVT_BUTTON, self._on_open_browser)
        self.open_folder_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.SetSizer(root)
        self.CentreOnParent()
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)
        close_btn.SetDefault()

        wx.CallAfter(self._realize_view)

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            wx.CallAfter(self._on_close_dialog, None)
            return
        event.Skip()

    def _page_html(self) -> str:
        from .ai.markdown_html import markdown_to_browser_page

        return markdown_to_browser_page(
            self._markdown,
            title=_("Edit changelog"),
            plain=False,
            tab_exit=True,
        )

    def _realize_view(self) -> None:
        if self._view_realized:
            return
        host = self._host
        view, is_webview = self._main._create_ai_html_view(host)
        view.SetMinSize((-1, 420))
        self._output = view
        self._output_is_webview = is_webview
        self._view_realized = True

        if is_webview:
            import wx.html2 as html2

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_navigating)
            view.SetPage(self._page_html(), "")
        else:
            # Fallback: plain text control with markdown source.
            view.ChangeValue(self._markdown)

        sizer = host.GetSizer()
        if sizer is None:
            sizer = wx.BoxSizer(wx.VERTICAL)
            host.SetSizer(sizer)
        else:
            sizer.Clear(delete_windows=True)
        sizer.Add(view, 1, wx.EXPAND)
        self._main._wire_ai_html_host(host, view, is_webview=is_webview)
        host.Layout()
        self.Layout()
        if is_webview:
            wx.CallLater(
                120,
                lambda: self._main._refresh_ai_html_tab_stops(
                    host, view, is_webview=True
                ),
            )
            wx.CallLater(
                300,
                lambda: self._main._refresh_ai_html_tab_stops(
                    host, view, is_webview=True
                ),
            )

    def _on_navigating(self, event) -> None:
        url = (event.GetURL() or "").strip()
        action = self._main._webview_host_action(url)
        if action == "close":
            event.Veto()
            wx.CallAfter(self._on_close_dialog, None)
            return
        if action in ("page_prev", "page_next"):
            event.Veto()
            return
        if action in ("next", "prev"):
            event.Veto()
            wx.CallAfter(self._leave_webview, action == "next")
            return
        if url.startswith(("http://", "https://", "mailto:")):
            event.Veto()
            try:
                webbrowser.open(url)
            except OSError:
                pass
            return
        event.Skip()

    def _leave_webview(self, forward: bool) -> None:
        if forward:
            if self._main._try_set_focus(self.open_browser_btn):
                return
            close_btn = self.FindWindowById(wx.ID_CLOSE)
            self._main._try_set_focus(close_btn)
            return
        # Nowhere above the WebView — stay on open-browser when Shift+Tab out.
        self._main._try_set_focus(self.open_browser_btn)

    def _on_open_browser(self, _event: wx.Event) -> None:
        import os
        import tempfile

        try:
            fd, name = tempfile.mkstemp(
                prefix="checkmate-changelog-",
                suffix=".html",
                text=True,
            )
            os.close(fd)
            path = Path(name)
            path.write_text(self._page_html(), encoding="utf-8")
            webbrowser.open(path.as_uri())
        except OSError as exc:
            wx.MessageBox(
                _("Could not open the changelog in a browser:\n{error}").format(
                    error=exc
                ),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_open_folder(self, _event: wx.Event) -> None:
        folder = self._path.parent
        try:
            if wx.Platform == "__WXMSW__":
                wx.LaunchDefaultApplication(str(folder))
            else:
                webbrowser.open(folder.as_uri())
        except Exception:
            try:
                webbrowser.open(folder.as_uri())
            except OSError as exc:
                wx.MessageBox(
                    _("Could not open the folder:\n{error}").format(error=exc),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )

    def _on_close_dialog(self, event: wx.Event | None = None) -> None:
        if getattr(self, "_closing", False):
            if isinstance(event, wx.CloseEvent):
                event.Veto()
            return
        self._closing = True
        if isinstance(event, wx.CloseEvent):
            event.Veto()
        wx.CallAfter(self._finish_close_dialog)

    def _on_close(self, event: wx.Event | None = None) -> None:
        self._on_close_dialog(event)

    def _finish_close_dialog(self) -> None:
        try:
            if not self:
                return
        except RuntimeError:
            return
        try:
            if self.IsModal():
                self.EndModal(wx.ID_CLOSE)
            else:
                self.Destroy()
        except RuntimeError:
            pass
