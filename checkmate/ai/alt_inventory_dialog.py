"""Dialog to view a Fido-style alt-text inventory report in a WebView."""

from __future__ import annotations

import logging
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import unquote

import wx

from ..i18n import _
from ..settings import ai_features_enabled
from .alt_dialog import AltSniffTestMixin
from .alt_labels import feature_run_button_label, feature_title

logger = logging.getLogger(__name__)

# Legacy ShowModal id (no longer used). Sniff test runs on a tab of this dialog.
ID_RUN_AI_HEALTH = wx.NewIdRef()

# Edge WebView2 crashes or serves a blank HWND if we Destroy a controller
# while it is still tearing down, then immediately create another.
_pending_webview_destroy: list[wx.Window] = []
_pending_later: list[wx.CallLater] = []


def _retain_calllater(timer: wx.CallLater) -> wx.CallLater:
    """Keep *timer* alive; wx.CallLater is GC'd if nothing references it."""
    kept: list[wx.CallLater] = []
    for existing in _pending_later:
        if existing is timer:
            continue
        try:
            if existing.IsRunning():
                kept.append(existing)
        except Exception:
            pass
    kept.append(timer)
    _pending_later[:] = kept
    return timer


def _safe_destroy_window(win: wx.Window | None) -> None:
    if win is None:
        return
    try:
        if win:
            win.Destroy()
    except Exception:
        pass


def flush_pending_webview_destroys() -> None:
    """Destroy any delayed Edge hosts now (app exit / next-modal handoff)."""
    pending = list(_pending_webview_destroy)
    _pending_webview_destroy.clear()
    for win in pending:
        _safe_destroy_window(win)


def webview_file_uri(path: Path, *, token: str | None = None) -> str:
    """``file://`` URI that Edge treats as a new navigation.

    Reusing the same path after a prior WebView (the on-disk export cache)
    often paints a blank document. A query token avoids that no-op.
    """
    uri = path.resolve().as_uri()
    nonce = token or uuid.uuid4().hex
    sep = "&" if "?" in uri else "?"
    return f"{uri}{sep}cm={nonce}"


def write_unique_view_html(src: Path) -> Path:
    """Copy *src* to a unique sibling so the ``file://`` path itself is new."""
    dest = src.parent / f".cm_view_{uuid.uuid4().hex}.html"
    dest.write_bytes(src.read_bytes())
    return dest


def cleanup_view_html(folder: Path, keep: Path | None = None) -> None:
    """Remove leftover ``.cm_view_*.html`` copies from a previous open."""
    keep_resolved = None
    if keep is not None:
        try:
            keep_resolved = keep.resolve()
        except OSError:
            keep_resolved = None
    for path in folder.glob(".cm_view_*.html"):
        try:
            if keep_resolved is not None and path.resolve() == keep_resolved:
                continue
            path.unlink()
        except OSError:
            pass


def pending_webview_destroy_count() -> int:
    return len(_pending_webview_destroy)


PREVIEW_URL_PREFIX = "https://checkmate.invalid/preview/"


def preview_href_for_index(index: int) -> str:
    """Navigation target for in-app thumbnail clicks (intercepted by the host)."""
    return f"{PREVIEW_URL_PREFIX}{int(index)}"


def preview_index_from_url(url: str) -> int | None:
    """Return the export image index from a preview navigation, or None."""
    raw = (url or "").strip()
    lower = raw.lower()
    for marker in (PREVIEW_URL_PREFIX, "checkmate://preview/"):
        if not lower.startswith(marker):
            continue
        rest = unquote(raw[len(marker) :].split("?", 1)[0]).strip("/").replace("\\", "/")
        if rest.startswith("index/"):
            rest = rest[6:]
        if rest.isdigit():
            return int(rest)
        return None
    return None


def preview_filename_from_url(url: str) -> str | None:
    """Return the image filename from ``checkmate://preview/<name>``, or None."""
    raw = (url or "").strip()
    lower = raw.lower()
    marker = "checkmate://preview/"
    if not lower.startswith(marker):
        return None
    name = unquote(raw[len(marker) :].split("?", 1)[0]).replace("\\", "/")
    if not name or "/" in name or name in {".", ".."} or name.startswith("."):
        return None
    if name.isdigit() or name.startswith("index/"):
        return None
    return name


def safe_export_image_path(folder: Path, filename: str) -> Path | None:
    """Resolve *filename* under ``images/`` or ``thumbs/``; reject path escape."""
    name = Path(filename).name
    if not name or name.startswith("."):
        return None
    root = folder.expanduser().resolve()
    for sub in ("images", "thumbs"):
        candidate = (root / sub / name).resolve()
        try:
            candidate.relative_to(root / sub)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def schedule_webview_window_destroy(win: wx.Window | None, *, delay_ms: int = 500) -> None:
    """Destroy after EndModal so Edge can drop the HWND first."""
    if win is None:
        return
    if win in _pending_webview_destroy:
        return
    _pending_webview_destroy.append(win)
    parent = None
    try:
        parent = win.GetParent()
    except Exception:
        parent = None

    def _go(window: wx.Window = win, host: wx.Window | None = parent) -> None:
        try:
            if window in _pending_webview_destroy:
                _pending_webview_destroy.remove(window)
        except ValueError:
            pass
        _safe_destroy_window(window)
        if host is None:
            return
        try:
            host.Enable(True)
        except RuntimeError:
            pass

    _retain_calllater(wx.CallLater(delay_ms, _go))


class AltTextReportDialog(AltSniffTestMixin, wx.Dialog):
    """Alt-text inventory report with an optional sniff-test page.

    Same pattern as issue details: notebook tabs are a strip only; each page
    has its own WebView host stacked in a content panel (show/hide).
    """

    _PAGE_REPORT = "report"
    _PAGE_SNIFF = "sniff"

    def __init__(self, parent: wx.Window, *, folder: Path, html_path: Path) -> None:
        super().__init__(
            parent,
            title=_("Alt text report"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.SetSize((900, 760))
        self.SetMinSize((720, 520))
        self.folder = Path(folder)
        self._html_path = Path(html_path)
        cleanup_view_html(self.folder)
        self.exit_code = int(wx.ID_CANCEL)
        self._view_realized = False
        self._output_is_webview = False
        self._closing = False
        self._load_gen = 0
        self._output: wx.Window | None = None
        self._pending_later: list[wx.CallLater] = []
        self._dialog_html_cache: str | None = None
        self._show_sniff = ai_features_enabled()
        self._notebook: wx.Notebook | None = None
        self._page_keys: list[str] = [self._PAGE_REPORT]
        self._active_page_key = self._PAGE_REPORT
        self._content_panel: wx.Panel | None = None
        self._sniff_run_panel: wx.Panel | None = None
        self._sniff_followup: wx.Panel | None = None
        self._sniff_actions: wx.Panel | None = None
        self._report_actions: wx.Panel | None = None

        from .. import main as main_mod

        self._main = main_mod
        self._init_sniff_state()

        root = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(self, label=_("Alt text report"))
        font = heading.GetFont()
        if font.IsOk():
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            heading.SetFont(font)
        root.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self._path_label = wx.StaticText(self, label=str(self.folder))
        from ..ui_appearance import secondary_text_colour

        self._path_label.SetForegroundColour(secondary_text_colour())
        root.Add(self._path_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._notebook = wx.Notebook(self, name=_("Alt text report pages"))
        self._page_keys = [self._PAGE_REPORT]
        if self._show_sniff:
            self._page_keys.append(self._PAGE_SNIFF)
        for key, label in (
            (self._PAGE_REPORT, _("Alt text report")),
            (self._PAGE_SNIFF, feature_title()),
        ):
            if key not in self._page_keys:
                continue
            page = wx.Panel(self._notebook)
            page.SetMinSize((-1, 1))
            self._notebook.AddPage(page, label)
        root.Add(self._notebook, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        if self._show_sniff:
            self._sniff_run_panel = self._build_sniff_run_panel(self)
            root.Add(
                self._sniff_run_panel,
                0,
                wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                8,
            )

        self._content_panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
        main_mod._win_ensure_control_parent(self._content_panel)
        self._host = main_mod._AiHtmlHostPanel(
            self._content_panel, name=_("Alt text report")
        )
        self._host.SetMinSize((-1, 280))
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        self._loading_label = wx.StaticText(
            self._host, label=_("Loading report…")
        )
        host_sizer.Add(self._loading_label, 0, wx.ALL, 8)
        self._host.SetSizer(host_sizer)
        main_mod._win_clear_tab_stop(self._host)

        content_sizer = wx.BoxSizer(wx.VERTICAL)
        content_sizer.Add(self._host, 1, wx.EXPAND)
        if self._show_sniff:
            self._sniff_host = main_mod._AiHtmlHostPanel(
                self._content_panel, name=feature_title()
            )
            self._sniff_host.SetMinSize((-1, 280))
            sniff_sizer = wx.BoxSizer(wx.VERTICAL)
            sniff_loading = wx.StaticText(
                self._sniff_host,
                label=_("Loading sniff test…"),
            )
            sniff_sizer.Add(sniff_loading, 0, wx.ALL, 8)
            self._sniff_host.SetSizer(sniff_sizer)
            main_mod._win_clear_tab_stop(self._sniff_host)
            self._sniff_host.Hide()
            content_sizer.Add(self._sniff_host, 1, wx.EXPAND)
        self._content_panel.SetSizer(content_sizer)
        root.Add(self._content_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self._report_actions = self._build_report_actions(self)
        root.Add(self._report_actions, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        if self._show_sniff:
            self._sniff_followup = self._build_sniff_followup(self)
            self._sniff_actions = self._build_sniff_actions(self)
            root.Add(self._sniff_followup, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
            root.Add(self._sniff_actions, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        close_row = wx.BoxSizer(wx.HORIZONTAL)
        close_row.AddStretchSpacer(1)
        self._close_btn = wx.Button(self, wx.ID_ANY, label=_("&Close"))
        close_row.Add(self._close_btn, 0)
        root.Add(close_row, 0, wx.EXPAND | wx.ALL, 12)

        self._close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self._notebook.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_notebook_page)
        self._id_prev_page = wx.NewIdRef()
        self._id_next_page = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, self._on_accel_prev_page, id=self._id_prev_page)
        self.Bind(wx.EVT_MENU, self._on_accel_next_page, id=self._id_next_page)
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [
                    (wx.ACCEL_CTRL, wx.WXK_PAGEUP, self._id_prev_page),
                    (wx.ACCEL_CTRL, wx.WXK_PAGEDOWN, self._id_next_page),
                ]
            )
        )

        self.SetSizer(root)
        self.CentreOnParent()
        # Escape is handled by CHAR_HOOK / in-page JS. Stock ID_CLOSE as the
        # escape id leaves a queued EndModal that instantly closes the next open.
        self.SetEscapeId(wx.ID_NONE)
        self.SetAffirmativeId(wx.ID_NONE)
        self._close_btn.SetDefault()
        self._apply_active_page(self._PAGE_REPORT, initial=True)

        # Do not CallAfter(_realize_view) from __init__: Destroy() of a previous
        # WebView can pump that callback before ShowModal, and Edge then paints
        # a blank HWND. Create the control only after this dialog is shown.
        self.Bind(wx.EVT_SHOW, self._on_show)

    def _build_sniff_run_panel(self, parent: wx.Window) -> wx.Panel:
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        self._main._win_clear_tab_stop(panel)
        self._main._win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.sniff_run_btn = wx.Button(panel, label=feature_run_button_label())
        self.sniff_run_btn.SetToolTip(
            _(
                "Sample images and assess decorative status and alt quality with AI"
            )
        )
        self.sniff_run_btn.Bind(wx.EVT_BUTTON, self._on_run_sniff)
        self.ai_health_btn = self.sniff_run_btn
        sizer.Add(self.sniff_run_btn, 0, wx.TOP | wx.BOTTOM, 4)
        panel.SetSizer(sizer)
        panel.Hide()
        return panel

    def _build_sniff_followup(self, parent: wx.Window) -> wx.Panel:
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        self._main._win_clear_tab_stop(panel)
        self._main._win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.followup_ctrl, self.ask_btn = self._main._add_followup_question_row(
            panel,
            sizer,
            on_ask=self._on_ask,
            ask_enabled=False,
        )
        self.followup_ctrl.Bind(wx.EVT_SET_FOCUS, self._on_followup_focus)
        self.followup_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_followup_kill_focus)
        panel.SetSizer(sizer)
        panel.Hide()
        return panel

    def _build_sniff_actions(self, parent: wx.Window) -> wx.Panel:
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        self._main._win_clear_tab_stop(panel)
        self._main._win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.assess_more_btn = wx.Button(panel, label=_("Assess &more…"))
        self.assess_more_btn.SetToolTip(
            _("Send additional images to the vision model (keeps earlier results)")
        )
        self.assess_more_btn.Bind(wx.EVT_BUTTON, self._on_assess_more)
        self.assess_more_btn.Enable(False)
        sizer.Add(self.assess_more_btn, 0, wx.RIGHT, 8)

        self.save_html_btn = wx.Button(panel, label=_("Save as &HTML…"))
        self.save_html_btn.Bind(wx.EVT_BUTTON, self._on_save_html)
        sizer.Add(self.save_html_btn, 0, wx.RIGHT, 8)

        self.save_md_btn = wx.Button(panel, label=_("Save as &Markdown…"))
        self.save_md_btn.Bind(wx.EVT_BUTTON, self._on_save_md)
        sizer.Add(self.save_md_btn, 0, wx.RIGHT, 8)

        self.copy_btn = wx.Button(panel, label=_("&Copy"))
        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy)
        sizer.Add(self.copy_btn, 0, wx.RIGHT, 8)

        self.view_browser_btn = wx.Button(panel, label=_("View in &browser"))
        self.view_browser_btn.SetToolTip(
            _("Open the sniff-test report in your web browser")
        )
        self.view_browser_btn.Bind(wx.EVT_BUTTON, self._on_view_browser)
        sizer.Add(self.view_browser_btn, 0)
        panel.SetSizer(sizer)
        panel.Hide()
        return panel

    def _build_report_actions(self, parent: wx.Window) -> wx.Panel:
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        self._main._win_clear_tab_stop(panel)
        self._main._win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.open_browser_btn = wx.Button(panel, label=_("Open in &browser"))
        self.open_folder_btn = wx.Button(panel, label=_("Open &folder"))
        self.open_browser_btn.SetToolTip(
            _("Open the alt text report in your browser")
        )
        self.open_folder_btn.SetToolTip(
            _("Reveal the export folder in the file manager")
        )
        self.open_browser_btn.Bind(wx.EVT_BUTTON, self._on_open_browser)
        self.open_folder_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        sizer.Add(self.open_browser_btn, 0, wx.RIGHT, 8)
        sizer.Add(self.open_folder_btn, 0)
        panel.SetSizer(sizer)
        return panel

    def _page_key_for_selection(self, sel: int) -> str:
        if 0 <= sel < len(self._page_keys):
            return self._page_keys[sel]
        return self._PAGE_REPORT

    def _on_notebook_page(self, event: wx.BookCtrlEvent) -> None:
        event.Skip()
        self._apply_active_page(self._page_key_for_selection(event.GetSelection()))

    def _on_accel_prev_page(self, _event: wx.Event) -> None:
        self._cycle_notebook_page(-1)

    def _on_accel_next_page(self, _event: wx.Event) -> None:
        self._cycle_notebook_page(1)

    def _cycle_notebook_page(self, delta: int) -> None:
        if self._notebook is None or not delta:
            return
        count = self._notebook.GetPageCount()
        if count <= 1:
            return
        sel = self._notebook.GetSelection()
        if sel < 0:
            return
        new_sel = sel + int(delta)
        if new_sel < 0 or new_sel >= count or new_sel == sel:
            return
        self._notebook.SetSelection(new_sel)

    def _select_page(self, key: str) -> None:
        if self._notebook is None:
            self._apply_active_page(key)
            return
        try:
            idx = self._page_keys.index(key)
        except ValueError:
            return
        if self._notebook.GetSelection() != idx:
            self._notebook.ChangeSelection(idx)
        self._apply_active_page(key)

    def _apply_active_page(self, key: str, *, initial: bool = False) -> None:
        self._active_page_key = key
        show_report = key == self._PAGE_REPORT
        show_sniff = key == self._PAGE_SNIFF and self._show_sniff

        self._host.Show(show_report)
        if self._sniff_host is not None:
            self._sniff_host.Show(show_sniff)
        if self._sniff_run_panel is not None:
            self._sniff_run_panel.Show(show_sniff)
        if self._report_actions is not None:
            self._report_actions.Show(show_report)
        if self._sniff_followup is not None:
            self._sniff_followup.Show(show_sniff)
        if self._sniff_actions is not None:
            self._sniff_actions.Show(show_sniff)

        if not initial and show_sniff:
            self._realize_sniff_view()

        self._refresh_host_tab_stops()
        try:
            self.Layout()
            if self._content_panel is not None:
                self._content_panel.Layout()
        except RuntimeError:
            pass

    def _refresh_host_tab_stops(self) -> None:
        key = self._active_page_key
        details = self._host
        sniff = getattr(self, "_sniff_host", None)
        on_report = key == self._PAGE_REPORT
        if on_report and getattr(self, "_output_is_webview", False):
            view = getattr(self, "_output", None)
            if view is not None:
                self._main._refresh_ai_html_tab_stops(details, view, is_webview=True)
            details._accept_kbd_focus = True
        else:
            self._main._win_clear_tab_stop(details)
            details._accept_kbd_focus = False

        if sniff is not None:
            on_sniff = key == self._PAGE_SNIFF
            ready = (
                on_sniff
                and getattr(self, "_sniff_view_realized", False)
                and getattr(self, "_sniff_is_webview", False)
            )
            if ready and self._sniff_view is not None:
                self._main._refresh_ai_html_tab_stops(
                    sniff, self._sniff_view, is_webview=True
                )
                sniff._accept_kbd_focus = True
            else:
                self._main._win_clear_tab_stop(sniff)
                sniff._accept_kbd_focus = False

        if self._content_panel is not None:
            self._main._win_clear_tab_stop(self._content_panel)
            self._main._win_ensure_control_parent(self._content_panel)

        for panel in (
            self._sniff_run_panel,
            self._report_actions,
            self._sniff_followup,
            self._sniff_actions,
        ):
            if panel is None:
                continue
            try:
                if panel.IsShown():
                    self._main._win_clear_tab_stop(panel)
                    self._main._win_ensure_control_parent(panel)
            except RuntimeError:
                pass

    def prepare(self, folder: Path, html_path: Path) -> None:
        """Reuse this dialog for another ShowModal without recreating Edge."""
        self._stop_pending_later()
        self.folder = Path(folder)
        self._html_path = Path(html_path)
        cleanup_view_html(self.folder)
        self.exit_code = int(wx.ID_CANCEL)
        self._closing = False
        self._dialog_html_cache = None
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        try:
            self._path_label.SetLabel(str(self.folder))
        except RuntimeError:
            pass
        self._reset_sniff_state()
        if self._notebook is not None:
            self._select_page(self._PAGE_REPORT)

    def _same_gen(self, gen: int) -> bool:
        if self._closing or int(gen) != int(getattr(self, "_load_gen", 0)):
            return False
        try:
            return bool(self)
        except RuntimeError:
            return False

    def _call_later(self, ms: int, fn) -> wx.CallLater:
        timer = wx.CallLater(ms, fn)
        self._pending_later.append(timer)
        return timer

    def _stop_pending_later(self) -> None:
        for timer in list(self._pending_later):
            try:
                timer.Stop()
            except Exception:
                pass
        self._pending_later.clear()

    def ShowModal(self):  # type: ignore[override]
        self._closing = False
        self.exit_code = int(wx.ID_CANCEL)
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        gen = self._load_gen
        wx.CallAfter(self._after_shown, gen)
        return super().ShowModal()

    def _after_shown(self, gen: int) -> None:
        if not self._same_gen(gen):
            return
        if not self._view_realized:
            self._realize_view()
        else:
            self._load_report()

    def _on_show(self, event: wx.ShowEvent) -> None:
        event.Skip()
        if not event.IsShown() or self._closing:
            return
        wx.CallAfter(self._after_shown, self._load_gen)

    def _inject_webview_key_handlers(self) -> None:
        """Escape/Tab-exit JS — Edge never delivers Escape to wx CHAR_HOOK."""
        if not self._output_is_webview or self._output is None or self._closing:
            return
        from .markdown_html import _WEBVIEW_TAB_EXIT_JS

        self._main._webview_run_script(self._output, _WEBVIEW_TAB_EXIT_JS)

    def _realize_view(self) -> None:
        if self._view_realized or self._closing:
            return
        try:
            if not self:
                return
        except RuntimeError:
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
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_webview_loaded)
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
            gen = self._load_gen
            self._call_later(
                120,
                lambda: self._refresh_tab_stops_if_gen(gen, host, view),
            )
            # First idle after New() is often still about:blank; SetPage twice.
            wx.CallAfter(self._load_report_if_gen, gen)
            self._call_later(300, lambda: self._load_report_if_gen(gen))
            self._call_later(250, lambda: self._inject_keys_if_gen(gen))

    def _refresh_tab_stops_if_gen(self, gen: int, host, view) -> None:
        if not self._same_gen(gen):
            return
        self._main._refresh_ai_html_tab_stops(host, view, is_webview=True)

    def _load_report_if_gen(self, gen: int) -> None:
        if not self._same_gen(gen):
            return
        self._load_report()

    def _inject_keys_if_gen(self, gen: int) -> None:
        if not self._same_gen(gen):
            return
        self._inject_webview_key_handlers()

    def _dialog_html(self) -> str:
        cached = getattr(self, "_dialog_html_cache", None)
        if cached is not None:
            return cached
        from .alt_export import inventory_webview_html

        try:
            html_doc = inventory_webview_html(self.folder)
        except Exception:
            logger.exception("Could not build in-dialog alt-text HTML")
            try:
                html_doc = self._html_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                html_doc = (
                    "<html><body><p>Could not load the alt text report.</p></body></html>"
                )
        self._dialog_html_cache = html_doc
        return html_doc

    def _load_report(self) -> None:
        """Load a self-contained report after the Edge HWND exists.

        ``SetPage`` (data-URI thumbs) is the path that reopens reliably.
        ``LoadURL(file://)`` often paints blank on the second open.
        """
        try:
            if not self:
                return
        except RuntimeError:
            return
        if self._closing or not self._output_is_webview or self._output is None:
            return
        html_doc = self._dialog_html()
        try:
            self._output.SetPage(html_doc, "")
            return
        except Exception:
            logger.exception("SetPage failed for alt text report")
        try:
            self._output.SetPage(html_doc, "about:blank")
        except Exception:
            logger.exception("SetPage about:blank fallback failed")

    def _on_webview_loaded(self, event) -> None:
        event.Skip()
        if self._closing:
            return
        # SetPage uses an empty base URL; about:blank here is not a failed load.
        try:
            from ..ui_appearance import apply_webview_appearance

            if self._output is not None:
                apply_webview_appearance(self._output)
        except Exception:
            pass
        self._inject_webview_key_handlers()

    def _show_full_preview_index(self, index: int) -> None:
        from .alt_export import load_alt_export

        try:
            export = load_alt_export(self.folder)
        except Exception:
            logger.exception("Could not load export for preview index %s", index)
            return
        for im in export.images:
            if int(im.index) != int(index):
                continue
            if im.image_path is not None and im.image_path.is_file():
                self._show_full_preview_path(im.image_path, im.filename or f"Image {index}")
                return
            if im.filename:
                self._show_full_preview(im.filename)
            return

    def _show_full_preview(self, filename: str) -> None:
        path = safe_export_image_path(self.folder, filename)
        if path is None:
            return
        self._show_full_preview_path(path, filename)

    def _show_full_preview_path(self, path: Path, title: str) -> None:
        dlg = wx.Dialog(
            self,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        try:
            image = wx.Image(str(path), wx.BITMAP_TYPE_ANY)
        except Exception:
            image = wx.NullImage
        if not image.IsOk():
            try:
                import fitz

                pix = fitz.Pixmap(str(path))
                tmp = path.with_name(path.stem + ".__preview__.jpg")
                tmp.write_bytes(pix.tobytes("jpeg", jpg_quality=80))
                image = wx.Image(str(tmp), wx.BITMAP_TYPE_JPEG)
                tmp.unlink(missing_ok=True)
            except Exception:
                image = wx.NullImage
        if not image.IsOk():
            dlg.Destroy()
            try:
                webbrowser.open(path.resolve().as_uri())
            except OSError:
                pass
            return
        display = wx.Display.GetFromWindow(self)
        if display == wx.NOT_FOUND:
            display = 0
        area = wx.Display(display).GetClientArea()
        max_w = max(320, int(area.Width * 0.9))
        max_h = max(240, int(area.Height * 0.85))
        w, h = image.GetWidth(), image.GetHeight()
        if w > max_w or h > max_h:
            scale = min(max_w / float(w or 1), max_h / float(h or 1))
            image = image.Scale(
                max(1, int(w * scale)),
                max(1, int(h * scale)),
                wx.IMAGE_QUALITY_HIGH,
            )
        bitmap = wx.StaticBitmap(dlg, bitmap=wx.Bitmap(image))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(bitmap, 1, wx.EXPAND | wx.ALL, 8)
        close_btn = wx.Button(dlg, wx.ID_CLOSE, label=_("&Close"))
        close_btn.Bind(wx.EVT_BUTTON, lambda _e: dlg.EndModal(wx.ID_CLOSE))
        sizer.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        dlg.SetSizerAndFit(sizer)
        dlg.CentreOnParent()
        dlg.ShowModal()
        dlg.Destroy()

    def _on_navigating(self, event) -> None:
        if self._closing:
            event.Veto()
            return
        url = (event.GetURL() or "").strip()
        preview_index = preview_index_from_url(url)
        if preview_index is not None:
            event.Veto()
            wx.CallAfter(self._show_full_preview_index, preview_index)
            return
        preview_name = preview_filename_from_url(url)
        if preview_name:
            event.Veto()
            wx.CallAfter(self._show_full_preview, preview_name)
            return
        action = self._main._webview_host_action(url)
        if action == "close":
            event.Veto()
            wx.CallAfter(self._on_close_dialog, None)
            return
        if action in ("page_prev", "page_next"):
            event.Veto()
            wx.CallAfter(
                self._cycle_notebook_page, -1 if action == "page_prev" else 1
            )
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
        # file:// (the report and its images) and about:blank must load.
        event.Skip()

    def _leave_webview(self, forward: bool) -> None:
        try_focus = self._main._try_set_focus
        if forward:
            if try_focus(self.open_browser_btn):
                return
            try_focus(self._close_btn)
            return
        if try_focus(self._notebook):
            return
        try_focus(self.open_browser_btn)

    def _release_webview(self) -> None:
        """Unbind Edge events; do not Stop/Hide the control (that blanks or crashes)."""
        view = self._output
        if view is None or not self._output_is_webview:
            return
        try:
            import wx.html2 as html2

            view.Unbind(html2.EVT_WEBVIEW_LOADED)
            view.Unbind(html2.EVT_WEBVIEW_NAVIGATING)
        except Exception:
            pass

    def _cleanup_view_copy(self) -> None:
        view_html = getattr(self, "_view_html", None)
        html_path = getattr(self, "_html_path", None)
        if view_html is None or view_html == html_path:
            return
        try:
            view_html.unlink(missing_ok=True)
        except OSError:
            pass

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
