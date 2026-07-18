"""wxPython main window for eBraille Checker GUI."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import wx
import wx.adv
import wx.dataview as dv
import wx.lib.newevent

from . import __version__
from .checker import (
    checker_status_text,
    is_exploded_path,
    is_packaged_path,
    run_check,
)
from .i18n import (
    LANGUAGES,
    _,
    get_language,
    load_language,
    set_language,
)
from .java_util import detect_java
from .models import CheckResult, Severity, Verdict
from .paths import (
    CHECKER_RELEASES_PAGE,
    CHECKER_REPO_PAGE,
    DAISY_WEBSITE,
    EBRAILLE_SPEC_URL,
    EBRAILLE_STANDARD_PAGE,
    application_dir,
    find_checker_jar,
    is_frozen,
)
from .updater import (
    check_for_update,
    ensure_checker_installed,
    install_release,
    read_effective_version,
)

ProgressEvent, EVT_PROGRESS = wx.lib.newevent.NewEvent()
ResultEvent, EVT_RESULT = wx.lib.newevent.NewEvent()
UpdateInfoEvent, EVT_UPDATE_INFO = wx.lib.newevent.NewEvent()
InstallDoneEvent, EVT_INSTALL_DONE = wx.lib.newevent.NewEvent()
JavaMissingEvent, EVT_JAVA_MISSING = wx.lib.newevent.NewEvent()

# ListCtrl is native on Windows but generic (and VoiceOver/Orca-invisible) on
# macOS/Linux. DataViewListCtrl is the reverse — use the native control per OS.
_USE_DATAVIEW_ISSUES = sys.platform != "win32"


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
    return _("eBraille Checker")


def filter_choices() -> tuple[str, ...]:
    return (
        _("All issues"),
        _("Errors only"),
        _("Warnings only"),
        _("Info / usage"),
    )


def _issue_column_specs() -> tuple[tuple[str, int], ...]:
    return (
        (_("Severity"), 90),
        (_("Code"), 100),
        (_("Location"), 280),
        (_("Message"), 320),
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
        self, severity: str, code: str, location: str, message: str
    ) -> None:
        if self._dataview:
            assert isinstance(self.ctrl, dv.DataViewListCtrl)
            self.ctrl.AppendItem([severity, code, location, message])
            return
        assert isinstance(self.ctrl, wx.ListCtrl)
        idx = self.ctrl.InsertItem(self.ctrl.GetItemCount(), severity)
        self.ctrl.SetItem(idx, 1, code)
        self.ctrl.SetItem(idx, 2, location)
        self.ctrl.SetItem(idx, 3, message)

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


class AboutDialog(wx.Dialog):
    """About box with multiple clickable links (native AboutBox allows only one)."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            title=_("About eBraille Checker GUI"),
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        root = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label=_("eBraille Checker GUI"))
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
                "eBraille Checker."
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
                icon_path = application_dir() / "installer" / "eBrailleChecker.ico"
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

    def _announce_result_pane(self) -> None:
        """Force a focus leave/enter so screen readers re-announce updated text.

        Changing the value while the control already has focus (e.g. after
        'Checking…') often produces no new announcement. Parking focus briefly
        elsewhere then returning triggers a fresh focus event.
        """
        if self.result_label.HasFocus():
            self._focus_select_button()
            wx.CallAfter(self._return_focus_to_result)
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
                "Select or drop a .ebrl file or folder — checking starts automatically"
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
        self.copy_btn = wx.Button(panel, label=_("&Copy summary"))
        self.copy_btn.SetToolTip(_("Copy the result summary (Ctrl+Shift+C)"))
        self.save_btn = wx.Button(panel, label=_("&Save report…"))
        self.save_btn.SetToolTip(_("Save the report to a file (Ctrl+S)"))
        filter_row.Add(self.filter_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        filter_row.Add(self.filter_choice, 0, wx.RIGHT, 12)
        filter_row.AddStretchSpacer(1)
        filter_row.Add(self.copy_btn, 0, wx.RIGHT, 4)
        filter_row.Add(self.save_btn, 0)

        self.issues_list = IssuesList(panel, name=_("Issues list"))
        self.issues_list.Bind(wx.EVT_SET_FOCUS, self.on_issues_list_focus)
        self.issues_list.Bind(wx.EVT_CHILD_FOCUS, self.on_issues_list_focus)

        issues_sizer.Add(filter_row, 0, wx.EXPAND | wx.ALL, 8)
        issues_sizer.Add(
            self.issues_list.ctrl,
            1,
            wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM,
            8,
        )
        root.Add(issues_sizer, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # --- Full log (collapsible via toggle) ---
        log_header = wx.BoxSizer(wx.HORIZONTAL)
        self.log_toggle = wx.ToggleButton(panel, label=_("Show full &log"))
        self.log_toggle.SetName(_("Show or hide full log"))
        self.log_toggle.SetToolTip(
            _("Show or hide the full checker log (Ctrl+L)")
        )
        log_header.Add(self.log_toggle, 0)
        root.Add(log_header, 0, wx.LEFT | wx.RIGHT, 10)

        self.log_ctrl = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.BORDER_SUNKEN,
            name=_("Full checker log"),
        )
        mono = wx.Font(
            9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL
        )
        self.log_ctrl.SetFont(mono)
        self.log_ctrl.Hide()
        root.Add(self.log_ctrl, 0, wx.EXPAND | wx.ALL, 10)
        self._log_sizer_item = root.GetItem(root.GetItemCount() - 1)
        self._log_sizer_item.SetProportion(0)

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
        self.menu_save = file_menu.Append(
            wx.ID_SAVEAS, _("&Save report…\tCtrl+S")
        )
        file_menu.AppendSeparator()
        self.menu_exit = file_menu.Append(wx.ID_EXIT, _("E&xit\tEsc"))
        menubar.Append(file_menu, _("&File"))

        edit_menu = wx.Menu()
        self.menu_copy = edit_menu.Append(
            wx.ID_COPY, _("&Copy summary\tCtrl+Shift+C")
        )
        edit_menu.AppendSeparator()
        self.menu_clear = edit_menu.Append(
            wx.ID_CLEAR, _("C&lear results\tCtrl+Shift+N")
        )
        menubar.Append(edit_menu, _("&Edit"))

        tools_menu = wx.Menu()
        self.menu_check = tools_menu.Append(
            wx.ID_ANY, _("&Re-check publication\tF5")
        )
        self.menu_toggle_log = tools_menu.Append(
            wx.ID_ANY, _("Show/hide full &log\tCtrl+L")
        )
        tools_menu.AppendSeparator()
        self.menu_update = tools_menu.Append(
            wx.ID_ANY, _("Check for &updates…")
        )
        self.menu_install = tools_menu.Append(
            wx.ID_ANY, _("&Download / reinstall checker…")
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

    def _bind(self) -> None:
        self.select_file_btn.Bind(wx.EVT_BUTTON, self.on_browse_file)
        self.select_folder_btn.Bind(wx.EVT_BUTTON, self.on_browse_folder)
        self.copy_btn.Bind(wx.EVT_BUTTON, self.on_copy_summary)
        self.save_btn.Bind(wx.EVT_BUTTON, self.on_save_report)
        self.filter_choice.Bind(wx.EVT_CHOICE, self.on_filter_changed)
        self.log_toggle.Bind(wx.EVT_TOGGLEBUTTON, self.on_toggle_log)
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
        self.Bind(wx.EVT_MENU, self.on_save_report, self.menu_save)
        self.Bind(wx.EVT_MENU, self.on_copy_summary, self.menu_copy)
        self.Bind(wx.EVT_MENU, self.on_clear_results, self.menu_clear)
        self.Bind(wx.EVT_MENU, self.on_check, self.menu_check)
        self.Bind(wx.EVT_MENU, self.on_menu_toggle_log, self.menu_toggle_log)
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
                "Select or drop a .ebrl file or folder — checking starts automatically"
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
        self.copy_btn.SetLabel(_("&Copy summary"))
        self.copy_btn.SetToolTip(_("Copy the result summary (Ctrl+Shift+C)"))
        self.save_btn.SetLabel(_("&Save report…"))
        self.save_btn.SetToolTip(_("Save the report to a file (Ctrl+S)"))
        self.issues_list.SetName(_("Issues list"))
        self.issues_list.SetColumnTitles(
            (_("Severity"), _("Code"), _("Location"), _("Message"))
        )
        show_log = self.log_ctrl.IsShown()
        self.log_toggle.SetLabel(
            _("Hide full &log") if show_log else _("Show full &log")
        )
        self.log_toggle.SetName(_("Show or hide full log"))
        self.log_toggle.SetToolTip(
            _("Show or hide the full checker log (Ctrl+L)")
        )
        self.log_ctrl.SetName(_("Full checker log"))
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
            self.log_ctrl,
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

                ensure_checker_installed(progress=progress)
                wx.PostEvent(self, ProgressEvent(message=_("Ready")))
                try:
                    latest, installed, available = check_for_update()
                    wx.PostEvent(
                        self,
                        UpdateInfoEvent(
                            latest=latest,
                            installed=installed,
                            available=available,
                            silent=True,
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
        self.path_ctrl.ChangeValue("")
        self._show_result_text(_("No check run yet."), title=None)
        self.issues_list.DeleteAllItems()
        self.filter_choice.SetSelection(0)
        self.log_ctrl.ChangeValue("")
        self._set_log_visible(False)
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
            if is_packaged_path(path) or is_exploded_path(path):
                chosen = path
                break

        if chosen is None:
            wx.MessageBox(
                _(
                    "Drop a packaged .ebrl file or an exploded publication folder."
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
        result = self._last_result
        if result is None:
            return
        filter_idx = self.filter_choice.GetSelection()
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
            self.issues_list.AppendRow(
                issue.severity.label,
                issue.code,
                issue.location,
                issue.message,
            )
        # Do not Focus()/Select() here — that steals keyboard focus from the
        # result pane and prevents screen readers from announcing the verdict.

    def _apply_result(self, result: CheckResult) -> None:
        self._last_result = result
        # Update content first (issues list must not steal focus afterward).
        self._show_result_text(
            result.result_display,
            title=result.headline,
            focus=False,
            verdict=result.verdict,
        )
        self.log_ctrl.SetValue(result.raw_log or result.error_message or "")
        self._populate_issues()
        self._update_status_bar()
        self._announce_result_pane()

    def _summary_text(self) -> str:
        result = self._last_result
        if result is None:
            return ""
        lines = [result.headline, ""]
        for issue in result.issues:
            lines.append(issue.summary_line())
        return "\n".join(lines).strip() + "\n"

    # --- Events ---

    def on_browse_file(self, _event: wx.CommandEvent) -> None:
        with wx.FileDialog(
            self,
            _("Select an eBraille publication"),
            wildcard=_(
                "eBraille (*.ebrl)|*.ebrl;*.Ebrl;*.EBRL|"
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
            _("Select an exploded eBraille publication folder"),
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
        self._show_result_text(event.message, update_title=False)

    def on_result_event(self, event: ResultEvent) -> None:
        self._set_busy(False)
        self._apply_result(event.result)
        self._flush_pending_open_paths()

    def on_filter_changed(self, _event: wx.CommandEvent) -> None:
        self._populate_issues()

    def on_issues_list_focus(self, event: wx.FocusEvent) -> None:
        event.Skip()
        # Tabbing into the list often lands on the header first; move to row 0.
        wx.CallAfter(self.issues_list.EnsureRowFocus)

    def on_toggle_log(self, event: wx.CommandEvent) -> None:
        show = bool(event.IsChecked()) if hasattr(event, "IsChecked") else self.log_toggle.GetValue()
        self._set_log_visible(show)

    def on_menu_toggle_log(self, _event: wx.CommandEvent) -> None:
        self._set_log_visible(not self.log_ctrl.IsShown())

    def _set_log_visible(self, show: bool) -> None:
        self.log_ctrl.Show(show)
        self.log_toggle.SetValue(show)
        self.log_toggle.SetLabel(
            _("Hide full &log") if show else _("Show full &log")
        )
        if show:
            self._log_sizer_item.SetProportion(1)
            self.log_ctrl.SetMinSize((-1, 160))
            self.panel.Layout()
            self.log_ctrl.SetFocus()
        else:
            self._log_sizer_item.SetProportion(0)
            self.log_ctrl.SetMinSize((-1, -1))
            self.panel.Layout()

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

    def on_save_report(self, _event: wx.CommandEvent) -> None:
        if self._last_result is None:
            wx.MessageBox(
                _("Run a check first."),
                _("Nothing to save"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        with wx.FileDialog(
            self,
            _("Save report"),
            defaultFile="ebraille-check-report.txt",
            wildcard=_(
                "Text files (*.txt)|*.txt|All files (*.*)|*.*"
            ),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
            body = self._summary_text()
            if self._last_result.raw_log:
                body += "\n\n" + _("--- Full log ---") + "\n" + self._last_result.raw_log
            path.write_text(body, encoding="utf-8")

    def on_check_updates(self, _event: wx.CommandEvent) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._show_result_text(
            _("Checking for updates…"), update_title=False
        )

        def worker() -> None:
            try:
                latest, installed, available = check_for_update()
                wx.PostEvent(
                    self,
                    UpdateInfoEvent(
                        latest=latest,
                        installed=installed,
                        available=available,
                        silent=False,
                        error=None,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                wx.PostEvent(
                    self,
                    UpdateInfoEvent(
                        latest=None,
                        installed=read_effective_version(),
                        available=False,
                        silent=False,
                        error=str(exc),
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
                        "Could not check for updates:\n{error}\n\nReleases: {url}",
                        error=event.error,
                        url=CHECKER_RELEASES_PAGE,
                    ),
                    _("Update check failed"),
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
            return

        latest = event.latest
        installed = event.installed
        if not event.available:
            if not event.silent:
                version = f" ({installed})" if installed else ""
                wx.MessageBox(
                    _("You have the latest checker{version}.", version=version),
                    _("Up to date"),
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            return

        msg = _(
            "A new eBraille Checker release is available.\n\n"
            "Installed: {installed}\n"
            "Latest: {tag} — {name}\n\n"
            "Download and install it now?",
            installed=installed or _("none"),
            tag=latest.tag,
            name=latest.name,
        )
        if (
            wx.MessageBox(
                msg, _("Update available"), wx.YES_NO | wx.ICON_QUESTION, self
            )
            != wx.YES
        ):
            return
        self._start_install(latest)

    def on_reinstall_checker(self, _event: wx.CommandEvent) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._show_result_text(
            _("Fetching latest release…"), update_title=False
        )

        def worker() -> None:
            try:
                latest, _, _ = check_for_update()
                wx.PostEvent(
                    self,
                    UpdateInfoEvent(
                        latest=latest,
                        installed=read_effective_version(),
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
                        latest=None,
                        installed=None,
                        available=False,
                        silent=False,
                        error=str(exc),
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _start_install(self, release) -> None:
        self._set_busy(True)
        self._show_result_text(
            _("Installing {tag}…", tag=release.tag), update_title=False
        )

        def worker() -> None:
            try:

                def progress(msg: str) -> None:
                    wx.PostEvent(self, ProgressEvent(message=msg))

                jar = install_release(release, progress=progress)
                wx.PostEvent(
                    self, InstallDoneEvent(ok=True, message=str(jar), error=None)
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
                    "Checker installed successfully.\n\n{path}",
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
