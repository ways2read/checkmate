"""Dialog to view a Fido-style alt-text inventory report in a WebView."""

from __future__ import annotations

import logging
import webbrowser
from pathlib import Path

import wx

from ..i18n import _
from ..settings import ai_features_enabled

logger = logging.getLogger(__name__)

# Returned from ShowModal when the user wants the AI health check.
ID_RUN_AI_HEALTH = wx.NewIdRef()


class AltTextReportDialog(wx.Dialog):
    """Show ``alt_text_report.html`` from an export folder."""

    def __init__(self, parent: wx.Window, *, folder: Path, html_path: Path) -> None:
        super().__init__(
            parent,
            title=_("Alt text report"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.SetSize((900, 720))
        self.folder = Path(folder)
        self._html_path = Path(html_path)
        self._view_realized = False
        self._output_is_webview = False
        self._closing = False

        from .. import main as main_mod

        self._main = main_mod

        root = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(self, label=_("Alt text report"))
        font = heading.GetFont()
        if font.IsOk():
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            heading.SetFont(font)
        root.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        path_label = wx.StaticText(self, label=str(self.folder))
        path_label.SetForegroundColour(wx.Colour(70, 70, 70))
        root.Add(path_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self._host = main_mod._AiHtmlHostPanel(self, name=_("Alt text report"))
        self._host.SetMinSize((-1, 420))
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        self._loading_label = wx.StaticText(
            self._host, label=_("Loading report…")
        )
        host_sizer.Add(self._loading_label, 0, wx.ALL, 8)
        self._host.SetSizer(host_sizer)
        main_mod._win_clear_tab_stop(self._host)
        root.Add(self._host, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.ai_health_btn = wx.Button(self, label=_("Run AI &health check…"))
        self.ai_health_btn.SetToolTip(
            _(
                "Sample images and assess decorative status and alt quality with AI"
            )
        )
        self.open_browser_btn = wx.Button(self, label=_("Open in &browser"))
        self.open_folder_btn = wx.Button(self, label=_("Open &folder"))
        self.open_browser_btn.SetToolTip(
            _("Open the alt text report in your browser")
        )
        self.open_folder_btn.SetToolTip(
            _("Reveal the export folder in the file manager")
        )
        close_btn = wx.Button(self, wx.ID_CLOSE, label=_("&Close"))

        if ai_features_enabled():
            btns.Add(self.ai_health_btn, 0, wx.RIGHT, 8)
        else:
            self.ai_health_btn.Hide()
        btns.Add(self.open_browser_btn, 0, wx.RIGHT, 8)
        btns.Add(self.open_folder_btn, 0, wx.RIGHT, 8)
        btns.AddStretchSpacer(1)
        btns.Add(close_btn, 0)
        root.Add(btns, 0, wx.EXPAND | wx.ALL, 12)

        self.ai_health_btn.Bind(wx.EVT_BUTTON, self._on_ai_health)
        self.open_browser_btn.Bind(wx.EVT_BUTTON, self._on_open_browser)
        self.open_folder_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        close_btn.Bind(wx.EVT_BUTTON, self._on_close)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.SetSizer(root)
        self.CentreOnParent()
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)
        close_btn.SetDefault()

        wx.CallAfter(self._realize_view)

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._on_close(event)
            return
        event.Skip()

    def _realize_view(self) -> None:
        if self._view_realized or self._closing:
            return
        host = self._host
        view, is_webview = self._main._create_ai_html_view(
            host, name=_("Alt text report")
        )
        view.SetMinSize((-1, 420))
        self._output = view
        self._output_is_webview = is_webview
        self._view_realized = True

        if is_webview:
            import wx.html2 as html2

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_navigating)
            try:
                view.LoadURL(self._html_path.resolve().as_uri())
            except Exception:
                logger.exception("LoadURL failed for %s", self._html_path)
                try:
                    view.SetPage(
                        self._html_path.read_text(encoding="utf-8", errors="replace"),
                        self._html_path.resolve().as_uri(),
                    )
                except OSError as exc:
                    wx.MessageBox(
                        _("Could not load the alt text report:\n{error}").format(
                            error=exc
                        ),
                        _("Alt text report"),
                        wx.OK | wx.ICON_ERROR,
                        self,
                    )
        else:
            # No WebView — open in the system browser and keep a short note.
            view.ChangeValue(
                _(
                    "Built-in browser view is unavailable.\n"
                    "Use “Open in browser” to view the alt text report."
                )
            )
            try:
                webbrowser.open(self._html_path.resolve().as_uri())
            except OSError:
                pass

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

    def _on_navigating(self, event) -> None:
        url = (event.GetURL() or "").strip()
        action = self._main._webview_host_action(url)
        if action == "close":
            event.Veto()
            wx.CallAfter(self._on_close, None)
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
        # Allow file:// navigations within the export (images, etc.).
        event.Skip()

    def _leave_webview(self, forward: bool) -> None:
        if forward:
            if (
                ai_features_enabled()
                and self.ai_health_btn.IsShown()
                and self._main._try_set_focus(self.ai_health_btn)
            ):
                return
            if self._main._try_set_focus(self.open_browser_btn):
                return
            close_btn = self.FindWindowById(wx.ID_CLOSE)
            self._main._try_set_focus(close_btn)
            return
        if ai_features_enabled() and self.ai_health_btn.IsShown():
            self._main._try_set_focus(self.ai_health_btn)
        else:
            self._main._try_set_focus(self.open_browser_btn)

    def _on_ai_health(self, _event: wx.Event) -> None:
        if self.IsModal():
            self.EndModal(int(ID_RUN_AI_HEALTH))
        else:
            self.Destroy()

    def _on_open_browser(self, _event: wx.Event) -> None:
        try:
            webbrowser.open(self._html_path.resolve().as_uri())
        except OSError as exc:
            wx.MessageBox(
                _("Could not open the report in a browser:\n{error}").format(
                    error=exc
                ),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_open_folder(self, _event: wx.Event) -> None:
        folder = self.folder
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

    def _on_close(self, _event) -> None:
        self._closing = True
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()
