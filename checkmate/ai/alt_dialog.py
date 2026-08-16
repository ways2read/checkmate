"""wx dialog for alt-text assessment results."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid
import webbrowser
from pathlib import Path

import wx
import wx.lib.newevent

from ..i18n import _
from .alt_assess import AltAssessResult, ask_alt_assess_followup
from .alt_labels import FEATURE_FILENAME_STEM, feature_html_basenames, feature_title
from .alt_report import assessment_markdown_export, build_assessment_html
from .alt_sample import assess_more_choice_labels
from .explain import ExplainResult, error_message_for_key
from .litellm_client import ai_libraries_status_message
from .markdown_html import append_followup_markdown

logger = logging.getLogger(__name__)

AltFollowupEvent, EVT_ALT_FOLLOWUP = wx.lib.newevent.NewEvent()


def _unlink_quietly(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass

_SCROLL_FOLLOWUP_JS = """
(function () {
  var el = document.getElementById('cm-latest-followup');
  if (!el) return false;
  el.scrollIntoView({behavior: 'smooth', block: 'center'});
  // Do not el.focus() — moving keyboard focus into Edge WebView2 after
  // SetPage can leave the host dialog/main frame unable to quit on Windows.
  return true;
})();
"""


def _pulse_progress(dlg: wx.ProgressDialog, message: str | None = None) -> bool:
    """Delegate to main so status changes are spoken to screen readers."""
    from .. import main as main_mod

    return main_mod._pulse_progress(dlg, message)


def _present_progress(dlg: wx.ProgressDialog, message: str) -> None:
    from .. import main as main_mod

    main_mod._present_progress_dialog(dlg, message)


def _progress_body(message: str) -> str:
    from .. import main as main_mod

    return main_mod._alt_assess_progress_body(message)


def _clear_progress(dlg: wx.ProgressDialog | None) -> None:
    from .. import main as main_mod

    main_mod._clear_progress_announce(dlg)

class AltAssessDialog(wx.Dialog):
    """Show alt-text assessment HTML with follow-up and export actions."""

    def __init__(self, parent: wx.Window, *, result: AltAssessResult) -> None:
        super().__init__(
            parent,
            title=feature_title(),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.SetSize((820, 700))
        self._main = parent
        self._result = result
        self._session = result.session
        self._synthesis_md = result.text or ""
        self._busy = False
        self._closing = False
        self._scroll_followup_after_load = False
        self._paint_gen = 0
        self._ai_cancel: threading.Event | None = None
        self._ai_progress: wx.ProgressDialog | None = None
        self._ai_progress_timer: wx.Timer | None = None
        self._is_webview = False
        self._close_btn: wx.Button | None = None

        root = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(self, label=feature_title())
        font = heading.GetFont()
        if font.IsOk():
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            heading.SetFont(font)
        root.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self._host = wx.Panel(self, name=feature_title())
        self._host.SetMinSize((-1, 400))
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        loading = wx.StaticText(self._host, label=_("Loading AI view…"))
        host_sizer.Add(loading, 0, wx.ALL, 8)
        self._host.SetSizer(host_sizer)
        root.Add(self._host, 1, wx.EXPAND | wx.ALL, 12)

        follow = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(self, label=_("Ask a follow-up question…"))
        follow.Add(label, 0, wx.BOTTOM, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.followup_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.ask_btn = wx.Button(self, label=_("Ask"))
        self.ask_btn.Enable(self._session is not None)
        row.Add(self.followup_ctrl, 1, wx.RIGHT, 6)
        row.Add(self.ask_btn, 0)
        follow.Add(row, 0, wx.EXPAND)
        root.Add(follow, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        actions = wx.BoxSizer(wx.HORIZONTAL)
        self.assess_more_btn = wx.Button(self, label=_("Assess more…"))
        self.view_browser_btn = wx.Button(self, label=_("View in browser"))
        self.save_html_btn = wx.Button(self, label=_("Save as HTML…"))
        self.save_md_btn = wx.Button(self, label=_("Save as Markdown…"))
        self.copy_btn = wx.Button(self, label=_("Copy to clipboard"))
        for btn in (
            self.assess_more_btn,
            self.view_browser_btn,
            self.save_html_btn,
            self.save_md_btn,
            self.copy_btn,
        ):
            actions.Add(btn, 0, wx.RIGHT, 6)
        root.Add(actions, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        footer.AddStretchSpacer(1)
        close_btn = wx.Button(self, id=wx.ID_CLOSE, label=_("Close"))
        self._close_btn = close_btn
        close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        footer.Add(close_btn, 0)
        root.Add(footer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.ask_btn.Bind(wx.EVT_BUTTON, self._on_ask)
        self.followup_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_ask)
        self.assess_more_btn.Bind(wx.EVT_BUTTON, self._on_assess_more)
        self.view_browser_btn.Bind(wx.EVT_BUTTON, self._on_view_browser)
        self.save_html_btn.Bind(wx.EVT_BUTTON, self._on_save_html)
        self.save_md_btn.Bind(wx.EVT_BUTTON, self._on_save_md)
        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy)
        self.Bind(EVT_ALT_FOLLOWUP, self._on_followup_event)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_window_destroy)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.SetSizer(root)
        self.CentreOnParent()
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)
        close_btn.SetDefault()
        self._sync_assess_more_enabled()

        self._view: wx.Window | None = None
        self._html_tmp: Path | None = None
        self._html_tmp_prev: list[Path] = []
        self._load_retries = 0
        self._webview_replaced = False
        self._pending_later: list[wx.CallLater] = []
        # Create the WebView after ShowModal so Edge is not a blank HWND.
        wx.CallAfter(self._realize_view)
        # Keep Enter-in-follow-up working without making Ask the dialog default
        # (Ask-as-default + WebView focus after follow-up can strand the frame).
        self.followup_ctrl.Bind(wx.EVT_SET_FOCUS, self._on_followup_focus)
        self.followup_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_followup_kill_focus)

    def apply_result(self, result: AltAssessResult) -> None:
        """Replace the displayed assessment (used after Assess more…)."""
        self._result = result
        self._session = result.session
        self._synthesis_md = result.text or ""
        self._sync_assess_more_enabled()
        self._set_busy(False)
        self._paint(scroll_followup=False)

    def _remaining_count(self) -> int:
        export = self._result.export
        if export is None:
            return 0
        return max(0, export.total - len(self._result.assessments))

    def _sync_assess_more_enabled(self) -> None:
        self.assess_more_btn.Enable(
            (not self._busy) and self._remaining_count() > 0
        )

    def _on_followup_focus(self, event: wx.FocusEvent) -> None:
        event.Skip()
        try:
            self.ask_btn.SetDefault()
        except RuntimeError:
            pass

    def _on_followup_kill_focus(self, event: wx.FocusEvent) -> None:
        event.Skip()
        btn = self._close_btn
        if btn is None:
            return
        try:
            btn.SetDefault()
        except RuntimeError:
            pass

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            wx.CallAfter(self._on_close_dialog, None)
            return
        event.Skip()

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

    def _alive(self) -> bool:
        if self._closing:
            return False
        try:
            return bool(self)
        except RuntimeError:
            return False

    def _current_html(self) -> str:
        self._result.text = self._synthesis_md
        return build_assessment_html(self._result, for_dialog=True)

    def _realize_view(self) -> None:
        if self._closing or self._view is not None:
            return
        host = self._host
        sizer = host.GetSizer()
        view: wx.Window
        is_webview = False
        try:
            import wx.html2 as html2

            view = html2.WebView.New(host)
            is_webview = True
            try:
                from ..ui_appearance import apply_webview_appearance

                apply_webview_appearance(view)
            except Exception:
                pass
            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_navigating)
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_webview_loaded)
        except Exception:
            view = wx.TextCtrl(
                host,
                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.BORDER_NONE,
            )
        if sizer is not None:
            sizer.Clear(delete_windows=True)
            sizer.Add(view, 1, wx.EXPAND)
        self._view = view
        self._is_webview = is_webview
        host.Layout()
        self.Layout()
        # WebView2 is not ready for LoadURL during construction.
        wx.CallAfter(self._paint)
        if is_webview:
            self._call_later(400, self._reload_webview_if_needed)

    def _on_navigating(self, event) -> None:
        if self._closing:
            event.Veto()
            return
        url = (event.GetURL() or "").strip()
        if url.startswith("checkmate://"):
            event.Veto()
            if "close" in url:
                wx.CallAfter(self._on_close_dialog, None)
            return
        if url.startswith(("http://", "https://", "mailto:")):
            event.Veto()
            try:
                webbrowser.open(url)
            except OSError:
                pass
            return
        # file:// (temp report) and about:blank must load.
        event.Skip()

    def _on_webview_loaded(self, event) -> None:
        event.Skip()
        if self._closing:
            return
        if self._view is not None:
            try:
                from ..ui_appearance import apply_webview_appearance

                apply_webview_appearance(self._view)
            except Exception:
                pass
        url = (event.GetURL() or "").strip().lower()
        if self._html_tmp is not None and (
            not url or url == "about:blank" or url.startswith("about:")
        ):
            self._reload_webview_if_needed()
            return
        if self._scroll_followup_after_load and self._is_webview and self._view:
            self._scroll_followup_after_load = False
            try:
                self._view.RunScript(_SCROLL_FOLLOWUP_JS)
            except Exception:
                logger.debug("Could not scroll to follow-up", exc_info=True)
            try:
                self.followup_ctrl.SetFocus()
            except RuntimeError:
                pass

    def _load_html_in_webview(self, html_doc: str) -> None:
        """Load report HTML via file://.

        Edge WebView2 ``SetPage`` / NavigateToString silently shows a blank
        document once the HTML (embedded image data-URIs) exceeds ~1–2 MB.
        Loading a temp file matches “View in browser”, which already works.
        """
        view = self._view
        if view is None:
            return
        from .alt_inventory_dialog import (
            cleanup_view_html,
            webview_file_uri,
            write_unique_view_html,
        )

        export = self._result.export
        try:
            if export is not None and export.folder:
                folder = Path(export.folder)
                canonical = folder / f"{FEATURE_FILENAME_STEM}.html"
                canonical.write_text(html_doc, encoding="utf-8")
                for leftover in feature_html_basenames() - {canonical.name}:
                    _unlink_quietly(folder / leftover)
                cleanup_view_html(folder)
                path = write_unique_view_html(canonical)
            else:
                tmp_dir = Path(tempfile.gettempdir()) / "checkmate_alt_assess"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                path = tmp_dir / f"assess_{os.getpid()}_{uuid.uuid4().hex}.html"
                path.write_text(html_doc, encoding="utf-8")
        except OSError:
            logger.exception("Could not write inspector HTML for WebView")
            try:
                view.SetPage(html_doc, "about:blank")
            except Exception:
                logger.exception("SetPage fallback failed")
            return
        prev = self._html_tmp
        self._html_tmp = path
        self._load_retries = 0
        if prev is not None and prev != path and not prev.name.startswith(".cm_view_"):
            self._html_tmp_prev.append(prev)
        elif prev is not None and prev != path:
            _unlink_quietly(prev)
        uri = webview_file_uri(path)
        logger.debug(
            "Inspector WebView LoadURL %s (%s bytes)", uri, path.stat().st_size
        )
        try:
            view.LoadURL(uri)
            return
        except Exception:
            logger.exception("LoadURL failed for inspector HTML")
        try:
            view.SetPage(html_doc, uri)
        except Exception:
            logger.exception("SetPage fallback failed for inspector HTML")

    def _reload_webview_if_needed(self) -> None:
        """If Edge loaded about:blank instead of the report, try the file again."""
        if not self._alive() or not self._is_webview or self._view is None:
            return
        if self._html_tmp is None:
            return
        view = self._view
        try:
            current = (view.GetCurrentURL() or "").strip().lower()
        except Exception:
            current = ""
        expected = self._html_tmp.name.lower()
        if expected and expected in current.replace("\\", "/"):
            return
        if not current:
            return
        if not (current == "about:blank" or current.startswith("about:")):
            return
        if self._load_retries >= 2:
            self._replace_webview()
            return
        self._load_retries += 1
        try:
            view.LoadURL(self._html_tmp.resolve().as_uri())
        except Exception:
            logger.debug("Inspector WebView reload failed", exc_info=True)

    def _replace_webview(self) -> None:
        """Recreate the Edge control once if reloads still show a blank document."""
        if self._webview_replaced or not self._alive():
            return
        self._webview_replaced = True
        from .alt_inventory_dialog import _safe_destroy_window

        old = self._view
        self._release_webview()
        host = self._host
        sizer = host.GetSizer()
        view: wx.Window
        is_webview = False
        try:
            import wx.html2 as html2

            view = html2.WebView.New(host)
            is_webview = True
            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_navigating)
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_webview_loaded)
        except Exception:
            view = wx.TextCtrl(
                host,
                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.BORDER_NONE,
            )
        self._view = view
        self._is_webview = is_webview
        self._load_retries = 0
        if sizer is not None:
            if old is not None:
                try:
                    sizer.Detach(old)
                except Exception:
                    pass
            sizer.Add(view, 1, wx.EXPAND)
        host.Layout()
        self.Layout()
        if old is not None and old is not view:
            self._call_later(500, lambda: _safe_destroy_window(old))
        wx.CallAfter(self._paint)
        if is_webview:
            self._call_later(400, self._reload_webview_if_needed)

    def _paint(self, *, scroll_followup: bool = False) -> None:
        if not self._alive() or self._view is None:
            return
        self._paint_gen += 1
        self._scroll_followup_after_load = bool(scroll_followup and self._is_webview)
        html_doc = self._current_html()
        if self._is_webview:
            self._load_html_in_webview(html_doc)
        else:
            setter = getattr(self._view, "ChangeValue", None) or getattr(
                self._view, "SetValue", None
            )
            if setter is not None:
                setter(self._synthesis_md)
            if scroll_followup:
                try:
                    self._view.ShowPosition(len(self._view.GetValue() or ""))
                except RuntimeError:
                    pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        ok = (not busy) and self._session is not None
        self.ask_btn.Enable(ok)
        self.followup_ctrl.Enable(ok)
        self._sync_assess_more_enabled()

    def _close_progress(self) -> None:
        if self._ai_progress_timer is not None:
            try:
                self._ai_progress_timer.Stop()
            except RuntimeError:
                pass
            self._ai_progress_timer = None
        if self._ai_progress is not None:
            dlg = self._ai_progress
            _clear_progress(dlg)
            try:
                dlg.Destroy()
            except RuntimeError:
                pass
            self._ai_progress = None
        self._ai_cancel = None

    def _on_progress_timer(self, _event: wx.TimerEvent) -> None:
        dlg = self._ai_progress
        cancel = self._ai_cancel
        if dlg is None or cancel is None:
            return
        if not _pulse_progress(dlg):
            cancel.set()
            _pulse_progress(dlg, _("Cancelling…"))
            self._close_progress()

    def _on_ask(self, _event: wx.Event) -> None:
        if self._busy or self._session is None:
            return
        question = self.followup_ctrl.GetValue().strip()
        if not question:
            return
        cancel = threading.Event()
        self._ai_cancel = cancel
        self._ai_progress = wx.ProgressDialog(
            feature_title(),
            _("Thinking…"),
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT,
        )
        _present_progress(self._ai_progress, _("Thinking…"))
        self._ai_progress_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_progress_timer, self._ai_progress_timer)
        self._ai_progress_timer.Start(200)
        self._set_busy(True)
        session = self._session

        def work() -> None:
            try:
                out = ask_alt_assess_followup(
                    session, question, cancel_event=cancel
                )
            except Exception as exc:
                out = ExplainResult(
                    ok=False,
                    error_key="provider_error",
                    text=str(exc),
                    session=session,
                )
            if cancel.is_set() and not (out.ok and (out.text or "").strip()):
                wx.CallAfter(self._close_progress)
                wx.CallAfter(self._set_busy, False)
                return
            try:
                wx.PostEvent(
                    self,
                    AltFollowupEvent(result=out, question=question),
                )
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_followup_event(self, event: AltFollowupEvent) -> None:
        if self._closing:
            return
        self._close_progress()
        out = event.result
        if not out.ok:
            if out.error_key != "cancelled":
                wx.MessageBox(
                    error_message_for_key(out.error_key, detail=out.text or ""),
                    feature_title(),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
            if out.session is not None:
                self._session = out.session
            self._set_busy(False)
            return
        self._session = out.session
        self._synthesis_md = append_followup_markdown(
            self._synthesis_md,
            heading=_("Follow-up"),
            question=getattr(event, "question", "") or "",
            answer=out.text or "",
        )
        self._paint(scroll_followup=True)
        self.followup_ctrl.SetValue("")
        self._set_busy(False)
        try:
            self.followup_ctrl.SetFocus()
        except RuntimeError:
            pass
        try:
            from ..telemetry import log_ai_alt_assess

            log_ai_alt_assess(followup=True)
        except Exception:
            pass

    def _on_assess_more(self, _event: wx.Event) -> None:
        if self._busy or self._result.export is None:
            return
        total = self._result.export.total
        already = len(self._result.assessments)
        rows = assess_more_choice_labels(total, already)
        if not rows:
            wx.MessageBox(
                _("All images in this export have already been assessed."),
                feature_title(),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if len(rows) == 1:
            _label, mode, percent = rows[0]
            percent = percent if percent is not None else 100
        else:
            choices = [label for label, _mode, _pct in rows]
            dlg = wx.SingleChoiceDialog(
                self,
                _(
                    "Already assessed: {already} of {total}.\n"
                    "Choose additional coverage (previous results are kept):"
                ).format(already=already, total=total),
                _("Assess more"),
                choices,
            )
            try:
                if dlg.ShowModal() != wx.ID_OK:
                    return
                _label, mode, percent = rows[dlg.GetSelection()]
                percent = percent if percent is not None else 100
            finally:
                dlg.Destroy()

        folder = self._result.export.folder
        prior = self._result
        cancel = threading.Event()
        self._ai_cancel = cancel
        start_msg = _progress_body(ai_libraries_status_message())
        self._ai_progress = wx.ProgressDialog(
            feature_title(),
            start_msg,
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT,
        )
        _present_progress(self._ai_progress, start_msg)
        self._ai_progress_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_progress_timer, self._ai_progress_timer)
        self._ai_progress_timer.Start(200)
        self._set_busy(True)

        def status(message: str) -> None:
            def update() -> None:
                if self._ai_progress is not None:
                    text = _progress_body(
                        _("Cancelling…")
                        if self._ai_cancel is not None and self._ai_cancel.is_set()
                        else message
                    )
                    cont = _pulse_progress(self._ai_progress, text)
                    if not cont and self._ai_cancel is not None:
                        self._ai_cancel.set()

            wx.CallAfter(update)

        def work() -> None:
            from .alt_assess import AltAssessResult, assess_alt_export
            from .litellm_client import preload_litellm

            ok, detail = preload_litellm()
            if not ok:
                out = AltAssessResult(
                    ok=False, error_key="no_litellm", detail=detail or ""
                )
            elif cancel.is_set():
                out = AltAssessResult(ok=False, error_key="cancelled")
            else:
                try:
                    out = assess_alt_export(
                        folder,
                        mode=mode,
                        percent=percent,
                        prior=prior,
                        cancel_event=cancel,
                        status_callback=status,
                    )
                except Exception as exc:
                    out = AltAssessResult(
                        ok=False, error_key="provider_error", detail=str(exc)
                    )

            def done() -> None:
                self._close_progress()
                if not out.ok:
                    if out.error_key != "cancelled":
                        wx.MessageBox(
                            error_message_for_key(
                                out.error_key, detail=out.detail or out.text or ""
                            ),
                            feature_title(),
                            wx.OK | wx.ICON_ERROR,
                            self,
                        )
                    self._set_busy(False)
                    return
                self.apply_result(out)
                try:
                    from ..telemetry import log_ai_alt_assess

                    log_ai_alt_assess()
                except Exception:
                    pass

            wx.CallAfter(done)

        threading.Thread(target=work, daemon=True).start()

    def _on_view_browser(self, _event: wx.Event) -> None:
        try:
            fd, name = tempfile.mkstemp(
                prefix="checkmate-alt-assess-", suffix=".html", text=True
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(build_assessment_html(self._result, for_dialog=False))
            webbrowser.open(Path(name).as_uri())
        except OSError as exc:
            wx.MessageBox(
                _("Could not open the explanation in a browser:\n{error}").format(
                    error=exc
                ),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_save_html(self, _event: wx.Event) -> None:
        # Nested FileDialog + Edge WebView can leave focus inside the browser
        # HWND; reclaim wx chrome before/after so Close/EndModal stays responsive.
        self._focus_dialog_chrome()
        with wx.FileDialog(
            self,
            _("Save as HTML"),
            defaultFile=f"{FEATURE_FILENAME_STEM}.html",
            wildcard=_("HTML files (*.html)|*.html|All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                self._focus_dialog_chrome()
                return
            path = Path(dlg.GetPath())
            if not path.suffix:
                path = path.with_suffix(".html")
            try:
                path.write_text(
                    build_assessment_html(self._result, for_dialog=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                wx.MessageBox(
                    _("Could not save the file:\n{error}").format(error=exc),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
        self._focus_dialog_chrome()

    def _on_save_md(self, _event: wx.Event) -> None:
        self._focus_dialog_chrome()
        with wx.FileDialog(
            self,
            _("Save as Markdown"),
            defaultFile=f"{FEATURE_FILENAME_STEM}.md",
            wildcard=_("Markdown files (*.md)|*.md|All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                self._focus_dialog_chrome()
                return
            path = Path(dlg.GetPath())
            if not path.suffix:
                path = path.with_suffix(".md")
            try:
                path.write_text(
                    assessment_markdown_export(self._result), encoding="utf-8"
                )
            except OSError as exc:
                wx.MessageBox(
                    _("Could not save the file:\n{error}").format(error=exc),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
        self._focus_dialog_chrome()

    def _on_copy(self, _event: wx.Event) -> None:
        text = assessment_markdown_export(self._result)
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()

    def _focus_dialog_chrome(self) -> None:
        """Move keyboard focus onto a wx control (never Edge WebView2)."""
        try:
            if self._close_btn is not None:
                self._close_btn.SetFocus()
            else:
                self.SetFocus()
        except RuntimeError:
            pass

    def _on_close_dialog(self, event: wx.Event | None = None) -> None:
        if self._closing:
            if isinstance(event, wx.CloseEvent):
                # ShowModal already returned; allow the caller's Destroy().
                event.Skip()
            return
        self._closing = True
        self._paint_gen += 1
        self._scroll_followup_after_load = False
        self._stop_pending_later()
        if self._ai_cancel is not None:
            self._ai_cancel.set()
        self._close_progress()
        self._release_webview()
        if isinstance(event, wx.CloseEvent):
            event.Veto()
        self._finish_close_dialog()

    def _on_close(self, event: wx.Event | None = None) -> None:
        self._on_close_dialog(event)

    def _release_webview(self) -> None:
        """Unbind Edge events; do not Stop/Hide the control (that blanks or crashes)."""
        self._stop_pending_later()
        self._focus_dialog_chrome()
        view = self._view
        if view is None or not self._is_webview:
            return
        try:
            import wx.html2 as html2

            view.Unbind(html2.EVT_WEBVIEW_LOADED)
            view.Unbind(html2.EVT_WEBVIEW_NAVIGATING)
        except Exception:
            pass

    def _cleanup_html_tmp(self) -> None:
        for leftover in list(self._html_tmp_prev):
            _unlink_quietly(leftover)
        self._html_tmp_prev.clear()
        # Keep the copy written beside the export (relative images/ resolve).
        if self._html_tmp is None or self._html_tmp.name not in feature_html_basenames():
            _unlink_quietly(self._html_tmp)
        self._html_tmp = None

    def _on_window_destroy(self, event: wx.WindowDestroyEvent) -> None:
        event.Skip()
        if event.GetEventObject() is not self:
            return
        self._cleanup_html_tmp()

    def _finish_close_dialog(self) -> None:
        try:
            if not self:
                return
        except RuntimeError:
            return
        self._focus_dialog_chrome()
        try:
            if self.IsModal():
                self.EndModal(wx.ID_CLOSE)
        except RuntimeError:
            pass
