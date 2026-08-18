"""wx dialog for alt-text assessment results."""

from __future__ import annotations

import logging
import os
import sys
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
from .markdown_html import (
    _WEBVIEW_SCROLL_LATEST_FOLLOWUP_JS,
    append_followup_markdown,
    followup_markdown_suffix,
    merge_followup_suffix,
)

logger = logging.getLogger(__name__)

AltFollowupEvent, EVT_ALT_FOLLOWUP = wx.lib.newevent.NewEvent()


def _unlink_quietly(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def webview_url_matches_html(url: str, html_path: Path | None) -> bool:
    """True when *url* is the current inspector HTML (not a stale copy)."""
    if html_path is None:
        return False
    name = html_path.name.lower()
    return bool(name) and name in (url or "").replace("\\", "/").lower()


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

class AltSniffTestMixin:
    """Sniff-test tab: its own WebView, follow-up, and Assess more controls.

    Mixed into ``AltTextReportDialog`` so the inventory report and sniff test
    stay in one modal (same pattern as issue details: separate hosts, show/hide).
    """

    def _init_sniff_state(self) -> None:
        self._sniff_result: AltAssessResult | None = None
        self._sniff_session = None
        self._sniff_synthesis_md = ""
        self._sniff_busy = False
        self._sniff_scroll_followup = False
        self._sniff_paint_gen = 0
        self._ai_cancel: threading.Event | None = None
        self._ai_progress: wx.ProgressDialog | None = None
        self._ai_progress_timer: wx.Timer | None = None
        self._sniff_is_webview = False
        self._sniff_view: wx.Window | None = None
        self._sniff_view_realized = False
        self._sniff_html_tmp: Path | None = None
        self._sniff_html_tmp_prev: list[Path] = []
        self._sniff_load_retries = 0
        self._sniff_webview_replaced = False
        self._sniff_host: wx.Window | None = None
        self.followup_ctrl: wx.TextCtrl | None = None
        self.ask_btn: wx.Button | None = None
        self.assess_more_btn: wx.Button | None = None
        self.sniff_run_btn: wx.Button | None = None
        self.view_browser_btn: wx.Button | None = None
        self.Bind(EVT_ALT_FOLLOWUP, self._on_followup_event)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_window_destroy)

    def _reset_sniff_state(self) -> None:
        self._sniff_result = None
        self._sniff_session = None
        self._sniff_synthesis_md = ""
        self._sniff_busy = False
        self._sniff_scroll_followup = False
        self._sniff_paint_gen = int(getattr(self, "_sniff_paint_gen", 0)) + 1
        self._sniff_html_tmp_prev = list(getattr(self, "_sniff_html_tmp_prev", []))
        if getattr(self, "_sniff_html_tmp", None) is not None:
            self._sniff_html_tmp_prev.append(self._sniff_html_tmp)
        self._sniff_html_tmp = None
        self._sync_assess_more_enabled()
        self._set_sniff_busy(False)
        if getattr(self, "_sniff_view_realized", False) and self._sniff_view is not None:
            self._sniff_show_idle()

    def apply_sniff_result(self, result: AltAssessResult) -> None:
        """Show a completed assessment on the sniff tab (keeps prior follow-ups)."""
        suffix = followup_markdown_suffix(self._sniff_synthesis_md)
        self._sniff_result = result
        self._sniff_session = result.session
        self._sniff_synthesis_md = merge_followup_suffix(result.text or "", suffix)
        self._set_sniff_busy(False)
        self._select_page(self._PAGE_SNIFF)
        self._realize_sniff_view()
        self._sniff_paint(scroll_followup=False)
        self._sync_assess_more_enabled()

    def _remaining_count(self) -> int:
        result = self._sniff_result
        if result is None or result.export is None:
            return 0
        return max(0, result.export.total - len(result.assessments))

    def _sync_assess_more_enabled(self) -> None:
        btn = getattr(self, "assess_more_btn", None)
        if btn is None:
            return
        try:
            btn.Enable((not self._sniff_busy) and self._remaining_count() > 0)
        except RuntimeError:
            pass

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
        if event.ControlDown() and event.GetKeyCode() in (
            wx.WXK_PAGEUP,
            wx.WXK_PAGEDOWN,
        ):
            delta = -1 if event.GetKeyCode() == wx.WXK_PAGEUP else 1
            self._cycle_notebook_page(delta)
            return
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

    def _main_mod(self):
        from .. import main as main_mod

        return main_mod

    def _sniff_idle_html(self) -> str:
        from .markdown_html import markdown_to_browser_page

        md = (
            f"## {feature_title()}\n\n"
            + _(
                "Run the AI Image Sniff Test to sample images and assess "
                "decorative status and alt quality."
            )
            + "\n"
        )
        return markdown_to_browser_page(
            md, title=feature_title(), tab_exit=True
        )

    def _sniff_show_idle(self) -> None:
        view = self._sniff_view
        if view is None:
            return
        html_doc = self._sniff_idle_html()
        if self._sniff_is_webview:
            try:
                view.SetPage(html_doc, "")
            except Exception:
                logger.debug("Could not show sniff-test placeholder", exc_info=True)
        else:
            setter = getattr(view, "ChangeValue", None) or getattr(view, "SetValue", None)
            if setter is not None:
                setter(
                    _(
                        "Run the AI Image Sniff Test to sample images and assess "
                        "decorative status and alt quality."
                    )
                )

    def _sniff_paint_or_idle(self) -> None:
        if self._sniff_result is not None and (self._sniff_synthesis_md or "").strip():
            self._sniff_paint(scroll_followup=False)
        else:
            self._sniff_show_idle()

    def _sniff_current_html(self, *, scroll_followup: bool = False) -> str:
        result = self._sniff_result
        if result is None:
            return self._sniff_idle_html()
        result.text = self._sniff_synthesis_md
        return build_assessment_html(
            result, for_dialog=True, scroll_followup=scroll_followup
        )

    def _realize_sniff_view(self) -> None:
        if self._closing or self._sniff_view is not None:
            return
        host = self._sniff_host
        if host is None:
            return
        main_mod = self._main_mod()
        view, is_webview = main_mod._create_ai_html_view(
            host, name=feature_title()
        )
        view.SetMinSize((-1, 280))
        if is_webview:
            import wx.html2 as html2

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_sniff_navigating)
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_sniff_webview_loaded)
        sizer = host.GetSizer()
        if sizer is None:
            sizer = wx.BoxSizer(wx.VERTICAL)
            host.SetSizer(sizer)
        else:
            sizer.Clear(delete_windows=True)
        sizer.Add(view, 1, wx.EXPAND)
        self._sniff_view = view
        self._sniff_is_webview = is_webview
        self._sniff_view_realized = True
        main_mod._wire_ai_html_host(host, view, is_webview=is_webview)
        host.Layout()
        self.Layout()
        wx.CallAfter(self._sniff_paint_or_idle)
        if is_webview:
            self._call_later(400, self._sniff_reload_if_needed)

    def _on_sniff_navigating(self, event) -> None:
        if self._closing:
            event.Veto()
            return
        url = (event.GetURL() or "").strip()
        action = self._main_mod()._webview_host_action(url)
        if action == "close":
            event.Veto()
            wx.CallAfter(self._on_close_dialog, None)
            return
        if action in ("next", "prev"):
            event.Veto()
            wx.CallAfter(self._leave_sniff_webview, action == "next")
            return
        if action in ("page_prev", "page_next"):
            event.Veto()
            wx.CallAfter(
                self._cycle_notebook_page, -1 if action == "page_prev" else 1
            )
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

    def _leave_sniff_webview(self, forward: bool) -> None:
        try_focus = self._main_mod()._try_set_focus
        if forward:
            for ctrl in (
                self.followup_ctrl,
                self.ask_btn,
                self.assess_more_btn,
                getattr(self, "view_browser_btn", None),
                self._close_btn,
            ):
                if try_focus(ctrl):
                    return
            return
        if try_focus(getattr(self, "sniff_run_btn", None)):
            return
        notebook = getattr(self, "_notebook", None)
        if try_focus(notebook):
            return
        try_focus(self._close_btn)

    def _on_sniff_webview_loaded(self, event) -> None:
        event.Skip()
        if self._closing:
            return
        if self._sniff_view is not None:
            try:
                from ..ui_appearance import apply_webview_appearance

                apply_webview_appearance(self._sniff_view)
            except Exception:
                pass
        url = (event.GetURL() or "").strip()
        if not webview_url_matches_html(url, self._sniff_html_tmp):
            if url:
                self._sniff_reload_if_needed()
            return
        self._cleanup_stale_view_html()
        if self._sniff_scroll_followup:
            self._schedule_scroll_followup()

    def _sniff_load_html(self, html_doc: str) -> None:
        """Load report HTML via file://.

        Edge WebView2 ``SetPage`` / NavigateToString silently shows a blank
        document once the HTML (embedded image data-URIs) exceeds ~1–2 MB.
        Loading a temp file matches “View in browser”, which already works.
        """
        view = self._sniff_view
        result = self._sniff_result
        if view is None or result is None:
            return
        from .alt_inventory_dialog import webview_file_uri, write_unique_view_html

        export = result.export
        try:
            if export is not None and export.folder:
                folder = Path(export.folder)
                canonical = folder / f"{FEATURE_FILENAME_STEM}.html"
                canonical.write_text(html_doc, encoding="utf-8")
                for leftover in feature_html_basenames() - {canonical.name}:
                    _unlink_quietly(folder / leftover)
                # Keep the currently displayed .cm_view_*.html until the new
                # file has loaded — deleting it first leaves Edge showing a
                # stale in-memory document.
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
        prev = self._sniff_html_tmp
        self._sniff_html_tmp = path
        self._sniff_load_retries = 0
        if prev is not None and prev != path:
            self._sniff_html_tmp_prev.append(prev)
        uri = webview_file_uri(path)
        logger.debug(
            "Inspector WebView LoadURL %s (%s bytes)", uri, path.stat().st_size
        )
        try:
            view.LoadURL(uri)
        except Exception:
            logger.exception("LoadURL failed for inspector HTML")
            try:
                view.SetPage(html_doc, uri)
            except Exception:
                logger.exception("SetPage fallback failed for inspector HTML")
        gen = self._sniff_paint_gen
        self._call_later(350, lambda: self._reload_if_gen(gen))
        self._call_later(900, lambda: self._reload_if_gen(gen))

    def _reload_if_gen(self, gen: int) -> None:
        if not self._alive() or int(gen) != int(self._sniff_paint_gen):
            return
        self._sniff_reload_if_needed()

    def _sniff_reload_if_needed(self) -> None:
        """If Edge is still on about:blank or a previous report file, load the current one."""
        if not self._alive() or not self._sniff_is_webview or self._sniff_view is None:
            return
        if self._sniff_html_tmp is None:
            return
        view = self._sniff_view
        try:
            current = (view.GetCurrentURL() or "").strip()
        except Exception:
            current = ""
        if webview_url_matches_html(current, self._sniff_html_tmp):
            return
        if current and self._sniff_load_retries >= 3:
            self._sniff_replace_webview()
            return
        if current:
            self._sniff_load_retries += 1
        from .alt_inventory_dialog import webview_file_uri

        try:
            view.LoadURL(webview_file_uri(self._sniff_html_tmp))
        except Exception:
            logger.debug("Inspector WebView reload failed", exc_info=True)

    def _cleanup_stale_view_html(self) -> None:
        """Remove previous unique HTML copies once the current file is showing."""
        keep = self._sniff_html_tmp
        result = self._sniff_result
        export = result.export if result is not None else None
        if export is not None and export.folder:
            from .alt_inventory_dialog import cleanup_view_html

            cleanup_view_html(Path(export.folder), keep=keep)
        kept: list[Path] = []
        for leftover in list(self._sniff_html_tmp_prev):
            if keep is not None and leftover == keep:
                kept.append(leftover)
                continue
            _unlink_quietly(leftover)
        self._sniff_html_tmp_prev = kept

    def _schedule_scroll_followup(self) -> None:
        self._sniff_scroll_followup = False
        gen = self._sniff_paint_gen
        for ms in (0, 50, 200, 500):
            self._call_later(ms, lambda g=gen: self._scroll_followup_if_gen(g))

    def _scroll_followup_if_gen(self, gen: int) -> None:
        if not self._alive() or int(gen) != int(self._sniff_paint_gen):
            return
        if not self._sniff_is_webview or self._sniff_view is None:
            return
        try:
            self._main_mod()._webview_run_script(
                self._sniff_view, _WEBVIEW_SCROLL_LATEST_FOLLOWUP_JS
            )
        except Exception:
            logger.debug("Could not scroll to follow-up", exc_info=True)
        try:
            self.followup_ctrl.SetFocus()
        except RuntimeError:
            pass

    def _sniff_replace_webview(self) -> None:
        """Recreate the Edge control once if reloads still show a blank document."""
        if self._sniff_webview_replaced or not self._alive():
            return
        self._sniff_webview_replaced = True
        from .alt_inventory_dialog import _safe_destroy_window

        old = self._sniff_view
        self._release_sniff_webview()
        host = self._sniff_host
        if host is None:
            return
        sizer = host.GetSizer()
        main_mod = self._main_mod()
        view, is_webview = main_mod._create_ai_html_view(
            host, name=feature_title()
        )
        if is_webview:
            import wx.html2 as html2

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_sniff_navigating)
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_sniff_webview_loaded)
        self._sniff_view = view
        self._sniff_is_webview = is_webview
        self._sniff_load_retries = 0
        if sizer is not None:
            if old is not None:
                try:
                    sizer.Detach(old)
                except Exception:
                    pass
            sizer.Add(view, 1, wx.EXPAND)
        try:
            main_mod._wire_ai_html_host(host, view, is_webview=is_webview)
        except Exception:
            pass
        host.Layout()
        self.Layout()
        if old is not None and old is not view:
            self._call_later(500, lambda: _safe_destroy_window(old))
        wx.CallAfter(self._sniff_paint_or_idle)
        if is_webview:
            self._call_later(400, self._sniff_reload_if_needed)

    def _sniff_paint(self, *, scroll_followup: bool = False) -> None:
        if not self._alive() or self._sniff_view is None:
            return
        if self._sniff_result is None:
            self._sniff_show_idle()
            return
        self._sniff_paint_gen += 1
        self._sniff_scroll_followup = bool(scroll_followup and self._sniff_is_webview)
        html_doc = self._sniff_current_html(scroll_followup=self._sniff_scroll_followup)
        if self._sniff_is_webview:
            self._sniff_load_html(html_doc)
        else:
            setter = getattr(self._sniff_view, "ChangeValue", None) or getattr(
                self._sniff_view, "SetValue", None
            )
            if setter is not None:
                setter(self._sniff_synthesis_md)
            if scroll_followup:
                try:
                    self._sniff_view.ShowPosition(len(self._sniff_view.GetValue() or ""))
                except RuntimeError:
                    pass

    def _set_sniff_busy(self, busy: bool) -> None:
        self._sniff_busy = busy
        ok = (not busy) and self._sniff_session is not None
        for ctrl in (getattr(self, "ask_btn", None), getattr(self, "followup_ctrl", None)):
            if ctrl is None:
                continue
            try:
                ctrl.Enable(ok)
            except RuntimeError:
                pass
        run = getattr(self, "sniff_run_btn", None)
        if run is not None:
            try:
                run.Enable(not busy)
            except RuntimeError:
                pass
        self._sync_assess_more_enabled()

    def _on_run_sniff(self, _event: wx.Event | None = None) -> None:
        """Top-of-page action: sample images and run the sniff test in this dialog."""
        if self._sniff_busy or self._closing:
            return
        self._select_page(self._PAGE_SNIFF)
        self._prompt_and_start_sniff(prior=None)

    def _prompt_and_start_sniff(self, *, prior) -> None:
        from .alt_sample import DEFAULT_SAMPLE_PERCENT, sample_choice_labels
        from .explain import error_message_for_key

        folder = Path(self.folder)
        try:
            from .alt_export import load_alt_export

            export = load_alt_export(folder)
        except FileNotFoundError as exc:
            wx.MessageBox(
                error_message_for_key("bad_export", detail=str(exc)),
                feature_title(),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        except ValueError as exc:
            wx.MessageBox(
                error_message_for_key("bad_export", detail=str(exc)),
                feature_title(),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        except Exception as exc:
            wx.MessageBox(
                _("Could not read the export folder:\n{error}").format(error=exc),
                feature_title(),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        counts = export.counts()
        choice_rows = sample_choice_labels(counts["total"])
        if not choice_rows:
            return
        if len(choice_rows) == 1:
            _label, mode, percent = choice_rows[0]
            percent = percent if percent is not None else 100
            self._start_sniff_assess(
                folder, mode=mode, percent=percent, prior=prior
            )
            return

        choices = [label for label, _mode, _pct in choice_rows]
        default_sel = 0
        for i, (_label, _mode, pct) in enumerate(choice_rows):
            if pct == DEFAULT_SAMPLE_PERCENT:
                default_sel = i
                break
        choice_dlg = wx.SingleChoiceDialog(
            self,
            _(
                "Document: {name}\n"
                "Images: {total} — with alt: {with_alt} — decorative: {decorative} — "
                "missing: {missing}\n\n"
                "Choose how many images to send to the vision model "
                "(samples are spread through the publication):"
            ).format(
                name=export.document_name,
                total=counts["total"],
                with_alt=counts["with_alt"],
                decorative=counts["decorative"],
                missing=counts["missing"],
            ),
            feature_title(),
            choices,
        )
        try:
            choice_dlg.SetSelection(default_sel)
            if choice_dlg.ShowModal() != wx.ID_OK:
                return
            _label, mode, percent = choice_rows[choice_dlg.GetSelection()]
            percent = percent if percent is not None else 100
        finally:
            choice_dlg.Destroy()
        self._start_sniff_assess(folder, mode=mode, percent=percent, prior=prior)

    def _start_sniff_assess(
        self, folder: Path, *, mode: str, percent: int, prior
    ) -> None:
        if self._sniff_busy:
            return
        if prior is None:
            self._sniff_synthesis_md = ""
            self._sniff_result = None
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
        self._set_sniff_busy(True)

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
                if self._closing:
                    return
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
                    self._set_sniff_busy(False)
                    return
                self.apply_sniff_result(out)
                try:
                    from ..telemetry import log_ai_alt_assess

                    log_ai_alt_assess()
                except Exception:
                    pass

            wx.CallAfter(done)

        threading.Thread(target=work, daemon=True).start()

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
        if self._sniff_busy or self._sniff_session is None:
            return
        if self.followup_ctrl is None:
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
        self._set_sniff_busy(True)
        session = self._sniff_session

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
                wx.CallAfter(self._set_sniff_busy, False)
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
                self._sniff_session = out.session
            self._set_sniff_busy(False)
            return
        self._sniff_session = out.session
        self._sniff_synthesis_md = append_followup_markdown(
            self._sniff_synthesis_md,
            heading=_("Follow-up"),
            question=getattr(event, "question", "") or "",
            answer=out.text or "",
        )
        if self._sniff_result is not None:
            self._sniff_result.text = self._sniff_synthesis_md
        self._realize_sniff_view()
        self._sniff_paint(scroll_followup=True)
        if self.followup_ctrl is not None:
            self.followup_ctrl.SetValue("")
        self._set_sniff_busy(False)
        try:
            if self.followup_ctrl is not None:
                self.followup_ctrl.SetFocus()
        except RuntimeError:
            pass
        try:
            from ..telemetry import log_ai_alt_assess

            log_ai_alt_assess(followup=True)
        except Exception:
            pass

    def _on_assess_more(self, _event: wx.Event) -> None:
        if self._sniff_busy or self._sniff_result is None or self._sniff_result.export is None:
            return
        total = self._sniff_result.export.total
        already = len(self._sniff_result.assessments)
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

        self._start_sniff_assess(
            self._sniff_result.export.folder,
            mode=mode,
            percent=percent,
            prior=self._sniff_result,
        )

    def _on_view_browser(self, _event: wx.Event) -> None:
        if self._sniff_result is None:
            return
        try:
            fd, name = tempfile.mkstemp(
                prefix="checkmate-alt-assess-", suffix=".html", text=True
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(build_assessment_html(self._sniff_result, for_dialog=False))
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
        if self._sniff_result is None:
            return
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
                    build_assessment_html(self._sniff_result, for_dialog=False),
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
        if self._sniff_result is None:
            return
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
                    assessment_markdown_export(self._sniff_result), encoding="utf-8"
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
        if self._sniff_result is None:
            return
        text = assessment_markdown_export(self._sniff_result)
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
                if self.IsModal():
                    event.Veto()
                else:
                    # ShowModal already returned; allow the caller's Destroy().
                    event.Skip()
            return
        self._closing = True
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        self._sniff_paint_gen += 1
        self._sniff_scroll_followup = False
        self._stop_pending_later()
        if self._ai_cancel is not None:
            self._ai_cancel.set()
        self._close_progress()
        cleanup = getattr(self, "_cleanup_view_copy", None)
        if callable(cleanup):
            cleanup()
        release_inventory = getattr(self, "_release_webview", None)
        if callable(release_inventory):
            release_inventory()
        self._release_sniff_webview()
        if isinstance(event, wx.CloseEvent):
            event.Veto()
        # Finish on the next idle so we are outside navigating/key handlers.
        wx.CallAfter(self._finish_close_dialog)

    def _on_close(self, event: wx.Event | None = None) -> None:
        self._on_close_dialog(event)

    def _release_sniff_webview(self) -> None:
        """Unbind Edge events; do not Stop/Hide the control (that blanks or crashes)."""
        view = self._sniff_view
        if view is None or not self._sniff_is_webview:
            return
        try:
            import wx.html2 as html2

            view.Unbind(html2.EVT_WEBVIEW_LOADED)
            view.Unbind(html2.EVT_WEBVIEW_NAVIGATING)
        except Exception:
            pass

    def _cleanup_sniff_html_tmp(self) -> None:
        for leftover in list(self._sniff_html_tmp_prev):
            _unlink_quietly(leftover)
        self._sniff_html_tmp_prev.clear()
        # Keep the copy written beside the export (relative images/ resolve).
        if self._sniff_html_tmp is None or self._sniff_html_tmp.name not in feature_html_basenames():
            _unlink_quietly(self._sniff_html_tmp)
        self._sniff_html_tmp = None

    def _on_window_destroy(self, event: wx.WindowDestroyEvent) -> None:
        event.Skip()
        if event.GetEventObject() is not self:
            return
        self._cleanup_sniff_html_tmp()

    def _blur_webview_for_close(self) -> None:
        """Move Win32 focus off Edge without WM_NEXTDLGCTL (that deadlocks WebView2)."""
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(self.GetHandle() or 0)
                if hwnd:
                    ctypes.windll.user32.SetFocus(hwnd)
            except Exception:
                pass
            return
        try:
            self.SetFocus()
        except RuntimeError:
            pass

    def _finish_close_dialog(self) -> None:
        try:
            if not self:
                return
        except RuntimeError:
            return
        self._blur_webview_for_close()
        try:
            if self.IsModal():
                self.exit_code = int(wx.ID_CLOSE)
                self.EndModal(self.exit_code)
        except RuntimeError:
            pass
