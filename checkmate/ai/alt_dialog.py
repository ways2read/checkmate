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
from ..settings import ai_features_enabled
from .alt_labels import FEATURE_FILENAME_STEM, feature_html_basenames, feature_title
from .explain import ExplainResult, error_message_for_key
from .fido_image_report import ImageReport
from .litellm_client import ai_libraries_status_message
from .markdown_html import (
    _WEBVIEW_SCROLL_LATEST_FOLLOWUP_JS,
    append_followup_markdown,
    compose_sniff_chat_markdown,
    followup_markdown_suffix,
    markdown_to_browser_page,
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
        self._sniff_result: ImageReport | None = None
        self._sniff_session = None
        self._modal_session = int(getattr(self, "_modal_session", 0) or 0)
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

    def _persist_qa_markdown(self) -> None:
        """Write follow-up Q&A into image_report.json so close/reopen keeps chat."""
        folder = Path(getattr(self, "folder", "") or "")
        if not folder:
            return
        suffix = followup_markdown_suffix(
            getattr(self, "_sniff_synthesis_md", "") or ""
        )
        if not suffix.strip():
            return
        try:
            from .fido_image_report import save_image_report_qa

            save_image_report_qa(folder, suffix)
        except Exception:
            logger.debug("Could not persist image-report chat", exc_info=True)
        for report in (
            getattr(self, "_report", None),
            getattr(self, "_sniff_result", None),
        ):
            if report is not None:
                try:
                    report.qa_markdown = suffix
                except Exception:
                    pass

    def apply_sniff_result(self, result: ImageReport) -> None:
        """Show a completed sniff (keeps prior follow-ups) on this page."""
        self._sniff_result = result
        self._sniff_synthesis_md = compose_sniff_chat_markdown(
            result.synthesis_markdown or "",
            result.qa_markdown or "",
            self._sniff_synthesis_md,
        )
        self._persist_qa_markdown()
        self._set_sniff_busy(False)
        keep_followups = bool(followup_markdown_suffix(self._sniff_synthesis_md))
        sync = getattr(self, "_sync_chat_chrome", None)
        try:
            if callable(sync):
                sync()
            elif keep_followups:
                self._realize_sniff_view()
        except RuntimeError:
            return
        self._sync_assess_more_enabled()

        def _paint() -> None:
            if not self._alive() or self._sniff_result is None:
                return
            self._sniff_persist_html()
            self._sniff_paint(scroll_followup=keep_followups)
            self._sniff_inject_followups(scroll=keep_followups)

        wx.CallAfter(_paint)
        self._call_later(200, lambda: self._sniff_inject_followups(scroll=keep_followups))
        self._call_later(500, _paint)

    def _remaining_count(self) -> int:
        result = self._sniff_result
        if result is None:
            return 0
        if not result.sample_is_partial():
            return 0
        return sum(1 for im in result.images if not im.has_ai)

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
            # Direct close — CallAfter stops running after a few Edge cycles.
            self._on_close_dialog(None)
            return
        from .conversation_pane import dialog_handles_composer_enter

        if dialog_handles_composer_enter(
            event, getattr(self, "followup_ctrl", None), self._on_ask
        ):
            return
        event.Skip()

    def _close_dialog_if_session(self, session: int) -> None:
        if int(getattr(self, "_modal_session", 0)) != int(session):
            return
        self._on_close_dialog(None)

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
        paint = getattr(self, "_paint_native_chat", None)
        if callable(paint):
            paint()
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
        md = (self._sniff_synthesis_md or "").strip()
        if not md:
            return self._sniff_idle_html()
        return markdown_to_browser_page(
            md, title=feature_title(), tab_exit=True
        )

    def _sniff_persist_html(self) -> None:
        """Write the sniff HTML beside the report (Save / View in browser)."""
        folder = Path(getattr(self, "folder", "") or "")
        if not folder:
            return
        html_doc = self._sniff_current_html(scroll_followup=False)
        try:
            canonical = folder / f"{FEATURE_FILENAME_STEM}.html"
            canonical.write_text(html_doc, encoding="utf-8")
        except OSError:
            logger.exception("Could not write inspector HTML")

    def _sniff_inject_followups(self, *, scroll: bool = True) -> bool:
        """Follow-ups live in the markdown page; a full repaint is enough."""
        return False

    def _realize_sniff_view(self) -> None:
        if self._closing:
            return
        if callable(getattr(self._sniff_view, "set_content", None)):
            paint = getattr(self, "_paint_native_chat", None)
            if callable(paint):
                paint()
            else:
                self._sniff_paint_or_idle()
            return
        if self._sniff_view is not None:
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
            session = int(getattr(self, "_modal_session", 0))
            wx.CallAfter(self._close_dialog_if_session, session)
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
        try:
            if self._sniff_view is not None:
                self._sniff_view._cm_ever_navigated = True  # type: ignore[attr-defined]
        except Exception:
            pass
        self._cleanup_stale_view_html()
        self._sniff_inject_followups(scroll=self._sniff_scroll_followup)
        if self._sniff_scroll_followup:
            self._schedule_scroll_followup()

    def _sniff_load_html(self, html_doc: str) -> None:
        """Load report HTML via file://.

        Edge WebView2 ``SetPage`` / NavigateToString silently shows a blank
        document once the HTML (embedded image data-URIs) exceeds ~1–2 MB.
        Loading a temp file matches “Open in browser”, which already works.
        """
        view = self._sniff_view
        if view is None:
            return
        from .alt_inventory_dialog import load_unique_file_in_webview

        folder = Path(getattr(self, "folder", "") or "")
        try:
            if folder:
                canonical = folder / f"{FEATURE_FILENAME_STEM}.html"
                canonical.write_text(html_doc, encoding="utf-8")
                for leftover in feature_html_basenames() - {canonical.name}:
                    _unlink_quietly(folder / leftover)
                path = load_unique_file_in_webview(view, canonical)
            else:
                tmp_dir = Path(tempfile.gettempdir()) / "checkmate_alt_assess"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                canonical = tmp_dir / f"assess_{os.getpid()}_{uuid.uuid4().hex}.html"
                canonical.write_text(html_doc, encoding="utf-8")
                path = load_unique_file_in_webview(view, canonical)
        except OSError:
            logger.exception("Could not write inspector HTML for WebView")
            path = None
        if path is None:
            try:
                view.SetPage(html_doc, "about:blank")
            except Exception:
                logger.exception("SetPage fallback failed")
            return
        prev = self._sniff_html_tmp
        self._sniff_html_tmp = path
        self._sniff_load_retries = 0
        # Keep the previously displayed file until Edge commits the new
        # navigation — deleting it first leaves the old document on screen.
        if prev is not None and prev != path:
            self._sniff_html_tmp_prev.append(prev)
        logger.debug(
            "Inspector WebView load %s (%s bytes)", path, path.stat().st_size
        )
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
        from .alt_inventory_dialog import webview_file_uri, webview_location_replace

        uri = webview_file_uri(self._sniff_html_tmp)
        if getattr(view, "_cm_ever_navigated", False):
            if webview_location_replace(view, uri):
                return
        try:
            view.LoadURL(uri)
        except Exception:
            logger.debug("Inspector WebView reload failed", exc_info=True)

    def _cleanup_stale_view_html(self) -> None:
        """Remove previous unique HTML copies once the current file is showing."""
        keep_fn = getattr(self, "_cm_view_keep_paths", None)
        keep_list = keep_fn() if callable(keep_fn) else [self._sniff_html_tmp]
        keep_list = [path for path in keep_list if path is not None]
        keep = self._sniff_html_tmp
        folder = Path(getattr(self, "folder", "") or "")
        if folder:
            from .alt_inventory_dialog import cleanup_view_html

            cleanup_view_html(folder, keep=keep_list)
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
        paint = getattr(self, "_paint_native_chat", None)
        if callable(paint):
            paint()
            if scroll_followup:
                focus = getattr(self._sniff_view, "focus_latest", None)
                if callable(focus):
                    focus()
            return
        if not (self._sniff_synthesis_md or "").strip():
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
        ok = (not busy) and ai_features_enabled()
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
        self._select_page(self._PAGE_REPORT)
        self._prompt_and_start_sniff(assess_all=False)

    def _current_image_report(self) -> ImageReport | None:
        report = getattr(self, "_report", None)
        if report is not None:
            return report
        try:
            from .fido_image_report import load_image_report

            return load_image_report(self.folder)
        except Exception:
            return None

    def _source_publication_path(self) -> Path | None:
        raw = getattr(self, "source_path", None)
        if raw:
            path = Path(raw)
            if path.is_file():
                return path
        from .fido_image_report import source_path_from_folder

        return source_path_from_folder(self.folder)

    def _ensure_qa_session(self):
        if self._sniff_session is not None:
            return self._sniff_session
        from .session import ExplainSession

        try:
            self._sniff_session = ExplainSession.create()
        except Exception:
            logger.exception("Could not start an image-report Q&A session")
            self._sniff_session = None
        return self._sniff_session

    def _prompt_and_start_sniff(self, *, assess_all: bool) -> None:
        from .explain import error_message_for_key
        from .fido_image_report import sample_percent_choices

        report = self._current_image_report()
        source = self._source_publication_path()
        if report is None or source is None:
            wx.MessageBox(
                error_message_for_key(
                    "bad_export",
                    detail=_("Image reports need Fido and a packaged EPUB or PDF."),
                ),
                feature_title(),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        if assess_all:
            self._start_fido_sniff(source, percent=None)
            return

        counts = report.counts()
        choice_rows = sample_percent_choices(counts["total"])
        if not choice_rows:
            return
        if len(choice_rows) == 1:
            _label, percent = choice_rows[0]
            self._start_fido_sniff(source, percent=percent)
            return

        choices = [label for label, _pct in choice_rows]
        default_sel = 0
        for i, (_label, pct) in enumerate(choice_rows):
            if pct == 25:
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
                name=report.document_name,
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
            _label, percent = choice_rows[choice_dlg.GetSelection()]
        finally:
            choice_dlg.Destroy()
        self._start_fido_sniff(source, percent=percent)

    def _start_fido_sniff(self, source: Path, *, percent: int | None) -> None:
        if self._sniff_busy:
            return
        from .fido_image_report import (
            FidoImageReportError,
            make_progress_dialog,
            run_fido_image_report,
        )
        from .image_report_qa import enrich_qa_session_with_sniff

        folder = Path(self.folder)
        self._sniff_synthesis_md = ""
        self._sniff_result = None
        cancel = threading.Event()
        self._ai_cancel = cancel
        start_msg = _progress_body(ai_libraries_status_message())
        self._ai_progress = make_progress_dialog(feature_title(), start_msg, self)
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
            error: Exception | None = None
            run = None
            try:
                run = run_fido_image_report(
                    source,
                    assess=True,
                    percent=percent,
                    dest=folder,
                    use_cache=False,
                    cancel_event=cancel,
                    progress=status,
                )
            except Exception as exc:
                error = exc

            def done() -> None:
                self._close_progress()
                if self._closing:
                    return
                if error is not None:
                    if isinstance(error, FidoImageReportError) and "cancel" in str(error).lower():
                        self._set_sniff_busy(False)
                        return
                    wx.MessageBox(
                        str(error),
                        feature_title(),
                        wx.OK | wx.ICON_ERROR,
                        self,
                    )
                    self._set_sniff_busy(False)
                    return
                report = run.report
                reload_fn = getattr(self, "_reload_after_sniff", None)
                if callable(reload_fn):
                    reload_fn()
                    report = getattr(self, "_report", None) or report
                enrich_qa_session_with_sniff(self._sniff_session, report)
                self.apply_sniff_result(report)
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
        if self._sniff_busy:
            return
        if self.followup_ctrl is None:
            return
        question = self.followup_ctrl.GetValue().strip()
        if not question:
            return
        report = self._current_image_report()
        if report is None:
            wx.MessageBox(
                _("Open an image report before asking a question."),
                feature_title(),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        session = self._ensure_qa_session()
        if session is None:
            wx.MessageBox(
                error_message_for_key("no_key"),
                feature_title(),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        from .image_report_qa import ask_report_qa

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

        def work() -> None:
            try:
                out = ask_report_qa(
                    session, report, question, cancel_event=cancel
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
        self._persist_qa_markdown()
        if self.followup_ctrl is not None:
            self.followup_ctrl.SetValue("")
        self._set_sniff_busy(False)
        sync = getattr(self, "_sync_chat_chrome", None)
        if callable(sync):
            sync()
        # Inject Q&A into the document already on screen. LoadURL of a new
        # file:// copy is often a no-op while ProgressDialog is tearing down.

        def _inject() -> None:
            if not self._alive():
                return
            self._realize_sniff_view()
            if self._sniff_view is None:
                return
            self._sniff_persist_html()
            self._sniff_inject_followups(scroll=True)
            try:
                if self.followup_ctrl is not None:
                    self.followup_ctrl.SetFocus()
            except RuntimeError:
                pass

        def _fallback_load() -> None:
            if not self._alive() or self._sniff_view is None:
                return
            self._sniff_paint(scroll_followup=True)

        wx.CallAfter(_inject)
        self._call_later(200, _inject)
        self._call_later(500, _fallback_load)
        try:
            from ..telemetry import log_ai_alt_assess

            log_ai_alt_assess(followup=True)
        except Exception:
            pass

    def _on_assess_more(self, _event: wx.Event) -> None:
        if self._sniff_busy:
            return
        report = self._current_image_report()
        if report is None or not report.sample_is_partial():
            wx.MessageBox(
                _("All images in this report have already been assessed."),
                feature_title(),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self._prompt_and_start_sniff(assess_all=True)

    def _sniff_export_markdown(self) -> str:
        report = self._current_image_report()
        parts = []
        if report is not None:
            synth = (report.synthesis_markdown or "").strip()
            qa = (report.qa_markdown or "").strip()
            if synth:
                parts.append(synth)
            if qa and qa not in synth:
                parts.append(qa)
        live = (self._sniff_synthesis_md or "").strip()
        if live:
            parts = [live]
        return "\n\n".join(parts).strip()

    def _on_view_browser(self, _event: wx.Event) -> None:
        md = self._sniff_export_markdown()
        if not md:
            return
        try:
            fd, name = tempfile.mkstemp(
                prefix="checkmate-alt-assess-", suffix=".html", text=True
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(markdown_to_browser_page(md, title=feature_title()))
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
        md = self._sniff_export_markdown()
        if not md:
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
                    markdown_to_browser_page(md, title=feature_title()),
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
        md = self._sniff_export_markdown()
        if not md:
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
                path.write_text(md, encoding="utf-8")
            except OSError as exc:
                wx.MessageBox(
                    _("Could not save the file:\n{error}").format(error=exc),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
        self._focus_dialog_chrome()

    def _on_copy(self, _event: wx.Event) -> None:
        text = self._sniff_export_markdown()
        if not text:
            return
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

    def _ensure_end_modal(self, code: int) -> None:
        """EndModal if wx still thinks this dialog is modal. Safe to call twice."""
        try:
            if self.IsModal():
                self.exit_code = int(code)
                self.EndModal(self.exit_code)
        except RuntimeError:
            pass

    def _on_close_dialog(self, event: wx.Event | None = None) -> None:
        if self._closing:
            # Idle CallAfter(_finish_close_dialog) can be dropped after Edge
            # cycles; a second Close (or Images) must still unblock ShowModal.
            if isinstance(event, wx.CloseEvent):
                event.Veto()
            self._ensure_end_modal(int(wx.ID_CLOSE))
            dismissed = getattr(self.GetParent(), "_inventory_dialog_dismissed", None)
            if callable(dismissed):
                dismissed(self)
            return
        self._closing = True
        self._persist_qa_markdown()
        try:
            from .conversation_pane import (
                remember_chat_pane_width,
                remember_webview_chat_dialog_size,
            )

            remember_chat_pane_width(getattr(self, "_splitter", None))
            remember_webview_chat_dialog_size(self, "image_report")
        except Exception:
            pass
        close_session = int(getattr(self, "_modal_session", 0))
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
        # Keep Edge NAVIGATING/LOADED bindings while this host is reused.
        # Unbinding here is why Escape (checkmate://close) only worked once.
        if isinstance(event, wx.CloseEvent):
            event.Veto()
        self._ensure_end_modal(int(wx.ID_CLOSE))
        dismissed = getattr(self.GetParent(), "_inventory_dialog_dismissed", None)
        if callable(dismissed):
            dismissed(self)
            return
        wx.CallAfter(self._finish_close_dialog, close_session)

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
        release_inventory = getattr(self, "_release_webview", None)
        if callable(release_inventory):
            release_inventory()
        self._release_sniff_webview()

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

    def _finish_close_dialog(self, session: int | None = None) -> None:
        try:
            if not self:
                return
        except RuntimeError:
            return
        if session is not None and int(session) != int(getattr(self, "_modal_session", 0)):
            return
        if not getattr(self, "_closing", False):
            return
        self._blur_webview_for_close()
        if getattr(self, "_rebuild_requested", False):
            from .alt_inventory_dialog import ID_REBUILD_REPORT

            code = int(ID_REBUILD_REPORT)
        else:
            code = int(wx.ID_CLOSE)
        self._ensure_end_modal(code)
