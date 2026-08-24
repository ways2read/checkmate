"""Dialog to view a Fido-style alt-text inventory report in a WebView."""

from __future__ import annotations

import json
import logging
import re
import uuid
import webbrowser
from html import escape as html_escape
from pathlib import Path
from urllib.parse import unquote

import wx

from ..i18n import _
from ..settings import ai_features_enabled
from .alt_dialog import AltSniffTestMixin, webview_url_matches_html
from .alt_labels import feature_title
from .fido_image_report import HTML_NAME, ImageReport, load_image_report
from .verdict_tally_bar import VerdictTallyBar

logger = logging.getLogger(__name__)

# Legacy ShowModal id (no longer used). Sniff test runs on a tab of this dialog.
ID_RUN_AI_HEALTH = wx.NewIdRef()
ID_REBUILD_REPORT = wx.NewIdRef()

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
    """``file://`` URI for a unique sibling copy.

    Do not put a query string on ``file://``: WebView2 reports
    ``ERR_FILE_NOT_FOUND`` and keeps the previous document. The unique
    filename is the cache-buster. *token* is ignored (call-site compat).
    """
    del token
    return path.resolve().as_uri()


def _webview_run_script(view: wx.Window, script: str) -> bool:
    from .. import main as main_mod

    return bool(main_mod._webview_run_script(view, script))


def webview_location_replace(view: wx.Window, uri: str) -> bool:
    """Navigate from inside the page. Host ``LoadURL`` is often a no-op."""
    script = (
        "(function(){try{"
        f"window.location.replace({json.dumps(uri)});"
        "return true;}catch(e){return false;}})();"
    )
    return _webview_run_script(view, script)


def webview_replace_via_file(view: wx.Window, uri: str) -> bool:
    """Fetch a sibling ``file://`` copy and write it into the live document."""
    script = f"""
(function() {{
  var uri = {json.dumps(uri)};
  function paint(html) {{
    try {{
      document.open('text/html');
      document.write(html);
      document.close();
    }} catch (e) {{
      try {{ window.location.replace(uri); }} catch (e2) {{}}
    }}
  }}
  try {{
    var xhr = new XMLHttpRequest();
    xhr.open('GET', uri, true);
    xhr.onload = function () {{
      if (xhr.responseText) paint(xhr.responseText);
      else try {{ window.location.replace(uri); }} catch (e3) {{}}
    }};
    xhr.onerror = function () {{
      try {{ window.location.replace(uri); }} catch (e4) {{}}
    }};
    xhr.send();
    return true;
  }} catch (e5) {{
    try {{ window.location.replace(uri); return true; }} catch (e6) {{ return false; }}
  }}
}})();
""".strip()
    return _webview_run_script(view, script)


_WEBVIEW_WRITE_LIMIT = 600_000


def webview_write_html_direct(view: wx.Window, html_doc: str) -> bool:
    """Replace the live document. Host ``LoadURL`` is often a no-op after first paint."""
    script = (
        "(function(){try{"
        "document.open('text/html');"
        f"document.write({json.dumps(html_doc)});"
        "document.close();"
        "return true;}catch(e){return false;}})();"
    )
    return _webview_run_script(view, script)


def _same_parent_folder(left: Path | None, right: Path) -> bool:
    if left is None:
        return True
    try:
        return left.resolve().parent == right.resolve().parent
    except OSError:
        return False


_BASE_TAG_RE = re.compile(r"<base\s[^>]*>", re.I)


def html_with_folder_base(html_doc: str, folder: Path) -> str:
    """Pin relative URLs to *folder* after ``document.write`` into a live page.

    Edge often keeps the previous ``file://`` document URL. A ``<base href>``
    makes ``images/`` resolve in the new report folder instead of the last one.
    """
    base = folder.resolve().as_uri()
    if not base.endswith("/"):
        base += "/"
    tag = f'<base href="{html_escape(base, quote=True)}">'
    if _BASE_TAG_RE.search(html_doc):
        return _BASE_TAG_RE.sub(tag, html_doc, count=1)
    lower = html_doc.lower()
    idx = lower.find("<head")
    if idx >= 0:
        end = html_doc.find(">", idx)
        if end >= 0:
            return html_doc[: end + 1] + tag + html_doc[end + 1 :]
    return tag + html_doc


def load_unique_file_in_webview(
    view: wx.Window,
    src: Path,
    *,
    previous_doc: Path | None = None,
) -> Path | None:
    """Show *src* in *view*.

    First paint: ``LoadURL`` of a unique sibling (no query string). After that,
    host ``LoadURL`` / ``location.replace`` often report success without
    changing the page — ``document.write`` is what actually replaces it.
    Relative ``images/`` are pinned with ``<base href>`` so a new report folder
    cannot keep thumbnails from the previous document.
    """
    already = bool(getattr(view, "_cm_ever_navigated", False))
    if already:
        try:
            html_doc = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.exception("Could not read HTML for WebView reload")
            html_doc = ""
        same_folder = _same_parent_folder(previous_doc, src)
        wrote = False
        if html_doc and len(html_doc) <= _WEBVIEW_WRITE_LIMIT:
            wrote = webview_write_html_direct(
                view, html_with_folder_base(html_doc, src.parent)
            )
            if wrote and same_folder:
                return src
        try:
            dest = write_unique_view_html(src)
        except Exception:
            logger.exception("Could not copy HTML for WebView reload")
            dest = None
        if wrote:
            return dest if dest is not None else src
        if dest is not None:
            uri = dest.resolve().as_uri()
            if not same_folder:
                if webview_location_replace(view, uri):
                    return dest
                load = getattr(view, "LoadURL", None)
                if callable(load):
                    try:
                        load(uri)
                        return dest
                    except Exception:
                        logger.exception("LoadURL failed for new report folder")
            else:
                webview_replace_via_file(view, uri)
                return dest
        if html_doc and webview_write_html_direct(
            view, html_with_folder_base(html_doc, src.parent)
        ):
            return src
        return None
    try:
        dest = write_unique_view_html(src)
    except Exception:
        logger.exception("Could not copy HTML for WebView")
        return None
    uri = dest.resolve().as_uri()
    load = getattr(view, "LoadURL", None)
    if callable(load):
        try:
            load(uri)
            return dest
        except Exception:
            logger.exception("LoadURL failed for unique HTML")
    if webview_write_html_direct(
        view, html_with_folder_base(src.read_text(encoding="utf-8", errors="replace"), src.parent)
    ):
        return dest
    return None


def write_unique_view_html(src: Path) -> Path:
    """Copy *src* to a unique sibling so the ``file://`` path itself is new."""
    dest = src.parent / f".cm_view_{uuid.uuid4().hex}.html"
    dest.write_bytes(src.read_bytes())
    return dest


def cleanup_view_html(
    folder: Path, keep: Path | list[Path] | tuple[Path, ...] | None = None
) -> None:
    """Remove leftover ``.cm_view_*.html`` copies from a previous open."""
    keep_resolved: set[Path] = set()
    if keep is not None:
        items = keep if isinstance(keep, (list, tuple)) else [keep]
        for item in items:
            if item is None:
                continue
            try:
                keep_resolved.add(Path(item).resolve())
            except OSError:
                pass
    for path in folder.glob(".cm_view_*.html"):
        try:
            if path.resolve() in keep_resolved:
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
    """Single-page image report: Fido HTML, verdict pills, optional Q&A."""

    _PAGE_REPORT = "report"
    _PAGE_SNIFF = "sniff"

    def __init__(
        self,
        parent: wx.Window,
        *,
        folder: Path,
        html_path: Path,
        source_path: Path | None = None,
    ) -> None:
        super().__init__(
            parent,
            title=_("Image report"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.SetSize((900, 760))
        self.SetMinSize((720, 520))
        self.folder = Path(folder)
        self._html_path = Path(html_path)
        self.source_path = Path(source_path) if source_path else None
        cleanup_view_html(self.folder)
        self.exit_code = int(wx.ID_CANCEL)
        self._rebuild_requested = False
        self._view_realized = False
        self._output_is_webview = False
        self._closing = False
        self._parked = False
        self._modal_session = 0
        self._load_gen = 0
        self._output: wx.Window | None = None
        self._view_html: Path | None = None
        self._view_html_prev: list[Path] = []
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
        self.chat_toggle_btn: wx.Button | None = None
        self.include_qa_chk: wx.CheckBox | None = None
        self.save_html_btn = None
        self.save_md_btn = None
        self.copy_btn = None

        from .. import main as main_mod

        self._main = main_mod
        self._init_sniff_state()

        root = wx.BoxSizer(wx.VERTICAL)

        self._tally_bar = VerdictTallyBar(self)
        root.Add(self._tally_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self._report: ImageReport | None = None
        self._refresh_report_model()

        self._content_panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
        main_mod._win_ensure_control_parent(self._content_panel)

        if self._show_sniff:
            from .conversation_pane import (
                ConversationScroller,
                bind_chat_sash_persist,
                make_chat_splitter,
            )
            from ..settings import chat_pane_shown as chat_pane_pref

            self._splitter = make_chat_splitter(self._content_panel)
            self._host = main_mod._AiHtmlHostPanel(
                self._splitter, name=_("Image report")
            )
            self._host.SetMinSize((-1, 280))
            host_sizer = wx.BoxSizer(wx.VERTICAL)
            self._loading_label = wx.StaticText(
                self._host, label=_("Loading report…")
            )
            host_sizer.Add(self._loading_label, 0, wx.ALL, 8)
            self._host.SetSizer(host_sizer)
            main_mod._win_clear_tab_stop(self._host)

            self._sniff_host = wx.Panel(self._splitter, name=_("Conversation"))
            self._sniff_host.SetMinSize((260, 200))
            chat_sizer = wx.BoxSizer(wx.VERTICAL)
            self._sniff_view = ConversationScroller(self._sniff_host)
            chat_sizer.Add(self._sniff_view, 1, wx.EXPAND)
            self._build_sniff_followup(self._sniff_host, chat_sizer)
            self._sniff_host.SetSizer(chat_sizer)
            self._sniff_is_webview = False
            self._sniff_view_realized = True
            self._chat_pane_shown = bool(chat_pane_pref())
            self._splitter.SplitVertically(self._host, self._sniff_host, 520)
            bind_chat_sash_persist(self._splitter)
            content_sizer = wx.BoxSizer(wx.VERTICAL)
            content_sizer.Add(self._splitter, 1, wx.EXPAND)
        else:
            self._splitter = None
            self._host = main_mod._AiHtmlHostPanel(
                self._content_panel, name=_("Image report")
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

        self._content_panel.SetSizer(content_sizer)
        root.Add(self._content_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self._report_actions = self._build_report_actions(self)
        root.Add(self._report_actions, 0, wx.EXPAND | wx.ALL, 12)

        self._close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self.SetSizer(root)
        self.CentreOnParent()
        # Escape is handled by CHAR_HOOK / in-page JS. Stock ID_CLOSE as the
        # escape id leaves a queued EndModal that instantly closes the next open.
        self.SetEscapeId(wx.ID_NONE)
        self.SetAffirmativeId(wx.ID_NONE)
        self._close_btn.SetDefault()
        self._apply_active_page(self._PAGE_REPORT, initial=True)
        if self._show_sniff:
            self._apply_chat_pane_shown(
                getattr(self, "_chat_pane_shown", False), persist=False
            )
            self._paint_native_chat()

        # Do not CallAfter(_realize_view) from __init__: Destroy() of a previous
        # WebView can pump that callback before ShowModal, and Edge then paints
        # a blank HWND. Create the control only after this dialog is shown.
        self.Bind(wx.EVT_SHOW, self._on_show)

    def _build_sniff_followup(self, parent: wx.Window, sizer: wx.Sizer) -> None:
        """Composer under the message list, matching the chat column width."""
        from ..settings import include_chat_in_html_report

        label_text = _("Ask a question…")
        hint = _("Type a message about this report")
        label = wx.StaticText(parent, label=label_text)
        label.SetToolTip(hint)
        self._main._win_clear_tab_stop(label)
        sizer.Add(label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.followup_ctrl = wx.TextCtrl(
            parent,
            value="",
            style=wx.TE_PROCESS_ENTER,
            name=label_text,
        )
        self.followup_ctrl.SetName(label_text)
        self.followup_ctrl.SetToolTip(hint)
        if hasattr(self.followup_ctrl, "SetAccessibleName"):
            try:
                self.followup_ctrl.SetAccessibleName(label_text)
            except Exception:
                pass
        self.followup_ctrl.Bind(wx.EVT_SET_FOCUS, self._on_followup_focus)
        self.followup_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_followup_kill_focus)
        self.followup_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_ask)
        sizer.Add(self.followup_ctrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)

        self.ask_btn = wx.Button(parent, label=_("Ask"))
        self.ask_btn.Bind(wx.EVT_BUTTON, self._on_ask)
        sizer.Add(self.ask_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)

        self.include_qa_chk = wx.CheckBox(
            parent, label=_("Include chat in HTML report")
        )
        self.include_qa_chk.SetToolTip(
            _("Open in browser includes the conversation")
        )
        self.include_qa_chk.SetValue(include_chat_in_html_report())
        self.include_qa_chk.Bind(wx.EVT_CHECKBOX, self._on_include_qa)
        sizer.Add(self.include_qa_chk, 0, wx.EXPAND | wx.ALL, 8)
        self.chat_toggle_btn = None

    def _build_report_actions(self, parent: wx.Window) -> wx.Panel:
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        self._main._win_clear_tab_stop(panel)
        self._main._win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.open_browser_btn = wx.Button(panel, label=_("Open in &browser"))
        self.open_folder_btn = wx.Button(panel, label=_("Open &folder"))
        self.rebuild_btn = wx.Button(panel, label=_("&Rebuild…"))
        self.open_browser_btn.SetToolTip(
            _("Open the image report in your browser")
        )
        self.open_folder_btn.SetToolTip(
            _("Reveal the export folder in the file manager")
        )
        self.rebuild_btn.SetToolTip(
            _("Ask Fido to build this image report again")
        )
        self.open_browser_btn.Bind(wx.EVT_BUTTON, self._on_open_browser)
        self.open_folder_btn.Bind(wx.EVT_BUTTON, self._on_open_folder)
        self.rebuild_btn.Bind(wx.EVT_BUTTON, self._on_rebuild_report)
        sizer.Add(self.open_browser_btn, 0, wx.RIGHT, 8)
        sizer.Add(self.open_folder_btn, 0, wx.RIGHT, 8)
        sizer.Add(self.rebuild_btn, 0, wx.RIGHT, 8)
        if self._show_sniff:
            self.chat_toggle_btn = wx.Button(panel, label=_("Show chat"))
            self.chat_toggle_btn.Bind(wx.EVT_BUTTON, self._on_toggle_chat)
            sizer.Add(self.chat_toggle_btn, 0, wx.RIGHT, 8)
        sizer.AddStretchSpacer(1)
        self._close_btn = wx.Button(panel, wx.ID_ANY, label=_("&Close"))
        sizer.Add(self._close_btn, 0)
        self.assess_more_btn = None
        self.view_browser_btn = None
        panel.SetSizer(sizer)
        return panel

    def _cycle_notebook_page(self, delta: int) -> None:
        return

    def _select_page(self, key: str) -> None:
        self._apply_active_page(self._PAGE_REPORT)

    def _apply_active_page(self, key: str, *, initial: bool = False) -> None:
        self._active_page_key = self._PAGE_REPORT
        self._host.Show(True)
        if self._report_actions is not None:
            self._report_actions.Show(True)
        self._sync_chat_chrome()
        self._refresh_host_tab_stops()
        try:
            self.Layout()
            if self._content_panel is not None:
                self._content_panel.Layout()
        except RuntimeError:
            pass

    def _on_toggle_chat(self, _event: wx.Event | None = None) -> None:
        self._apply_chat_pane_shown(not getattr(self, "_chat_pane_shown", False))

    def _on_include_qa(self, _event: wx.Event) -> None:
        from ..settings import set_include_chat_in_html_report

        chk = getattr(self, "include_qa_chk", None)
        if chk is None:
            return
        set_include_chat_in_html_report(bool(chk.GetValue()))

    def _apply_chat_pane_shown(self, shown: bool, *, persist: bool = True) -> None:
        from .conversation_pane import set_chat_pane_shown
        from ..settings import set_chat_pane_shown as persist_chat

        splitter = getattr(self, "_splitter", None)
        if splitter is None or self._sniff_host is None:
            return
        self._chat_pane_shown = bool(shown)
        set_chat_pane_shown(
            splitter,
            self._host,
            self._sniff_host,
            shown=self._chat_pane_shown,
            toggle=getattr(self, "chat_toggle_btn", None),
        )
        try:
            self.Layout()
            if self._content_panel is not None:
                self._content_panel.Layout()
        except RuntimeError:
            pass
        if persist:
            persist_chat(self._chat_pane_shown)

    def _paint_native_chat(self) -> None:
        from .markdown_html import (
            conversation_idle_prompt,
            conversation_turns_from_report_md,
        )

        view = getattr(self, "_sniff_view", None)
        setter = getattr(view, "set_content", None)
        if not callable(setter):
            return
        md = getattr(self, "_sniff_synthesis_md", "") or ""
        if not md.strip():
            report = getattr(self, "_report", None)
            if report is not None:
                from .markdown_html import compose_sniff_chat_markdown

                md = compose_sniff_chat_markdown(
                    report.synthesis_markdown or "",
                    report.qa_markdown or "",
                )
        turns = conversation_turns_from_report_md(md)
        setter(turns, idle=conversation_idle_prompt())
        if turns:
            focus = getattr(view, "focus_latest", None)
            if callable(focus):
                focus()

    def _sync_chat_chrome(self) -> None:
        if not self._show_sniff:
            return
        self._paint_native_chat()
        self._set_sniff_busy(self._sniff_busy)
        try:
            self.Layout()
            if self._content_panel is not None:
                self._content_panel.Layout()
        except RuntimeError:
            pass

    def _refresh_host_tab_stops(self) -> None:
        details = self._host
        sniff = getattr(self, "_sniff_host", None)
        if getattr(self, "_output_is_webview", False):
            view = getattr(self, "_output", None)
            if view is not None:
                self._main._refresh_ai_html_tab_stops(details, view, is_webview=True)
            details._accept_kbd_focus = True
        else:
            self._main._win_clear_tab_stop(details)
            details._accept_kbd_focus = False

        if sniff is not None:
            ready = (
                self._sniff_host.IsShown()
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
            self._report_actions,
            getattr(self, "_sniff_host", None),
        ):
            if panel is None:
                continue
            try:
                if panel.IsShown():
                    self._main._win_clear_tab_stop(panel)
                    self._main._win_ensure_control_parent(panel)
            except RuntimeError:
                pass

    def prepare(
        self,
        folder: Path,
        html_path: Path,
        source_path: Path | None = None,
    ) -> None:
        """Reuse this dialog for another show without recreating Edge."""
        self._stop_pending_later()
        prev_folder = Path(getattr(self, "folder", "") or "")
        new_folder = Path(folder)
        same_folder = bool(prev_folder) and prev_folder == new_folder
        keep_md = (self._sniff_synthesis_md or "") if same_folder else ""
        keep_session = self._sniff_session if same_folder else None
        if not same_folder:
            persist = getattr(self, "_persist_qa_markdown", None)
            if callable(persist):
                persist()
        self.folder = new_folder
        self._html_path = Path(html_path)
        if source_path is not None:
            self.source_path = Path(source_path)
        self.exit_code = int(wx.ID_CANCEL)
        self._rebuild_requested = False
        self._closing = False
        self._parked = False
        self._modal_session = int(getattr(self, "_modal_session", 0)) + 1
        self._dialog_html_cache = None
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        self._refresh_report_model()
        self._reset_sniff_state()
        self._sniff_synthesis_md = keep_md
        self._sniff_session = keep_session

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
        self._rebuild_requested = False
        self._modal_session = int(getattr(self, "_modal_session", 0)) + 1
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        gen = self._load_gen
        wx.CallAfter(self._after_shown, gen)
        return super().ShowModal()

    def _after_shown(self, gen: int) -> None:
        try:
            if not self._same_gen(gen):
                return
            if not self._view_realized:
                self._realize_view()
            else:
                self._load_report()
                self._inject_webview_key_handlers()
                self._call_later(80, lambda: self._inject_keys_if_gen(gen))
                self._call_later(250, lambda: self._inject_keys_if_gen(gen))
            self._seed_sniff_if_needed()
            if self._show_sniff:
                splitter = getattr(self, "_splitter", None)
                if splitter is not None:
                    from .conversation_pane import restore_chat_sash

                    restore_chat_sash(splitter)
        except RuntimeError:
            return

    def _seed_sniff_if_needed(self) -> None:
        """If Fido already assessed images, enable chat on this page."""
        if not self._show_sniff or self._sniff_result is not None:
            return
        report = self._report
        if report is None or not report.has_ai:
            return
        from .image_report_qa import (
            enrich_qa_session_with_sniff,
            restore_qa_session_from_markdown,
        )

        try:
            if not self or self._sniff_host is None or not self._sniff_host:
                return
        except RuntimeError:
            return
        self._ensure_qa_session()
        session = self._sniff_session
        if session is not None and not session.messages:
            restore_qa_session_from_markdown(
                session,
                report,
                (self._sniff_synthesis_md or report.qa_markdown or ""),
            )
        if session is not None and not session.messages:
            enrich_qa_session_with_sniff(session, report)
        self.apply_sniff_result(report)

    def _on_show(self, event: wx.ShowEvent) -> None:
        event.Skip()
        if not event.IsShown() or self._closing or getattr(self, "_parked", False):
            return
        # Direct — idle CallAfter stops after a few Edge create/destroy cycles.
        self._after_shown(self._load_gen)

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
            host, name=_("Image report")
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
                    "Use “Open in browser” to view the image report."
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
            # First idle after New() is often still about:blank; load, then
            # retry the *same* file if LOADED never fired (do not write a
            # second copy — that races cleanup into ERR_FILE_NOT_FOUND).
            wx.CallAfter(self._load_report_if_gen, gen)
            self._call_later(300, lambda: self._retry_report_load_if_gen(gen))
            self._call_later(250, lambda: self._inject_keys_if_gen(gen))

    def _refresh_tab_stops_if_gen(self, gen: int, host, view) -> None:
        if not self._same_gen(gen):
            return
        self._main._refresh_ai_html_tab_stops(host, view, is_webview=True)

    def _load_report_if_gen(self, gen: int) -> None:
        if not self._same_gen(gen):
            return
        self._load_report()

    def _retry_report_load_if_gen(self, gen: int) -> None:
        if not self._same_gen(gen) or self._output is None:
            return
        if getattr(self._output, "_cm_ever_navigated", False):
            return
        unique = getattr(self, "_view_html", None)
        if unique is not None and unique.is_file():
            try:
                self._output.LoadURL(unique.resolve().as_uri())
                return
            except Exception:
                logger.exception("Retry LoadURL failed for alt text report")
        self._load_report()

    def _inject_keys_if_gen(self, gen: int) -> None:
        if not self._same_gen(gen):
            return
        self._inject_webview_key_handlers()

    def _refresh_report_model(self) -> None:
        try:
            self._report = load_image_report(self.folder)
        except Exception:
            logger.exception("Could not load image_report.json from %s", self.folder)
            self._report = None
        bar = getattr(self, "_tally_bar", None)
        if bar is not None and self._report is not None:
            bar.set_counts(self._report.verdict_tally())

    def _reload_after_sniff(self) -> None:
        """Pick up Fido HTML/JSON rewritten into the same folder after a sniff."""
        self._dialog_html_cache = None
        html = Path(self.folder) / HTML_NAME
        if html.is_file():
            self._html_path = html
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        self._refresh_report_model()
        self._load_report()

    def _dialog_html(self) -> str:
        cached = getattr(self, "_dialog_html_cache", None)
        if cached is not None:
            return cached
        try:
            html_doc = self._html_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.exception("Could not read Fido HTML report")
            html_doc = (
                "<html><body><p>Could not load the image report.</p></body></html>"
            )
        self._dialog_html_cache = html_doc
        return html_doc

    def _load_report(self) -> None:
        """Load Fido HTML after the Edge HWND exists.

        A unique sibling ``file://`` path avoids Edge treating a repeat
        navigation as a no-op (blank second open). Relative ``images/``
        still resolve because the copy lives in the report folder.
        """
        try:
            if not self:
                return
        except RuntimeError:
            return
        if self._closing or not self._output_is_webview or self._output is None:
            return
        unique = load_unique_file_in_webview(
            self._output,
            self._html_path,
            previous_doc=getattr(self, "_view_html", None),
        )
        if unique is not None:
            try:
                in_place = unique.resolve() == self._html_path.resolve()
            except OSError:
                in_place = unique == self._html_path
            if not in_place:
                prev = getattr(self, "_view_html", None)
                self._view_html = unique
                if prev is not None and prev != unique:
                    self._view_html_prev.append(prev)
            else:
                # document.write does not fire LOADED; re-inject Escape JS.
                gen = self._load_gen
                self._inject_webview_key_handlers()
                self._call_later(80, lambda: self._inject_keys_if_gen(gen))
                self._call_later(250, lambda: self._inject_keys_if_gen(gen))
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

    def reload_report(self) -> None:
        """Reload HTML and AI chrome after Fido rewrote the export folder."""
        self._dialog_html_cache = None
        html = Path(self.folder) / HTML_NAME
        if html.is_file():
            self._html_path = html
        self._load_gen = int(getattr(self, "_load_gen", 0)) + 1
        self._refresh_report_model()
        self._load_report()
        self._seed_sniff_if_needed()

    def _navigate_report_if_gen(self, gen: int, uri: str) -> None:
        if not self._same_gen(gen) or self._output is None:
            return
        if webview_location_replace(self._output, uri):
            return
        try:
            self._output.LoadURL(uri)
        except Exception:
            logger.exception("LoadURL failed for alt text report")
            html_doc = self._dialog_html()
            try:
                self._output.SetPage(html_doc, "")
            except Exception:
                logger.exception("SetPage failed for alt text report")

    def _cm_view_keep_paths(self) -> list[Path]:
        keep: list[Path] = []
        for item in (
            getattr(self, "_view_html", None),
            getattr(self, "_sniff_html_tmp", None),
        ):
            if item is not None:
                keep.append(item)
        return keep

    def _cleanup_stale_report_view_html(self) -> None:
        keep = self._cm_view_keep_paths()
        cleanup_view_html(self.folder, keep=keep)
        kept: list[Path] = []
        keep_set = set(keep)
        for leftover in list(self._view_html_prev):
            if leftover in keep_set:
                kept.append(leftover)
                continue
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
        self._view_html_prev = kept

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
        url = ""
        try:
            url = (event.GetURL() or "").strip()
        except Exception:
            pass
        if webview_url_matches_html(url, getattr(self, "_view_html", None)):
            try:
                if self._output is not None:
                    self._output._cm_ever_navigated = True  # type: ignore[attr-defined]
            except Exception:
                pass
            self._cleanup_stale_report_view_html()

    def _show_full_preview_index(self, index: int) -> None:
        report = self._report
        if report is None:
            self._refresh_report_model()
            report = self._report
        if report is None:
            return
        for im in report.images:
            if int(im.index) != int(index):
                continue
            path = safe_export_image_path(self.folder, im.filename)
            if path is not None:
                self._show_full_preview_path(path, im.filename or f"Image {index}")
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
            self._on_close_dialog(None)
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
            if try_focus(self.open_folder_btn):
                return
            if try_focus(getattr(self, "rebuild_btn", None)):
                return
            try_focus(self._close_btn)
            return
        if try_focus(getattr(self, "_tally_bar", None)):
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
        # Leave the file Edge last loaded. Deleting it while the control still
        # holds that document makes the next LoadURL a no-op (stale report).
        for leftover in list(self._view_html_prev):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass
        self._view_html_prev.clear()

    def _chat_markdown_for_html_report(self) -> str:
        from .markdown_html import followup_markdown_suffix

        live = (getattr(self, "_sniff_synthesis_md", "") or "").strip()
        suffix = followup_markdown_suffix(live) if live else ""
        if suffix.strip():
            return suffix.strip()
        report = getattr(self, "_report", None)
        return (getattr(report, "qa_markdown", None) or "").strip()

    def _html_report_for_export(self) -> str:
        from .markdown_html import html_report_with_chat

        canonical = Path(self.folder) / HTML_NAME
        source = canonical if canonical.is_file() else Path(self._html_path)
        try:
            raw = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        include = True
        chk = getattr(self, "include_qa_chk", None)
        if chk is not None:
            include = bool(chk.GetValue())
        return html_report_with_chat(
            raw, self._chat_markdown_for_html_report(), include=include
        )

    def _on_open_browser(self, _event: wx.Event) -> None:
        html_doc = self._html_report_for_export()
        dest = Path(self.folder) / ".cm_browser_report.html"
        try:
            dest.write_text(html_doc, encoding="utf-8")
            webbrowser.open(dest.resolve().as_uri())
        except OSError as exc:
            wx.MessageBox(
                _("Could not open the report in a browser:\n{error}").format(
                    error=exc
                ),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_save_html(self, _event: wx.Event) -> None:
        html_doc = self._html_report_for_export()
        if not html_doc.strip():
            return
        self._focus_dialog_chrome()
        with wx.FileDialog(
            self,
            _("Save as HTML"),
            defaultFile=HTML_NAME,
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
                path.write_text(html_doc, encoding="utf-8")
            except OSError as exc:
                wx.MessageBox(
                    _("Could not save the file:\n{error}").format(error=exc),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
        self._focus_dialog_chrome()

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

    def _on_rebuild_report(self, _event: wx.Event) -> None:
        parent = self.GetParent()
        rebuild = getattr(parent, "_rebuild_image_report", None)
        if callable(rebuild):
            rebuild()
            return
        self._rebuild_requested = True
        self.exit_code = int(ID_REBUILD_REPORT)
        self._on_close_dialog(None)
