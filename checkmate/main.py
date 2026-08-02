"""wxPython main window for CheckMate."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import wx
import wx.adv
import wx.dataview as dv
import wx.lib.newevent

from . import __version__
from .checker import (
    checker_status_text,
    run_check,
)
from .fido_settings import fido_settings_present
from .i18n import (
    LANGUAGES,
    _,
    get_language,
    load_language,
    set_language,
)
from .java_util import detect_java
from .models import CheckResult, Issue, Severity, Verdict
from .paths import (
    CHECKER_REPO_PAGE,
    DAISY_WEBSITE,
    EBRAILLE_SPEC_URL,
    EBRAILLE_STANDARD_PAGE,
    EPUBCHECK_REPO_PAGE,
    VERAPDF_HOME_PAGE,
    application_dir,
    is_frozen,
)
from .publication import is_checkable_path
from .report_export import format_text_report, report_title, save_report
from .settings import read_settings, update_settings
from .updater import (
    EBRAILLE_TOOL,
    EPUBCHECK_TOOL,
    VERAPDF_TOOL,
    ReleaseInfo,
    ToolUpdateInfo,
    check_for_updates,
    ensure_tools_installed,
    install_release,
)
from .warmup import run_startup_warmup

ProgressEvent, EVT_PROGRESS = wx.lib.newevent.NewEvent()
ResultEvent, EVT_RESULT = wx.lib.newevent.NewEvent()
UpdateInfoEvent, EVT_UPDATE_INFO = wx.lib.newevent.NewEvent()
InstallDoneEvent, EVT_INSTALL_DONE = wx.lib.newevent.NewEvent()
JavaMissingEvent, EVT_JAVA_MISSING = wx.lib.newevent.NewEvent()
ExplainAiEvent, EVT_EXPLAIN_AI = wx.lib.newevent.NewEvent()

# ListCtrl is native on Windows but generic (and VoiceOver/Orca-invisible) on
# macOS/Linux. DataViewListCtrl is the reverse — use the native control per OS.
_USE_DATAVIEW_ISSUES = sys.platform != "win32"


class _FocusableReadOnlyText(wx.TextCtrl):
    """Single-line read-only field that stays in the keyboard tab order."""

    def AcceptsFocus(self) -> bool:  # noqa: N802 — wx override
        return True

    def AcceptsFocusFromKeyboard(self) -> bool:  # noqa: N802 — wx override
        return True


def _ensure_win_tab_stop(window: wx.Window) -> None:
    """Force WS_TABSTOP on MSW — TE_READONLY edits often omit it."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.GetHandle())
        if not hwnd:
            return
        gwl_style = -16
        ws_tabstop = 0x00010000
        user32 = ctypes.windll.user32
        style = int(user32.GetWindowLongW(hwnd, gwl_style))
        if style & ws_tabstop:
            return
        user32.SetWindowLongW(hwnd, gwl_style, style | ws_tabstop)
    except Exception:
        pass


def _macos_make_first_responder(window: wx.Window) -> bool:
    """Set Cocoa first responder. wx.SetFocus often fails when a TextCtrl exists."""
    handle = window.GetHandle()
    if not handle:
        return False
    try:
        import ctypes
        import ctypes.util

        lib = ctypes.util.find_library("objc")
        if not lib:
            return False
        objc = ctypes.cdll.LoadLibrary(lib)
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        def _sel(name: str) -> ctypes.c_void_p:
            return objc.sel_registerName(name.encode("utf-8"))

        nsview = ctypes.c_void_p(handle)
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        nswindow = send(nsview, _sel("window"))
        if not nswindow:
            return False

        send_bool = objc.objc_msgSend
        send_bool.restype = ctypes.c_bool
        send_bool.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        ok = bool(
            send_bool(
                ctypes.c_void_p(nswindow),
                _sel("makeFirstResponder:"),
                nsview,
            )
        )
        return ok
    except Exception:
        return False


def app_title() -> str:
    return _("CheckMate")


def filter_choices() -> tuple[str, ...]:
    return (
        _("All issues"),
        _("Errors only"),
        _("Warnings only"),
        _("Info / usage"),
    )


def source_filter_choices(sources: list[str] | None = None) -> tuple[str, ...]:
    """Choices for the Source filter: combined run, then each checker name."""
    names = list(sources or [])
    return (_("EPUBCheck + Ace"), *names)


def _result_source_names(result: CheckResult) -> list[str]:
    """Checker names present in a result, for the Source filter/column."""
    if result.source_counts:
        return [name for name, _ in result.source_counts if name]
    seen: list[str] = []
    for issue in result.issues:
        if issue.source and issue.source not in seen:
            seen.append(issue.source)
    if seen:
        return seen
    name = (result.tool_name or "").strip()
    if name and " + " not in name:
        return [name]
    return []


def _unique_code_rows(issues: list[Issue]) -> list[tuple[Issue, int]]:
    """Keep first instance of each (source, code), with occurrence counts."""
    groups: dict[tuple[str, str], list] = {}
    order: list[tuple[str, str]] = []
    for issue in issues:
        key = (issue.source or "", issue.code or "")
        if key not in groups:
            groups[key] = [issue, 1]
            order.append(key)
        else:
            groups[key][1] += 1
    return [(groups[key][0], int(groups[key][1])) for key in order]


def _issue_column_specs() -> tuple[tuple[str, int], ...]:
    return (
        (_("Severity"), 90),
        (_("Source"), 120),
        (_("Code"), 100),
        (_("Location"), 260),
        (_("Message"), 300),
    )


class IssuesList:
    """Issues table with a ListCtrl-like API over the platform-native control."""

    def __init__(self, parent: wx.Window, name: str) -> None:
        self._dataview = _USE_DATAVIEW_ISSUES
        if self._dataview:
            self.ctrl: wx.Window = dv.DataViewListCtrl(
                parent,
                style=dv.DV_SINGLE | dv.DV_ROW_LINES | wx.BORDER_SUNKEN,
            )
            assert isinstance(self.ctrl, dv.DataViewListCtrl)
            self.ctrl.SetName(name)
            for title, width in _issue_column_specs():
                self.ctrl.AppendTextColumn(
                    title, width=width, mode=dv.DATAVIEW_CELL_INERT
                )
        else:
            self.ctrl = wx.ListCtrl(
                parent,
                style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                name=name,
            )
            assert isinstance(self.ctrl, wx.ListCtrl)
            for idx, (title, width) in enumerate(_issue_column_specs()):
                self.ctrl.InsertColumn(idx, title, width=width)

    def SetName(self, name: str) -> None:
        self.ctrl.SetName(name)

    def SetDropTarget(self, target: wx.DropTarget) -> None:
        self.ctrl.SetDropTarget(target)

    def Bind(self, event, handler) -> None:
        self.ctrl.Bind(event, handler)

    def DeleteAllItems(self) -> None:
        # Both DataViewListCtrl and ListCtrl expose DeleteAllItems.
        self.ctrl.DeleteAllItems()  # type: ignore[attr-defined]

    def GetItemCount(self) -> int:
        return int(self.ctrl.GetItemCount())  # type: ignore[attr-defined]

    def AppendRow(
        self,
        severity: str,
        source: str,
        code: str,
        location: str,
        message: str,
    ) -> None:
        if self._dataview:
            assert isinstance(self.ctrl, dv.DataViewListCtrl)
            self.ctrl.AppendItem([severity, source, code, location, message])
            return
        assert isinstance(self.ctrl, wx.ListCtrl)
        idx = self.ctrl.InsertItem(self.ctrl.GetItemCount(), severity)
        self.ctrl.SetItem(idx, 1, source)
        self.ctrl.SetItem(idx, 2, code)
        self.ctrl.SetItem(idx, 3, location)
        self.ctrl.SetItem(idx, 4, message)

    def SetColumnTitles(self, titles: tuple[str, ...]) -> None:
        for idx, title in enumerate(titles):
            if self._dataview:
                assert isinstance(self.ctrl, dv.DataViewListCtrl)
                self.ctrl.GetColumn(idx).SetTitle(title)
            else:
                assert isinstance(self.ctrl, wx.ListCtrl)
                col = self.ctrl.GetColumn(idx)
                col.SetText(title)
                self.ctrl.SetColumn(idx, col)

    def EnsureRowFocus(self) -> None:
        if self.GetItemCount() <= 0:
            return
        if self._dataview:
            assert isinstance(self.ctrl, dv.DataViewListCtrl)
            if self.ctrl.GetSelectedRow() < 0:
                self.ctrl.SelectRow(0)
            self.ctrl.SetFocus()
            return
        assert isinstance(self.ctrl, wx.ListCtrl)
        if self.ctrl.GetFocusedItem() < 0:
            self.ctrl.Focus(0)
            self.ctrl.Select(0)

    def GetSelectedRow(self) -> int:
        if self._dataview:
            assert isinstance(self.ctrl, dv.DataViewListCtrl)
            return int(self.ctrl.GetSelectedRow())
        assert isinstance(self.ctrl, wx.ListCtrl)
        return int(self.ctrl.GetFirstSelected())


class IssueDetailDialog(wx.Dialog):
    """Full issue text in a keyboard-navigable, read-only view."""

    def __init__(
        self,
        parent: wx.Window,
        issue: Issue,
        *,
        count: int = 1,
        check_result: CheckResult | None = None,
    ) -> None:
        super().__init__(
            parent,
            title=_("Issue details"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self._issue = issue
        self._issue_count = max(1, int(count))
        self._check_result = check_result
        self._session = None
        self._busy = False
        self._ai_markdown = ""
        self._ai_plain = False
        self._show_ai = fido_settings_present()
        if self._show_ai:
            self.SetSize((780, 760))
            self.SetMinSize((700, 520))
        else:
            self.SetSize((640, 420))
            self.SetMinSize((480, 320))
        root = wx.BoxSizer(wx.VERTICAL)

        lines = [
            _("Severity: {value}", value=issue.severity.label),
            _("Source: {value}", value=issue.source or "—"),
            _("Code: {value}", value=issue.code or "—"),
        ]
        if count > 1:
            lines.append(_("Occurrences: {n}", n=count))
        lines.extend(
            [
                "",
                _("Location"),
                issue.location or _("(none)"),
                "",
                _("Message"),
                issue.message or _("(none)"),
            ]
        )
        body = "\n".join(lines)
        text = wx.TextCtrl(
            self,
            value=body,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.BORDER_SUNKEN,
            name=_("Issue details"),
        )
        text.SetMinSize((-1, 120))
        # With AI: details 1/3, explainer 2/3. Without AI: details fills the dialog.
        root.Add(text, 1, wx.EXPAND | wx.ALL, 12)

        if self._show_ai:
            ai_box = wx.StaticBox(self, label=_("Explain with AI"))
            ai_sizer = wx.StaticBoxSizer(ai_box, wx.VERTICAL)

            top_row = wx.BoxSizer(wx.HORIZONTAL)
            self.explain_btn = wx.Button(self, label=_("Explain with AI"))
            self.explain_btn.SetToolTip(
                _(
                    "Ask AI to explain this issue in plain language "
                    "(uses FIDO AI settings)"
                )
            )
            top_row.Add(self.explain_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

            model_label = wx.StaticText(self, label=_("Model:"))
            top_row.Add(model_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
            self.ai_model_ctrl = _FocusableReadOnlyText(
                self,
                value=self._ai_model_display_text(),
                style=wx.TE_READONLY | wx.BORDER_SUNKEN,
                name=_("AI model"),
            )
            self.ai_model_ctrl.SetToolTip(
                _("AI model selected in FIDO (read-only)")
            )
            _ensure_win_tab_stop(self.ai_model_ctrl)
            top_row.Add(self.ai_model_ctrl, 1, wx.EXPAND)
            ai_sizer.Add(top_row, 0, wx.EXPAND | wx.ALL, 6)

            self.ai_status = wx.StaticText(self, label="")
            # Give status a name so screen readers can find the announcement.
            self.ai_status.SetName(_("AI status"))
            ai_sizer.Add(self.ai_status, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            # Multiline TextCtrl (not HtmlWindow): screen readers can read it,
            # and it does not trap focus / Escape the way HtmlWindow can.
            # Use View in browser for formatted HTML.
            self.ai_output = wx.TextCtrl(
                self,
                value="",
                style=wx.TE_MULTILINE
                | wx.TE_READONLY
                | wx.TE_WORDWRAP
                | wx.BORDER_SUNKEN,
                name=_("AI explanation"),
            )
            self.ai_output.SetMinSize((-1, 240))
            ai_sizer.Add(self.ai_output, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            follow_row = wx.BoxSizer(wx.HORIZONTAL)
            self.followup_ctrl = wx.TextCtrl(
                self,
                value="",
                style=wx.TE_PROCESS_ENTER,
                name=_("Follow-up question"),
            )
            if hasattr(self.followup_ctrl, "SetHint"):
                self.followup_ctrl.SetHint(_("Ask a follow-up question…"))
            self.ask_btn = wx.Button(self, label=_("Ask"))
            self.ask_btn.Enable(False)
            follow_row.Add(self.followup_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
            follow_row.Add(self.ask_btn, 0)
            ai_sizer.Add(follow_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            root.Add(ai_sizer, 2, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

            self.explain_btn.Bind(wx.EVT_BUTTON, self._on_explain)
            self.ask_btn.Bind(wx.EVT_BUTTON, self._on_ask_followup)
            self.followup_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_ask_followup)
            self.Bind(EVT_EXPLAIN_AI, self._on_explain_ai_event)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        if self._show_ai:
            self.view_browser_btn = wx.Button(self, label=_("View in browser"))
            self.view_browser_btn.SetToolTip(
                _("Open the explanation in your web browser")
            )
            self.view_browser_btn.Enable(False)
            footer.Add(self.view_browser_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

            self.save_html_btn = wx.Button(self, label=_("Save as HTML…"))
            self.save_html_btn.SetToolTip(_("Save the explanation as an HTML file"))
            self.save_html_btn.Enable(False)
            footer.Add(self.save_html_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

            self.save_md_btn = wx.Button(self, label=_("Save as Markdown…"))
            self.save_md_btn.SetToolTip(_("Save the explanation as a Markdown file"))
            self.save_md_btn.Enable(False)
            footer.Add(self.save_md_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

            self.copy_ai_btn = wx.Button(self, label=_("Copy to clipboard"))
            self.copy_ai_btn.SetToolTip(
                _("Copy the explanation markdown to the clipboard")
            )
            self.copy_ai_btn.Enable(False)
            footer.Add(self.copy_ai_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

            self.view_browser_btn.Bind(wx.EVT_BUTTON, self._on_view_ai_browser)
            self.save_html_btn.Bind(wx.EVT_BUTTON, self._on_save_ai_html)
            self.save_md_btn.Bind(wx.EVT_BUTTON, self._on_save_ai_markdown)
            self.copy_ai_btn.Bind(wx.EVT_BUTTON, self._on_copy_ai_clipboard)

        footer.AddStretchSpacer(1)
        close_btn = wx.Button(self, id=wx.ID_CLOSE, label=_("Close"))
        close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        footer.Add(close_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(footer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(root)
        self.CentreOnParent()
        # Modal dialogs must EndModal — Close() alone can leave ShowModal hung.
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_dialog_char_hook)
        close_btn.SetDefault()
        text.SetFocus()
        text.SetInsertionPoint(0)

    def _on_dialog_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._on_close_dialog(event)
            return
        event.Skip()

    def _on_close_dialog(self, event: wx.Event | None = None) -> None:
        # Always allow closing, even while an explain request is in flight.
        if self.IsModal():
            self.EndModal(wx.ID_CLOSE)
        else:
            self.Destroy()

    def _ai_model_display_text(self) -> str:
        from .fido_settings import selected_model_service_string

        model = selected_model_service_string().strip()
        return model if model else _("(no model selected)")

    def _refresh_ai_model_display(self) -> None:
        if not self._show_ai:
            return
        self.ai_model_ctrl.SetValue(self._ai_model_display_text())

    def _has_ai_content(self) -> bool:
        return bool((self._ai_markdown or "").strip()) or self._ai_plain

    def _set_ai_export_enabled(self, enabled: bool) -> None:
        if not self._show_ai:
            return
        self.view_browser_btn.Enable(enabled)
        self.save_html_btn.Enable(enabled)
        self.save_md_btn.Enable(enabled)
        self.copy_ai_btn.Enable(enabled)

    def _ai_export_markdown(self) -> str:
        from .ai.markdown_html import export_explanation_markdown

        return export_explanation_markdown(
            self._issue,
            self._ai_markdown,
            count=self._issue_count,
        )

    def _ai_browser_html(self) -> str:
        from .ai.markdown_html import markdown_to_browser_page

        title = _("AI explanation")
        code = (self._issue.code or "").strip()
        if code:
            title = f"{title} — {code}"
        return markdown_to_browser_page(self._ai_export_markdown(), title=title)

    def _set_ai_content(self, markdown_text: str, *, plain: bool = False) -> None:
        from .ai.markdown_html import with_ai_disclaimer

        self._ai_plain = plain
        raw = markdown_text or ""
        self._ai_markdown = raw if plain else with_ai_disclaimer(raw)
        self.ai_output.SetValue(self._ai_markdown)
        self.ai_output.SetInsertionPoint(0)
        self._set_ai_export_enabled(self._has_ai_content())

    def _on_view_ai_browser(self, _event: wx.Event) -> None:
        if not self._has_ai_content():
            return
        try:
            fd, name = tempfile.mkstemp(
                prefix="checkmate-explain-",
                suffix=".html",
                text=True,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self._ai_browser_html())
            webbrowser.open(Path(name).as_uri())
            self.ai_status.SetLabel(_("Opened in browser."))
        except OSError as exc:
            wx.MessageBox(
                _("Could not open the explanation in a browser:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_save_ai_html(self, _event: wx.Event) -> None:
        if not self._has_ai_content():
            return
        from .ai.markdown_html import explanation_filename_stem

        stem = explanation_filename_stem(self._issue.code)
        with wx.FileDialog(
            self,
            _("Save AI explanation as HTML"),
            defaultFile=f"{stem}.html",
            wildcard=_("HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
        try:
            path.write_text(self._ai_browser_html(), encoding="utf-8")
            self.ai_status.SetLabel(_("Saved to {path}", path=path))
        except OSError as exc:
            wx.MessageBox(
                _("Could not save the explanation:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_save_ai_markdown(self, _event: wx.Event) -> None:
        if not self._has_ai_content():
            return
        from .ai.markdown_html import explanation_filename_stem

        stem = explanation_filename_stem(self._issue.code)
        with wx.FileDialog(
            self,
            _("Save AI explanation as Markdown"),
            defaultFile=f"{stem}.md",
            wildcard=_(
                "Markdown files (*.md)|*.md;*.markdown|All files (*.*)|*.*"
            ),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
        try:
            path.write_text(self._ai_export_markdown(), encoding="utf-8")
            self.ai_status.SetLabel(_("Saved to {path}", path=path))
        except OSError as exc:
            wx.MessageBox(
                _("Could not save the explanation:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_copy_ai_clipboard(self, _event: wx.Event) -> None:
        if not self._has_ai_content():
            return
        text = self._ai_markdown or ""
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()
            self.ai_status.SetLabel(_("Copied to clipboard."))
            # Modal confirmation so screen readers announce the result.
            wx.MessageBox(
                _("The explanation was copied to the clipboard."),
                _("Copied to clipboard"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            self.copy_ai_btn.SetFocus()
        else:
            wx.MessageBox(
                _("Could not copy to the clipboard."),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        if not self._show_ai:
            return
        self.explain_btn.Enable(not busy)
        self.ask_btn.Enable(not busy and self._session is not None)
        self.followup_ctrl.Enable(not busy)
        self._set_ai_export_enabled(not busy and self._has_ai_content())
        if status:
            self.ai_status.SetLabel(status)
        elif not busy:
            self.ai_status.SetLabel("")
        self.Layout()

    def _on_explain(self, _event: wx.Event) -> None:
        if self._busy:
            return
        self._set_busy(True, _("Explaining…"))
        issue = self._issue
        result = self._check_result

        def work() -> None:
            from .ai.explain import explain_issue

            try:
                out = explain_issue(issue, result)
            except Exception as exc:
                from .ai.explain import ExplainResult

                out = ExplainResult(ok=False, error_key="provider_error", text=str(exc))
            try:
                wx.PostEvent(self, ExplainAiEvent(kind="explain", result=out))
            except RuntimeError:
                # Dialog was closed while the request was running.
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_ask_followup(self, _event: wx.Event) -> None:
        if self._busy or self._session is None:
            return
        question = self.followup_ctrl.GetValue().strip()
        if not question:
            return
        self._set_busy(True, _("Thinking…"))
        session = self._session

        def work() -> None:
            from .ai.explain import ask_followup

            try:
                out = ask_followup(session, question)
            except Exception as exc:
                from .ai.explain import ExplainResult

                out = ExplainResult(
                    ok=False,
                    error_key="provider_error",
                    text=str(exc),
                    session=session,
                )
            try:
                wx.PostEvent(
                    self,
                    ExplainAiEvent(kind="followup", result=out, question=question),
                )
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_explain_ai_event(self, event: ExplainAiEvent) -> None:
        from .ai.explain import error_message_for_key
        from .ai.markdown_html import append_followup_markdown

        result = event.result
        kind = getattr(event, "kind", "explain")
        self._set_busy(False)
        self._refresh_ai_model_display()
        if not result.ok:
            msg = error_message_for_key(result.error_key, detail=result.text or "")
            self._set_ai_content(msg, plain=True)
            self.ai_status.SetLabel(_("Could not explain this issue."))
            if result.session is not None:
                self._session = result.session
                self.ask_btn.Enable(True)
            return

        self._session = result.session
        self.ask_btn.Enable(True)
        if kind == "followup":
            question = getattr(event, "question", "") or ""
            self._ai_markdown = append_followup_markdown(
                self._ai_markdown,
                heading=_("Follow-up"),
                question=question,
                answer=result.text or "",
            )
            self._set_ai_content(self._ai_markdown)
            self.followup_ctrl.SetValue("")
        else:
            self._set_ai_content(result.text or "")
        self.ai_status.SetLabel(_("Done"))
        self.ai_output.SetFocus()
        self.ai_output.SetInsertionPoint(0)


class AboutDialog(wx.Dialog):
    """About box with multiple clickable links (native AboutBox allows only one)."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            title=_("About CheckMate"),
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        root = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label=_("CheckMate"))
        title_font = title.GetFont()
        title_font.SetPointSize(title_font.GetPointSize() + 2)
        title_font.MakeBold()
        title.SetFont(title_font)
        root.Add(title, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        version = wx.StaticText(
            self, label=_("Version {version}", version=__version__)
        )
        root.Add(version, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        desc = wx.StaticText(
            self,
            label=_(
                "An accessible, cross-platform front-end for the DAISY "
                "eBraille Checker, W3C EPUBCheck, and veraPDF (PDF/UA)."
            ),
        )
        desc.Wrap(420)
        root.Add(desc, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        links_label = wx.StaticText(self, label=_("Links"))
        links_font = links_label.GetFont()
        links_font.MakeBold()
        links_label.SetFont(links_font)
        root.Add(links_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)

        links = wx.BoxSizer(wx.VERTICAL)
        for label, url in (
            (_("DAISY Consortium website"), DAISY_WEBSITE),
            (_("eBraille on the DAISY website"), EBRAILLE_STANDARD_PAGE),
            (_("eBraille specification"), EBRAILLE_SPEC_URL),
            (_("eBraille Checker"), CHECKER_REPO_PAGE),
            (_("EPUBCheck"), EPUBCHECK_REPO_PAGE),
            (_("veraPDF"), VERAPDF_HOME_PAGE),
        ):
            link = wx.adv.HyperlinkCtrl(self, label=label, url=url)
            link.SetName(label)
            links.Add(link, 0, wx.TOP, 6)
        root.Add(links, 0, wx.LEFT | wx.RIGHT, 16)

        buttons = self.CreateStdDialogButtonSizer(wx.OK)
        if buttons is not None:
            root.Add(buttons, 0, wx.EXPAND | wx.ALL, 16)

        self.SetSizer(root)
        self.Fit()
        self.CentreOnParent()


class PublicationDropTarget(wx.FileDropTarget):
    """Accept a publication file or folder dropped onto the window."""

    def __init__(self, frame: MainFrame) -> None:
        super().__init__()
        self.frame = frame

    def OnDropFiles(self, _x: int, _y: int, filenames: list[str]) -> bool:
        if not filenames:
            return False
        self.frame.open_publication_paths(filenames)
        return True


def parse_launch_paths(argv: list[str] | None = None) -> list[str]:
    """Return publication paths passed on the command line (Explorer / Finder / CLI)."""
    args = list(sys.argv[1:] if argv is None else argv)
    paths: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            paths.append(arg)
            continue
        if arg in ("--open", "-o"):
            skip_next = True
            continue
        # macOS Finder adds -psn_… when launching the .app
        if arg.startswith("-psn_"):
            continue
        if arg.startswith("-") and not Path(arg).exists():
            continue
        paths.append(arg)
    return paths


class EBrailleApp(wx.App):
    """wx app that accepts files from the shell (Windows args / macOS Open)."""

    def __init__(self, initial_paths: list[str] | None = None) -> None:
        self._pending_paths = list(initial_paths or [])
        self.frame: MainFrame | None = None
        super().__init__(False)

    def OnInit(self) -> bool:  # noqa: N802 — wx API
        self.frame = MainFrame(initial_paths=self._pending_paths)
        self._pending_paths.clear()
        self.frame.Show()
        return True

    def MacOpenFiles(self, fileNames: list[str]) -> None:  # noqa: N802 — wx API
        """Finder 'Open With' / double-click while the app is running or launching."""
        if not fileNames:
            return
        if self.frame is not None:
            wx.CallAfter(self.frame.open_publication_paths, list(fileNames))
        else:
            self._pending_paths.extend(fileNames)


class MainFrame(wx.Frame):
    def __init__(self, initial_paths: list[str] | None = None) -> None:
        load_language()
        super().__init__(
            None,
            title=app_title(),
            size=(860, 640),
        )
        self._last_result: CheckResult | None = None
        self._displayed_issues: list[Issue] = []
        self._displayed_counts: list[int] = []
        self._busy = False
        self._lang_menu_items: dict[str, wx.MenuItem] = {}
        self._initial_focus_pending = True
        self._pending_open_paths = list(initial_paths or [])
        self._apply_window_icon()
        self._build_ui()
        self._bind()
        self._enable_drag_drop()
        # Lightweight status only — detect_java() is too slow for the UI thread.
        self.SetStatusText(_("Starting…"))
        self.Centre()
        self.Layout()
        if sys.platform == "darwin":
            # On macOS, Cocoa assigns first responder to the path TextCtrl when
            # the window activates; wx.SetFocus cannot override that. Use native
            # makeFirstResponder once the frame becomes active.
            self.Bind(wx.EVT_ACTIVATE, self._on_initial_activate_focus)
        else:
            wx.CallAfter(self._focus_select_button)
        wx.CallAfter(self._startup_tasks)

    def _apply_window_icon(self) -> None:
        """Use the app icon in the title bar / task switcher when available."""
        try:
            if is_frozen() and sys.platform == "win32":
                icon = wx.Icon(sys.executable, wx.BITMAP_TYPE_ICO)
            else:
                icon_path = application_dir() / "installer" / "CheckMate.ico"
                if not icon_path.is_file():
                    return
                icon = wx.Icon(str(icon_path), wx.BITMAP_TYPE_ICO)
            if icon.IsOk():
                self.SetIcon(icon)
        except Exception:  # noqa: BLE001 — icon is cosmetic; never block startup
            pass

    def _set_result_title(self, summary: str | None = None) -> None:
        """Append the verdict to the app name in the title bar."""
        if summary:
            clean = " ".join(summary.split())
            if len(clean) > 80:
                clean = clean[:77] + "…"
            self.SetTitle(f"{app_title()} — {clean}")
        else:
            self.SetTitle(app_title())

    def _set_result_accessible_name(self, text: str) -> None:
        """Include the verdict in the accessible name so Tab announces it."""
        spoken = " ".join(text.split())
        self.result_label.SetName(_("Check result: {text}", text=spoken))

    def _show_result_text(
        self,
        display: str,
        *,
        title: str | None = None,
        focus: bool = False,
        update_title: bool = True,
        verdict: Verdict | None = None,
    ) -> None:
        """Update the result pane value, accessible name, colors, and optionally the title."""
        self.result_label.ChangeValue(display)
        self._set_result_accessible_name(display)
        self._set_result_colors(verdict)
        if update_title:
            self._set_result_title(title)
        if focus:
            self._announce_result_pane()
        elif self.result_label.HasFocus():
            # The text changed under focus (e.g. progress updates): re-select
            # it so screen readers announce the new message.
            wx.CallAfter(self._prepare_result_for_review)

    def _announce_result_pane(self) -> None:
        """Force a focus leave/enter so screen readers re-announce updated text.

        Changing the value while the control already has focus (e.g. after
        'Checking…') often produces no new announcement. Parking focus briefly
        elsewhere then returning triggers a fresh focus event.
        """
        if self.result_label.HasFocus():
            if self.select_file_btn.IsEnabled():
                self._focus_select_button()
                wx.CallAfter(self._return_focus_to_result)
            else:
                # Busy (buttons disabled): can't park focus on a disabled
                # button, so collapse and re-select instead — screen readers
                # announce the fresh selection.
                self.result_label.SetSelection(0, 0)
                wx.CallAfter(self._prepare_result_for_review)
        else:
            self.result_label.SetFocus()
            wx.CallAfter(self._prepare_result_for_review)

    def _return_focus_to_result(self) -> None:
        self.result_label.SetFocus()
        wx.CallAfter(self._prepare_result_for_review)

    def _set_result_colors(self, verdict: Verdict | None) -> None:
        """Color the result pane by verdict; None restores system defaults."""
        if verdict is None:
            self.result_label.SetForegroundColour(wx.NullColour)
            self.result_label.SetBackgroundColour(wx.NullColour)
            self.result_label.Refresh()
            return
        # Dark text + soft tint: readable on light themes; wording still carries meaning.
        fg, bg = {
            Verdict.PASSED: (wx.Colour(0, 110, 45), wx.Colour(228, 245, 231)),
            Verdict.PASSED_WITH_WARNINGS: (
                wx.Colour(150, 85, 0),
                wx.Colour(255, 242, 220),
            ),
            Verdict.FAILED: (wx.Colour(160, 25, 25), wx.Colour(252, 228, 228)),
            Verdict.ERROR: (wx.Colour(160, 25, 25), wx.Colour(252, 228, 228)),
        }[verdict]
        self.result_label.SetForegroundColour(fg)
        self.result_label.SetBackgroundColour(bg)
        self.result_label.Refresh()

    def _prepare_result_for_review(self) -> None:
        """Select all so screen readers announce the result text on focus."""
        if not self.result_label.HasFocus():
            return
        end = self.result_label.GetLastPosition()
        if end <= 0:
            return
        self.result_label.SetSelection(0, end)

    def on_result_focus(self, event: wx.FocusEvent) -> None:
        event.Skip()
        wx.CallAfter(self._prepare_result_for_review)

    def _focus_select_button(self) -> None:
        if sys.platform == "darwin":
            # wx.SetFocus is unreliable on macOS whenever a TextCtrl is present;
            # Cocoa keeps/returns first responder to the text field.
            if not _macos_make_first_responder(self.select_file_btn):
                self.select_file_btn.SetFocus()
            return
        self.select_file_btn.SetFocus()

    def _focus_select_button_if_still_on_path(self) -> None:
        """Initial-focus retry: only steal focus back from the path field."""
        focused = wx.Window.FindFocus()
        if focused is None or focused is self.path_ctrl:
            self._focus_select_button()

    def _on_initial_activate_focus(self, event: wx.ActivateEvent) -> None:
        event.Skip()
        if not event.GetActive() or not self._initial_focus_pending:
            return
        self._initial_focus_pending = False
        self.Unbind(wx.EVT_ACTIVATE, handler=self._on_initial_activate_focus)
        # Defer past Cocoa's default first-responder assignment to the path field.
        wx.CallLater(50, self._focus_select_button)
        wx.CallLater(300, self._focus_select_button_if_still_on_path)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        # --- Input ---
        self.publication_box = wx.StaticBox(panel, label=_("Publication"))
        input_sizer = wx.StaticBoxSizer(self.publication_box, wx.VERTICAL)

        path_row = wx.BoxSizer(wx.HORIZONTAL)
        self.path_label = wx.StaticText(panel, label=_("Path:"))
        # Create the primary action before the path field so it appears earlier
        # in the macOS accessibility tree (VoiceOver often starts there).
        self.select_file_btn = wx.Button(panel, label=_("Select &file…"))
        self.select_file_btn.SetName(_("Select file"))
        self.select_file_btn.SetToolTip(
            _("Select a packaged publication (Ctrl+O)")
        )
        self.select_folder_btn = wx.Button(panel, label=_("Select f&older…"))
        self.select_folder_btn.SetName(_("Select folder"))
        self.select_folder_btn.SetToolTip(
            _("Select an exploded publication folder (Ctrl+Shift+O)")
        )
        self.path_ctrl = wx.TextCtrl(
            panel, style=wx.TE_PROCESS_ENTER, name=_("Publication")
        )
        self.path_ctrl.SetHint(
            _(
                "Select or drop a .ebrl / .epub / .pdf file or folder — "
                "checking starts automatically"
            )
        )
        # Keep visual/tab order: path → select file → select folder.
        self.path_ctrl.MoveBeforeInTabOrder(self.select_file_btn)
        path_row.Add(self.path_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        path_row.Add(self.path_ctrl, 1, wx.EXPAND | wx.RIGHT, 8)
        path_row.Add(self.select_file_btn, 0, wx.RIGHT, 4)
        path_row.Add(self.select_folder_btn, 0)

        input_sizer.Add(path_row, 0, wx.EXPAND | wx.ALL, 8)
        root.Add(input_sizer, 0, wx.EXPAND | wx.ALL, 10)

        # --- Result ---
        self.result_box = wx.StaticBox(panel, label=_("Result"))
        result_sizer = wx.StaticBoxSizer(self.result_box, wx.VERTICAL)
        self.result_label = wx.TextCtrl(
            panel,
            value=_("No check run yet."),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP | wx.BORDER_SUNKEN,
            name=_("Check result"),
        )
        font = self.result_label.GetFont()
        font.SetPointSize(font.GetPointSize() + 2)
        font.MakeBold()
        self.result_label.SetFont(font)
        self.result_label.SetMinSize((-1, 72))
        self.result_label.Bind(wx.EVT_SET_FOCUS, self.on_result_focus)
        self._set_result_accessible_name(_("No check run yet."))
        result_sizer.Add(self.result_label, 0, wx.EXPAND | wx.ALL, 8)
        root.Add(result_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # --- Issues ---
        self.issues_box = wx.StaticBox(panel, label=_("Issues"))
        issues_sizer = wx.StaticBoxSizer(self.issues_box, wx.VERTICAL)
        filter_row = wx.BoxSizer(wx.HORIZONTAL)
        self.filter_label = wx.StaticText(panel, label=_("Filter:"))
        self.filter_choice = wx.Choice(panel, choices=list(filter_choices()))
        self.filter_choice.SetSelection(0)
        self.filter_choice.SetName(_("Issue filter"))
        self.source_label = wx.StaticText(panel, label=_("Source:"))
        self.source_choice = wx.Choice(
            panel, choices=list(source_filter_choices())
        )
        self.source_choice.SetSelection(0)
        self.source_choice.SetName(_("Issue source filter"))
        self.source_choice.SetToolTip(
            _("Show issues from a specific checker, or all")
        )
        # Populated from the last result's checker name(s).
        self._source_filter_names: list[str] = []
        # Only shown for multi-checker runs (EPUBCheck + Ace).
        self.source_label.Hide()
        self.source_choice.Hide()
        self.unique_codes_cb = wx.CheckBox(
            panel, label=_("Show one example of each issue")
        )
        self.unique_codes_cb.SetName(
            _("Show one example of each issue")
        )
        self.unique_codes_cb.SetToolTip(
            _(
                "List each issue code once, with a count of how many "
                "times it occurred. Useful when one rule has many instances."
            )
        )
        self.unique_codes_cb.SetValue(
            bool(read_settings().get("unique_codes", False))
        )
        self.copy_btn = wx.Button(panel, label=_("&Copy summary"))
        self.copy_btn.SetToolTip(_("Copy the result summary (Ctrl+Shift+C)"))
        self.report_btn = wx.Button(panel, label=_("&Report…"))
        self.report_btn.SetToolTip(
            _("View or save reports, copy the summary, or view the full log")
        )
        self.filter_row = filter_row
        filter_row.Add(self.filter_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        filter_row.Add(
            self.filter_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12
        )
        filter_row.Add(self.source_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        filter_row.Add(
            self.source_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12
        )
        filter_row.Show(self.source_label, False)
        filter_row.Show(self.source_choice, False)
        filter_row.Add(
            self.unique_codes_cb, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12
        )
        filter_row.AddStretchSpacer(1)
        filter_row.Add(self.copy_btn, 0, wx.RIGHT, 4)
        filter_row.Add(self.report_btn, 0)

        self.issues_list = IssuesList(panel, name=_("Issues list"))
        self.issues_list.Bind(wx.EVT_SET_FOCUS, self.on_issues_list_focus)
        self.issues_list.Bind(wx.EVT_CHILD_FOCUS, self.on_issues_list_focus)
        if _USE_DATAVIEW_ISSUES:
            self.issues_list.Bind(
                dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.on_issue_activated
            )
        else:
            self.issues_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_issue_activated)
        self.issues_list.Bind(wx.EVT_CHAR_HOOK, self.on_issues_char_hook)
        self.issues_hint = wx.StaticText(
            panel,
            label=_("Press Enter or double-click an issue to read the full details."),
        )
        self.issues_hint.SetName(_("Issues hint"))

        issues_sizer.Add(filter_row, 0, wx.EXPAND | wx.ALL, 8)
        issues_sizer.Add(
            self.issues_list.ctrl,
            1,
            wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
            8,
        )
        issues_sizer.Add(
            self.issues_hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8
        )
        root.Add(issues_sizer, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        panel.SetSizer(root)
        self.panel = panel
        self.root_sizer = root

        self._build_menubar()
        self.CreateStatusBar(1)
        self.SetStatusText(_("Ready"))

    def _build_menubar(self) -> None:
        menubar = wx.MenuBar()
        file_menu = wx.Menu()
        self.menu_open_file = file_menu.Append(
            wx.ID_OPEN, _("Select &file…\tCtrl+O")
        )
        self.menu_open_folder = file_menu.Append(
            wx.ID_ANY, _("Select f&older…\tCtrl+Shift+O")
        )
        file_menu.AppendSeparator()
        self.menu_exit = file_menu.Append(wx.ID_EXIT, _("E&xit\tEsc"))
        menubar.Append(file_menu, _("&File"))

        report_menu = wx.Menu()
        self.menu_view_text = report_menu.Append(
            wx.ID_ANY, _("View &text report\tCtrl+T")
        )
        self.menu_save_text = report_menu.Append(
            wx.ID_ANY, _("Save &text report…\tCtrl+Shift+S")
        )
        report_menu.AppendSeparator()
        self.menu_view_html = report_menu.Append(
            wx.ID_ANY, _("View &HTML report in browser\tCtrl+H")
        )
        self.menu_save_html = report_menu.Append(
            wx.ID_SAVEAS, _("Save &HTML report…\tCtrl+S")
        )
        report_menu.AppendSeparator()
        self.menu_copy = report_menu.Append(
            wx.ID_COPY, _("&Copy summary\tCtrl+Shift+C")
        )
        self.menu_clear = report_menu.Append(
            wx.ID_CLEAR, _("C&lear results\tCtrl+Shift+N")
        )
        report_menu.AppendSeparator()
        self.menu_view_log = report_menu.Append(
            wx.ID_ANY, _("View full &log\tCtrl+L")
        )
        self._report_menu_index = menubar.GetMenuCount()
        menubar.Append(report_menu, _("&Report"))

        tools_menu = wx.Menu()
        self.menu_check = tools_menu.Append(
            wx.ID_ANY, _("&Re-check publication\tF5")
        )
        tools_menu.AppendSeparator()
        self.menu_update = tools_menu.Append(
            wx.ID_ANY, _("Check for &updates…")
        )
        self.menu_install = tools_menu.Append(
            wx.ID_ANY, _("&Download / reinstall checkers…")
        )
        menubar.Append(tools_menu, _("&Tools"))

        lang_menu = wx.Menu()
        self._lang_menu_items = {}
        current = get_language()
        for code, label in LANGUAGES.items():
            item = lang_menu.AppendRadioItem(wx.ID_ANY, label)
            self._lang_menu_items[code] = item
            if code == current:
                item.Check(True)
            self.Bind(
                wx.EVT_MENU,
                lambda _e, lang=code: self.on_language_selected(lang),
                item,
            )
        menubar.Append(lang_menu, _("&Language"))

        help_menu = wx.Menu()
        self.menu_about = help_menu.Append(wx.ID_ABOUT, _("&About"))
        menubar.Append(help_menu, _("&Help"))
        self.SetMenuBar(menubar)
        self._bind_menus()
        self._update_report_actions_enabled()

    def _update_report_actions_enabled(self) -> None:
        """Enable Report menu / button only when a check result exists.

        Uses both per-item Enable and EnableTop so the top-level Report menu
        greys out correctly on Windows and macOS.
        """
        enabled = self._last_result is not None
        for item in (
            self.menu_view_text,
            self.menu_save_text,
            self.menu_view_html,
            self.menu_save_html,
            self.menu_copy,
            self.menu_clear,
            self.menu_view_log,
        ):
            item.Enable(enabled)
        self.report_btn.Enable(enabled)
        menubar = self.GetMenuBar()
        if menubar is None:
            return
        idx = getattr(self, "_report_menu_index", -1)
        if 0 <= idx < menubar.GetMenuCount():
            menubar.EnableTop(idx, enabled)

    def _bind(self) -> None:
        self.select_file_btn.Bind(wx.EVT_BUTTON, self.on_browse_file)
        self.select_folder_btn.Bind(wx.EVT_BUTTON, self.on_browse_folder)
        self.copy_btn.Bind(wx.EVT_BUTTON, self.on_copy_summary)
        self.report_btn.Bind(wx.EVT_BUTTON, self.on_report_button)
        self.filter_choice.Bind(wx.EVT_CHOICE, self.on_filter_changed)
        self.source_choice.Bind(wx.EVT_CHOICE, self.on_filter_changed)
        self.unique_codes_cb.Bind(wx.EVT_CHECKBOX, self.on_unique_codes_changed)
        self.path_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_check)
        self._bind_menus()

        self.Bind(EVT_PROGRESS, self.on_progress_event)
        self.Bind(EVT_RESULT, self.on_result_event)
        self.Bind(EVT_UPDATE_INFO, self.on_update_info_event)
        self.Bind(EVT_INSTALL_DONE, self.on_install_done_event)
        self.Bind(EVT_JAVA_MISSING, self.on_java_missing_event)

    def _bind_menus(self) -> None:
        self.Bind(wx.EVT_MENU, self.on_browse_file, self.menu_open_file)
        self.Bind(wx.EVT_MENU, self.on_browse_folder, self.menu_open_folder)
        self.Bind(wx.EVT_MENU, self.on_view_text_report, self.menu_view_text)
        self.Bind(wx.EVT_MENU, self.on_save_text_report, self.menu_save_text)
        self.Bind(wx.EVT_MENU, self.on_view_html_report, self.menu_view_html)
        self.Bind(wx.EVT_MENU, self.on_save_html_report, self.menu_save_html)
        self.Bind(wx.EVT_MENU, self.on_copy_summary, self.menu_copy)
        self.Bind(wx.EVT_MENU, self.on_clear_results, self.menu_clear)
        self.Bind(wx.EVT_MENU, self.on_check, self.menu_check)
        self.Bind(wx.EVT_MENU, self.on_view_full_log, self.menu_view_log)
        self.Bind(wx.EVT_MENU, self.on_check_updates, self.menu_update)
        self.Bind(wx.EVT_MENU, self.on_reinstall_checker, self.menu_install)
        self.Bind(wx.EVT_MENU, self.on_about, self.menu_about)
        self.Bind(wx.EVT_MENU, lambda _e: self.Close(), id=wx.ID_EXIT)
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [(wx.ACCEL_NORMAL, wx.WXK_ESCAPE, wx.ID_EXIT)]
            )
        )

    def on_language_selected(self, lang: str) -> None:
        if lang == get_language():
            return
        set_language(lang)
        self._apply_ui_language()

    def _apply_ui_language(self) -> None:
        """Refresh visible UI strings after a language change."""
        filter_sel = self.filter_choice.GetSelection()
        self.publication_box.SetLabel(_("Publication"))
        self.path_label.SetLabel(_("Path:"))
        self.path_ctrl.SetHint(
            _(
                "Select or drop a .ebrl / .epub / .pdf file or folder — "
                "checking starts automatically"
            )
        )
        self.select_file_btn.SetLabel(_("Select &file…"))
        self.select_file_btn.SetName(_("Select file"))
        self.select_file_btn.SetToolTip(
            _("Select a packaged publication (Ctrl+O)")
        )
        self.select_folder_btn.SetLabel(_("Select f&older…"))
        self.select_folder_btn.SetName(_("Select folder"))
        self.select_folder_btn.SetToolTip(
            _("Select an exploded publication folder (Ctrl+Shift+O)")
        )
        self.result_box.SetLabel(_("Result"))
        self.result_label.SetName(_("Check result"))
        self._set_result_accessible_name(self.result_label.GetValue())
        self.issues_box.SetLabel(_("Issues"))
        self.filter_label.SetLabel(_("Filter:"))
        self.filter_choice.SetName(_("Issue filter"))
        self.filter_choice.Set(list(filter_choices()))
        if 0 <= filter_sel < self.filter_choice.GetCount():
            self.filter_choice.SetSelection(filter_sel)
        prev_source = self._selected_source_name()
        self.source_label.SetLabel(_("Source:"))
        self.source_choice.SetName(_("Issue source filter"))
        self.source_choice.SetToolTip(
            _("Show issues from a specific checker, or all")
        )
        self._sync_source_filter_choices(prefer=prev_source)
        self.unique_codes_cb.SetLabel(
            _("Show one example of each issue")
        )
        self.unique_codes_cb.SetName(
            _("Show one example of each issue")
        )
        self.unique_codes_cb.SetToolTip(
            _(
                "List each issue code once, with a count of how many "
                "times it occurred. Useful when one rule has many instances."
            )
        )
        self.copy_btn.SetLabel(_("&Copy summary"))
        self.copy_btn.SetToolTip(_("Copy the result summary (Ctrl+Shift+C)"))
        self.report_btn.SetLabel(_("&Report…"))
        self.report_btn.SetToolTip(
            _("View or save reports, copy the summary, or view the full log")
        )
        self.issues_list.SetName(_("Issues list"))
        self.issues_list.SetColumnTitles(
            (_("Severity"), _("Source"), _("Code"), _("Location"), _("Message"))
        )
        self.issues_hint.SetLabel(
            _("Press Enter or double-click an issue to read the full details.")
        )
        self.issues_hint.SetName(_("Issues hint"))
        self._build_menubar()
        if self._last_result is not None:
            self._refresh_result_text()
        else:
            self._show_result_text(_("No check run yet."), title=None)
        self._update_status_bar()
        self.panel.Layout()
        self.Layout()

    def _refresh_result_text(self) -> None:
        result = self._last_result
        if result is None:
            return
        self._show_result_text(result.result_display, title=result.headline, verdict=result.verdict)
        self._populate_issues()

    def _enable_drag_drop(self) -> None:
        # Attach to the panel and key children so drops work across the UI
        for window in (
            self.panel,
            self.path_ctrl,
            self.issues_list,
            self.result_label,
        ):
            window.SetDropTarget(PublicationDropTarget(self))

    # --- Startup ---

    def _startup_tasks(self) -> None:
        # Let the first paint finish, then do Java/checker work off the UI thread.
        self.Layout()
        self.Refresh()

        def worker() -> None:
            try:
                if detect_java() is None:
                    wx.PostEvent(self, JavaMissingEvent())

                def progress(msg: str) -> None:
                    wx.PostEvent(self, ProgressEvent(message=msg))

                ensure_tools_installed(progress=progress)
                # One-time after install/upgrade: run each tool once so AV
                # scans (Defender) happen now, not during the first check.
                run_startup_warmup(progress=progress)
                wx.PostEvent(self, ProgressEvent(message=_("Ready")))
                try:
                    updates = check_for_updates()
                    available = any(u.available for u in updates)
                    wx.PostEvent(
                        self,
                        UpdateInfoEvent(
                            updates=updates,
                            available=available,
                            silent=True,
                            error=None,
                            force=False,
                        ),
                    )
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001
                wx.PostEvent(
                    self,
                    ProgressEvent(message=f"Checker setup failed: {exc}"),
                )

        threading.Thread(target=worker, daemon=True).start()

    def on_java_missing_event(self, _event: JavaMissingEvent) -> None:
        from .java_util import has_bundled_java

        if has_bundled_java():
            message = _(
                "The bundled Java runtime could not be started.\n\n"
                "On macOS, reinstall from a current signed build. "
                "You can also install a system Java Runtime (JRE 17+) "
                "as a temporary workaround."
            )
        else:
            message = _(
                "Java was not found.\n\n"
                "If you are running from source, install a Java Runtime "
                "(JRE 17 or newer recommended) and ensure java is on your PATH.\n\n"
                "If you received a packaged build, reinstall from the full "
                "distribution folder — it should include a runtime/ directory "
                "with a bundled JRE.\n\n"
                "The checker itself can still be downloaded, but checks "
                "cannot run without Java."
            )
        wx.MessageBox(
            message,
            _("Java required"),
            wx.OK | wx.ICON_WARNING,
            self,
        )
        self._focus_select_button()

    # --- Helpers ---

    def _update_status_bar(self) -> None:
        """Status bar is reserved for checker/Java version info only."""
        self.SetStatusText(checker_status_text())

    def _restore_result_display(self) -> None:
        """Restore the result area after temporary progress messages."""
        if self._last_result is not None:
            self._refresh_result_text()
        else:
            self._show_result_text(_("No check run yet."), title=None)

    def _clear_to_launch_state(self) -> None:
        """Reset the UI to the same state as a fresh launch."""
        self._last_result = None
        self._displayed_issues = []
        self._displayed_counts = []
        self.path_ctrl.ChangeValue("")
        self._show_result_text(_("No check run yet."), title=None)
        self.issues_list.DeleteAllItems()
        self.filter_choice.SetSelection(0)
        self._source_filter_names = []
        self.source_choice.Set(list(source_filter_choices()))
        self.source_choice.SetSelection(0)
        self._set_source_filter_visible(False)
        self._update_report_actions_enabled()
        self._update_status_bar()
        self._focus_select_button()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.select_file_btn.Enable(not busy)
        self.select_folder_btn.Enable(not busy)
        self.path_ctrl.Enable(not busy)

    def _current_path(self) -> Path | None:
        text = self.path_ctrl.GetValue().strip().strip('"')
        if not text:
            return None
        return Path(text)

    def open_publication_paths(
        self,
        filenames: list[str],
        *,
        notify_if_busy: bool = False,
    ) -> None:
        """Open paths from drop, CLI, or Finder; run check on the first usable one."""
        if not filenames:
            return
        if self._busy:
            if notify_if_busy:
                wx.MessageBox(
                    _(
                        "A check is already running. Wait for it to finish, then drop again."
                    ),
                    _("Busy"),
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
                return
            self._pending_open_paths.extend(filenames)
            return

        paths = [Path(name) for name in filenames]
        chosen: Path | None = None
        for path in paths:
            if is_checkable_path(path):
                chosen = path
                break

        if chosen is None:
            wx.MessageBox(
                _(
                    "Drop a packaged .ebrl, .epub, or .pdf file, or an exploded "
                    "eBraille/EPUB publication folder."
                ),
                _("Unsupported drop"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        if len(paths) > 1:
            wx.MessageBox(
                _(
                    "Using first publication ({name}); ignored {count} other item(s).",
                    name=chosen.name,
                    count=len(paths) - 1,
                ),
                _("Multiple items"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )

        self.path_ctrl.SetValue(str(chosen))
        self.on_check(None)

    def open_dropped_paths(self, filenames: list[str]) -> None:
        """Handle paths dropped onto the window."""
        self.open_publication_paths(filenames, notify_if_busy=True)

    def _flush_pending_open_paths(self) -> None:
        if self._busy or not self._pending_open_paths:
            return
        pending = self._pending_open_paths
        self._pending_open_paths = []
        self.open_publication_paths(pending)

    def _populate_issues(self) -> None:
        self.issues_list.DeleteAllItems()
        self._displayed_issues = []
        self._displayed_counts = []
        result = self._last_result
        if result is None:
            return
        filter_idx = self.filter_choice.GetSelection()
        source_name = (
            self._selected_source_name() if self.source_choice.IsShown() else None
        )
        filtered: list[Issue] = []
        for issue in result.issues:
            if filter_idx == 1 and issue.severity not in (
                Severity.FATAL,
                Severity.ERROR,
            ):
                continue
            if filter_idx == 2 and issue.severity != Severity.WARNING:
                continue
            if filter_idx == 3 and issue.severity not in (
                Severity.INFO,
                Severity.USAGE,
            ):
                continue
            if source_name and issue.source != source_name:
                continue
            filtered.append(issue)

        rows: list[tuple[Issue, int]]
        if self.unique_codes_cb.GetValue():
            rows = _unique_code_rows(filtered)
        else:
            rows = [(issue, 1) for issue in filtered]

        for issue, count in rows:
            self._displayed_issues.append(issue)
            self._displayed_counts.append(count)
            code = issue.code
            if count > 1:
                code = _("{code} ×{n}", code=code, n=count)
            self.issues_list.AppendRow(
                issue.severity.label,
                issue.source or "—",
                code,
                issue.location,
                issue.message,
            )
        # Do not Focus()/Select() here — that steals keyboard focus from the
        # result pane and prevents screen readers from announcing the verdict.

    def _selected_issue(self) -> Issue | None:
        row = self.issues_list.GetSelectedRow()
        if row < 0 or row >= len(self._displayed_issues):
            return None
        return self._displayed_issues[row]

    def _selected_issue_count(self) -> int:
        row = self.issues_list.GetSelectedRow()
        if row < 0 or row >= len(self._displayed_counts):
            return 1
        return self._displayed_counts[row]

    def _show_issue_details(self, issue: Issue | None = None) -> None:
        if issue is None:
            issue = self._selected_issue()
            count = self._selected_issue_count()
        else:
            try:
                count = self._displayed_counts[self._displayed_issues.index(issue)]
            except ValueError:
                count = 1
        if issue is None:
            return
        dlg = IssueDetailDialog(
            self,
            issue,
            count=count,
            check_result=self._last_result,
        )
        dlg.ShowModal()
        dlg.Destroy()

    def on_issue_activated(self, _event: wx.Event) -> None:
        self._show_issue_details()

    def on_issues_char_hook(self, event: wx.KeyEvent) -> None:
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE):
            if self._selected_issue() is not None:
                self._show_issue_details()
                return
        event.Skip()

    def _selected_source_name(self) -> str | None:
        """Return the selected checker name, or None for EPUBCheck + Ace."""
        idx = self.source_choice.GetSelection()
        if idx <= 0:
            return None
        names = self._source_filter_names
        if idx > len(names):
            return None
        return names[idx - 1]

    def _set_source_filter_visible(self, visible: bool) -> None:
        """Show or hide the Source filter (only useful for multi-checker runs)."""
        self.filter_row.Show(self.source_label, visible)
        self.filter_row.Show(self.source_choice, visible)
        self.source_label.Enable(visible)
        self.source_choice.Enable(visible)
        self.panel.Layout()

    def _sync_source_filter_choices(
        self,
        *,
        prefer: str | None = None,
        reset_to_all: bool = False,
    ) -> None:
        """Rebuild Source choices from the last result's checker name(s)."""
        result = self._last_result
        sources = _result_source_names(result) if result is not None else []
        self._source_filter_names = sources
        self.source_choice.Set(list(source_filter_choices(sources)))
        # Only EPUBCheck + Ace is a multi-checker run; hide for single tools
        # (.ebrl, .pdf, DAISY/DTBook via Pipeline, EPUBCheck-only, etc.).
        visible = "EPUBCheck" in sources and "Ace" in sources
        self._set_source_filter_visible(visible)
        if not visible or reset_to_all or prefer is None:
            self.source_choice.SetSelection(0)
            return
        try:
            self.source_choice.SetSelection(sources.index(prefer) + 1)
        except ValueError:
            self.source_choice.SetSelection(0)

    def _apply_result(self, result: CheckResult) -> None:
        self._last_result = result
        # Update content first (issues list must not steal focus afterward).
        self._show_result_text(
            result.result_display,
            title=result.headline,
            focus=False,
            verdict=result.verdict,
        )
        # Source filter lists the checker(s) that produced this result.
        self._sync_source_filter_choices(prefer=None, reset_to_all=True)
        self._populate_issues()
        self._update_report_actions_enabled()
        self._update_status_bar()
        self._announce_result_pane()

    def _summary_text(self) -> str:
        result = self._last_result
        if result is None:
            return ""
        return format_text_report(result, include_full_log=False)

    # --- Events ---

    def on_browse_file(self, _event: wx.CommandEvent) -> None:
        with wx.FileDialog(
            self,
            _("Select an eBraille, EPUB, or PDF publication"),
            wildcard=_(
                "Publications (*.ebrl;*.epub;*.pdf)|"
                "*.ebrl;*.Ebrl;*.EBRL;*.epub;*.EPUB;*.pdf;*.PDF|"
                "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
                "EPUB (*.epub)|*.epub;*.EPUB|"
                "PDF (*.pdf)|*.pdf;*.PDF|"
                "All files (*.*)|*.*"
            ),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self.path_ctrl.SetValue(dlg.GetPath())
            self.on_check(None)

    def on_browse_folder(self, _event: wx.CommandEvent) -> None:
        with wx.DirDialog(
            self,
            _("Select an exploded eBraille or EPUB publication folder"),
            style=wx.DD_DIR_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self.path_ctrl.SetValue(dlg.GetPath())
            self.on_check(None)

    def on_check(self, _event: wx.CommandEvent | None) -> None:
        if self._busy:
            return
        path = self._current_path()
        if path is None:
            wx.MessageBox(
                _("Select a publication file or folder first."),
                _("Nothing to check"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            self._focus_select_button()
            return
        if not path.exists():
            wx.MessageBox(
                _("Path not found:\n{path}", path=path),
                _("Invalid path"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        self._set_busy(True)
        # Focus the result pane so "Checking…" is announced; results will
        # leave-and-refocus the same control when they arrive.
        self._show_result_text(
            _("Checking…"), title=_("Checking…"), focus=True
        )
        self.issues_list.DeleteAllItems()

        def worker() -> None:
            def progress(msg: str) -> None:
                wx.PostEvent(self, ProgressEvent(message=msg))

            result = run_check(path, exploded=None, progress=progress)
            wx.PostEvent(self, ResultEvent(result=result))

        threading.Thread(target=worker, daemon=True).start()

    def on_progress_event(self, event: ProgressEvent) -> None:
        # Keep the status bar on version info; show progress in the result area.
        if event.message == _("Ready"):
            self._update_status_bar()
            # Prefer starting a shell-opened publication over resetting to idle.
            if self._pending_open_paths:
                self._flush_pending_open_paths()
            elif not self._busy:
                self._restore_result_display()
            return
        # While a check runs, move focus into the result pane so each progress
        # message ("Checking with Ace…", …) is announced by screen readers.
        self._show_result_text(event.message, update_title=False, focus=self._busy)

    def on_result_event(self, event: ResultEvent) -> None:
        self._set_busy(False)
        self._apply_result(event.result)
        self._flush_pending_open_paths()

    def on_filter_changed(self, _event: wx.CommandEvent) -> None:
        self._populate_issues()

    def on_unique_codes_changed(self, _event: wx.CommandEvent) -> None:
        update_settings(unique_codes=bool(self.unique_codes_cb.GetValue()))
        self._populate_issues()

    def on_issues_list_focus(self, event: wx.FocusEvent) -> None:
        event.Skip()
        # Tabbing into the list often lands on the header first; move to row 0.
        wx.CallAfter(self.issues_list.EnsureRowFocus)

    def on_copy_summary(self, _event: wx.CommandEvent) -> None:
        text = self._summary_text()
        if not text:
            wx.MessageBox(
                _("Run a check first."),
                _("Nothing to copy"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()

    def on_clear_results(self, _event: wx.CommandEvent) -> None:
        if self._busy:
            wx.MessageBox(
                _(
                    "A check is already running. Wait for it to finish, then clear."
                ),
                _("Busy"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        self._clear_to_launch_state()

    def on_report_button(self, _event: wx.CommandEvent) -> None:
        menu = wx.Menu()
        view_text = menu.Append(wx.ID_ANY, _("View &text report\tCtrl+T"))
        save_text = menu.Append(wx.ID_ANY, _("Save &text report…\tCtrl+Shift+S"))
        menu.AppendSeparator()
        view_html = menu.Append(wx.ID_ANY, _("View &HTML report in browser\tCtrl+H"))
        save_html = menu.Append(wx.ID_ANY, _("Save &HTML report…\tCtrl+S"))
        menu.AppendSeparator()
        copy_item = menu.Append(wx.ID_ANY, _("&Copy summary\tCtrl+Shift+C"))
        clear_item = menu.Append(wx.ID_ANY, _("C&lear results\tCtrl+Shift+N"))
        menu.AppendSeparator()
        log_item = menu.Append(wx.ID_ANY, _("View full &log\tCtrl+L"))
        menu.Bind(wx.EVT_MENU, self.on_view_text_report, view_text)
        menu.Bind(wx.EVT_MENU, self.on_save_text_report, save_text)
        menu.Bind(wx.EVT_MENU, self.on_view_html_report, view_html)
        menu.Bind(wx.EVT_MENU, self.on_save_html_report, save_html)
        menu.Bind(wx.EVT_MENU, self.on_copy_summary, copy_item)
        menu.Bind(wx.EVT_MENU, self.on_clear_results, clear_item)
        menu.Bind(wx.EVT_MENU, self.on_view_full_log, log_item)
        self.PopupMenu(menu)
        menu.Destroy()

    def _require_result(self, empty_title: str) -> CheckResult | None:
        if self._last_result is None:
            wx.MessageBox(
                _("Run a check first."),
                empty_title,
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return None
        return self._last_result

    def _report_default_stem(self, result: CheckResult) -> str:
        name = (result.tool_name or "").strip().lower()
        if "epubcheck" in name and "ace" in name:
            return "epubcheck-ace-report"
        if name == EPUBCHECK_TOOL.display_name.lower() or "epubcheck" in name:
            return "epubcheck-report"
        if name == EBRAILLE_TOOL.display_name.lower() or "ebraille" in name:
            return "ebraille-checker-report"
        if name == VERAPDF_TOOL.display_name.lower() or "verapdf" in name:
            return "verapdf-report"
        if name == "ace" or name.startswith("ace "):
            return "ace-report"
        return "check-report"

    def _show_text_dialog(self, title: str, body: str) -> None:
        """Read-only monospaced text viewer used for reports and the full log."""
        dlg = wx.Dialog(
            self,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        dlg.SetSize((720, 560))
        sizer = wx.BoxSizer(wx.VERTICAL)
        text = wx.TextCtrl(
            dlg,
            value=body,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.BORDER_SUNKEN,
            name=title,
        )
        mono = wx.Font(
            9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL
        )
        text.SetFont(mono)
        sizer.Add(text, 1, wx.EXPAND | wx.ALL, 8)
        buttons = dlg.CreateStdDialogButtonSizer(wx.CLOSE)
        if buttons is not None:
            sizer.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        dlg.SetSizer(sizer)
        dlg.CentreOnParent()
        text.SetFocus()
        dlg.ShowModal()
        dlg.Destroy()

    def on_view_text_report(self, _event: wx.CommandEvent) -> None:
        result = self._require_result(_("Nothing to view"))
        if result is None:
            return
        body = format_text_report(result, include_full_log=True)
        self._show_text_dialog(report_title(result), body)

    def on_view_full_log(self, _event: wx.CommandEvent) -> None:
        result = self._require_result(_("Nothing to view"))
        if result is None:
            return
        body = result.raw_log or result.error_message or _("The log is empty.")
        self._show_text_dialog(_("Full checker log"), body)

    def on_save_text_report(self, _event: wx.CommandEvent) -> None:
        result = self._require_result(_("Nothing to save"))
        if result is None:
            return
        stem = self._report_default_stem(result)
        with wx.FileDialog(
            self,
            _("Save text report"),
            defaultFile=f"{stem}.txt",
            wildcard=_("Text files (*.txt)|*.txt|All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
            if not path.suffix:
                path = path.with_suffix(".txt")
            save_report(path, result, fmt="text", include_full_log=True)
            self.SetStatusText(_("Report saved to {path}", path=path))
            wx.CallLater(4000, self._update_status_bar)

    def on_view_html_report(self, _event: wx.CommandEvent) -> None:
        result = self._require_result(_("Nothing to view"))
        if result is None:
            return
        try:
            fd, name = tempfile.mkstemp(
                prefix="ebraille-report-",
                suffix=".html",
                text=True,
            )
            os.close(fd)
            path = Path(name)
            save_report(path, result, fmt="html", include_full_log=True)
            webbrowser.open(path.as_uri())
            self.SetStatusText(_("Opened HTML report in browser."))
            wx.CallLater(4000, self._update_status_bar)
        except OSError as exc:
            wx.MessageBox(
                _("Could not open HTML report:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def on_save_html_report(self, _event: wx.CommandEvent) -> None:
        result = self._require_result(_("Nothing to save"))
        if result is None:
            return
        stem = self._report_default_stem(result)
        with wx.FileDialog(
            self,
            _("Save HTML report"),
            defaultFile=f"{stem}.html",
            wildcard=_(
                "HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*"
            ),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
            suffix = path.suffix.lower()
            if suffix not in {".html", ".htm"}:
                path = path.with_suffix(".html")
            save_report(path, result, fmt="html", include_full_log=True)
            self.SetStatusText(_("Report saved to {path}", path=path))
            wx.CallLater(4000, self._update_status_bar)

    def on_check_updates(self, _event: wx.CommandEvent) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._show_result_text(
            _("Checking for updates…"), update_title=False
        )

        def worker() -> None:
            try:
                updates = check_for_updates()
                available = any(u.available for u in updates)
                errors = [u.error for u in updates if u.error]
                wx.PostEvent(
                    self,
                    UpdateInfoEvent(
                        updates=updates,
                        available=available,
                        silent=False,
                        error="; ".join(errors) if errors and not available else None,
                        force=False,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                wx.PostEvent(
                    self,
                    UpdateInfoEvent(
                        updates=[],
                        available=False,
                        silent=False,
                        error=str(exc),
                        force=False,
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def on_update_info_event(self, event: UpdateInfoEvent) -> None:
        # Silent startup update probe must not clobber an in-flight publication
        # check (e.g. launched from Explorer / Finder with a file path).
        if event.silent:
            if not self._busy:
                self._restore_result_display()
            self._update_status_bar()
            if self._busy:
                return
        else:
            self._set_busy(False)
            self._restore_result_display()
            self._update_status_bar()
        if getattr(event, "error", None):
            if not event.silent:
                wx.MessageBox(
                    _(
                        "Could not check for updates:\n{error}",
                        error=event.error,
                    ),
                    _("Update check failed"),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
            return

        updates: list[ToolUpdateInfo] = list(getattr(event, "updates", None) or [])
        force = bool(getattr(event, "force", False))
        to_install = [
            u for u in updates if u.latest is not None and (force or u.available)
        ]

        if not to_install:
            if not event.silent:
                lines = []
                for u in updates:
                    ver = f" ({u.installed})" if u.installed else ""
                    lines.append(f"{u.tool.display_name}{ver}")
                detail = "\n".join(lines)
                wx.MessageBox(
                    _(
                        "You have the latest checkers.\n\n{detail}",
                        detail=detail or _("none"),
                    ),
                    _("Up to date"),
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            return

        if force:
            msg = _(
                "Download and reinstall the latest checkers now?\n\n{detail}",
                detail="\n".join(
                    f"{u.tool.display_name}: {u.latest.tag}"
                    for u in to_install
                    if u.latest is not None
                ),
            )
        else:
            detail_lines = []
            for u in to_install:
                if u.latest is None:
                    continue
                detail_lines.append(
                    _(
                        "{name}\n  Installed: {installed}\n  Latest: {tag} — {label}",
                        name=u.tool.display_name,
                        installed=u.installed or _("none"),
                        tag=u.latest.tag,
                        label=u.latest.name,
                    )
                )
            msg = _(
                "New checker releases are available.\n\n"
                "{detail}\n\n"
                "Download and install them now?",
                detail="\n\n".join(detail_lines),
            )
        if (
            wx.MessageBox(
                msg, _("Update available"), wx.YES_NO | wx.ICON_QUESTION, self
            )
            != wx.YES
        ):
            return
        releases = [u.latest for u in to_install if u.latest is not None]
        self._start_install(releases)

    def on_reinstall_checker(self, _event: wx.CommandEvent) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._show_result_text(
            _("Fetching latest releases…"), update_title=False
        )

        def worker() -> None:
            try:
                updates = check_for_updates()
                wx.PostEvent(
                    self,
                    UpdateInfoEvent(
                        updates=updates,
                        available=True,
                        silent=False,
                        error=None,
                        force=True,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                wx.PostEvent(
                    self,
                    UpdateInfoEvent(
                        updates=[],
                        available=False,
                        silent=False,
                        error=str(exc),
                        force=False,
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _start_install(self, releases: list[ReleaseInfo]) -> None:
        if not releases:
            return
        self._set_busy(True)
        labels = ", ".join(r.tag for r in releases)
        self._show_result_text(
            _("Installing {tag}…", tag=labels), update_title=False
        )

        def worker() -> None:
            try:

                def progress(msg: str) -> None:
                    wx.PostEvent(self, ProgressEvent(message=msg))

                paths: list[str] = []
                for release in releases:
                    jar = install_release(release, progress=progress)
                    paths.append(str(jar))
                wx.PostEvent(
                    self,
                    InstallDoneEvent(
                        ok=True, message="\n".join(paths), error=None
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                wx.PostEvent(
                    self, InstallDoneEvent(ok=False, message="", error=str(exc))
                )

        threading.Thread(target=worker, daemon=True).start()

    def on_install_done_event(self, event: InstallDoneEvent) -> None:
        self._set_busy(False)
        self._restore_result_display()
        self._update_status_bar()
        if event.ok:
            wx.MessageBox(
                _(
                    "Checkers installed successfully.\n\n{path}",
                    path=event.message,
                ),
                _("Installed"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
        else:
            wx.MessageBox(
                _("Installation failed:\n{error}", error=event.error),
                _("Install failed"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def on_about(self, _event: wx.CommandEvent) -> None:
        with AboutDialog(self) as dlg:
            dlg.ShowModal()


def run_app(argv: list[str] | None = None) -> None:
    paths = parse_launch_paths(argv)
    app = EBrailleApp(initial_paths=paths)
    app.MainLoop()
