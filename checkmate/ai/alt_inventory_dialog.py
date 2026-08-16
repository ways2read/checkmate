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
from .alt_labels import feature_run_button_label

logger = logging.getLogger(__name__)

# Returned from ShowModal when the user wants the AI health check.
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
        cleanup_view_html(self.folder)
        self.exit_code = int(wx.ID_CANCEL)
        self._view_realized = False
        self._output_is_webview = False
        self._closing = False
        self._load_gen = 0
        self._output: wx.Window | None = None
        self._pending_later: list[wx.CallLater] = []
        self._dialog_html_cache: str | None = None

        from .. import main as main_mod

        self._main = main_mod

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
        root.Add(self._path_label, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

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
        self.ai_health_btn = wx.Button(self, label=feature_run_button_label())
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
        self._close_btn = wx.Button(self, wx.ID_ANY, label=_("&Close"))

        if ai_features_enabled():
            btns.Add(self.ai_health_btn, 0, wx.RIGHT, 8)
        else:
            self.ai_health_btn.Hide()
        btns.Add(self.open_browser_btn, 0, wx.RIGHT, 8)
        btns.Add(self.open_folder_btn, 0, wx.RIGHT, 8)
        btns.AddStretchSpacer(1)
        btns.Add(self._close_btn, 0)
        root.Add(btns, 0, wx.EXPAND | wx.ALL, 12)

        self.ai_health_btn.Bind(wx.EVT_BUTTON, self._on_ai_health)
        self.open_browser_btn.Bind(wx.EVT_BUTTON, self._on_open_browser)
        self.open_folder_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        self._close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.SetSizer(root)
        self.CentreOnParent()
        # Escape is handled by CHAR_HOOK / in-page JS. Stock ID_CLOSE as the
        # escape id leaves a queued EndModal that instantly closes the next open.
        self.SetEscapeId(wx.ID_NONE)
        # Do not make Close the affirmative id: EndModal(ID_RUN_AI_HEALTH)
        # plus a later CloseEvent would otherwise become wx.ID_CLOSE.
        self.SetAffirmativeId(wx.ID_NONE)
        self._close_btn.SetDefault()

        # Do not CallAfter(_realize_view) from __init__: Destroy() of a previous
        # WebView can pump that callback before ShowModal, and Edge then paints
        # a blank HWND. Create the control only after this dialog is shown.
        self.Bind(wx.EVT_SHOW, self._on_show)

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

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            gen = self._load_gen
            wx.CallAfter(self._close_if_gen, gen)
            return
        event.Skip()

    def _close_if_gen(self, gen: int) -> None:
        if not self._same_gen(gen):
            return
        self._on_close_dialog(None)

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
        if forward:
            if (
                ai_features_enabled()
                and self.ai_health_btn.IsShown()
                and self._main._try_set_focus(self.ai_health_btn)
            ):
                return
            if self._main._try_set_focus(self.open_browser_btn):
                return
            self._main._try_set_focus(self._close_btn)
            return
        if ai_features_enabled() and self.ai_health_btn.IsShown():
            self._main._try_set_focus(self.ai_health_btn)
        else:
            self._main._try_set_focus(self.open_browser_btn)

    def _release_webview(self) -> None:
        """Unbind Edge events; do not Stop/Hide the control (that blanks or crashes)."""
        self._stop_pending_later()
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

    def _on_ai_health(self, _event: wx.Event) -> None:
        # Mark closed before EndModal so Destroy()'s EVT_CLOSE cannot schedule
        # a second EndModal(wx.ID_CLOSE) and drop ID_RUN_AI_HEALTH.
        if self._closing:
            return
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        self._closing = True
        self._stop_pending_later()
        self._cleanup_view_copy()
        self.exit_code = int(ID_RUN_AI_HEALTH)
        if self.IsModal():
            self.EndModal(self.exit_code)

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

    def _on_close_dialog(self, event: wx.Event | None = None) -> None:
        # Avoid tearing down WebView2 inside navigating/key handlers
        # (SetEscapeId + CHAR_HOOK + checkmate://close can all fire).
        if self._closing:
            if isinstance(event, wx.CloseEvent):
                if self.IsModal():
                    # Still inside ShowModal after EndModal(ID_RUN_AI_HEALTH).
                    # Skip() lets wx EndModal(ID_CLOSE) and drops the inspector.
                    event.Veto()
                else:
                    # ShowModal already returned; allow the caller's Destroy().
                    event.Skip()
            return
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        self._closing = True
        self._stop_pending_later()
        self._cleanup_view_copy()
        if isinstance(event, wx.CloseEvent):
            event.Veto()
        self._finish_close_dialog()

    # Compatibility for callers / host Escape wiring.
    def _on_close(self, event: wx.Event | None = None) -> None:
        self._on_close_dialog(event)

    def _finish_close_dialog(self) -> None:
        try:
            if not self:
                return
        except RuntimeError:
            return
        try:
            # Only EndModal while the dialog loop is still running. Never
            # Destroy here — the ShowModal caller owns teardown.
            if self.IsModal():
                self.exit_code = int(wx.ID_CLOSE)
                self.EndModal(self.exit_code)
        except RuntimeError:
            pass
