"""wxPython main window for CheckMate."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
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
    CUSTOM_I18N_FORMAT,
    CUSTOM_I18N_VERSION,
    TEXT_DIRECTION_LTR,
    TEXT_DIRECTION_RTL,
    _,
    _normalize_lang_code,
    effective_languages,
    export_language,
    get_language,
    get_text_direction,
    hide_language,
    import_custom_language,
    is_language_hidden,
    is_registered_language,
    language_display_name,
    language_native_name,
    load_language,
    read_catalog,
    set_language,
    unhide_language,
    write_overlay_catalog,
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
    images_dir,
    is_frozen,
)
from .publication import is_checkable_path
from .report_export import format_text_report, report_title, save_report
from .settings import (
    ai_features_enabled,
    ai_translation_warning_shown,
    mark_ai_translation_warning_shown,
    read_settings,
    show_issues_always,
    update_settings,
)
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
from .kb.viewer import (
    open_knowledge_base_home,
    open_knowledge_base_url,
    run_kb_update_with_progress,
)
from .kb.store import is_kb_url

ProgressEvent, EVT_PROGRESS = wx.lib.newevent.NewEvent()
ResultEvent, EVT_RESULT = wx.lib.newevent.NewEvent()
UpdateInfoEvent, EVT_UPDATE_INFO = wx.lib.newevent.NewEvent()
InstallDoneEvent, EVT_INSTALL_DONE = wx.lib.newevent.NewEvent()
JavaMissingEvent, EVT_JAVA_MISSING = wx.lib.newevent.NewEvent()
ExplainAiEvent, EVT_EXPLAIN_AI = wx.lib.newevent.NewEvent()
FixAiEvent, EVT_FIX_AI = wx.lib.newevent.NewEvent()
ApplyFixEvent, EVT_APPLY_FIX = wx.lib.newevent.NewEvent()
OverviewAiEvent, EVT_OVERVIEW_AI = wx.lib.newevent.NewEvent()
AltAssessAiEvent, EVT_ALT_ASSESS_AI = wx.lib.newevent.NewEvent()

# ListCtrl is native on Windows but generic (and VoiceOver/Orca-invisible) on
# macOS/Linux. DataViewListCtrl is the reverse — use the native control per OS.
_USE_DATAVIEW_ISSUES = sys.platform != "win32"


def _create_ai_html_view(
    parent: wx.Window, *, name: str | None = None
) -> tuple[wx.Window, bool]:
    """
    Prefer ``wx.html2.WebView`` (Edge/WebKit) for structured HTML that screen
    readers can browse. Fall back to a read-only TextCtrl when no backend works.

    ``wx.html.HtmlWindow`` is avoided: it has poor accessibility and can trap
    focus / Escape inside modal dialogs. Escape/Tab exit are handled in-page via
    ``checkmate://`` navigations so WebView2 cannot swallow those keys.
    """
    accessible_name = name or _("AI explanation")
    try:
        import wx.html2 as html2
    except ImportError:
        html2 = None  # type: ignore

    if html2 is not None:
        backends: list[object] = []
        if sys.platform == "win32":
            # Edge/WebView2 only — do not fall back to IE. The IE backend's
            # RunScriptAsync is a stub that only logs "RunScriptAsync not
            # supported" (shown as a wx error dialog), and IE a11y is poor.
            backends.append(getattr(html2, "WebViewBackendEdge", None))
        else:
            backends.append(getattr(html2, "WebViewBackendWebKit", None))
        backends.append(None)  # platform default

        for backend in backends:
            if backend is not None and hasattr(html2.WebView, "IsBackendAvailable"):
                try:
                    if not html2.WebView.IsBackendAvailable(backend):
                        continue
                except Exception:
                    continue
            try:
                if backend is None:
                    # On Windows, skip the anonymous default if it would be IE.
                    if sys.platform == "win32":
                        edge = getattr(html2, "WebViewBackendEdge", None)
                        if edge is not None and hasattr(
                            html2.WebView, "IsBackendAvailable"
                        ):
                            try:
                                if not html2.WebView.IsBackendAvailable(edge):
                                    continue
                            except Exception:
                                continue
                    view = html2.WebView.New(parent)
                else:
                    view = html2.WebView.New(parent, backend=backend)
            except Exception:
                continue
            if view is None:
                continue
            view.SetName(accessible_name)
            if hasattr(view, "SetAccessibleName"):
                try:
                    view.SetAccessibleName(accessible_name)
                except Exception:
                    pass
            try:
                view.EnableContextMenu(True)
            except Exception:
                pass
            # Tab-stop ownership is decided in ``_wire_ai_html_host``.
            return view, True

    text = wx.TextCtrl(
        parent,
        value="",
        style=wx.TE_MULTILINE
        | wx.TE_READONLY
        | wx.TE_WORDWRAP
        | wx.BORDER_SUNKEN,
        name=accessible_name,
    )
    _ensure_win_tab_stop(text)
    return text, False


class _FocusableReadOnlyText(wx.TextCtrl):
    """Single-line read-only field that stays in the keyboard tab order."""

    def AcceptsFocus(self) -> bool:  # noqa: N802 — wx override
        return True

    def AcceptsFocusFromKeyboard(self) -> bool:  # noqa: N802 — wx override
        return True


def _ensure_win_tab_stop(window: wx.Window) -> None:
    """Force WS_TABSTOP on MSW — TE_READONLY edits / WebView often omit it."""
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


def _win_clear_tab_stop(window: wx.Window) -> None:
    """Remove WS_TABSTOP so a host panel does not sit in the Tab cycle."""
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
        if not (style & ws_tabstop):
            return
        user32.SetWindowLongW(hwnd, gwl_style, style & ~ws_tabstop)
    except Exception:
        pass


def _win_ensure_control_parent(window: wx.Window) -> None:
    """Ensure WS_EX_CONTROLPARENT so dialog Tab traversal enters this container."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.GetHandle())
        if not hwnd:
            return
        gwl_exstyle = -20
        ws_ex_controlparent = 0x00010000
        user32 = ctypes.windll.user32
        ex = int(user32.GetWindowLongW(hwnd, gwl_exstyle))
        if ex & ws_ex_controlparent:
            return
        user32.SetWindowLongW(hwnd, gwl_exstyle, ex | ws_ex_controlparent)
    except Exception:
        pass


def _win_clear_control_parent(window: wx.Window) -> None:
    """
    Remove WS_EX_CONTROLPARENT so the window itself can be a dialog Tab stop.

    With CONTROLPARENT set, Windows ``GetNextDlgTabItem`` searches children and
    skips the parent — so a host with no tab-stop children (WebView chrome
    cleared) is jumped over entirely.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.GetHandle())
        if not hwnd:
            return
        gwl_exstyle = -20
        ws_ex_controlparent = 0x00010000
        user32 = ctypes.windll.user32
        ex = int(user32.GetWindowLongW(hwnd, gwl_exstyle))
        if not (ex & ws_ex_controlparent):
            return
        user32.SetWindowLongW(hwnd, gwl_exstyle, ex & ~ws_ex_controlparent)
    except Exception:
        pass


def _win_force_foreground(window: wx.Window) -> bool:
    """Try to make ``window`` the Win32 foreground top-level.

    Uses AttachThreadInput + SetForegroundWindow. Does **not** synthesize Alt
    keypresses — that toggles the menu bar and floods Narrator/NVDA.
    """
    if sys.platform != "win32":
        try:
            window.Raise()
            return True
        except Exception:
            return False
    try:
        import ctypes

        hwnd = int(window.GetHandle() or 0)
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        fg = int(user32.GetForegroundWindow() or 0)
        if fg == hwnd:
            return True
        this_thread = int(kernel32.GetCurrentThreadId())
        fg_thread = 0
        if fg:
            fg_thread = int(user32.GetWindowThreadProcessId(fg, None))
        attached = False
        if fg_thread and fg_thread != this_thread:
            attached = bool(user32.AttachThreadInput(this_thread, fg_thread, True))
        try:
            if hasattr(window, "IsIconized") and window.IsIconized():
                window.Iconize(False)
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(this_thread, fg_thread, False)
        return int(user32.GetForegroundWindow() or 0) == hwnd
    except Exception:
        return False


def _webview_host_action(url: str) -> str | None:
    """
    Return host action for in-page ``checkmate://`` navigations, else None.

    ``next`` / ``prev`` move dialog focus out of the WebView; ``page_prev`` /
    ``page_next`` switch notebook pages; ``close`` closes the modal. Edge may
    append a slash or empty path segment.
    """
    u = (url or "").strip().lower()
    if u.startswith("checkmate://focus-next"):
        return "next"
    if u.startswith("checkmate://focus-prev"):
        return "prev"
    if u.startswith("checkmate://page-prev"):
        return "page_prev"
    if u.startswith("checkmate://page-next"):
        return "page_next"
    # Edge may report checkmate://close, checkmate://close/, or odd path forms.
    if u.startswith("checkmate:") and "close" in u.split("?", 1)[0]:
        return "close"
    return None


def _add_followup_question_row(
    parent: wx.Window,
    sizer: wx.Sizer,
    *,
    on_ask,
    ask_enabled: bool = False,
) -> tuple[wx.TextCtrl, wx.Button]:
    """
    Follow-up edit + Ask, with a real StaticText label for screen readers.

    Windows cue banners (SetHint) are not exposed to MSAA/UIA; a preceding
    static label is what Narrator/NVDA announce when the edit is focused.
    """
    label_text = _("Ask a follow-up question…")
    label = wx.StaticText(parent, label=label_text)
    # Keep the label out of the Tab cycle; it is the accessible name buddy.
    _win_clear_tab_stop(label)
    sizer.Add(label, 0, wx.TOP, 4)

    follow_row = wx.BoxSizer(wx.HORIZONTAL)
    # Create the edit immediately after the static so MSW treats it as the label.
    ctrl = wx.TextCtrl(
        parent,
        value="",
        style=wx.TE_PROCESS_ENTER,
        name=label_text,
    )
    ctrl.SetName(label_text)
    if hasattr(ctrl, "SetAccessibleName"):
        try:
            ctrl.SetAccessibleName(label_text)
        except Exception:
            pass
    ask_btn = wx.Button(parent, label=_("Ask"))
    ask_btn.Enable(ask_enabled)
    ctrl.Enable(ask_enabled)
    follow_row.Add(ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
    follow_row.Add(ask_btn, 0)
    sizer.Add(follow_row, 0, wx.EXPAND | wx.BOTTOM, 4)
    ask_btn.Bind(wx.EVT_BUTTON, on_ask)
    ctrl.Bind(wx.EVT_TEXT_ENTER, on_ask)
    return ctrl, ask_btn


def _popup_menu_below(window: wx.Window, menu: wx.Menu, anchor: wx.Window) -> None:
    """Show ``menu`` aligned under ``anchor`` (not at the mouse / default corner)."""
    try:
        height = int(anchor.GetSize().GetHeight())
        screen = anchor.ClientToScreen((0, height))
        pos = window.ScreenToClient(screen)
        window.PopupMenu(menu, pos)
    except Exception:
        window.PopupMenu(menu)


def _try_set_focus(window: wx.Window | None) -> bool:
    """
    Focus a wx control, stealing Win32 focus from an Edge document HWND if needed.

    Plain ``SetFocus()`` often fails to pull keyboard focus out of
    ``Chrome_RenderWidgetHostHWND``; dialog-manager + ``user32.SetFocus`` do.
    """
    if window is None:
        return False
    try:
        if not window or not window.IsEnabled() or not window.IsShown():
            return False
        _win_dialog_focus_hwnd(window)
        window.SetFocus()
        if sys.platform == "win32":
            import ctypes

            hwnd = int(window.GetHandle() or 0)
            if hwnd:
                ctypes.windll.user32.SetFocus(hwnd)
        return True
    except Exception:
        return False


# Last announced progress text per dialog (id(dlg) -> (message, monotonic time)).
_progress_announce_state: dict[int, tuple[str, float]] = {}
_PROGRESS_ANNOUNCE_THROTTLE_S = 0.5


def _clear_progress_announce(dlg: wx.Window | None) -> None:
    if dlg is None:
        return
    _progress_announce_state.pop(id(dlg), None)


def _seed_progress_announce(dlg: wx.Window | None, message: str) -> None:
    """Remember the opening message without firing a NAMECHANGE (focus already speaks)."""
    if dlg is None:
        return
    msg = (message or "").strip()
    if not msg:
        return
    _progress_announce_state[id(dlg)] = (msg, time.monotonic())


def _announce_progress_status(dlg: wx.Window | None, message: str) -> None:
    """
    Speak a progress status change without moving focus.

    ``Pulse(message)`` only updates a static label, which Windows screen
    readers do not announce. Speak via accessible_output2 (same approach as
    FIDO) with interrupt so phase changes replace stale speech. Animation-only
    empty ``Pulse()`` calls must not go through here.
    """
    if dlg is None:
        return
    msg = (message or "").strip()
    if not msg:
        return
    try:
        if not dlg:
            return
    except RuntimeError:
        return

    now = time.monotonic()
    key = id(dlg)
    prev = _progress_announce_state.get(key)
    if prev is not None:
        prev_msg, prev_t = prev
        if prev_msg == msg and (now - prev_t) < _PROGRESS_ANNOUNCE_THROTTLE_S:
            return
        if prev_msg == msg:
            return
    _progress_announce_state[key] = (msg, now)

    try:
        from .accessibility import speak

        speak(msg, interrupt=True)
    except Exception:
        pass


def _pulse_progress(
    dlg: wx.ProgressDialog | None,
    message: str | None = None,
    *,
    announce: bool = True,
) -> bool:
    """
    Pulse a ProgressDialog.

    Pass ``message`` when the status string changes so screen readers can hear
    it. Leave ``message`` as ``None`` for animation-only timer ticks (silent).
    """
    if dlg is None:
        return True
    try:
        if message is None:
            cont, _skip = dlg.Pulse()
        else:
            cont, _skip = dlg.Pulse(message)
            if announce:
                _announce_progress_status(dlg, message)
        return bool(cont)
    except RuntimeError:
        return False


def _pulse_ui_translate(
    dlg: wx.ProgressDialog,
    msg: str,
    result: dict,
    cancel_event: threading.Event,
) -> None:
    try:
        cont = _pulse_progress(dlg, msg)
        if not cont:
            result["abort"] = True
            cancel_event.set()
    except Exception:
        pass


def _present_progress_dialog(dlg: wx.Window, message: str) -> None:
    """Pulse, raise, and focus a ProgressDialog so screen readers announce it."""
    try:
        if hasattr(dlg, "Pulse"):
            dlg.Pulse(message)  # type: ignore[attr-defined]
    except Exception:
        pass
    # Seed without NAMECHANGE — focus below already announces the dialog once.
    _seed_progress_announce(dlg, message)
    try:
        dlg.Raise()
        dlg.Show(True)
    except Exception:
        return
    _win_force_foreground(dlg)

    def _try_focus(window: wx.Window) -> bool:
        try:
            if not window or not window.GetHandle():
                return False
            window.SetFocus()
            return True
        except Exception:
            # MSW ProgressDialog children can report as focusable while their
            # HWND is still invalid (wxAssertionError from SetFocus).
            return False

    # Prefer an interactive child so NVDA announces the dialog; frame-only
    # focus often produces silence. Never let focus failures abort Explain.
    try:
        focused = False
        for child in dlg.GetChildren():
            if not isinstance(child, wx.Window):
                continue
            try:
                wants = child.AcceptsFocusFromKeyboard()
            except Exception:
                continue
            if wants and _try_focus(child):
                focused = True
                break
        if not focused:
            _try_focus(dlg)
    except Exception:
        pass
    try:
        wx.SafeYield(dlg)
    except Exception:
        try:
            wx.Yield()
        except Exception:
            pass


def _win_webview_document_hwnd(root_hwnd: int) -> int | None:
    """
    Inner document HWND for Edge WebView2 / IE (needed for screen-reader focus).

    NVDA expects focus on ``Chrome_RenderWidgetHostHWND`` / ``Internet Explorer_Server``,
    not only the outer wx WebView wrapper.
    """
    if sys.platform != "win32" or not root_hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        targets = {
            "Chrome_RenderWidgetHostHWND",
            "Internet Explorer_Server",
        }
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd: int, _lparam: int) -> bool:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in targets:
                found.append(int(hwnd))
                return False  # stop enumeration
            return True

        # EnumChildWindows walks all descendants.
        user32.EnumChildWindows(int(root_hwnd), _enum, 0)
        return found[0] if found else None
    except Exception:
        return None


def _win_get_focus_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        return int(ctypes.windll.user32.GetFocus() or 0)
    except Exception:
        return 0


def _win_focus_is_webview_document(view: wx.Window) -> bool:
    """True when Win32 focus is already inside the WebView document."""
    try:
        outer = int(view.GetHandle() or 0)
    except RuntimeError:
        return False
    doc = _win_webview_document_hwnd(outer)
    return bool(doc) and _win_get_focus_hwnd() == doc


def _tab_into_webview_direction() -> str:
    """Return 'next' (Tab) or 'prev' (Shift+Tab) for entry into the WebView."""
    try:
        if wx.GetKeyState(wx.WXK_SHIFT):
            return "prev"
    except Exception:
        pass
    return "next"


# Edge WebView2 MoveFocus reasons (ICoreWebView2Controller::MoveFocus).
_COREWEBVIEW2_MOVE_FOCUS_PROGRAMMATIC = 0
_COREWEBVIEW2_MOVE_FOCUS_NEXT = 1
_COREWEBVIEW2_MOVE_FOCUS_PREVIOUS = 2

# ICoreWebView2Controller IID — MoveFocus lives here, not on ICoreWebView2
# (wx GetNativeBackend returns the latter).
_IID_ICOREWEBVIEW2_CONTROLLER = (
    0x4D00C0D1,
    0x9434,
    0x4EB6,
    (0x80, 0x78, 0x86, 0x97, 0xA5, 0x60, 0x33, 0x4F),
)


def _win_webview_controller_move_focus(view: wx.Window, reason: int) -> bool:
    """
    Call ICoreWebView2Controller::MoveFocus when possible.

    wx OnSetFocus only uses PROGRAMMATIC. Tabbing into a host panel then
    SetFocus()'ing the WebView never runs NEXT/PREVIOUS, so Edge can leave
    keyboard input in limbo until a mouse click. Prefer NEXT/PREVIOUS on Tab.
    """
    if sys.platform != "win32":
        return False
    get_native = getattr(view, "GetNativeBackend", None)
    if not callable(get_native):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        raw = get_native()
        if raw is None:
            return False
        ptr = int(raw)
        if not ptr:
            return False

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        iid = GUID()
        iid.Data1 = _IID_ICOREWEBVIEW2_CONTROLLER[0]
        iid.Data2 = _IID_ICOREWEBVIEW2_CONTROLLER[1]
        iid.Data3 = _IID_ICOREWEBVIEW2_CONTROLLER[2]
        for i, b in enumerate(_IID_ICOREWEBVIEW2_CONTROLLER[3]):
            iid.Data4[i] = b

        # IUnknown::QueryInterface on the native ICoreWebView2* (usually fails —
        # controller is a sibling object — but cheap to try).
        this = ctypes.c_void_p(ptr)
        vtbl = ctypes.cast(this, ctypes.POINTER(ctypes.c_void_p)).contents
        vtbl_ptrs = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
        qi = ctypes.CFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(vtbl_ptrs[0])
        controller = ctypes.c_void_p()
        hr = qi(this, ctypes.byref(iid), ctypes.byref(controller))
        if hr != 0 or not controller.value:
            return False

        c_this = controller
        c_vtbl = ctypes.cast(c_this, ctypes.POINTER(ctypes.c_void_p)).contents
        c_ptrs = ctypes.cast(c_vtbl, ctypes.POINTER(ctypes.c_void_p))
        # IUnknown(3) + get/put IsVisible(2) + Bounds(2) + Zoom(2) +
        # zoom events(2) + SetBoundsAndZoomFactor(1) + MoveFocus = index 12.
        move_focus = ctypes.CFUNCTYPE(
            ctypes.HRESULT, ctypes.c_void_p, ctypes.c_int
        )(c_ptrs[12])
        hr = move_focus(c_this, int(reason))
        # Release the QI'd controller pointer.
        release = ctypes.CFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(c_ptrs[2])
        release(c_this)
        return hr == 0
    except Exception:
        return False


def _win_webview_synthetic_click(doc_hwnd: int) -> bool:
    """
    Arm Edge keyboard input with a client-area click.

    DOM focus / scroll alone is not enough: until WebView2 receives a real
    activation (MoveFocus NEXT/PREVIOUS or a mouse click), Tab keys stay in
    limbo. Click the top-left padding (body has 1rem padding) so we do not
    activate a link.
    """
    if sys.platform != "win32" or not doc_hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WM_LBUTTONDOWN = 0x0201
        WM_LBUTTONUP = 0x0202
        MK_LBUTTON = 0x0001
        rect = wintypes.RECT()
        if not user32.GetClientRect(int(doc_hwnd), ctypes.byref(rect)):
            return False
        if rect.right < 4 or rect.bottom < 4:
            return False
        x, y = 2, 2
        lp = (y << 16) | (x & 0xFFFF)
        user32.SetFocus(int(doc_hwnd))
        # Synchronous so the click finishes before follow-up DOM focus JS.
        user32.SendMessageW(int(doc_hwnd), WM_LBUTTONDOWN, MK_LBUTTON, lp)
        user32.SendMessageW(int(doc_hwnd), WM_LBUTTONUP, 0, lp)
        return True
    except Exception:
        return False


def _win_dialog_focus_hwnd(ctrl: wx.Window) -> bool:
    """Focus a control via WM_NEXTDLGCTL (dialog-manager path, not SetFocus)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        hwnd = int(ctrl.GetHandle() or 0)
        if not hwnd:
            return False
        top = ctrl.GetTopLevelParent()
        top_hwnd = int(top.GetHandle() or 0) if top else 0
        if not top_hwnd:
            return False
        WM_NEXTDLGCTL = 0x0028
        ctypes.windll.user32.SendMessageW(top_hwnd, WM_NEXTDLGCTL, hwnd, 1)
        return True
    except Exception:
        return False


# On Tab entry: scroll to top and focus the document body so reading starts at
# the beginning. Do not jump to the first link (next Tab reaches links).
# Shift+Tab entry focuses the last link instead.
_WEBVIEW_ACTIVATE_DOM_JS_NEXT = """
(function () {
  try {
    var body = document.body;
    if (!body) { return 'none'; }
    if (!(body.tabIndex < 0)) { body.tabIndex = -1; }
    var active = document.activeElement;
    var onChrome = !active
      || active === body
      || active === document.documentElement;
    if (!onChrome) { return 'kept'; }
    window.scrollTo(0, 0);
    if (document.documentElement) {
      document.documentElement.scrollTop = 0;
    }
    body.scrollTop = 0;
    body.focus();
    return 'body';
  } catch (e) {
    return 'err';
  }
})();
""".strip()

# After a follow-up answer reloads the page: put the newest question at the top
# of the viewport and move document focus there for screen readers.
_WEBVIEW_REVEAL_LATEST_FOLLOWUP_JS = """
(function () {
  try {
    var el = document.getElementById('cm-latest-followup');
    if (!el) { return 'none'; }
    if (!(el.tabIndex < 0)) { el.tabIndex = -1; }
    try {
      var top = 0;
      try {
        var rect = el.getBoundingClientRect();
        top = (window.pageYOffset || document.documentElement.scrollTop || 0)
          + rect.top - 12;
      } catch (e0) {
        top = el.offsetTop || 0;
      }
      if (top < 0) top = 0;
      window.scrollTo(0, top);
      if (document.documentElement) {
        document.documentElement.scrollTop = top;
      }
      if (document.body) {
        document.body.scrollTop = top;
      }
      el.scrollIntoView({ block: 'start', inline: 'nearest', behavior: 'auto' });
    } catch (e) {
      try { el.scrollIntoView(true); } catch (e2) {}
    }
    try {
      el.focus({ preventScroll: true });
    } catch (e3) {
      try { el.focus(); } catch (e4) {}
    }
    return 'latest';
  } catch (e5) {
    return 'err';
  }
})();
""".strip()

# Dialog path: scroll only — el.focus() steals Win32 focus into Edge and leaves
# the SR / keyboard gate (host panel) and document fighting each other.
_WEBVIEW_SCROLL_LATEST_FOLLOWUP_JS = """
(function () {
  try {
    var el = document.getElementById('cm-latest-followup');
    if (!el) { return 'none'; }
    try {
      var top = 0;
      try {
        var rect = el.getBoundingClientRect();
        top = (window.pageYOffset || document.documentElement.scrollTop || 0)
          + rect.top - 12;
      } catch (e0) {
        top = el.offsetTop || 0;
      }
      if (top < 0) top = 0;
      window.scrollTo(0, top);
      if (document.documentElement) {
        document.documentElement.scrollTop = top;
      }
      if (document.body) {
        document.body.scrollTop = top;
      }
      el.scrollIntoView({ block: 'start', inline: 'nearest', behavior: 'auto' });
    } catch (e) {
      try { el.scrollIntoView(true); } catch (e2) {}
    }
    return 'scrolled';
  } catch (e5) {
    return 'err';
  }
})();
""".strip()

_WEBVIEW_ACTIVATE_DOM_JS_PREV = """
(function () {
  try {
    var body = document.body;
    if (!body) { return 'none'; }
    if (!(body.tabIndex < 0)) { body.tabIndex = -1; }
    var active = document.activeElement;
    var onChrome = !active
      || active === body
      || active === document.documentElement;
    if (!onChrome) { return 'kept'; }
    var list = Array.prototype.slice.call(document.querySelectorAll(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )).filter(function (el) {
      if (el.disabled) return false;
      if (el.getAttribute('aria-hidden') === 'true') return false;
      var rects = el.getClientRects();
      return rects && rects.length > 0;
    });
    if (list.length) {
      list[list.length - 1].focus();
      return 'last';
    }
    body.focus();
    return 'body';
  } catch (e) {
    return 'err';
  }
})();
""".strip()

# Back-compat alias used by older call sites / mental model.
_WEBVIEW_ACTIVATE_DOM_JS = _WEBVIEW_ACTIVATE_DOM_JS_NEXT


def _webview_activate_dom_js(direction: str) -> str:
    if direction == "prev":
        return _WEBVIEW_ACTIVATE_DOM_JS_PREV
    return _WEBVIEW_ACTIVATE_DOM_JS_NEXT


def _webview_run_script(view: wx.Window, script: str) -> bool:
    """Run JavaScript in a wx.html2.WebView; tolerate API shape differences."""
    # Prefer sync RunScript. The base wxWebView::RunScriptAsync only calls
    # wxLogError("RunScriptAsync not supported") without raising (IE / stubs),
    # which pops an error dialog and looks like success to Python callers.
    sync_fn = getattr(view, "RunScript", None)
    if callable(sync_fn):
        try:
            with wx.LogNull():
                sync_fn(script)
            return True
        except TypeError:
            try:
                out: list[str] = []
                with wx.LogNull():
                    sync_fn(script, out)
                return True
            except Exception:
                pass
        except Exception:
            pass
    async_fn = getattr(view, "RunScriptAsync", None)
    if callable(async_fn):
        try:
            with wx.LogNull():
                async_fn(script)
            return True
        except Exception:
            pass
    return False


def _markdown_has_latest_followup(markdown_text: str) -> bool:
    return 'id="cm-latest-followup"' in (markdown_text or "")


def _reveal_latest_followup_in_webview(
    view: wx.Window,
    *,
    retries: int = 8,
) -> bool:
    """
    Activate the WebView document and scroll/focus ``#cm-latest-followup``.

    Returns True when activation was attempted successfully enough to stop
    retrying. Unlike Tab-entry activation, this does not scroll to the top.
    """
    try:
        if not view:
            return False
    except RuntimeError:
        return False

    already_doc = sys.platform == "win32" and _win_focus_is_webview_document(view)
    if not already_doc:
        try:
            _win_dialog_focus_hwnd(view)
            view.SetFocus()
        except RuntimeError:
            return False
        if not _win_webview_controller_move_focus(
            view, _COREWEBVIEW2_MOVE_FOCUS_PROGRAMMATIC
        ):
            _win_webview_controller_move_focus(
                view, _COREWEBVIEW2_MOVE_FOCUS_NEXT
            )

    if sys.platform != "win32":
        _webview_run_script(view, _WEBVIEW_REVEAL_LATEST_FOLLOWUP_JS)
        return True

    try:
        import ctypes

        outer = int(view.GetHandle() or 0)
        doc = _win_webview_document_hwnd(outer)
        if doc:
            if not already_doc:
                ctypes.windll.user32.SetFocus(doc)
            _webview_run_script(view, _WEBVIEW_REVEAL_LATEST_FOLLOWUP_JS)
            # Arm Edge keyboard, then re-apply scroll/focus (click lands at 2,2).
            if not already_doc:
                _win_webview_synthetic_click(doc)
                _webview_run_script(view, _WEBVIEW_REVEAL_LATEST_FOLLOWUP_JS)
            return True
    except Exception:
        pass

    if retries > 0:
        wx.CallLater(
            100,
            lambda v=view, n=retries: _reveal_latest_followup_in_webview(
                v, retries=n - 1
            ),
        )
        return False

    _webview_run_script(view, _WEBVIEW_REVEAL_LATEST_FOLLOWUP_JS)
    return False


def _focus_ai_html_view(
    view: wx.Window,
    *,
    is_webview: bool,
    retries: int = 10,
    direction: str | None = None,
    arm_keyboard: bool | None = None,
) -> bool:
    """
    Put keyboard focus into the AI HTML pane (and its document HWND on Windows).

    Returns True when activation was attempted successfully enough to stop
    retrying. Edge needs Win32 focus plus a DOM focus target; avoid calling
    ``view.SetFocus()`` when the document already has focus (that re-enters
    SET_FOCUS handlers and loops).

    When entering from outside the document, also arm WebView2 keyboard input
    (MoveFocus NEXT/PREVIOUS and/or a synthetic client click). DOM scroll/focus
    alone leaves Tab in limbo until a mouse click.
    """
    try:
        if not view:
            return False
    except RuntimeError:
        return False

    if not is_webview:
        try:
            view.SetFocus()
        except RuntimeError:
            return False
        if hasattr(view, "SetInsertionPoint"):
            try:
                view.SetInsertionPoint(0)
            except RuntimeError:
                pass
        return True

    already_doc = sys.platform == "win32" and _win_focus_is_webview_document(view)
    if direction is None:
        direction = _tab_into_webview_direction()
    if arm_keyboard is None:
        # Only force Edge keyboard arming when Tabbing in from outside.
        arm_keyboard = not already_doc

    if not already_doc:
        try:
            # Prefer dialog-manager focus so WebView2 can treat Tab as NEXT.
            _win_dialog_focus_hwnd(view)
            view.SetFocus()
        except RuntimeError:
            return False
        move_reason = (
            _COREWEBVIEW2_MOVE_FOCUS_PREVIOUS
            if direction == "prev"
            else _COREWEBVIEW2_MOVE_FOCUS_NEXT
        )
        if not _win_webview_controller_move_focus(view, move_reason):
            # wx OnSetFocus already tried PROGRAMMATIC; still attempt it again.
            _win_webview_controller_move_focus(
                view, _COREWEBVIEW2_MOVE_FOCUS_PROGRAMMATIC
            )

    dom_js = _webview_activate_dom_js(direction)

    if sys.platform != "win32":
        _webview_run_script(view, dom_js)
        return True

    try:
        import ctypes

        outer = int(view.GetHandle() or 0)
        doc = _win_webview_document_hwnd(outer)
        if doc:
            if not already_doc:
                ctypes.windll.user32.SetFocus(doc)
            _webview_run_script(view, dom_js)
            # MoveFocus alone is often unavailable (controller QI fails). A
            # one-shot client click arms Edge keyboard on Tab-in; Escape and
            # Tab-boundary navigations then exit via checkmate:// handlers.
            if arm_keyboard and not already_doc:
                _win_webview_synthetic_click(doc)
                _webview_run_script(view, dom_js)
            return True
    except Exception:
        pass

    if retries > 0:
        wx.CallLater(
            100,
            lambda v=view, n=retries, d=direction, a=arm_keyboard: _focus_ai_html_view(
                v,
                is_webview=True,
                retries=n - 1,
                direction=d,
                arm_keyboard=a,
            ),
        )
        return False

    _webview_run_script(view, dom_js)
    return False


def _refresh_ai_html_tab_stops(
    host: wx.Window,
    view: wx.Window,
    *,
    is_webview: bool,
) -> None:
    """
    Keep a single dialog Tab stop for the AI HTML pane.

    Edge WebView2's outer HWND often accepts dialog focus without activating the
    document, so Tab appears to skip the pane. The host panel is the stable
    WS_TABSTOP (and must NOT be WS_EX_CONTROLPARENT, or Windows skips it while
    searching empty children). Focus is then forwarded into the document HWND.
    """
    if is_webview:
        # Leaf tab-stop: dialog manager must focus the host itself.
        if host.HasFlag(wx.TAB_TRAVERSAL):
            host.ToggleWindowStyle(wx.TAB_TRAVERSAL)
        _win_clear_control_parent(host)
        _ensure_win_tab_stop(host)
        _win_clear_tab_stop(view)
    else:
        # TextCtrl child must be reachable via CONTROLPARENT recursion.
        if not host.HasFlag(wx.TAB_TRAVERSAL):
            host.ToggleWindowStyle(wx.TAB_TRAVERSAL)
        _win_ensure_control_parent(host)
        _win_clear_tab_stop(host)
        _ensure_win_tab_stop(view)


def _wire_ai_html_host(
    host: wx.Window,
    view: wx.Window,
    *,
    is_webview: bool,
) -> None:
    """
    Put the AI HTML pane in the Tab cycle.

    For WebView, the host panel is the keyboard surface (Escape / Tab stay in
    wx). Enter/Space activates Edge for in-page link browsing; in-page JS then
    exits via ``checkmate://``. Auto-diving into the document on Tab creates a
    focus limbo where neither wx nor Edge receives keys.

    For the TextCtrl fallback, the text control itself remains the Tab stop.
    """
    if isinstance(host, _AiHtmlHostPanel):
        host.bind_ai_view(view, is_webview=is_webview)
    _refresh_ai_html_tab_stops(host, view, is_webview=is_webview)

    if is_webview:
        tip = _(
            "AI explanation: Tab moves to the next control; Enter activates the "
            "page to browse links. Escape closes this dialog. Inside the page, "
            "Tab after the last link (or Ctrl+Tab) returns to the dialog."
        )
        try:
            host.SetToolTip(tip)
            view.SetToolTip(tip)
        except Exception:
            pass

        def _dialog() -> wx.Window | None:
            try:
                return host.GetTopLevelParent()
            except RuntimeError:
                return None

        def _on_host_char_hook(event: wx.KeyEvent) -> None:
            key = event.GetKeyCode()
            dlg = _dialog()
            if key == wx.WXK_ESCAPE:
                if dlg is not None and hasattr(dlg, "_on_close_dialog"):
                    # Defer so we are not tearing down inside the key handler.
                    wx.CallAfter(dlg._on_close_dialog)  # type: ignore[attr-defined]
                    return
            if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE):
                _focus_ai_html_view(
                    view,
                    is_webview=True,
                    retries=2,
                    direction="next",
                    arm_keyboard=True,
                )
                return
            event.Skip()

        host.Bind(wx.EVT_CHAR_HOOK, _on_host_char_hook)
        return

    # TextCtrl: forward host focus into the read-only text pane.
    focusing = {"busy": False}

    def _push_focus() -> None:
        if focusing["busy"]:
            return
        focusing["busy"] = True
        try:
            _focus_ai_html_view(view, is_webview=False, retries=2)
        finally:
            wx.CallAfter(lambda: focusing.__setitem__("busy", False))

    def _on_host_focus(event: wx.FocusEvent) -> None:
        event.Skip()
        wx.CallAfter(_push_focus)

    host.Bind(wx.EVT_SET_FOCUS, _on_host_focus)


class _AiHtmlHostPanel(wx.Panel):
    """
    Deferred WebView container.

    Before the view exists it stays out of the Tab cycle. Once wired for a
    WebView it becomes the dialog Tab stop and keeps keyboard focus in wx
    (Enter activates the Edge document for link browsing).
    """

    def __init__(self, *args, **kwargs) -> None:
        # Avoid default wxTAB_TRAVERSAL / WS_EX_CONTROLPARENT until we know
        # whether this hosts a WebView (leaf tab-stop) or a TextCtrl.
        if "style" not in kwargs:
            kwargs["style"] = wx.BORDER_NONE
        super().__init__(*args, **kwargs)
        self._ai_view: wx.Window | None = None
        self._ai_is_webview = False
        self._accept_kbd_focus = False

    def bind_ai_view(self, view: wx.Window, *, is_webview: bool) -> None:
        self._ai_view = view
        self._ai_is_webview = bool(is_webview)
        # WebView: host is the Tab gate. Text fallback: child TextCtrl is.
        self._accept_kbd_focus = bool(is_webview)
        _refresh_ai_html_tab_stops(self, view, is_webview=self._ai_is_webview)

    def AcceptsFocus(self) -> bool:  # noqa: N802 — wx override
        return self._accept_kbd_focus

    def AcceptsFocusFromKeyboard(self) -> bool:  # noqa: N802 — wx override
        return self._accept_kbd_focus

    def AcceptsFocusRecursively(self) -> bool:  # noqa: N802 — wx override
        # WebView gate is a leaf: do not search (non-tab-stop) children.
        if self._ai_is_webview:
            return False
        return super().AcceptsFocusRecursively()

    def SetFocusFromKbd(self) -> None:  # noqa: N802 — wx override
        """Tab lands on the host; keep focus here (do not auto-dive into Edge)."""
        if self._ai_is_webview:
            try:
                self.SetFocus()
            except RuntimeError:
                pass
            return
        view = self._ai_view
        if view is not None:
            try:
                view.SetFocus()
            except RuntimeError:
                pass
            return
        super().SetFocusFromKbd()


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


# How check/progress milestones are announced to screen readers:
#   "focus"     — quiet focus into Result (no text selection).
#   "selection" — legacy leave/enter + leave text selected.
_ANNOUNCE_MODE = "focus"
# When False, keep the window title as plain "CheckMate" (no milestone suffix).
_UPDATE_TITLE_BAR = False


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


def _issue_details_dialog_title(issue: Issue) -> str:
    """Window title: generic label, plus checker code when known."""
    base = _("Issue details")
    code = (issue.code or "").strip()
    if code:
        return f"{base} — {code}"
    return base


class IssueDetailDialog(wx.Dialog):
    """Issue details with optional Knowledge Base / Explain / Fix pages."""

    _PAGE_ISSUE = "issue"
    _PAGE_KB = "kb"
    _PAGE_EXPLAIN = "explain"
    _PAGE_FIX = "fix"

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
            title=_issue_details_dialog_title(issue),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self._issue = issue
        self._issue_count = max(1, int(count))
        self._check_result = check_result
        self._explain_session = None
        self._fix_session = None
        self._busy = False
        self._explain_markdown = ""
        self._explain_plain = False
        self._fix_markdown = ""
        self._fix_plain = False
        self._ai_output_is_webview = False
        self._ai_focus_after_load = False
        self._ai_view_realized = False
        self._ai_painted_channel: str | None = None
        self._fix_proposal = None
        self._batch_proposal = None
        self.applied_fix_verify = None
        self._ai_cancel: threading.Event | None = None
        self._ai_progress: wx.ProgressDialog | None = None
        self._ai_progress_timer: wx.Timer | None = None
        self._show_ai = ai_features_enabled()
        self._show_fix = self._show_ai and self._fix_available_for_result(check_result)
        self._matching_like_this = 1
        self._notebook: wx.Notebook | None = None
        self._page_keys: list[str] = [self._PAGE_ISSUE]
        self._active_page_key = self._PAGE_ISSUE
        self._kb_panel = None
        self._kb_en_rel = ""
        self._kb_host = None
        if self._show_fix and check_result is not None:
            from .ai.context import issues_matching_seed

            self._matching_like_this = max(
                1, len(issues_matching_seed(issue, check_result))
            )
        if self._show_ai:
            self.SetSize((780, 760))
            self.SetMinSize((700, 520))
        else:
            self.SetSize((640, 560))
            self.SetMinSize((480, 360))

        self._kb_title = ""
        self._kb_url = ""
        try:
            from .ai.resources import primary_kb_resource
            from .kb.store import en_relative_path_from_url, is_kb_url

            kb = primary_kb_resource(issue)
            if kb and is_kb_url(kb[1]):
                self._kb_title, self._kb_url = kb[0], kb[1]
                self._kb_en_rel = en_relative_path_from_url(self._kb_url) or ""
        except Exception:
            pass
        self._show_kb = bool(self._kb_url and self._kb_en_rel)
        self._use_notebook = self._show_ai or self._show_kb

        self._build_details_plain(issue, count)
        root = wx.BoxSizer(wx.VERTICAL)

        if self._show_ai:
            model_row = wx.BoxSizer(wx.HORIZONTAL)
            model_label = wx.StaticText(self, label=_("Model:"))
            model_row.Add(model_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
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
            model_row.Add(self.ai_model_ctrl, 1, wx.EXPAND)
            root.Add(model_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

            self.ai_status = wx.StaticText(self, label="")
            self.ai_status.SetName(_("AI status"))
            root.Add(self.ai_status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)

        if self._use_notebook:
            self._notebook = wx.Notebook(self, name=_("Issue details pages"))
            self._page_keys = [self._PAGE_ISSUE]
            if self._show_kb:
                self._page_keys.append(self._PAGE_KB)
            if self._show_ai:
                self._page_keys.append(self._PAGE_EXPLAIN)
                if self._show_fix:
                    self._page_keys.append(self._PAGE_FIX)

            for key, label in (
                (self._PAGE_ISSUE, _("Issue")),
                (self._PAGE_KB, _("Knowledge Base")),
                (self._PAGE_EXPLAIN, _("Explain with AI")),
                (self._PAGE_FIX, _("Fix with AI")),
            ):
                if key not in self._page_keys:
                    continue
                page = wx.Panel(self._notebook)
                page.SetMinSize((-1, 1))
                self._notebook.AddPage(page, label)

            # Create action panels BEFORE the content/WebView panel so MSW Tab
            # order (creation / Z-order) matches the visual layout. MoveBefore
            # during __init__ is unreliable before HWNDs exist.
            self._issue_controls = None
            self._explain_actions = (
                self._build_explain_actions(self) if self._show_ai else None
            )
            self._fix_actions = (
                self._build_fix_actions(self) if self._show_fix else None
            )

            self._content_panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
            _win_ensure_control_parent(self._content_panel)
            self._build_details_host(self._content_panel)
            content_sizer = wx.BoxSizer(wx.VERTICAL)
            content_sizer.Add(self._details_host, 1, wx.EXPAND)
            if self._show_kb:
                self._build_kb_host_placeholder(self._content_panel)
                content_sizer.Add(self._kb_host, 1, wx.EXPAND)
            if self._show_ai:
                self._build_ai_host_placeholder(self._content_panel)
                content_sizer.Add(self._ai_output_host, 1, wx.EXPAND)
            else:
                self._ai_output_host = None  # type: ignore[assignment]
                self.ai_output = None  # type: ignore[assignment]
            self._content_panel.SetSizer(content_sizer)

            self._explain_followup = (
                self._build_explain_followup(self) if self._show_ai else None
            )
            self._fix_followup = (
                self._build_fix_followup(self) if self._show_fix else None
            )
            # Aliases used by page show/hide and older helpers.
            self._explain_controls = self._explain_followup
            self._fix_controls = self._fix_followup

            root.Add(self._notebook, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
            if self._explain_actions is not None:
                root.Add(
                    self._explain_actions,
                    0,
                    wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                    8,
                )
            if self._fix_actions is not None:
                root.Add(
                    self._fix_actions,
                    0,
                    wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP,
                    8,
                )
            root.Add(self._content_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
            if self._explain_followup is not None:
                root.Add(self._explain_followup, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
            if self._fix_followup is not None:
                root.Add(self._fix_followup, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

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
            if self._show_ai:
                self.Bind(EVT_EXPLAIN_AI, self._on_explain_ai_event)
                self.Bind(EVT_FIX_AI, self._on_fix_ai_event)
                self.Bind(EVT_APPLY_FIX, self._on_apply_fix_event)
        else:
            self._build_details_host(self)
            root.Add(self._details_host, 1, wx.EXPAND | wx.ALL, 12)
            self._issue_controls = None
            self._explain_actions = None
            self._explain_followup = None
            self._fix_actions = None
            self._fix_followup = None
            self._explain_controls = None
            self._fix_controls = None
            self._ai_output_host = None  # type: ignore[assignment]
            self.ai_output = None  # type: ignore[assignment]
            self._kb_host = None

        export = wx.BoxSizer(wx.HORIZONTAL)
        self.view_browser_btn = wx.Button(self, label=_("View in browser"))
        self.view_browser_btn.SetToolTip(_("Open the current view in your web browser"))
        export.Add(self.view_browser_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.save_html_btn = wx.Button(self, label=_("Save as HTML…"))
        self.save_html_btn.SetToolTip(_("Save the current view as an HTML file"))
        export.Add(self.save_html_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.save_md_btn = wx.Button(self, label=_("Save as Markdown…"))
        self.save_md_btn.SetToolTip(_("Save the AI explanation as a Markdown file"))
        export.Add(self.save_md_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.copy_btn = wx.Button(self, label=_("Copy to clipboard"))
        self.copy_btn.SetToolTip(_("Copy the current view to the clipboard"))
        export.Add(self.copy_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        # Alias used by older AI helpers.
        self.copy_ai_btn = self.copy_btn
        root.Add(export, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self.view_browser_btn.Bind(wx.EVT_BUTTON, self._on_view_browser)
        self.save_html_btn.Bind(wx.EVT_BUTTON, self._on_save_html)
        self.save_md_btn.Bind(wx.EVT_BUTTON, self._on_save_markdown)
        self.copy_btn.Bind(wx.EVT_BUTTON, self._on_copy_clipboard)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        footer.AddStretchSpacer(1)
        close_btn = wx.Button(self, id=wx.ID_CLOSE, label=_("Close"))
        close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        footer.Add(close_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(footer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(root)
        self.CentreOnParent()
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_dialog_char_hook)
        close_btn.SetDefault()

        self._apply_active_page(self._PAGE_ISSUE, initial=True)
        if self._details_is_webview:
            _try_set_focus(self._details_host)
        else:
            _try_set_focus(self.details_view)
            try:
                if hasattr(self.details_view, "SetInsertionPoint"):
                    self.details_view.SetInsertionPoint(0)
            except RuntimeError:
                pass

    def _build_details_plain(self, issue: Issue, count: int) -> None:
        lines = [
            _("Code: {value}", value=issue.code or "—"),
            _("Severity: {value}", value=issue.severity.label),
        ]
        impact = (issue.impact or "").strip()
        if impact:
            lines.append(_("Impact: {value}", value=impact.title()))
        ruleset = (issue.ruleset or "").strip()
        if ruleset:
            lines.append(_("Ruleset: {value}", value=ruleset))
        lines.append(_("Source: {value}", value=issue.source or "—"))
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
        from .ai.markdown_html import _issue_help_fields

        help_title, help_text, help_url = _issue_help_fields(issue)
        if help_title or help_text or help_url:
            lines.extend(["", _("Help")])
            if help_title:
                lines.append(help_title)
            if help_text:
                lines.append(help_text)
            if help_url:
                lines.append(help_url)
        self._details_plain = "\n".join(lines)

    def _build_details_host(self, parent: wx.Window) -> None:
        self._details_is_webview = False
        self._details_host = _AiHtmlHostPanel(parent, name=_("Issue details"))
        self._details_host.SetMinSize((-1, 200))
        details_sizer = wx.BoxSizer(wx.VERTICAL)
        self._details_host.SetSizer(details_sizer)
        view, is_webview = _create_ai_html_view(
            self._details_host, name=_("Issue details")
        )
        view.SetMinSize((-1, 200))
        if is_webview:
            import wx.html2 as html2

            from .ai.markdown_html import issue_details_page

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_details_webview_navigating)
            view.SetPage(
                issue_details_page(
                    self._issue, count=self._issue_count, tab_exit=True
                ),
                "",
            )
        else:
            if hasattr(view, "ChangeValue"):
                view.ChangeValue(self._details_plain)
            else:
                view.SetValue(self._details_plain)
        details_sizer.Add(view, 1, wx.EXPAND)
        self.details_view = view
        self._details_is_webview = is_webview
        _wire_ai_html_host(self._details_host, view, is_webview=is_webview)
        if is_webview:
            wx.CallLater(
                100,
                lambda h=self._details_host, v=view: _refresh_ai_html_tab_stops(
                    h, v, is_webview=True
                ),
            )

    def _build_ai_host_placeholder(self, parent: wx.Window) -> None:
        self._ai_output_host = _AiHtmlHostPanel(parent, name=_("AI explanation"))
        self._ai_output_host.SetMinSize((-1, 200))
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        self._ai_loading_label = wx.StaticText(
            self._ai_output_host, label=_("Loading AI view…")
        )
        host_sizer.Add(self._ai_loading_label, 0, wx.ALL, 8)
        self._ai_output_host.SetSizer(host_sizer)
        _win_clear_tab_stop(self._ai_output_host)
        self.ai_output = self._ai_output_host  # until realized
        self._ai_output_host.Hide()

    def _build_kb_host_placeholder(self, parent: wx.Window) -> None:
        self._kb_host = wx.Panel(parent, name=_("Knowledge Base"))
        self._kb_host.SetMinSize((-1, 200))
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        loading = wx.StaticText(self._kb_host, label=_("Loading…"))
        host_sizer.Add(loading, 0, wx.ALL, 8)
        self._kb_host.SetSizer(host_sizer)
        self._kb_panel = None
        self._kb_host.Hide()

    def _realize_kb_panel(self) -> None:
        """Create the embedded KB article panel on first visit to the KB tab."""
        if not self._show_kb or self._kb_panel is not None:
            return
        host = self._kb_host
        if host is None:
            return
        try:
            if not host:
                return
        except RuntimeError:
            return
        from .kb.viewer import KnowledgeBaseArticlePanel

        sizer = host.GetSizer()
        if sizer is not None:
            sizer.Clear(delete_windows=True)
        else:
            sizer = wx.BoxSizer(wx.VERTICAL)
            host.SetSizer(sizer)
        self._kb_panel = KnowledgeBaseArticlePanel(
            host,
            en_rel=self._kb_en_rel,
            on_content_ready=lambda: wx.CallAfter(self._refresh_export_enabled),
            on_page_nav=self._cycle_notebook_page,
        )
        sizer.Add(self._kb_panel, 1, wx.EXPAND)
        host.Layout()
        try:
            self.Layout()
        except RuntimeError:
            pass
        wx.CallAfter(self._kb_panel.start_initial_load)

    def _build_explain_actions(self, parent: wx.Window) -> wx.Panel:
        """Primary Explain action — lives above the AI WebView."""
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        _win_clear_tab_stop(panel)
        _win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.explain_btn = wx.Button(panel, label=_("Explain this issue"))
        _ensure_win_tab_stop(self.explain_btn)
        self.explain_btn.SetToolTip(
            _(
                "Ask AI to explain this issue in plain language "
                "(uses FIDO AI settings)"
            )
        )
        self.explain_btn.Bind(wx.EVT_BUTTON, self._on_explain)
        sizer.Add(self.explain_btn, 0, wx.TOP | wx.BOTTOM, 4)
        panel.SetSizer(sizer)
        panel.Hide()
        return panel

    def _build_explain_followup(self, parent: wx.Window) -> wx.Panel:
        """Follow-up row — stays below the AI WebView."""
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        _win_clear_tab_stop(panel)
        _win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.followup_ctrl, self.ask_btn = _add_followup_question_row(
            panel,
            sizer,
            on_ask=self._on_ask_followup,
            ask_enabled=False,
        )
        panel.SetSizer(sizer)
        panel.Hide()
        return panel

    def _build_fix_actions(self, parent: wx.Window) -> wx.Panel:
        """Suggest-fix actions — live above the AI WebView."""
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        _win_clear_tab_stop(panel)
        _win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.fix_btn = wx.Button(panel, label=_("Suggest fix with AI"))
        _ensure_win_tab_stop(self.fix_btn)
        self.fix_btn.SetToolTip(
            _(
                "Ask AI to suggest a minimal markup fix for this EPUB "
                "or eBraille issue (uses FIDO AI settings)"
            )
        )
        self.fix_btn.Bind(wx.EVT_BUTTON, self._on_fix)
        row.Add(self.fix_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self.fix_all_btn = wx.Button(panel, label=_("Suggest fix for many"))
        _ensure_win_tab_stop(self.fix_all_btn)
        self.fix_all_btn.SetToolTip(
            _(
                "Ask AI to suggest unique fixes for every issue with the "
                "same checker code in this report (uses FIDO AI settings)"
            )
        )
        self.fix_all_btn.Enable(self._matching_like_this > 1)
        self.fix_all_btn.Bind(wx.EVT_BUTTON, self._on_fix_all)
        row.Add(self.fix_all_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(row, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 4)
        panel.SetSizer(sizer)
        panel.Hide()
        return panel

    def _build_fix_followup(self, parent: wx.Window) -> wx.Panel:
        """Apply + follow-up — stay below the AI WebView."""
        panel = wx.Panel(parent, style=wx.TAB_TRAVERSAL)
        _win_clear_tab_stop(panel)
        _win_ensure_control_parent(panel)
        sizer = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.apply_fix_btn = wx.Button(panel, label=_("Apply fix and validate"))
        _ensure_win_tab_stop(self.apply_fix_btn)
        self.apply_fix_btn.SetToolTip(
            _(
                "Write the proposed fix into the publication, "
                "then re-check and confirm whether the issue is resolved"
            )
        )
        self.apply_fix_btn.Enable(False)
        self.apply_fix_btn.Bind(wx.EVT_BUTTON, self._on_apply_fix)
        row.Add(self.apply_fix_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(row, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 4)

        self.fix_followup_ctrl, self.fix_ask_btn = _add_followup_question_row(
            panel,
            sizer,
            on_ask=self._on_ask_followup,
            ask_enabled=False,
        )

        panel.SetSizer(sizer)
        panel.Hide()
        return panel

    def _page_key_for_selection(self, sel: int) -> str:
        if 0 <= sel < len(self._page_keys):
            return self._page_keys[sel]
        return self._PAGE_ISSUE

    def _on_notebook_page(self, event: wx.BookCtrlEvent) -> None:
        event.Skip()
        self._apply_active_page(self._page_key_for_selection(event.GetSelection()))

    def _apply_active_page(self, key: str, *, initial: bool = False) -> None:
        self._active_page_key = key
        show_issue = key == self._PAGE_ISSUE
        show_kb = key == self._PAGE_KB
        show_explain = key == self._PAGE_EXPLAIN
        show_fix = key == self._PAGE_FIX

        self._details_host.Show(show_issue)
        if self._kb_host is not None:
            self._kb_host.Show(show_kb)
        if self._show_ai and self._ai_output_host is not None:
            self._ai_output_host.Show(show_explain or show_fix)

        if self._issue_controls is not None:
            self._issue_controls.Show(show_issue)
        if getattr(self, "_explain_actions", None) is not None:
            self._explain_actions.Show(show_explain)
        if getattr(self, "_explain_followup", None) is not None:
            self._explain_followup.Show(show_explain)
        if getattr(self, "_fix_actions", None) is not None:
            self._fix_actions.Show(show_fix)
        if getattr(self, "_fix_followup", None) is not None:
            self._fix_followup.Show(show_fix)

        if not initial:
            if show_kb:
                self._realize_kb_panel()
            if self._show_ai and (show_explain or show_fix):
                if not getattr(self, "_ai_view_realized", False):
                    self._realize_ai_html_view()
                else:
                    self._paint_active_ai_channel(focus=False)

        self._refresh_host_tab_stops()
        self._refresh_export_enabled()
        try:
            self.Layout()
            if getattr(self, "_content_panel", None) is not None:
                self._content_panel.Layout()
        except RuntimeError:
            pass

    def _refresh_host_tab_stops(self) -> None:
        """Keep only the visible page host in the dialog Tab cycle."""
        details = getattr(self, "_details_host", None)
        kb = getattr(self, "_kb_host", None)
        ai = getattr(self, "_ai_output_host", None)
        key = self._active_page_key

        if details is not None:
            on_issue = key == self._PAGE_ISSUE
            if on_issue and getattr(self, "_details_is_webview", False):
                view = getattr(self, "details_view", None)
                if view is not None:
                    _refresh_ai_html_tab_stops(details, view, is_webview=True)
                if isinstance(details, _AiHtmlHostPanel):
                    details._accept_kbd_focus = True
            else:
                _win_clear_tab_stop(details)
                if isinstance(details, _AiHtmlHostPanel):
                    details._accept_kbd_focus = False

        if kb is not None:
            # KB panel manages its own chrome; keep the host out of the cycle.
            _win_clear_tab_stop(kb)

        if ai is not None:
            on_ai = key in (self._PAGE_EXPLAIN, self._PAGE_FIX)
            ready = (
                on_ai
                and getattr(self, "_ai_view_realized", False)
                and getattr(self, "_ai_output_is_webview", False)
            )
            if ready:
                view = getattr(self, "ai_output", None)
                if view is not None:
                    _refresh_ai_html_tab_stops(ai, view, is_webview=True)
                if isinstance(ai, _AiHtmlHostPanel):
                    ai._accept_kbd_focus = True
            else:
                _win_clear_tab_stop(ai)
                if isinstance(ai, _AiHtmlHostPanel):
                    ai._accept_kbd_focus = False

        content = getattr(self, "_content_panel", None)
        if content is not None:
            _win_clear_tab_stop(content)
            _win_ensure_control_parent(content)

        # Action panels must be CONTROLPARENT containers (not tab stops themselves).
        # If the panel is also WS_TABSTOP, forward Tab can land on/skip the panel
        # while Shift+Tab still finds the button inside.
        for panel in (
            getattr(self, "_explain_actions", None),
            getattr(self, "_fix_actions", None),
            getattr(self, "_explain_followup", None),
            getattr(self, "_fix_followup", None),
        ):
            if panel is None:
                continue
            try:
                if panel.IsShown():
                    _win_clear_tab_stop(panel)
                    _win_ensure_control_parent(panel)
            except RuntimeError:
                pass

    def _active_is_ai_page(self) -> bool:
        return self._active_page_key in (self._PAGE_EXPLAIN, self._PAGE_FIX)

    def _ai_channel_key(self, page: str | None = None) -> str:
        key = self._active_page_key if page is None else page
        return "fix" if key == self._PAGE_FIX else "explain"

    def _ai_markdown_for(self, channel: str | None = None) -> str:
        ch = channel or self._ai_channel_key()
        return self._fix_markdown if ch == "fix" else self._explain_markdown

    def _ai_plain_for(self, channel: str | None = None) -> bool:
        ch = channel or self._ai_channel_key()
        return self._fix_plain if ch == "fix" else self._explain_plain

    def _store_ai_channel(
        self,
        markdown_text: str,
        *,
        plain: bool,
        channel: str,
    ) -> None:
        from .ai.markdown_html import with_ai_disclaimer

        raw = markdown_text or ""
        stored = raw if plain else with_ai_disclaimer(raw)
        if channel == "fix":
            self._fix_markdown = stored
            self._fix_plain = plain
        else:
            self._explain_markdown = stored
            self._explain_plain = plain

    def _ai_channel_title(self, channel: str | None = None) -> str:
        ch = channel or self._ai_channel_key()
        if ch == "fix":
            return _("Suggested fix")
        return _("AI explanation")

    def _realize_ai_html_view(self) -> None:
        """Create the WebView after the dialog is visible (avoids a long freeze)."""
        if not self._show_ai or getattr(self, "_ai_view_realized", False):
            return
        host = getattr(self, "_ai_output_host", None)
        if host is None:
            return
        try:
            if not host:  # destroyed
                return
        except RuntimeError:
            return

        self.explain_btn.Enable(False)
        if self._show_fix:
            self.fix_btn.Enable(False)
            self.fix_all_btn.Enable(False)
        if hasattr(self, "ai_status"):
            self.ai_status.SetLabel(_("Loading AI view…"))

        view, is_webview = _create_ai_html_view(host)
        view.SetMinSize((-1, 240))
        if is_webview:
            import wx.html2 as html2

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_ai_webview_navigating)
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_ai_webview_loaded)
            from .ai.markdown_html import ai_idle_placeholder_page

            view.SetPage(
                ai_idle_placeholder_page(
                    title=self._ai_channel_title(), tab_exit=True
                ),
                "",
            )
        else:
            view.ChangeValue(_("AI-generated responses will be shown here."))

        sizer = host.GetSizer()
        if sizer is not None:
            sizer.Clear(delete_windows=True)
            sizer.Add(view, 1, wx.EXPAND)
        self.ai_output = view
        self._ai_output_is_webview = is_webview
        self._ai_view_realized = True
        self._ai_painted_channel = None
        _wire_ai_html_host(host, view, is_webview=is_webview)
        host.Layout()
        self.Layout()
        # Edge recreates child HWNDs asynchronously — keep host as the Tab gate.
        if is_webview:
            wx.CallLater(100, self._refresh_host_tab_stops)
            wx.CallLater(300, self._refresh_host_tab_stops)
        else:
            self._refresh_host_tab_stops()

        self.explain_btn.Enable(not self._busy)
        if self._show_fix:
            self.fix_btn.Enable(not self._busy)
            self.fix_all_btn.Enable(
                (not self._busy) and self._matching_like_this > 1
            )
        if hasattr(self, "ai_status") and not self._busy:
            if self.ai_status.GetLabel() == _("Loading AI view…"):
                self.ai_status.SetLabel("")
        # Paint whichever AI page is active (explain or fix may already have text).
        self._paint_active_ai_channel(focus=False)

    @staticmethod
    def _fix_available_for_result(check_result: CheckResult | None) -> bool:
        from .ai.context import fix_allowed_for_result

        return fix_allowed_for_result(check_result)

    def _on_dialog_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            wx.CallAfter(self._on_close_dialog)
            return
        event.Skip()

    def _on_accel_prev_page(self, _event: wx.CommandEvent) -> None:
        self._cycle_notebook_page(-1)

    def _on_accel_next_page(self, _event: wx.CommandEvent) -> None:
        self._cycle_notebook_page(1)

    def _cycle_notebook_page(self, delta: int) -> None:
        """Switch to previous/next details page (Ctrl+PgUp / Ctrl+PgDn)."""
        if self._notebook is None or not delta:
            return
        count = self._notebook.GetPageCount()
        if count <= 1:
            return
        sel = self._notebook.GetSelection()
        if sel < 0:
            return
        new_sel = sel + int(delta)
        if new_sel < 0 or new_sel >= count:
            return
        if new_sel == sel:
            return
        self._notebook.SetSelection(new_sel)

    def _on_open_kb_article(self, _event: wx.CommandEvent) -> None:
        if self._show_kb:
            self._select_page(self._PAGE_KB)
            return
        url = (self._kb_url or "").strip()
        if not url:
            return
        open_knowledge_base_url(self, url)

    def _open_kb_link_in_dialog(self, url: str) -> None:
        """Prefer the KB tab when present; otherwise open the standalone viewer."""
        if self._show_kb:
            self._select_page(self._PAGE_KB)
            self._realize_kb_panel()
            panel = self._kb_panel
            if panel is not None:
                from .kb.store import en_relative_path_from_url

                en_rel = en_relative_path_from_url(url)
                if en_rel:
                    panel.load_article(en_rel)
                    return
        open_knowledge_base_url(self, url)

    def _on_details_webview_navigating(self, event) -> None:
        """Tab/Escape leave the details WebView; open http(s) externally."""
        url = (event.GetURL() or "").strip()
        action = _webview_host_action(url)
        if action == "close":
            event.Veto()
            wx.CallAfter(self._on_close_dialog)
            return
        if action in ("page_prev", "page_next"):
            event.Veto()
            wx.CallAfter(
                self._cycle_notebook_page, -1 if action == "page_prev" else 1
            )
            return
        if action in ("next", "prev"):
            event.Veto()
            wx.CallAfter(self._leave_details_webview, action == "next")
            return
        if url.startswith(("http://", "https://", "mailto:")):
            event.Veto()
            if url.startswith(("http://", "https://")) and is_kb_url(url):
                wx.CallAfter(self._open_kb_link_in_dialog, url)
                return
            try:
                webbrowser.open(url)
            except OSError:
                pass
            return
        event.Skip()

    def _leave_details_webview(self, forward: bool) -> None:
        """Move dialog focus out of the issue-details WebView."""
        if forward:
            if _try_set_focus(getattr(self, "view_browser_btn", None)):
                return
            close_btn = self.FindWindowById(wx.ID_CLOSE)
            _try_set_focus(close_btn)
            return
        if self._notebook is not None and _try_set_focus(self._notebook):
            return
        if _try_set_focus(getattr(self, "ai_model_ctrl", None)):
            return
        close_btn = self.FindWindowById(wx.ID_CLOSE)
        _try_set_focus(close_btn)

    def _on_close_dialog(self, event: wx.Event | None = None) -> None:
        # Guard against SetEscapeId + CHAR_HOOK + checkmate://close all firing.
        # A second pass that Destroy()s while ShowModal is unwinding leaves the
        # main frame disabled/frozen.
        if getattr(self, "_closing", False):
            if isinstance(event, wx.CloseEvent):
                event.Veto()
            return
        self._closing = True
        # Cancel deferred WebView focus/reveal/paint chains (follow-up SetPage
        # + MoveFocus into Edge can otherwise keep the process alive after exit).
        self._ai_focus_gen = int(getattr(self, "_ai_focus_gen", 0)) + 1
        if self._ai_cancel is not None:
            self._ai_cancel.set()
        panel = getattr(self, "_kb_panel", None)
        if panel is not None:
            try:
                panel.mark_closing()
            except Exception:
                pass
        # Do not reclaim focus onto this dialog after teardown.
        self._close_ai_progress(reclaim_focus=False)
        if isinstance(event, wx.CloseEvent):
            # We EndModal ourselves on the next tick.
            event.Veto()
        # Never steal focus from Edge via WM_NEXTDLGCTL here — that deadlocks
        # WebView2 when Escape arrives through checkmate://close. Finish close
        # on the next idle so we are outside navigating/key handlers.
        wx.CallAfter(self._finish_close_dialog)

    def _finish_close_dialog(self) -> None:
        """Complete modal teardown after Escape/close (safe for WebView2)."""
        try:
            if not self:
                return
        except RuntimeError:
            return
        # Soft blur: focus the dialog HWND only (no dialog-manager dance).
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(self.GetHandle() or 0)
                if hwnd:
                    ctypes.windll.user32.SetFocus(hwnd)
            except Exception:
                pass
        try:
            if self.IsModal():
                self.EndModal(wx.ID_CLOSE)
            else:
                self.Destroy()
        except RuntimeError:
            pass

    def _ai_dialog_alive(self) -> bool:
        """False while closing or after the dialog window is gone."""
        if getattr(self, "_closing", False):
            return False
        try:
            return bool(self)
        except RuntimeError:
            return False

    def _ai_model_display_text(self) -> str:
        from .fido_settings import selected_model_service_string

        model = selected_model_service_string().strip()
        return model if model else _("(no model selected)")

    def _refresh_ai_model_display(self) -> None:
        if not self._show_ai:
            return
        self.ai_model_ctrl.SetValue(self._ai_model_display_text())

    def _has_ai_content(self, channel: str | None = None) -> bool:
        ch = channel or self._ai_channel_key()
        return bool((self._ai_markdown_for(ch) or "").strip()) or self._ai_plain_for(ch)

    def _set_apply_fix_enabled(self, enabled: bool) -> None:
        if not self._show_fix:
            return
        has_proposal = self._fix_proposal is not None or self._batch_proposal is not None
        self.apply_fix_btn.Enable(enabled and has_proposal)

    def _set_ai_export_enabled(self, enabled: bool) -> None:
        # Kept for call sites; refresh based on the active page.
        self._refresh_export_enabled()

    def _refresh_export_enabled(self) -> None:
        on_ai = self._show_ai and self._active_is_ai_page()
        on_kb = self._active_page_key == self._PAGE_KB
        has_ai = self._has_ai_content()
        if on_ai:
            can = (not self._busy) and has_ai
            self.view_browser_btn.Enable(can)
            self.save_html_btn.Enable(can)
            self.copy_btn.Enable(can)
            self.save_md_btn.Enable(can)
            self.save_md_btn.Show(True)
        elif on_kb:
            panel = self._kb_panel
            can = (not self._busy) and bool(panel is not None and panel.content_ready)
            self.view_browser_btn.Enable(can or (panel is not None and bool(panel.online_url())))
            self.save_html_btn.Enable(can)
            self.copy_btn.Enable(can)
            self.save_md_btn.Enable(False)
            self.save_md_btn.Show(self._show_ai)
        else:
            # Issue page — always exportable.
            self.view_browser_btn.Enable(not self._busy)
            self.save_html_btn.Enable(not self._busy)
            self.copy_btn.Enable(not self._busy)
            self.save_md_btn.Enable(False)
            self.save_md_btn.Show(self._show_ai)
        try:
            self.Layout()
        except RuntimeError:
            pass

    def _issue_browser_html(self) -> str:
        from .ai.markdown_html import issue_details_page

        return issue_details_page(
            self._issue, count=self._issue_count, tab_exit=False
        )

    def _ai_export_markdown(self) -> str:
        from .ai.markdown_html import export_explanation_markdown

        return export_explanation_markdown(
            self._issue,
            self._ai_markdown_for(),
            count=self._issue_count,
        )

    def _ai_browser_html(self) -> str:
        from .ai.markdown_html import markdown_to_browser_page

        title = self._ai_channel_title()
        code = (self._issue.code or "").strip()
        if code:
            title = f"{title} — {code}"
        return markdown_to_browser_page(self._ai_export_markdown(), title=title)

    def _on_ai_webview_navigating(self, event) -> None:
        """Open http(s)/mailto links externally; allow in-dialog SetPage loads."""
        url = (event.GetURL() or "").strip()
        action = _webview_host_action(url)
        if action == "close":
            event.Veto()
            wx.CallAfter(self._on_close_dialog)
            return
        if action in ("page_prev", "page_next"):
            event.Veto()
            wx.CallAfter(
                self._cycle_notebook_page, -1 if action == "page_prev" else 1
            )
            return
        if action in ("next", "prev"):
            event.Veto()
            wx.CallAfter(self._leave_ai_webview, action == "next")
            return
        if url.startswith(("http://", "https://", "mailto:")):
            event.Veto()
            if url.startswith(("http://", "https://")) and is_kb_url(url):
                wx.CallAfter(self._open_kb_link_in_dialog, url)
                return
            try:
                webbrowser.open(url)
            except OSError:
                pass
            return
        event.Skip()

    def _leave_ai_webview(self, forward: bool) -> None:
        """Move dialog focus out of the WebView after Tab-at-boundary / Ctrl+Tab."""
        if forward:
            # Below the WebView: follow-up / apply controls.
            if self._active_page_key == self._PAGE_EXPLAIN:
                if _try_set_focus(getattr(self, "followup_ctrl", None)):
                    return
                if _try_set_focus(getattr(self, "ask_btn", None)):
                    return
            elif self._active_page_key == self._PAGE_FIX:
                if _try_set_focus(getattr(self, "apply_fix_btn", None)):
                    return
                if _try_set_focus(getattr(self, "fix_followup_ctrl", None)):
                    return
                if _try_set_focus(getattr(self, "fix_ask_btn", None)):
                    return
            if _try_set_focus(getattr(self, "view_browser_btn", None)):
                return
            close_btn = self.FindWindowById(wx.ID_CLOSE)
            _try_set_focus(close_btn)
            return
        # Above the WebView: primary Suggest / Explain actions, then notebook.
        if self._active_page_key == self._PAGE_EXPLAIN:
            if _try_set_focus(getattr(self, "explain_btn", None)):
                return
        elif self._active_page_key == self._PAGE_FIX:
            if _try_set_focus(getattr(self, "fix_all_btn", None)):
                return
            if _try_set_focus(getattr(self, "fix_btn", None)):
                return
        if self._notebook is not None and _try_set_focus(self._notebook):
            return
        if _try_set_focus(getattr(self, "ai_model_ctrl", None)):
            return
        close_btn = self.FindWindowById(wx.ID_CLOSE)
        _try_set_focus(close_btn)

    def _on_ai_webview_loaded(self, event) -> None:
        """After SetPage, move focus into the WebView for screen-reader review."""
        event.Skip()
        if not self._ai_dialog_alive():
            return
        host = getattr(self, "_ai_output_host", None)
        try:
            if host is not None:
                _refresh_ai_html_tab_stops(
                    host, self.ai_output, is_webview=True
                )
        except RuntimeError:
            pass
        if not self._ai_focus_after_load:
            return
        self._ai_focus_after_load = False
        md = self._ai_markdown_for()
        if _markdown_has_latest_followup(md or ""):
            self._schedule_reveal_latest_followup()
        else:
            self._schedule_ai_output_focus()

    def _ai_dialog_html(self, *, plain: bool = False, channel: str | None = None) -> str:
        """HTML document for the in-dialog WebView (same styling as browser view)."""
        from .ai.markdown_html import markdown_to_browser_page

        # Keep the <title> generic. Appending the Ace/EPUBCheck code (e.g.
        # aria-required-children) makes Narrator/NVDA announce it as the
        # document name, which sounds like a dialog accessibility error.
        ch = channel or self._ai_channel_key()
        title = self._ai_channel_title(ch)
        body = self._ai_markdown_for(ch)
        if plain:
            return markdown_to_browser_page(
                body or "",
                title=title,
                plain=True,
                tab_exit=True,
            )
        return markdown_to_browser_page(
            body or "",
            title=title,
            plain=False,
            tab_exit=True,
        )

    def _focus_ai_output_now(self) -> bool:
        """Try once to put focus on the AI pane host (not the Edge document)."""
        if not self._ai_dialog_alive():
            return True
        if not self._show_ai or not getattr(self, "_ai_view_realized", False):
            return True
        try:
            if not self:
                return True
        except RuntimeError:
            return True
        try:
            self.Raise()
        except RuntimeError:
            return False
        _win_force_foreground(self)
        # Keep keyboard in wx on the host gate — diving into Edge after Explain
        # often leaves focus limbo where Escape/Tab do nothing.
        if self._ai_output_is_webview:
            host = getattr(self, "_ai_output_host", None)
            view = self.ai_output
            if host is not None and view is not None:
                _refresh_ai_html_tab_stops(host, view, is_webview=True)
                if isinstance(host, _AiHtmlHostPanel):
                    host._accept_kbd_focus = True
            return _try_set_focus(host if host is not None else view)
        return _focus_ai_html_view(
            self.ai_output,
            is_webview=False,
            retries=0,
        )

    def _schedule_ai_output_focus(self) -> None:
        """Focus the AI pane after progress UI teardown (and WebView load)."""
        # One coordinated retry chain — supersedes any previous schedule so we
        # never stack parallel “wx webview” focus storms.
        if not self._ai_dialog_alive():
            return
        self._ai_focus_gen = int(getattr(self, "_ai_focus_gen", 0)) + 1
        gen = self._ai_focus_gen

        def _attempt(remaining: int) -> None:
            if not self._ai_dialog_alive():
                return
            if gen != getattr(self, "_ai_focus_gen", 0):
                return
            if self._focus_ai_output_now():
                return
            if remaining > 0:
                wx.CallLater(200, lambda: _attempt(remaining - 1))

        wx.CallAfter(lambda: _attempt(5))

    def _schedule_reveal_latest_followup(self) -> None:
        """After a follow-up, scroll/focus the newest question in the HTML view."""
        if not self._ai_dialog_alive():
            return
        self._ai_focus_gen = int(getattr(self, "_ai_focus_gen", 0)) + 1
        gen = self._ai_focus_gen

        def _attempt(remaining: int) -> None:
            if not self._ai_dialog_alive():
                return
            if gen != getattr(self, "_ai_focus_gen", 0):
                return
            if self._reveal_latest_followup_now():
                return
            if remaining > 0:
                wx.CallLater(200, lambda: _attempt(remaining - 1))

        wx.CallAfter(lambda: _attempt(5))

    def _reveal_latest_followup_now(self) -> bool:
        """Try once to put viewport + SR focus on the newest follow-up question."""
        if not self._ai_dialog_alive():
            return True
        if not self._show_ai or not getattr(self, "_ai_view_realized", False):
            return True
        try:
            if not self:
                return True
            self.Raise()
        except RuntimeError:
            return False
        _win_force_foreground(self)
        if self._ai_output_is_webview:
            # Prefer JS scroll only — MoveFocus/synthetic click into Edge after
            # follow-up has been linked to unclean app shutdown on Windows.
            # Also avoid el.focus() in-page: that steals Win32 focus into Edge
            # while we intentionally keep the host panel as the keyboard gate.
            try:
                _webview_run_script(
                    self.ai_output, _WEBVIEW_SCROLL_LATEST_FOLLOWUP_JS
                )
            except Exception:
                return False
            # Land keyboard/SR on the WebView host (Enter still activates Edge).
            host = getattr(self, "_ai_output_host", None)
            return _try_set_focus(host if host is not None else self.ai_output)
        # TextCtrl fallback: jump near the latest "You asked" / question text.
        view = self.ai_output
        if hasattr(view, "SetInsertionPoint") and hasattr(view, "GetValue"):
            try:
                text = view.GetValue() or ""
                # Jump near the latest follow-up separator when present.
                idx = text.rfind("---")
                if idx < 0:
                    idx = 0
                view.SetInsertionPoint(min(idx, len(text)))
                view.ShowPosition(min(idx, len(text)))
                view.SetFocus()
            except RuntimeError:
                return False
            return True
        return _try_set_focus(view)

    def _paint_active_ai_channel(self, *, focus: bool = False) -> None:
        """Show the stored Explain or Fix transcript in the shared AI WebView."""
        if getattr(self, "_closing", False):
            return
        if not self._active_is_ai_page():
            return
        if not getattr(self, "_ai_view_realized", False):
            return
        channel = self._ai_channel_key()
        markdown_text = self._ai_markdown_for(channel)
        plain = self._ai_plain_for(channel)
        has = bool((markdown_text or "").strip()) or plain
        self._ai_painted_channel = channel
        if not has:
            if self._ai_output_is_webview:
                from .ai.markdown_html import ai_idle_placeholder_page

                self._ai_focus_after_load = False
                self.ai_output.SetPage(
                    ai_idle_placeholder_page(
                        title=self._ai_channel_title(channel), tab_exit=True
                    ),
                    "",
                )
            else:
                self.ai_output.SetValue(_("AI-generated responses will be shown here."))
            self._set_ai_export_enabled(False)
            return
        if self._ai_output_is_webview:
            self._ai_focus_after_load = bool(focus)
            self.ai_output.SetPage(
                self._ai_dialog_html(plain=plain, channel=channel), ""
            )
            if focus and not self._ai_focus_after_load:
                if channel == "explain" and _markdown_has_latest_followup(
                    markdown_text or ""
                ):
                    self._schedule_reveal_latest_followup()
                elif channel == "fix" and _markdown_has_latest_followup(
                    markdown_text or ""
                ):
                    self._schedule_reveal_latest_followup()
                else:
                    self._schedule_ai_output_focus()
        else:
            self.ai_output.SetValue(markdown_text)
            if _markdown_has_latest_followup(markdown_text or ""):
                if focus:
                    self._schedule_reveal_latest_followup()
            else:
                self.ai_output.SetInsertionPoint(0)
                if focus:
                    self._schedule_ai_output_focus()
        self._set_ai_export_enabled(True)

    def _set_ai_content(
        self,
        markdown_text: str,
        *,
        plain: bool = False,
        focus: bool = False,
        channel: str | None = None,
    ) -> None:
        if getattr(self, "_closing", False):
            return
        ch = channel or self._ai_channel_key()
        self._store_ai_channel(markdown_text, plain=plain, channel=ch)
        if not getattr(self, "_ai_view_realized", False):
            self._set_ai_export_enabled(self._has_ai_content())
            return
        # Only paint when the user is looking at this channel (or no AI page yet).
        if self._active_is_ai_page() and self._ai_channel_key() != ch:
            self._refresh_export_enabled()
            return
        self._paint_active_ai_channel(focus=focus)

    def _on_view_browser(self, _event: wx.Event) -> None:
        if self._active_is_ai_page():
            if not self._has_ai_content():
                return
            html = self._ai_browser_html()
            prefix = "checkmate-explain-"
            use_temp = True
        elif self._active_page_key == self._PAGE_KB:
            panel = self._kb_panel
            if panel is None:
                return
            # Prefer the live online article when exporting "View in browser".
            url = panel.online_url()
            if url:
                try:
                    webbrowser.open(url)
                    if self._show_ai and hasattr(self, "ai_status"):
                        self.ai_status.SetLabel(_("Opened in browser."))
                except OSError as exc:
                    wx.MessageBox(
                        _(
                            "Could not open the Knowledge Base article in a browser:\n{error}",
                            error=exc,
                        ),
                        _("Error"),
                        wx.OK | wx.ICON_ERROR,
                        self,
                    )
                return
            html = panel.export_html()
            if not html.strip():
                return
            prefix = "checkmate-kb-"
            use_temp = True
        else:
            html = self._issue_browser_html()
            prefix = "checkmate-issue-"
            use_temp = True
        if not use_temp:
            return
        try:
            fd, name = tempfile.mkstemp(prefix=prefix, suffix=".html", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(html)
            webbrowser.open(Path(name).as_uri())
            if self._show_ai and hasattr(self, "ai_status"):
                self.ai_status.SetLabel(_("Opened in browser."))
        except OSError as exc:
            if self._active_is_ai_page():
                msg = _(
                    "Could not open the explanation in a browser:\n{error}",
                    error=exc,
                )
            elif self._active_page_key == self._PAGE_KB:
                msg = _(
                    "Could not open the Knowledge Base article in a browser:\n{error}",
                    error=exc,
                )
            else:
                msg = _(
                    "Could not open the issue details in a browser:\n{error}",
                    error=exc,
                )
            wx.MessageBox(msg, _("Error"), wx.OK | wx.ICON_ERROR, self)

    def _on_save_html(self, _event: wx.Event) -> None:
        from .ai.markdown_html import explanation_filename_stem

        if self._active_is_ai_page():
            if not self._has_ai_content():
                return
            stem = explanation_filename_stem(self._issue.code)
            title = _("Save AI explanation as HTML")
            html = self._ai_browser_html()
        elif self._active_page_key == self._PAGE_KB:
            panel = self._kb_panel
            if panel is None or not panel.content_ready:
                return
            stem = explanation_filename_stem(self._kb_en_rel or "kb-article")
            title = _("Save Knowledge Base article as HTML")
            html = panel.export_html()
        else:
            code = (self._issue.code or "issue").strip() or "issue"
            stem = explanation_filename_stem(code)
            title = _("Save issue details as HTML")
            html = self._issue_browser_html()
        with wx.FileDialog(
            self,
            title,
            defaultFile=f"{stem}.html",
            wildcard=_("HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
        try:
            path.write_text(html, encoding="utf-8")
            if self._show_ai and hasattr(self, "ai_status"):
                self.ai_status.SetLabel(_("Saved to {path}", path=path))
        except OSError as exc:
            wx.MessageBox(
                _("Could not save the explanation:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_save_markdown(self, _event: wx.Event) -> None:
        if not self._active_is_ai_page() or not self._has_ai_content():
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
            if hasattr(self, "ai_status"):
                self.ai_status.SetLabel(_("Saved to {path}", path=path))
        except OSError as exc:
            wx.MessageBox(
                _("Could not save the explanation:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_copy_clipboard(self, _event: wx.Event) -> None:
        if self._active_is_ai_page():
            if not self._has_ai_content():
                return
            text = self._ai_markdown_for() or ""
            if self._active_page_key == self._PAGE_FIX:
                copied_msg = _("The suggested fix was copied to the clipboard.")
            else:
                copied_msg = _("The explanation was copied to the clipboard.")
        elif self._active_page_key == self._PAGE_KB:
            panel = self._kb_panel
            if panel is None or not panel.content_ready:
                return
            text = panel.export_plain()
            copied_msg = _("The Knowledge Base article was copied to the clipboard.")
        else:
            text = self._details_plain or ""
            copied_msg = _("The issue details were copied to the clipboard.")
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()
            if self._show_ai and hasattr(self, "ai_status"):
                self.ai_status.SetLabel(_("Copied to clipboard."))
            wx.MessageBox(
                copied_msg,
                _("Copied to clipboard"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            self.copy_btn.SetFocus()
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
            self._refresh_export_enabled()
            return
        self.explain_btn.Enable(not busy)
        if self._show_fix:
            self.fix_btn.Enable(not busy)
            self.fix_all_btn.Enable((not busy) and self._matching_like_this > 1)
            self._set_apply_fix_enabled(not busy)
        self._sync_followup_enabled()
        self._refresh_export_enabled()
        if status:
            self.ai_status.SetLabel(status)
        elif not busy:
            self.ai_status.SetLabel("")
        self.Layout()

    def _sync_followup_enabled(self) -> None:
        """Enable Ask + edit only when there is AI content to follow up on."""
        if not self._show_ai:
            return
        busy = self._busy
        explain_ok = (
            (not busy)
            and self._explain_session is not None
            and self._has_ai_content("explain")
        )
        self.ask_btn.Enable(explain_ok)
        self.followup_ctrl.Enable(explain_ok)
        if self._show_fix:
            fix_ok = (
                (not busy)
                and self._fix_session is not None
                and self._has_ai_content("fix")
            )
            if hasattr(self, "fix_ask_btn"):
                self.fix_ask_btn.Enable(fix_ok)
            if hasattr(self, "fix_followup_ctrl"):
                self.fix_followup_ctrl.Enable(fix_ok)

    def _ai_status_callback(self, message: str) -> None:
        def update() -> None:
            if self._ai_progress is not None:
                cont = _pulse_progress(self._ai_progress, message)
                if not cont and self._ai_cancel is not None:
                    self._ai_cancel.set()
            if self._show_ai:
                self.ai_status.SetLabel(message)

        wx.CallAfter(update)

    def _present_ai_progress(self, message: str) -> None:
        """Paint the progress dialog and give it focus for screen readers."""
        dlg = self._ai_progress
        if dlg is None:
            return
        _present_progress_dialog(dlg, message)

    def _open_ai_progress(self, title: str, message: str) -> threading.Event:
        # Closing a previous dialog must not reclaim parent focus — that steals
        # activation from the new progress window before screen readers hear it.
        self._close_ai_progress(reclaim_focus=False)
        cancel = threading.Event()
        self._ai_cancel = cancel
        self._ai_progress = wx.ProgressDialog(
            title,
            message,
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT,
        )
        self._present_ai_progress(message)
        self._ai_progress_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_ai_progress_timer, self._ai_progress_timer)
        self._ai_progress_timer.Start(200)
        return cancel

    def _on_ai_progress_timer(self, _event: wx.TimerEvent) -> None:
        dlg = self._ai_progress
        cancel = self._ai_cancel
        if dlg is None or cancel is None:
            return
        cont = _pulse_progress(dlg)  # animation only — do not announce
        if not cont:
            cancel.set()
            _pulse_progress(dlg, _("Cancelling…"))
            self._close_ai_progress(reclaim_focus=True)
            self._set_busy(False)
            if self._show_ai:
                self.ai_status.SetLabel(_("Cancelled."))

    def _close_ai_progress(self, *, reclaim_focus: bool = True) -> None:
        # Bump generation so a deferred reclaim from an older close cannot steal
        # focus from a newly opened progress dialog.
        self._ai_progress_reclaim_gen = (
            int(getattr(self, "_ai_progress_reclaim_gen", 0)) + 1
        )
        reclaim_gen = self._ai_progress_reclaim_gen
        timer = self._ai_progress_timer
        if timer is not None:
            try:
                if timer.IsRunning():
                    timer.Stop()
            except RuntimeError:
                pass
            self._ai_progress_timer = None
        dlg = self._ai_progress
        self._ai_progress = None
        _clear_progress_announce(dlg)
        if dlg is not None:
            try:
                dlg.Destroy()
            except RuntimeError:
                pass
            if not reclaim_focus:
                return
            # Modal progress teardown often leaves no foreground window; reclaim
            # activation for this dialog so later SetFocus into the WebView sticks.
            def _reclaim() -> None:
                if reclaim_gen != getattr(self, "_ai_progress_reclaim_gen", 0):
                    return
                if getattr(self, "_ai_progress", None) is not None:
                    return
                try:
                    if not self:
                        return
                    self.Raise()
                    _win_force_foreground(self)
                except RuntimeError:
                    return

            wx.CallAfter(_reclaim)
            wx.CallLater(100, _reclaim)

    def _fail_ai_libraries(self, detail: str) -> None:
        """UI-thread handler when LiteLLM preload fails in a worker."""
        self._close_ai_progress(reclaim_focus=True)
        self._set_busy(False)
        from .ai.explain import error_message_for_key

        msg = error_message_for_key("no_litellm", detail=detail)
        if hasattr(self, "ai_status"):
            self.ai_status.SetLabel(_("Could not load AI libraries."))
        wx.MessageBox(msg, _("Error"), wx.OK | wx.ICON_ERROR, self)

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

    def _on_explain(self, _event: wx.Event) -> None:
        if self._busy:
            return
        self._select_page(self._PAGE_EXPLAIN)
        cancel = self._open_ai_progress(
            _("Explain this issue"), _("Loading AI libraries…")
        )
        self._set_busy(True, _("Loading AI libraries…"))
        issue = self._issue
        result = self._check_result
        status_cb = self._ai_status_callback

        def work() -> None:
            from .ai.explain import ExplainResult, explain_issue
            from .ai.litellm_client import preload_litellm

            # Keep preload off the UI thread so the progress dialog can paint,
            # pulse, and be announced by screen readers.
            ok, detail = preload_litellm()
            if not ok:
                wx.CallAfter(self._fail_ai_libraries, detail)
                return
            if cancel.is_set():
                return
            try:
                out = explain_issue(
                    issue,
                    result,
                    cancel_event=cancel,
                    status_callback=status_cb,
                )
            except Exception as exc:
                out = ExplainResult(ok=False, error_key="provider_error", text=str(exc))
            if cancel.is_set():
                return
            try:
                wx.PostEvent(self, ExplainAiEvent(kind="explain", result=out))
            except RuntimeError:
                # Dialog was closed while the request was running.
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_fix(self, _event: wx.Event) -> None:
        if self._busy or not self._show_fix:
            return
        self._select_page(self._PAGE_FIX)
        self._fix_proposal = None
        self._batch_proposal = None
        self._set_apply_fix_enabled(False)
        cancel = self._open_ai_progress(
            _("Suggest fix with AI"), _("Loading AI libraries…")
        )
        self._set_busy(True, _("Loading AI libraries…"))
        issue = self._issue
        result = self._check_result
        status_cb = self._ai_status_callback

        def work() -> None:
            from .ai.fix import FixResult, propose_fix
            from .ai.litellm_client import preload_litellm

            ok, detail = preload_litellm()
            if not ok:
                wx.CallAfter(self._fail_ai_libraries, detail)
                return
            if cancel.is_set():
                return
            try:
                out = propose_fix(
                    issue,
                    result,
                    cancel_event=cancel,
                    status_callback=status_cb,
                )
            except Exception as exc:
                out = FixResult(ok=False, error_key="provider_error", text=str(exc))
            if cancel.is_set():
                return
            try:
                wx.PostEvent(self, FixAiEvent(result=out))
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_fix_all(self, _event: wx.Event) -> None:
        if self._busy or not self._show_fix:
            return
        if self._matching_like_this <= 1:
            return
        self._select_page(self._PAGE_FIX)
        self._fix_proposal = None
        self._batch_proposal = None
        self._set_apply_fix_enabled(False)
        cancel = self._open_ai_progress(
            _("Suggest fix for many"), _("Loading AI libraries…")
        )
        self._set_busy(True, _("Loading AI libraries…"))
        issue = self._issue
        result = self._check_result
        status_cb = self._ai_status_callback

        def work() -> None:
            from .ai.fix import FixResult, propose_batch_fix
            from .ai.litellm_client import preload_litellm

            ok, detail = preload_litellm()
            if not ok:
                wx.CallAfter(self._fail_ai_libraries, detail)
                return
            if cancel.is_set():
                return
            try:
                out = propose_batch_fix(
                    issue,
                    result,
                    cancel_event=cancel,
                    status_callback=status_cb,
                )
            except Exception as exc:
                out = FixResult(ok=False, error_key="provider_error", text=str(exc))
            if cancel.is_set():
                return
            try:
                wx.PostEvent(self, FixAiEvent(result=out))
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_ask_followup(self, _event: wx.Event) -> None:
        channel = self._ai_channel_key()
        if channel == "fix":
            session = self._fix_session
            question_ctrl = getattr(self, "fix_followup_ctrl", None)
            page = self._PAGE_FIX
        else:
            session = self._explain_session
            question_ctrl = self.followup_ctrl
            page = self._PAGE_EXPLAIN
        if self._busy or session is None or question_ctrl is None:
            return
        question = question_ctrl.GetValue().strip()
        if not question:
            return
        self._select_page(page)
        cancel = self._open_ai_progress(_("Follow-up"), _("Loading AI libraries…"))
        self._set_busy(True, _("Loading AI libraries…"))
        status_cb = self._ai_status_callback
        issue = self._issue
        check_result = self._check_result

        def work() -> None:
            from .ai.explain import ExplainResult, ask_followup
            from .ai.fix import FixResult, ask_fix_followup
            from .ai.litellm_client import preload_litellm

            ok, detail = preload_litellm()
            if not ok:
                wx.CallAfter(self._fail_ai_libraries, detail)
                return
            if cancel.is_set():
                return

            def _thinking() -> None:
                if self._ai_progress is not None:
                    _pulse_progress(self._ai_progress, _("Thinking…"))
                self._set_busy(True, _("Thinking…"))

            wx.CallAfter(_thinking)
            try:
                if channel == "fix":
                    out = ask_fix_followup(
                        session,
                        question,
                        issue=issue,
                        check_result=check_result,
                        cancel_event=cancel,
                        status_callback=status_cb,
                    )
                else:
                    out = ask_followup(
                        session,
                        question,
                        cancel_event=cancel,
                        status_callback=status_cb,
                    )
            except Exception as exc:
                if channel == "fix":
                    out = FixResult(
                        ok=False,
                        error_key="provider_error",
                        text=str(exc),
                        session=session,
                    )
                else:
                    out = ExplainResult(
                        ok=False,
                        error_key="provider_error",
                        text=str(exc),
                        session=session,
                    )
            # Prefer showing a completed reply over a late cancel race with the
            # progress dialog (Pulse can flip to cancelled while the reply lands).
            if cancel.is_set() and not (out.ok and (out.text or "").strip()):
                return
            try:
                if channel == "fix":
                    wx.PostEvent(
                        self,
                        FixAiEvent(result=out, question=question),
                    )
                else:
                    wx.PostEvent(
                        self,
                        ExplainAiEvent(
                            kind="followup", result=out, question=question
                        ),
                    )
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_explain_ai_event(self, event: ExplainAiEvent) -> None:
        from .ai.explain import error_message_for_key
        from .ai.markdown_html import append_followup_markdown

        result = event.result
        kind = getattr(event, "kind", "explain")
        self._close_ai_progress()
        self._set_busy(False)
        self._refresh_ai_model_display()
        if not result.ok:
            if result.error_key == "cancelled":
                self.ai_status.SetLabel(_("Cancelled."))
                return
            msg = error_message_for_key(result.error_key, detail=result.text or "")
            self._set_ai_content(msg, plain=True, focus=True, channel="explain")
            self.ai_status.SetLabel(_("Could not explain this issue."))
            if result.session is not None:
                self._explain_session = result.session
            self._sync_followup_enabled()
            return

        self._explain_session = result.session
        if kind == "followup":
            question = getattr(event, "question", "") or ""
            md = append_followup_markdown(
                self._explain_markdown,
                heading=_("Follow-up"),
                question=question,
                answer=result.text or "",
            )
            # Defer SetPage until after ProgressDialog teardown / Layout so Edge
            # WebView2 reliably paints the updated document (follow-ups were
            # landing in markdown but staying off-screen or unpainted).

            def _paint_followup() -> None:
                if not self._ai_dialog_alive():
                    return
                try:
                    if not self:
                        return
                except RuntimeError:
                    return
                # Paint, scroll to the new question, then focus the WebView host.
                self._set_ai_content(md, focus=True, channel="explain")
                self.followup_ctrl.SetValue("")
                self.ai_status.SetLabel(_("Done"))
                self._sync_followup_enabled()

            wx.CallAfter(_paint_followup)
            try:
                from .telemetry import log_ai_explain

                log_ai_explain(followup=True)
            except Exception:
                pass
            return

        self._set_ai_content(result.text or "", focus=True, channel="explain")
        self._sync_followup_enabled()
        try:
            from .telemetry import log_ai_explain

            log_ai_explain(followup=False)
        except Exception:
            pass
        self.ai_status.SetLabel(_("Done"))

    def _on_fix_ai_event(self, event: FixAiEvent) -> None:
        from .ai.fix import error_message_for_key
        from .ai.markdown_html import append_followup_markdown

        result = event.result
        question = (getattr(event, "question", "") or "").strip()
        self._close_ai_progress()
        self._set_busy(False)
        self._refresh_ai_model_display()
        if not result.ok:
            if result.error_key == "cancelled":
                self.ai_status.SetLabel(_("Cancelled."))
                return
            msg = error_message_for_key(result.error_key, detail=result.text or "")
            if not question:
                self._fix_proposal = None
                self._batch_proposal = None
                self._set_apply_fix_enabled(False)
            self._set_ai_content(msg, plain=True, focus=True, channel="fix")
            self.ai_status.SetLabel(
                _("Could not continue the fix conversation.")
                if question
                else _("Could not propose a fix.")
            )
            if result.session is not None:
                self._fix_session = result.session
            self._sync_followup_enabled()
            return

        self._fix_session = result.session

        if question:
            # Follow-up on the proposed fix (e.g. supply a real ISBN).
            if result.proposal is not None:
                self._fix_proposal = result.proposal
                self._batch_proposal = None
            elif result.batch is not None:
                self._batch_proposal = result.batch
                self._fix_proposal = None
            md = append_followup_markdown(
                self._fix_markdown,
                heading=_("Follow-up"),
                question=question,
                answer=result.text or "",
            )

            def _paint_fix_followup() -> None:
                if not self._ai_dialog_alive():
                    return
                try:
                    if not self:
                        return
                except RuntimeError:
                    return
                self._set_ai_content(md, focus=True, channel="fix")
                if hasattr(self, "fix_followup_ctrl"):
                    self.fix_followup_ctrl.SetValue("")
                if result.proposal is not None or result.batch is not None:
                    self._set_apply_fix_enabled(True)
                    self.ai_status.SetLabel(
                        _("Fix updated. Review, then Apply fix and validate.")
                    )
                else:
                    self.ai_status.SetLabel(_("Done"))
                self._sync_followup_enabled()

            wx.CallAfter(_paint_fix_followup)
            try:
                from .telemetry import log_ai_fix

                log_ai_fix(applied=False)
            except Exception:
                pass
            return

        self._fix_proposal = result.proposal
        self._batch_proposal = result.batch
        self._set_ai_content(result.text or "", focus=True, channel="fix")
        self._sync_followup_enabled()
        try:
            from .telemetry import log_ai_fix

            log_ai_fix(applied=False)
        except Exception:
            pass
        if result.proposal is None and result.batch is None:
            self._set_apply_fix_enabled(False)
            self.ai_status.SetLabel(_("Done"))
        else:
            self._set_apply_fix_enabled(True)
            if result.batch is not None:
                self.ai_status.SetLabel(
                    _(
                        "Batch fix suggested ({n} patch(es)). Review, then "
                        "Apply fix and validate."
                    ).format(n=len(result.batch.patches))
                )
            else:
                self.ai_status.SetLabel(
                    _("Fix suggested. Review, then Apply fix and validate.")
                )

    def _on_apply_fix(self, _event: wx.Event) -> None:
        if self._busy or not self._show_fix:
            return
        if self._fix_proposal is None and self._batch_proposal is None:
            return
        target = ""
        if self._check_result and self._check_result.target_path:
            target = self._check_result.target_path
        if not target:
            wx.MessageBox(
                _("The publication path is missing or no longer exists."),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        self._set_busy(True, _("Applying fix…"))
        proposal = self._fix_proposal
        batch = self._batch_proposal

        def work() -> None:
            from .ai.fix import apply_proposed_fix, apply_proposed_fixes

            try:
                if batch is not None:
                    out = apply_proposed_fixes(batch.patches, target)
                else:
                    out = apply_proposed_fix(proposal, target)
            except Exception as exc:
                from .epub_package import ApplyResult

                out = ApplyResult(ok=False, error_key="write_failed", detail=str(exc))
            try:
                wx.PostEvent(self, ApplyFixEvent(result=out))
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_apply_fix_event(self, event: ApplyFixEvent) -> None:
        from .ai.fix import PendingFixVerify, error_message_for_key, parse_extra_backups
        from .epub_package import restore_path_for_apply

        result = event.result
        self._set_busy(False)
        if not result.ok:
            msg = error_message_for_key(result.error_key, detail=result.detail or "")
            self.ai_status.SetLabel(_("Could not apply the fix."))
            wx.MessageBox(msg, _("Error"), wx.OK | wx.ICON_ERROR, self)
            self._set_apply_fix_enabled(True)
            return

        proposal = self._fix_proposal
        batch = self._batch_proposal
        self._fix_proposal = None
        self._batch_proposal = None
        self._set_apply_fix_enabled(False)
        try:
            from .telemetry import log_ai_fix

            log_ai_fix(applied=True)
        except Exception:
            pass
        target = ""
        if self._check_result and self._check_result.target_path:
            target = self._check_result.target_path
        changelog_path = ""
        extra_backups = parse_extra_backups(result.detail or "")
        if target and result.backup_path and self._check_result is not None:
            if batch is not None:
                rationale = batch.rationale
                member = result.member or (
                    batch.patches[0].file if batch.patches else ""
                )
                try:
                    from .edit_log import log_batch_fix_applied

                    log_file = log_batch_fix_applied(
                        target_path=target,
                        issue=self._issue,
                        backup_path=result.backup_path,
                        patches=[
                            (p.file, p.original, p.replacement) for p in batch.patches
                        ],
                        rationale=rationale,
                        matched_issue_count=batch.matched_issue_count
                        or self._matching_like_this,
                        extra_backups=extra_backups,
                    )
                    changelog_path = str(log_file)
                except OSError:
                    changelog_path = ""
                self.applied_fix_verify = PendingFixVerify(
                    issue=self._issue,
                    target_path=target,
                    backup_path=result.backup_path,
                    restore_to=restore_path_for_apply(target, result),
                    before_result=self._check_result,
                    member=member,
                    rationale=rationale,
                    changelog_path=changelog_path,
                    batch_mode=True,
                    patch_count=len(batch.patches),
                    matched_before=batch.matched_issue_count or self._matching_like_this,
                    extra_backups=extra_backups,
                )
            else:
                member = result.member or (proposal.file if proposal else "")
                rationale = proposal.rationale if proposal else ""
                original = proposal.original if proposal else ""
                replacement = proposal.replacement if proposal else ""
                try:
                    from .edit_log import log_fix_applied

                    log_file = log_fix_applied(
                        target_path=target,
                        issue=self._issue,
                        member=member,
                        backup_path=result.backup_path,
                        rationale=rationale,
                        original=original,
                        replacement=replacement,
                    )
                    changelog_path = str(log_file)
                except OSError:
                    changelog_path = ""
                self.applied_fix_verify = PendingFixVerify(
                    issue=self._issue,
                    target_path=target,
                    backup_path=result.backup_path,
                    restore_to=restore_path_for_apply(target, result),
                    before_result=self._check_result,
                    member=member,
                    rationale=rationale,
                    original=original,
                    replacement=replacement,
                    changelog_path=changelog_path,
                )
        else:
            self.applied_fix_verify = None
        # Close and let the main window re-check (verify / optional revert after).
        if self.IsModal():
            self.EndModal(wx.ID_APPLY)
        else:
            self.Destroy()


class AiOverviewDialog(wx.Dialog):
    """Show a report-level AI overview with view/save/copy actions."""

    def __init__(
        self,
        parent: wx.Window,
        *,
        markdown_text: str,
        result: CheckResult,
        session=None,
    ) -> None:
        super().__init__(
            parent,
            title=_("AI overview"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        self.SetSize((720, 640))
        self._result = result
        self._session = session
        self._busy = False
        self._ai_cancel: threading.Event | None = None
        self._ai_progress: wx.ProgressDialog | None = None
        self._ai_progress_timer: wx.Timer | None = None
        self._ai_plain = False
        self._ai_output_is_webview = False
        self._ai_focus_after_load = False
        self._ai_view_realized = False
        self._ai_focus_gen = 0
        self._closing = False
        from .ai.markdown_html import with_ai_disclaimer

        self._ai_markdown = with_ai_disclaimer(markdown_text or "")

        root = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(self, label=_("AI overview"))
        heading_font = heading.GetFont()
        if heading_font.IsOk():
            heading_font.SetWeight(wx.FONTWEIGHT_BOLD)
            heading.SetFont(heading_font)
        root.Add(heading, 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)

        self._ai_output_host = _AiHtmlHostPanel(self, name=_("AI overview"))
        self._ai_output_host.SetMinSize((-1, 360))
        host_sizer = wx.BoxSizer(wx.VERTICAL)
        self._ai_loading_label = wx.StaticText(
            self._ai_output_host, label=_("Loading AI view…")
        )
        host_sizer.Add(self._ai_loading_label, 0, wx.ALL, 8)
        self._ai_output_host.SetSizer(host_sizer)
        _win_clear_tab_stop(self._ai_output_host)
        self.ai_output = self._ai_output_host
        root.Add(self._ai_output_host, 1, wx.EXPAND | wx.ALL, 12)

        follow_sizer = wx.BoxSizer(wx.VERTICAL)
        self.followup_ctrl, self.ask_btn = _add_followup_question_row(
            self,
            follow_sizer,
            on_ask=self._on_ask_followup,
            ask_enabled=self._session is not None,
        )
        root.Add(follow_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        actions = wx.BoxSizer(wx.HORIZONTAL)
        self.view_browser_btn = wx.Button(self, label=_("View in browser"))
        self.save_html_btn = wx.Button(self, label=_("Save as HTML…"))
        self.save_md_btn = wx.Button(self, label=_("Save as Markdown…"))
        self.copy_ai_btn = wx.Button(self, label=_("Copy to clipboard"))
        for btn in (
            self.view_browser_btn,
            self.save_html_btn,
            self.save_md_btn,
            self.copy_ai_btn,
        ):
            actions.Add(btn, 0, wx.RIGHT, 6)
        root.Add(actions, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        footer.AddStretchSpacer(1)
        close_btn = wx.Button(self, id=wx.ID_CLOSE, label=_("Close"))
        close_btn.Bind(wx.EVT_BUTTON, self._on_close_dialog)
        footer.Add(close_btn, 0)
        root.Add(footer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.view_browser_btn.Bind(wx.EVT_BUTTON, self._on_view_browser)
        self.save_html_btn.Bind(wx.EVT_BUTTON, self._on_save_html)
        self.save_md_btn.Bind(wx.EVT_BUTTON, self._on_save_markdown)
        self.copy_ai_btn.Bind(wx.EVT_BUTTON, self._on_copy_clipboard)
        self.Bind(EVT_EXPLAIN_AI, self._on_followup_ai_event)

        self.SetSizer(root)
        self.CentreOnParent()
        self.SetEscapeId(wx.ID_CLOSE)
        self.SetAffirmativeId(wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, self._on_close_dialog)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_dialog_char_hook)
        close_btn.SetDefault()

    def _on_dialog_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            wx.CallAfter(self._on_close_dialog)
            return
        event.Skip()

    def _realize_ai_html_view(self) -> None:
        if getattr(self, "_ai_view_realized", False):
            return
        host = getattr(self, "_ai_output_host", None)
        if host is None:
            return
        view, is_webview = _create_ai_html_view(host)
        view.SetMinSize((-1, 360))
        if is_webview:
            import wx.html2 as html2

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_webview_navigating)
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_webview_loaded)
            from .ai.markdown_html import ai_idle_placeholder_page

            view.SetPage(
                ai_idle_placeholder_page(title=_("AI overview"), tab_exit=True),
                "",
            )
        else:
            view.ChangeValue(_("AI-generated responses will be shown here."))
        sizer = host.GetSizer()
        if sizer is not None:
            sizer.Clear(delete_windows=True)
            sizer.Add(view, 1, wx.EXPAND)
        self.ai_output = view
        self._ai_output_is_webview = is_webview
        self._ai_view_realized = True
        _wire_ai_html_host(host, view, is_webview=is_webview)
        host.Layout()
        self.Layout()
        if is_webview:
            wx.CallLater(
                100,
                lambda h=host, v=view: _refresh_ai_html_tab_stops(
                    h, v, is_webview=True
                ),
            )
            wx.CallLater(
                300,
                lambda h=host, v=view: _refresh_ai_html_tab_stops(
                    h, v, is_webview=True
                ),
            )
        self._paint_content(focus=True)

    def _on_webview_navigating(self, event) -> None:
        url = (event.GetURL() or "").strip()
        action = _webview_host_action(url)
        if action == "close":
            event.Veto()
            wx.CallAfter(self._on_close_dialog)
            return
        if action in ("page_prev", "page_next"):
            # Overview has no notebook pages; ignore (keys stay in Edge otherwise).
            event.Veto()
            return
        if action in ("next", "prev"):
            event.Veto()
            wx.CallAfter(self._leave_ai_webview, action == "next")
            return
        if url.startswith(("http://", "https://", "mailto:")):
            event.Veto()
            if url.startswith(("http://", "https://")) and is_kb_url(url):
                wx.CallAfter(open_knowledge_base_url, self, url)
                return
            try:
                webbrowser.open(url)
            except OSError:
                pass
            return
        event.Skip()

    def _leave_ai_webview(self, forward: bool) -> None:
        if forward:
            if _try_set_focus(getattr(self, "followup_ctrl", None)):
                return
            if _try_set_focus(getattr(self, "ask_btn", None)):
                return
            if _try_set_focus(getattr(self, "view_browser_btn", None)):
                return
            close_btn = self.FindWindowById(wx.ID_CLOSE)
            _try_set_focus(close_btn)
            return
        close_btn = self.FindWindowById(wx.ID_CLOSE)
        _try_set_focus(close_btn)

    def _on_webview_loaded(self, event) -> None:
        event.Skip()
        if not self._ai_dialog_alive():
            return
        host = getattr(self, "_ai_output_host", None)
        try:
            if host is not None:
                _refresh_ai_html_tab_stops(
                    host, self.ai_output, is_webview=True
                )
        except RuntimeError:
            pass
        if not self._ai_focus_after_load:
            return
        self._ai_focus_after_load = False
        if _markdown_has_latest_followup(getattr(self, "_ai_markdown", "") or ""):
            self._schedule_reveal_latest_followup()
        else:
            self._schedule_output_focus()

    def _schedule_output_focus(self) -> None:
        if not self._ai_dialog_alive():
            return
        self._ai_focus_gen = int(getattr(self, "_ai_focus_gen", 0)) + 1
        gen = self._ai_focus_gen

        def _attempt(remaining: int) -> None:
            if not self._ai_dialog_alive():
                return
            if gen != getattr(self, "_ai_focus_gen", 0):
                return
            if self._focus_output():
                return
            if remaining > 0:
                wx.CallLater(200, lambda: _attempt(remaining - 1))

        wx.CallAfter(lambda: _attempt(5))

    def _schedule_reveal_latest_followup(self) -> None:
        if not self._ai_dialog_alive():
            return
        self._ai_focus_gen = int(getattr(self, "_ai_focus_gen", 0)) + 1
        gen = self._ai_focus_gen

        def _attempt(remaining: int) -> None:
            if not self._ai_dialog_alive():
                return
            if gen != getattr(self, "_ai_focus_gen", 0):
                return
            if self._reveal_latest_followup_now():
                return
            if remaining > 0:
                wx.CallLater(200, lambda: _attempt(remaining - 1))

        wx.CallAfter(lambda: _attempt(5))

    def _reveal_latest_followup_now(self) -> bool:
        if not self._ai_dialog_alive():
            return True
        try:
            if not self:
                return True
            self.Raise()
        except RuntimeError:
            return False
        _win_force_foreground(self)
        if self._ai_output_is_webview:
            try:
                _webview_run_script(
                    self.ai_output, _WEBVIEW_SCROLL_LATEST_FOLLOWUP_JS
                )
            except Exception:
                return False
            host = getattr(self, "_ai_output_host", None)
            view = self.ai_output
            if host is not None and view is not None:
                _refresh_ai_html_tab_stops(host, view, is_webview=True)
                if isinstance(host, _AiHtmlHostPanel):
                    host._accept_kbd_focus = True
            return _try_set_focus(host if host is not None else view)
        view = self.ai_output
        if hasattr(view, "SetInsertionPoint") and hasattr(view, "GetValue"):
            try:
                text = view.GetValue() or ""
                idx = text.rfind("---")
                if idx < 0:
                    idx = 0
                view.SetInsertionPoint(min(idx, len(text)))
                view.ShowPosition(min(idx, len(text)))
                view.SetFocus()
            except RuntimeError:
                return False
            return True
        return _try_set_focus(view)

    def _focus_output(self) -> bool:
        if not self._ai_dialog_alive():
            return True
        try:
            if not self:
                return True
            self.Raise()
        except RuntimeError:
            return False
        _win_force_foreground(self)
        # Keep keyboard in wx on the host gate — diving into Edge leaves a
        # limbo where Escape/Tab do nothing (same fix as IssueDetailDialog).
        if self._ai_output_is_webview:
            host = getattr(self, "_ai_output_host", None)
            view = self.ai_output
            if host is not None and view is not None:
                _refresh_ai_html_tab_stops(host, view, is_webview=True)
                if isinstance(host, _AiHtmlHostPanel):
                    host._accept_kbd_focus = True
            return _try_set_focus(host if host is not None else view)
        return _focus_ai_html_view(
            self.ai_output,
            is_webview=False,
            retries=0,
        )

    def _dialog_html(self) -> str:
        from .ai.markdown_html import markdown_to_browser_page

        return markdown_to_browser_page(
            self._ai_markdown or "",
            title=_("AI overview"),
            plain=self._ai_plain,
            tab_exit=True,
        )

    def _paint_content(self, *, focus: bool = False) -> None:
        if not self._ai_dialog_alive():
            return
        if not self._ai_view_realized:
            return
        if self._ai_output_is_webview:
            self._ai_focus_after_load = bool(focus)
            self.ai_output.SetPage(self._dialog_html(), "")
            if focus and not self._ai_focus_after_load:
                if _markdown_has_latest_followup(self._ai_markdown or ""):
                    self._schedule_reveal_latest_followup()
                else:
                    self._schedule_output_focus()
        else:
            self.ai_output.SetValue(self._ai_markdown or "")
            if _markdown_has_latest_followup(self._ai_markdown or ""):
                if focus:
                    self._schedule_reveal_latest_followup()
            elif focus:
                self._schedule_output_focus()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        can_ask = (not busy) and self._session is not None and bool(
            (self._ai_markdown or "").strip()
        )
        self.ask_btn.Enable(can_ask)
        self.followup_ctrl.Enable(can_ask)
        for btn in (
            self.view_browser_btn,
            self.save_html_btn,
            self.save_md_btn,
            self.copy_ai_btn,
        ):
            btn.Enable(not busy and bool((self._ai_markdown or "").strip()))

    def _ai_status_callback(self, message: str) -> None:
        def update() -> None:
            if self._ai_progress is not None:
                cont = _pulse_progress(self._ai_progress, message)
                if not cont and self._ai_cancel is not None:
                    self._ai_cancel.set()

        wx.CallAfter(update)

    def _present_ai_progress(self, message: str) -> None:
        dlg = self._ai_progress
        if dlg is None:
            return
        _present_progress_dialog(dlg, message)

    def _open_ai_progress(self, title: str, message: str) -> threading.Event:
        self._close_ai_progress(reclaim_focus=False)
        cancel = threading.Event()
        self._ai_cancel = cancel
        self._ai_progress = wx.ProgressDialog(
            title,
            message,
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT,
        )
        self._present_ai_progress(message)
        self._ai_progress_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_ai_progress_timer, self._ai_progress_timer)
        self._ai_progress_timer.Start(200)
        return cancel

    def _on_ai_progress_timer(self, _event: wx.TimerEvent) -> None:
        dlg = self._ai_progress
        cancel = self._ai_cancel
        if dlg is None or cancel is None:
            return
        cont = _pulse_progress(dlg)  # animation only — do not announce
        if not cont:
            cancel.set()
            _pulse_progress(dlg, _("Cancelling…"))
            self._close_ai_progress(reclaim_focus=True)
            self._set_busy(False)

    def _close_ai_progress(self, *, reclaim_focus: bool = True) -> None:
        self._ai_progress_reclaim_gen = (
            int(getattr(self, "_ai_progress_reclaim_gen", 0)) + 1
        )
        reclaim_gen = self._ai_progress_reclaim_gen
        timer = self._ai_progress_timer
        if timer is not None:
            try:
                if timer.IsRunning():
                    timer.Stop()
            except RuntimeError:
                pass
            self._ai_progress_timer = None
        dlg = self._ai_progress
        self._ai_progress = None
        _clear_progress_announce(dlg)
        if dlg is not None:
            try:
                dlg.Destroy()
            except RuntimeError:
                pass
            if not reclaim_focus:
                return

            def _reclaim() -> None:
                if reclaim_gen != getattr(self, "_ai_progress_reclaim_gen", 0):
                    return
                try:
                    if not self:
                        return
                    self.Raise()
                    _win_force_foreground(self)
                except RuntimeError:
                    return

            wx.CallLater(50, _reclaim)

    def _fail_ai_libraries(self, detail: str = "") -> None:
        self._close_ai_progress()
        self._set_busy(False)
        from .ai.explain import error_message_for_key

        msg = error_message_for_key("no_litellm", detail=detail or "")
        wx.MessageBox(msg, _("AI overview"), wx.OK | wx.ICON_ERROR, self)

    def _on_ask_followup(self, _event: wx.Event) -> None:
        if self._busy or self._session is None:
            return
        question = self.followup_ctrl.GetValue().strip()
        if not question:
            return
        cancel = self._open_ai_progress(_("Follow-up"), _("Loading AI libraries…"))
        self._set_busy(True)
        session = self._session
        status_cb = self._ai_status_callback

        def work() -> None:
            from .ai.explain import ExplainResult
            from .ai.litellm_client import preload_litellm
            from .ai.overview import ask_overview_followup

            ok, detail = preload_litellm()
            if not ok:
                wx.CallAfter(self._fail_ai_libraries, detail)
                return
            if cancel.is_set():
                return

            def _thinking() -> None:
                if self._ai_progress is not None:
                    _pulse_progress(self._ai_progress, _("Thinking…"))

            wx.CallAfter(_thinking)
            try:
                out = ask_overview_followup(
                    session,
                    question,
                    cancel_event=cancel,
                    status_callback=status_cb,
                )
            except Exception as exc:
                out = ExplainResult(
                    ok=False,
                    error_key="provider_error",
                    text=str(exc),
                    session=session,
                )
            if cancel.is_set() and not (out.ok and (out.text or "").strip()):
                return
            try:
                wx.PostEvent(
                    self,
                    ExplainAiEvent(kind="followup", result=out, question=question),
                )
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_followup_ai_event(self, event: ExplainAiEvent) -> None:
        from .ai.explain import error_message_for_key
        from .ai.markdown_html import append_followup_markdown

        result = event.result
        self._close_ai_progress()
        if not result.ok:
            if result.error_key == "cancelled":
                self._set_busy(False)
                return
            msg = error_message_for_key(result.error_key, detail=result.text or "")
            wx.MessageBox(msg, _("AI overview"), wx.OK | wx.ICON_ERROR, self)
            if result.session is not None:
                self._session = result.session
            self._set_busy(False)
            return

        self._session = result.session
        question = getattr(event, "question", "") or ""
        self._ai_markdown = append_followup_markdown(
            self._ai_markdown,
            heading=_("Follow-up"),
            question=question,
            answer=result.text or "",
        )
        self._ai_plain = False

        def _paint_followup() -> None:
            if not self._ai_dialog_alive():
                return
            try:
                if not self:
                    return
            except RuntimeError:
                return
            self._paint_content(focus=True)
            self.followup_ctrl.SetValue("")
            self._set_busy(False)

        wx.CallAfter(_paint_followup)
        try:
            from .telemetry import log_ai_overview

            log_ai_overview(followup=True)
        except Exception:
            pass

    def _export_markdown(self) -> str:
        from .ai.markdown_html import with_ai_disclaimer

        body = self._ai_markdown or ""
        # Already includes disclaimer from __init__.
        if not body.strip():
            return with_ai_disclaimer("")
        title = _("AI overview")
        blocks = [f"# {title}\n"]
        for line in self._result.report_meta_lines():
            blocks.append(line)
        blocks.append("")
        blocks.append(body.rstrip())
        blocks.append("")
        return "\n".join(blocks).rstrip() + "\n"

    def _on_view_browser(self, _event: wx.Event) -> None:
        import os
        import tempfile

        try:
            fd, name = tempfile.mkstemp(prefix="checkmate-overview-", suffix=".html", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(self._dialog_html())
            webbrowser.open(Path(name).as_uri())
        except OSError as exc:
            wx.MessageBox(
                _("Could not open the explanation in a browser:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_save_html(self, _event: wx.Event) -> None:
        with wx.FileDialog(
            self,
            _("Save AI overview as HTML"),
            defaultFile="ai-overview.html",
            wildcard=_("HTML files (*.html)|*.html;*.htm|All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
        try:
            path.write_text(self._dialog_html(), encoding="utf-8")
        except OSError as exc:
            wx.MessageBox(
                _("Could not save the explanation:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_save_markdown(self, _event: wx.Event) -> None:
        with wx.FileDialog(
            self,
            _("Save AI overview as Markdown"),
            defaultFile="ai-overview.md",
            wildcard=_("Markdown files (*.md)|*.md;*.markdown|All files (*.*)|*.*"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
        try:
            path.write_text(self._export_markdown(), encoding="utf-8")
        except OSError as exc:
            wx.MessageBox(
                _("Could not save the explanation:\n{error}", error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def _on_copy_clipboard(self, _event: wx.Event) -> None:
        text = self._ai_markdown or ""
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
            finally:
                wx.TheClipboard.Close()

    def _on_close_dialog(self, event: wx.Event | None = None) -> None:
        # Guard against SetEscapeId + CHAR_HOOK + checkmate://close all firing.
        # A second pass that Destroy()s while ShowModal is unwinding leaves the
        # main frame disabled/frozen.
        if getattr(self, "_closing", False):
            if isinstance(event, wx.CloseEvent):
                event.Veto()
            return
        self._closing = True
        # Cancel deferred WebView focus/reveal/paint chains.
        self._ai_focus_gen = int(getattr(self, "_ai_focus_gen", 0)) + 1
        if self._ai_cancel is not None:
            self._ai_cancel.set()
        self._close_ai_progress(reclaim_focus=False)
        if isinstance(event, wx.CloseEvent):
            event.Veto()
        # Never steal focus from Edge via WM_NEXTDLGCTL here — that deadlocks
        # WebView2 when Escape arrives through checkmate://close. Finish close
        # on the next idle so we are outside navigating/key handlers.
        wx.CallAfter(self._finish_close_dialog)

    def _finish_close_dialog(self) -> None:
        """Complete modal teardown after Escape/close (safe for WebView2)."""
        try:
            if not self:
                return
        except RuntimeError:
            return
        # Soft blur: focus the dialog HWND only (no dialog-manager dance).
        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(self.GetHandle() or 0)
                if hwnd:
                    ctypes.windll.user32.SetFocus(hwnd)
            except Exception:
                pass
        try:
            if self.IsModal():
                self.EndModal(wx.ID_CLOSE)
            else:
                self.Destroy()
        except RuntimeError:
            pass

    def _ai_dialog_alive(self) -> bool:
        if getattr(self, "_closing", False):
            return False
        try:
            return bool(self)
        except RuntimeError:
            return False


class AddLanguageDialog(wx.Dialog):
    """Choose a preset or custom UI language to AI-translate or restore."""

    def __init__(self, parent: wx.Window) -> None:
        from .i18n_ai import UI_LANGUAGE_PRESETS

        super().__init__(
            parent,
            title=_("Add language"),
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        self._presets = list(UI_LANGUAGE_PRESETS)
        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            self,
            label=_(
                "Add a UI language. AI can translate the strings, or you can "
                "restore a language you previously removed."
            ),
        )
        intro.Wrap(420)
        root.Add(intro, 0, wx.ALL, 12)

        choice_row = wx.BoxSizer(wx.HORIZONTAL)
        choice_row.Add(
            wx.StaticText(self, label=_("Language:")),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        labels = [
            f"{native} ({display})" for _c, native, display, _d in self._presets
        ]
        labels.append(_("Custom…"))
        self._custom_index = len(labels) - 1
        self.lang_choice = wx.Choice(self, choices=labels)
        self.lang_choice.SetSelection(0)
        self.lang_choice.SetName(_("Language"))
        self.lang_choice.Bind(wx.EVT_CHOICE, self._on_choice)
        choice_row.Add(self.lang_choice, 1, wx.EXPAND)
        root.Add(choice_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)

        self.custom_panel = wx.Panel(self)
        custom_sizer = wx.FlexGridSizer(4, 2, 8, 8)
        custom_sizer.AddGrowableCol(1, 1)
        custom_sizer.Add(
            wx.StaticText(self.custom_panel, label=_("Code:")),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.code_ctrl = wx.TextCtrl(self.custom_panel)
        self.code_ctrl.SetHint(_("e.g. it, zh-hans"))
        self.code_ctrl.SetName(_("Language code"))
        custom_sizer.Add(self.code_ctrl, 1, wx.EXPAND)
        custom_sizer.Add(
            wx.StaticText(self.custom_panel, label=_("Menu name:")),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.native_ctrl = wx.TextCtrl(self.custom_panel)
        self.native_ctrl.SetHint(_("Native name shown in the Language menu"))
        self.native_ctrl.SetName(_("Menu name"))
        custom_sizer.Add(self.native_ctrl, 1, wx.EXPAND)
        custom_sizer.Add(
            wx.StaticText(self.custom_panel, label=_("English name:")),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.display_ctrl = wx.TextCtrl(self.custom_panel)
        self.display_ctrl.SetHint(_("English name for AI prompts"))
        self.display_ctrl.SetName(_("English name"))
        custom_sizer.Add(self.display_ctrl, 1, wx.EXPAND)
        custom_sizer.Add(
            wx.StaticText(self.custom_panel, label=_("Text direction:")),
            0,
            wx.ALIGN_CENTER_VERTICAL,
        )
        self.direction_choice = wx.Choice(
            self.custom_panel,
            choices=[_("Left to right (LTR)"), _("Right to left (RTL)")],
        )
        self.direction_choice.SetSelection(0)
        self.direction_choice.SetName(_("Text direction"))
        custom_sizer.Add(self.direction_choice, 1, wx.EXPAND)
        self.custom_panel.SetSizer(custom_sizer)
        root.Add(self.custom_panel, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)

        # Direction for presets (shown always so RTL presets are clear).
        dir_row = wx.BoxSizer(wx.HORIZONTAL)
        dir_row.Add(
            wx.StaticText(self, label=_("Text direction:")),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        self.preset_direction = wx.Choice(
            self, choices=[_("Left to right (LTR)"), _("Right to left (RTL)")]
        )
        self.preset_direction.SetSelection(0)
        self.preset_direction.SetName(_("Text direction"))
        dir_row.Add(self.preset_direction, 1, wx.EXPAND)
        root.Add(dir_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 12)
        self._dir_row = dir_row

        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        if btns:
            root.Add(btns, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(root)
        self._on_choice(None)
        self.Fit()
        self.CentreOnParent()

    def _on_choice(self, _event: wx.CommandEvent | None) -> None:
        custom = self.lang_choice.GetSelection() == self._custom_index
        self.custom_panel.Enable(custom)
        self.custom_panel.Show(custom)
        self.preset_direction.Enable(not custom)
        self.preset_direction.Show(not custom)
        if not custom:
            sel = self.lang_choice.GetSelection()
            if 0 <= sel < len(self._presets):
                _c, _n, _d, direction = self._presets[sel]
                self.preset_direction.SetSelection(
                    1 if direction == TEXT_DIRECTION_RTL else 0
                )
        self.Layout()
        self.Fit()

    def selection(self) -> tuple[str, str, str, str] | None:
        """Return (code, native_name, display_name, direction) or None."""
        sel = self.lang_choice.GetSelection()
        if sel < 0:
            return None
        if sel == self._custom_index:
            code = _normalize_lang_code(self.code_ctrl.GetValue())
            native = self.native_ctrl.GetValue().strip()
            display = self.display_ctrl.GetValue().strip()
            direction = (
                TEXT_DIRECTION_RTL
                if self.direction_choice.GetSelection() == 1
                else TEXT_DIRECTION_LTR
            )
            if not code or not native or not display:
                return None
            return code, native, display, direction
        code, native, display, _preset_dir = self._presets[sel]
        direction = (
            TEXT_DIRECTION_RTL
            if self.preset_direction.GetSelection() == 1
            else TEXT_DIRECTION_LTR
        )
        return code, native, display, direction


class EditLanguageStringsDialog(wx.Dialog):
    """Browse and edit translated UI strings for one language catalog."""

    def __init__(self, parent: wx.Window, catalog: dict) -> None:
        self._catalog = {
            "format": catalog.get("format") or CUSTOM_I18N_FORMAT,
            "version": catalog.get("version") or CUSTOM_I18N_VERSION,
            "code": catalog["code"],
            "native_name": catalog["native_name"],
            "display_name": catalog["display_name"],
            "direction": catalog.get("direction") or TEXT_DIRECTION_LTR,
            "source_msgid_hash": catalog.get("source_msgid_hash") or "",
            "strings": dict(catalog.get("strings") or {}),
        }
        title = _("Edit strings — {name}").format(
            name=self._catalog["native_name"]
        )
        super().__init__(
            parent,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._keys = sorted(self._catalog["strings"].keys())
        self._filtered = list(self._keys)

        root = wx.BoxSizer(wx.VERTICAL)
        filter_row = wx.BoxSizer(wx.HORIZONTAL)
        filter_row.Add(
            wx.StaticText(self, label=_("Filter:")),
            0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT,
            8,
        )
        self.filter_ctrl = wx.TextCtrl(self)
        self.filter_ctrl.SetName(_("Filter strings"))
        self.filter_ctrl.Bind(wx.EVT_TEXT, self._on_filter)
        filter_row.Add(self.filter_ctrl, 1, wx.EXPAND)
        root.Add(filter_row, 0, wx.ALL | wx.EXPAND, 12)

        self.list = wx.ListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN
        )
        self.list.InsertColumn(0, _("English"))
        self.list.InsertColumn(1, _("Translation"))
        self.list.SetName(_("UI strings"))
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        root.Add(self.list, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 12)

        edit_row = wx.BoxSizer(wx.VERTICAL)
        edit_row.Add(
            wx.StaticText(self, label=_("Selected English string:")),
            0,
            wx.BOTTOM,
            4,
        )
        self.msgid_ctrl = wx.TextCtrl(self, style=wx.TE_READONLY | wx.TE_MULTILINE)
        self.msgid_ctrl.SetMinSize((-1, 48))
        self.msgid_ctrl.SetName(_("English string"))
        edit_row.Add(self.msgid_ctrl, 0, wx.EXPAND | wx.BOTTOM, 8)
        edit_row.Add(
            wx.StaticText(self, label=_("Translation:")),
            0,
            wx.BOTTOM,
            4,
        )
        self.translation_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.translation_ctrl.SetMinSize((-1, 72))
        self.translation_ctrl.SetName(_("Translation"))
        self.translation_ctrl.Bind(wx.EVT_TEXT, self._on_translation_edit)
        edit_row.Add(self.translation_ctrl, 0, wx.EXPAND)
        root.Add(edit_row, 0, wx.ALL | wx.EXPAND, 12)

        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        if btns:
            root.Add(btns, 0, wx.ALL | wx.EXPAND, 12)
        self.SetSizer(root)
        self.SetMinSize((640, 480))
        self._refill_list()
        self.list.SetColumnWidth(0, 280)
        self.list.SetColumnWidth(1, 280)
        self.CentreOnParent()

    def _refill_list(self) -> None:
        needle = self.filter_ctrl.GetValue().casefold().strip()
        self._filtered = [
            k
            for k in self._keys
            if not needle
            or needle in k.casefold()
            or needle in str(self._catalog["strings"].get(k, "")).casefold()
        ]
        self.list.DeleteAllItems()
        for key in self._filtered:
            idx = self.list.InsertItem(self.list.GetItemCount(), key)
            self.list.SetItem(idx, 1, self._catalog["strings"].get(key, ""))

    def _on_filter(self, _event: wx.CommandEvent) -> None:
        self._refill_list()

    def _on_select(self, _event: wx.ListEvent) -> None:
        idx = self.list.GetFirstSelected()
        if idx < 0 or idx >= len(self._filtered):
            return
        key = self._filtered[idx]
        self.msgid_ctrl.ChangeValue(key)
        self.translation_ctrl.ChangeValue(self._catalog["strings"].get(key, ""))

    def _on_translation_edit(self, _event: wx.CommandEvent) -> None:
        idx = self.list.GetFirstSelected()
        if idx < 0 or idx >= len(self._filtered):
            return
        key = self._filtered[idx]
        value = self.translation_ctrl.GetValue()
        self._catalog["strings"][key] = value
        self.list.SetItem(idx, 1, value)

    def catalog(self) -> dict:
        return self._catalog


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
        self._instance_checker: wx.SingleInstanceChecker | None = None
        self._open_watcher = None
        super().__init__(False)

    def OnInit(self) -> bool:  # noqa: N802 — wx API
        from .settings import single_instance_enabled
        from .single_instance import (
            OpenRequestWatcher,
            bring_checkmate_window_to_front,
            send_open_paths,
        )
        from .telemetry import init_app_telemetry

        # Keep a reference so the lock is held for the life of this process.
        self._instance_checker = wx.SingleInstanceChecker("CheckMate-single-instance")
        if single_instance_enabled() and self._instance_checker.IsAnotherRunning():
            if self._pending_paths:
                send_open_paths(self._pending_paths)
            bring_checkmate_window_to_front()
            return False

        init_app_telemetry(self)
        self.frame = MainFrame(initial_paths=self._pending_paths)
        self._pending_paths.clear()
        self.frame.Show()
        self._open_watcher = OpenRequestWatcher(
            self,
            self._on_external_open_paths,
        )
        return True

    def _on_external_open_paths(self, paths: list[str]) -> None:
        """Handle publication paths forwarded from a second CheckMate launch."""
        if self.frame is None or not paths:
            return
        bring = True
        try:
            from .single_instance import bring_checkmate_window_to_front

            bring_checkmate_window_to_front()
        except Exception:
            bring = False
        if not bring and self.frame is not None:
            try:
                self.frame.Raise()
            except Exception:
                pass
        wx.CallAfter(self.frame.open_publication_paths, list(paths))

    def OnExit(self) -> int:  # noqa: N802 — wx API
        watcher = getattr(self, "_open_watcher", None)
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass
            self._open_watcher = None
        return super().OnExit()

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
        self._pending_fix_verify = None
        self._busy = False
        self._overview_cancel: threading.Event | None = None
        self._overview_progress: wx.ProgressDialog | None = None
        self._overview_progress_timer: wx.Timer | None = None
        self._alt_assess_cancel: threading.Event | None = None
        self._alt_assess_progress: wx.ProgressDialog | None = None
        self._alt_assess_progress_timer: wx.Timer | None = None
        self.menu_ai_overview: wx.MenuItem | None = None
        self.menu_alt_assess: wx.MenuItem | None = None
        self.menu_settings: wx.MenuItem | None = None
        self._lang_menu_items: dict[str, wx.MenuItem] = {}
        self._issues_visible = True
        self._issues_height_delta = 0
        self._source_filter_wanted = False
        self._result_icon_cache: dict[tuple[str, int], wx.Bitmap] = {}
        self._result_icon_key: str | None = None
        self._initial_focus_pending = True
        self._pending_open_paths = list(initial_paths or [])
        self._apply_window_icon()
        self._build_ui()
        self._apply_layout_direction()
        self._bind()
        self._enable_drag_drop()
        # Lightweight status only — detect_java() is too slow for the UI thread.
        self.SetStatusText(_("Starting…"))
        self.Layout()
        self._result_icon_key = None
        self._update_result_status_icon()
        # Issues start collapsed; centre the compact window.
        self._set_issues_panel_visible(False, keep_center=False)
        self.Centre()
        # Min sizes are unreliable before the first Show; re-check after paint.
        wx.CallAfter(self._ensure_result_text_height, keep_center=False)
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
        """Optionally append a status/verdict message to the title bar."""
        if not _UPDATE_TITLE_BAR:
            self.SetTitle(app_title())
            return
        if summary:
            clean = " ".join(summary.split())
            self.SetTitle(f"{app_title()} — {clean}")
        else:
            self.SetTitle(app_title())

    def _set_result_accessible_name(self, text: str) -> None:
        """Include the verdict in the accessible name so Tab announces it."""
        spoken = " ".join(text.split())
        self.result_label.SetName(_("Check result: {text}", text=spoken))

    def _focus_result_for_announcement(self, *, keep: bool = False) -> None:
        """Focus Result so screen readers announce the new text (no highlight).

        Mid-check: quiet SetFocus only (no Raise / foreground fight). Final
        verdict uses ``_reclaim_focus_after_check`` from the result event.
        """
        self._announce_focus_gen = getattr(self, "_announce_focus_gen", 0) + 1
        gen = self._announce_focus_gen

        if keep:
            self._reclaim_focus_after_check(gen=gen)
            return

        def _enter_result() -> None:
            if getattr(self, "_announce_focus_gen", 0) != gen:
                return
            try:
                self.result_label.SetSelection(0, 0)
                if self.result_label.HasFocus():
                    return
                if not _try_set_focus(self.result_label):
                    self.result_label.SetFocus()
            except Exception:
                return

        wx.CallAfter(_enter_result)

    def _reclaim_focus_after_check(self, *, gen: int | None = None) -> None:
        """Raise the frame and put keyboard focus in Result after a check.

        Retries a few times if checker process exit steals activation. Stops
        early once the frame is foreground and Result has focus.
        """
        if gen is None:
            self._announce_focus_gen = getattr(self, "_announce_focus_gen", 0) + 1
            gen = self._announce_focus_gen

        def _attempt(n: int) -> None:
            if getattr(self, "_announce_focus_gen", 0) != gen:
                return
            try:
                if self.IsIconized():
                    self.Iconize(False)
                self.Show(True)
                self.Raise()
                foreground_ok = _win_force_foreground(self)
                self.result_label.SetSelection(0, 0)
                if not _try_set_focus(self.result_label):
                    self.result_label.SetFocus()
                focused = self.result_label.HasFocus()
                if foreground_ok and focused:
                    return
            except Exception:
                pass
            if n < 4:
                wx.CallLater(100 + n * 100, lambda: _attempt(n + 1))

        wx.CallAfter(lambda: _attempt(0))

    def _speak_status(self, text: str, *, progress: bool = False) -> None:
        """Speak a status line through NVDA/JAWS (interruptible; no-op if unavailable).

        ``progress=True`` uses a shorter throttle so living check updates are
        spoken without queuing a backlog of timer ticks.
        """
        msg = (text or "").strip()
        if not msg:
            return
        try:
            from .accessibility import announce, speak

            if progress:
                announce(msg, progress=True)
            else:
                speak(msg, interrupt=True)
        except Exception:
            pass

    def _show_result_text(
        self,
        display: str,
        *,
        title: str | None = None,
        focus: bool = False,
        update_title: bool = True,
        verdict: Verdict | None = None,
    ) -> None:
        """Update the result pane value, accessible name, colors, and title.

        ``focus=True`` requests a screen-reader announcement.
        """
        self.result_label.ChangeValue(display)
        self._set_result_accessible_name(display)
        self._set_result_colors(verdict)
        spoken = title if title is not None else display

        if focus:
            # Direct speech so milestones are heard even when focus alone is quiet.
            self._speak_status(spoken)

        if _ANNOUNCE_MODE == "focus":
            if update_title or focus:
                self._set_result_title(spoken if (focus or title) else title)
            if focus:
                self._focus_result_for_announcement(keep=False)
            return

        # Legacy selection / focus-leave path.
        if update_title:
            self._set_result_title(title)
        if focus:
            self._announce_result_pane()
        elif self.result_label.HasFocus():
            wx.CallAfter(self._prepare_result_for_review)

    def _announce_result_pane(self) -> None:
        """Announce the current result (focus mode defers reclaim)."""
        if _ANNOUNCE_MODE == "focus":
            if self._last_result is not None:
                self._set_result_title(self._last_result.headline)
            else:
                text = self.result_label.GetValue().strip()
                self._set_result_title(text or None)
            # Focus/foreground reclaim runs once from on_result_event after layout.
            return
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

    def _result_icon_display_size(self) -> int:
        """Square icon edge matching the result text box height."""
        if hasattr(self, "result_label") and self.result_label is not None:
            h = self.result_label.GetMinSize().height
            if h > 0:
                return h
        return 72

    def _result_status_icon_key(self) -> str:
        """Return down / wait / ok / x for the result status graphic."""
        if self._busy:
            return "wait"
        result = self._last_result
        if result is None:
            return "down"
        if result.issues or result.verdict in (Verdict.FAILED, Verdict.ERROR):
            return "x"
        return "ok"

    def _result_status_icon_path(self, key: str) -> Path | None:
        names = {
            "down": "checkmate-down.png",
            "wait": "checkmate-wait.png",
            "ok": "checkmate.png",
            "x": "checkmate-x.png",
        }
        path = images_dir() / names[key]
        return path if path.is_file() else None

    def _scaled_result_icon_bitmap(self, key: str, size: int) -> wx.Bitmap | None:
        cached = self._result_icon_cache.get((key, size))
        if cached is not None and cached.IsOk():
            return cached
        path = self._result_status_icon_path(key)
        if path is None:
            return None
        image = wx.Image(str(path), wx.BITMAP_TYPE_PNG)
        if not image.IsOk():
            return None
        if image.GetWidth() != size or image.GetHeight() != size:
            image = image.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
        bitmap = wx.Bitmap(image)
        if not bitmap.IsOk():
            return None
        self._result_icon_cache[(key, size)] = bitmap
        return bitmap

    def _update_result_status_icon(self) -> None:
        """Show waiting / pass / issues graphic beside the result box."""
        if not hasattr(self, "result_icon"):
            return
        key = self._result_status_icon_key()
        size = self._result_icon_display_size()
        if key == self._result_icon_key:
            current = self.result_icon.GetBitmap()
            if current.IsOk() and current.GetWidth() == size:
                return
        bitmap = self._scaled_result_icon_bitmap(key, size)
        if bitmap is None:
            self.result_icon.SetBitmap(wx.NullBitmap)
            self.result_icon.SetToolTip("")
            self._result_icon_key = None
            return
        self.result_icon.SetMinSize((size, size))
        self.result_icon.SetSize((size, size))
        self.result_icon.SetBitmap(bitmap)
        tip = {
            "down": _("Waiting for a publication"),
            "wait": _("Check in progress"),
            "ok": _("No issues found"),
            "x": _("Issues found"),
        }[key]
        open_hint = _("Click to select a file")
        self.result_icon.SetToolTip(f"{tip}\n{open_hint}")
        self.result_icon.SetName(
            _("Check status: {status}. {action}", status=tip, action=open_hint)
        )
        self._result_icon_key = key
        icon_sizer = getattr(self, "result_icon_sizer", None)
        if icon_sizer is not None:
            icon_sizer.Layout()
        row = getattr(self, "result_row", None)
        if row is not None:
            row.Layout()

    def _prepare_result_for_review(self) -> None:
        """Select all so screen readers announce the result text on focus."""
        if _ANNOUNCE_MODE != "selection":
            return
        if not self.result_label.HasFocus():
            return
        end = self.result_label.GetLastPosition()
        if end <= 0:
            return
        self.result_label.SetSelection(0, end)

    def on_result_focus(self, event: wx.FocusEvent) -> None:
        event.Skip()
        if _ANNOUNCE_MODE == "selection":
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

        # --- Result (status icon + summary + action buttons) ---
        result_row = wx.BoxSizer(wx.HORIZONTAL)
        self.result_box = wx.StaticBox(panel, label=_("Result"))
        result_sizer = wx.StaticBoxSizer(self.result_box, wx.VERTICAL)
        self.result_label = wx.TextCtrl(
            panel,
            value=_("CheckMate ready."),
            style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_WORDWRAP | wx.BORDER_SUNKEN,
            name=_("Check result"),
        )
        font = self.result_label.GetFont()
        font.SetPointSize(font.GetPointSize() + 1)
        font.MakeBold()
        self.result_label.SetFont(font)
        # Size from real text metrics so four lines are not clipped by chrome.
        line_h = self.result_label.GetCharHeight()
        if hasattr(self.result_label, "GetSizeFromTextSize"):
            text_h = self.result_label.GetSizeFromTextSize(-1, line_h * 4).height
            text_h = max(text_h + 12, line_h * 4 + 24)
        else:
            text_h = line_h * 4 + 36
        self._result_text_min_height = text_h
        self.result_label.SetMinSize((-1, text_h))
        self.result_label.Bind(wx.EVT_SET_FOCUS, self.on_result_focus)
        self._set_result_accessible_name(_("CheckMate ready."))
        result_sizer.Add(self.result_label, 1, wx.EXPAND | wx.ALL, 8)

        icon_size = self._result_icon_display_size()
        self.result_icon = wx.StaticBitmap(panel, size=(icon_size, icon_size))
        self.result_icon.SetName(_("Check status"))
        self.result_icon.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        # Center the bitmap in the result-row height (StaticBitmap paints
        # top-left inside a taller control on Windows).
        self.result_icon_sizer = wx.BoxSizer(wx.VERTICAL)
        self.result_icon_sizer.AddStretchSpacer(1)
        self.result_icon_sizer.Add(self.result_icon, 0, wx.ALIGN_CENTER)
        self.result_icon_sizer.AddStretchSpacer(1)

        result_btns = wx.BoxSizer(wx.VERTICAL)
        self.copy_btn = wx.Button(panel, label=_("&Copy summary"))
        self.copy_btn.SetToolTip(_("Copy the result summary (Ctrl+Shift+C)"))
        self.report_btn = wx.Button(panel, label=_("&Report…"))
        self.report_btn.SetToolTip(
            _(
                "View or save reports, copy the summary, or view the full log"
            )
        )
        self.ai_overview_btn = None
        if fido_settings_present():
            self.ai_overview_btn = wx.Button(panel, label=_("AI &overview"))
            self.ai_overview_btn.SetToolTip(
                _("Generate an AI overview of this report (Ctrl+Shift+A)")
            )
        self.show_issues_btn = wx.Button(panel, label=_("Show &issues"))
        self.show_issues_btn.SetToolTip(_("Show the issues list"))
        result_action_btns = self._result_action_buttons()
        for i, btn in enumerate(result_action_btns):
            border = wx.BOTTOM if i < len(result_action_btns) - 1 else 0
            result_btns.Add(btn, 0, wx.EXPAND | border, 4 if border else 0)
        self.result_btns = result_btns
        self._size_result_action_buttons()
        self._set_ai_overview_btn_visible(ai_features_enabled())
        self.result_row = result_row
        result_row.Add(
            self.result_icon_sizer, 0, wx.EXPAND | wx.RIGHT, 10
        )
        result_row.Add(result_sizer, 1, wx.EXPAND | wx.RIGHT, 12)
        result_row.Add(result_btns, 0, wx.EXPAND)
        # Static box chrome (~label + padding) above/around the text box.
        result_row.SetMinSize(
            (
                -1,
                max(
                    self._result_text_min_height + 44,
                    result_btns.GetMinSize().height,
                ),
            )
        )
        root.Add(result_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self._update_result_status_icon()

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
            self.unique_codes_cb, 0, wx.ALIGN_CENTER_VERTICAL
        )

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
        self.issues_sizer = issues_sizer
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
        self.menu_ai_overview = None
        if ai_features_enabled():
            self.menu_ai_overview = report_menu.Append(
                wx.ID_ANY, _("AI &overview…\tCtrl+Shift+A")
            )
            report_menu.AppendSeparator()
        self.menu_view_html = report_menu.Append(
            wx.ID_ANY, _("View &HTML report in browser\tCtrl+H")
        )
        self.menu_save_html = report_menu.Append(
            wx.ID_SAVEAS, _("Save &HTML report…\tCtrl+S")
        )
        report_menu.AppendSeparator()
        self.menu_view_text = report_menu.Append(
            wx.ID_ANY, _("View &text report\tCtrl+T")
        )
        self.menu_save_text = report_menu.Append(
            wx.ID_ANY, _("Save &text report…\tCtrl+Shift+S")
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
        self.menu_view_changelog = report_menu.Append(
            wx.ID_ANY, _("View edit &changelog…\tCtrl+Shift+G")
        )
        self.menu_view_changelog.SetHelp(
            _(
                "Open the CheckMate edit changelog for this publication "
                "(AI fixes and backups), when one exists"
            )
        )
        self._report_menu_index = menubar.GetMenuCount()
        menubar.Append(report_menu, _("&Report"))

        tools_menu = wx.Menu()
        self.menu_check = tools_menu.Append(
            wx.ID_ANY, _("&Re-check publication\tF5")
        )
        self.menu_alt_assess = None
        if ai_features_enabled():
            tools_menu.AppendSeparator()
            self.menu_alt_assess = tools_menu.Append(
                wx.ID_ANY, _("Assess &alt text export…")
            )
            self.menu_alt_assess.SetHelp(
                _(
                    "Open a Fido alt-text export folder and assess alt quality with AI"
                )
            )
        tools_menu.AppendSeparator()
        self.menu_settings = tools_menu.Append(
            wx.ID_PREFERENCES, _("&Settings…")
        )
        self.menu_settings.SetHelp(
            _(
                "General preferences, EPUB checkers, and PDF validation profile"
            )
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
        languages = effective_languages()
        for code, label in languages.items():
            submenu = wx.Menu()
            select_item = submenu.AppendCheckItem(wx.ID_ANY, _("&Select"))
            self._lang_menu_items[code] = select_item
            if code == current:
                select_item.Check(True)
            self.Bind(
                wx.EVT_MENU,
                lambda _e, lang=code: self.on_language_selected(lang),
                select_item,
            )
            if code != "en":
                submenu.AppendSeparator()
                edit_item = submenu.Append(wx.ID_ANY, _("&Edit strings…"))
                update_item = submenu.Append(
                    wx.ID_ANY, _("&Update translations…")
                )
                regenerate_item = submenu.Append(
                    wx.ID_ANY, _("&Regenerate all…")
                )
                export_item = submenu.Append(wx.ID_ANY, _("E&xport…"))
                remove_item = submenu.Append(wx.ID_ANY, _("&Remove…"))
                self.Bind(
                    wx.EVT_MENU,
                    lambda _e, lang=code: self.on_edit_language_strings(lang),
                    edit_item,
                )
                self.Bind(
                    wx.EVT_MENU,
                    lambda _e, lang=code: self.on_update_language(lang),
                    update_item,
                )
                self.Bind(
                    wx.EVT_MENU,
                    lambda _e, lang=code: self.on_regenerate_language(lang),
                    regenerate_item,
                )
                self.Bind(
                    wx.EVT_MENU,
                    lambda _e, lang=code: self.on_export_language(lang),
                    export_item,
                )
                self.Bind(
                    wx.EVT_MENU,
                    lambda _e, lang=code: self.on_remove_language(lang),
                    remove_item,
                )
            # Submenu titles cannot be radio items; mark the active language
            # with a filled blob so it remains visible in the Language list.
            menu_label = f"● {label}" if code == current else label
            lang_menu.AppendSubMenu(submenu, menu_label)
        lang_menu.AppendSeparator()
        self.menu_add_language = lang_menu.Append(wx.ID_ANY, _("&Add language…"))
        self.menu_import_language = lang_menu.Append(
            wx.ID_ANY, _("&Import language…")
        )
        menubar.Append(lang_menu, _("&Language"))

        help_menu = wx.Menu()
        self.menu_knowledge_base = help_menu.Append(
            wx.ID_ANY, _("&Knowledge Base…")
        )
        self.menu_knowledge_base.SetHelp(
            _("Open the offline DAISY Accessible Publishing Knowledge Base")
        )
        self.menu_update_kb = help_menu.Append(
            wx.ID_ANY, _("&Update Knowledge Base…")
        )
        self.menu_update_kb.SetHelp(
            _("Download or refresh Knowledge Base articles from kb.daisy.org")
        )
        help_menu.AppendSeparator()
        self.menu_open_app_log = help_menu.Append(
            wx.ID_ANY, _("Open debugging &log…")
        )
        help_menu.AppendSeparator()
        self.menu_about = help_menu.Append(wx.ID_ABOUT, _("&About"))
        menubar.Append(help_menu, _("&Help"))
        self.SetMenuBar(menubar)
        self._bind_menus()
        self._update_report_actions_enabled()

    def _result_action_buttons(self) -> list[wx.Button]:
        buttons = [self.copy_btn, self.report_btn]
        if self.ai_overview_btn is not None:
            buttons.append(self.ai_overview_btn)
        buttons.append(self.show_issues_btn)
        return buttons

    def _size_result_action_buttons(self) -> None:
        """Give result-column buttons shared extra width for translations."""
        buttons = self._result_action_buttons()
        if not buttons:
            return
        # Reset so GetBestSize reflects the current labels.
        for btn in buttons:
            btn.SetMinSize(wx.DefaultSize)
        btn_min_w = max(btn.GetBestSize().width for btn in buttons)
        btn_min_w = max(btn_min_w + 24, 140)
        for btn in buttons:
            btn.SetMinSize((btn_min_w, -1))

    def _set_ai_overview_btn_visible(self, visible: bool) -> None:
        """Show or hide the AI overview button (remove from layout when off)."""
        btn = self.ai_overview_btn
        if btn is None:
            return
        self.result_btns.Show(btn, visible)
        if visible:
            btn.Show()
        else:
            btn.Hide()

    def _apply_ai_features_visibility(self) -> None:
        """Show or hide AI entry points after the training toggle changes."""
        enabled = ai_features_enabled()
        self._set_ai_overview_btn_visible(enabled)
        self._size_result_action_buttons()
        self.panel.Layout()
        self.Layout()
        self._result_icon_key = None
        self._update_result_status_icon()
        # Rebuild so Report → AI overview appears/disappears with the toggle.
        self._build_menubar()

    def _update_report_actions_enabled(self) -> None:
        """Enable Report menu / button only when a check result exists.

        Uses both per-item Enable and EnableTop so the top-level Report menu
        greys out correctly on Windows and macOS.
        """
        enabled = self._last_result is not None
        changelog_ok = self._changelog_path_if_present() is not None
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
        if self.menu_ai_overview is not None:
            self.menu_ai_overview.Enable(enabled)
        if self.menu_alt_assess is not None:
            self.menu_alt_assess.Enable(ai_features_enabled() and not self._busy)
        self.menu_view_changelog.Enable(changelog_ok)
        # AI overview is hidden when features are off; only enable when shown.
        if self.ai_overview_btn is not None and ai_features_enabled():
            self.ai_overview_btn.Enable(enabled)
        self.copy_btn.Enable(enabled)
        self.report_btn.Enable(enabled or changelog_ok)
        self._update_show_issues_button()
        menubar = self.GetMenuBar()
        if menubar is None:
            return
        idx = getattr(self, "_report_menu_index", -1)
        if 0 <= idx < menubar.GetMenuCount():
            menubar.EnableTop(idx, enabled or changelog_ok)

    def _bind(self) -> None:
        self.select_file_btn.Bind(wx.EVT_BUTTON, self.on_browse_file)
        self.select_folder_btn.Bind(wx.EVT_BUTTON, self.on_browse_folder)
        self.result_icon.Bind(wx.EVT_LEFT_UP, self.on_result_icon_click)
        if self.ai_overview_btn is not None:
            self.ai_overview_btn.Bind(wx.EVT_BUTTON, self.on_ai_overview)
        self.show_issues_btn.Bind(wx.EVT_BUTTON, self.on_show_issues)
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
        self.Bind(EVT_OVERVIEW_AI, self._on_overview_ai_event)
        self.Bind(EVT_ALT_ASSESS_AI, self._on_alt_assess_ai_event)

    def _bind_menus(self) -> None:
        self.Bind(wx.EVT_MENU, self.on_browse_file, self.menu_open_file)
        self.Bind(wx.EVT_MENU, self.on_browse_folder, self.menu_open_folder)
        self.Bind(wx.EVT_MENU, self.on_view_text_report, self.menu_view_text)
        self.Bind(wx.EVT_MENU, self.on_save_text_report, self.menu_save_text)
        self.Bind(wx.EVT_MENU, self.on_view_html_report, self.menu_view_html)
        self.Bind(wx.EVT_MENU, self.on_save_html_report, self.menu_save_html)
        if self.menu_ai_overview is not None:
            self.Bind(wx.EVT_MENU, self.on_ai_overview, self.menu_ai_overview)
        if self.menu_alt_assess is not None:
            self.Bind(wx.EVT_MENU, self.on_alt_assess_export, self.menu_alt_assess)
        self.Bind(wx.EVT_MENU, self.on_copy_summary, self.menu_copy)
        self.Bind(wx.EVT_MENU, self.on_clear_results, self.menu_clear)
        self.Bind(wx.EVT_MENU, self.on_check, self.menu_check)
        self.Bind(wx.EVT_MENU, self.on_settings, self.menu_settings)
        self.Bind(wx.EVT_MENU, self.on_view_full_log, self.menu_view_log)
        self.Bind(wx.EVT_MENU, self.on_view_changelog, self.menu_view_changelog)
        self.Bind(wx.EVT_MENU, self.on_check_updates, self.menu_update)
        self.Bind(wx.EVT_MENU, self.on_reinstall_checker, self.menu_install)
        self.Bind(wx.EVT_MENU, self.on_knowledge_base, self.menu_knowledge_base)
        self.Bind(wx.EVT_MENU, self.on_update_knowledge_base, self.menu_update_kb)
        self.Bind(wx.EVT_MENU, self.on_open_app_log, self.menu_open_app_log)
        self.Bind(wx.EVT_MENU, self.on_about, self.menu_about)
        self.Bind(wx.EVT_MENU, self.on_add_language, self.menu_add_language)
        self.Bind(wx.EVT_MENU, self.on_import_language, self.menu_import_language)
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
        self._maybe_show_ai_translation_warning(lang)

    def _maybe_show_ai_translation_warning(self, lang: str) -> None:
        """One-time notice when switching away from English."""
        code = _normalize_lang_code(lang)
        if not code or code.split("-", 1)[0] == "en":
            return
        if ai_translation_warning_shown():
            return
        wx.MessageBox(
            _(
                "Some CheckMate translations are generated by AI "
                "(including Knowledge Base articles and languages added with AI) "
                "and may contain mistakes."
            ),
            _("Language"),
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
        mark_ai_translation_warning_shown()

    def _ai_required_for_language_tools(self) -> bool:
        if not ai_features_enabled():
            wx.MessageBox(
                _(
                    "AI features must be enabled in Settings to add or update "
                    "UI languages."
                ),
                _("Add language"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return False
        if not fido_settings_present():
            wx.MessageBox(
                _(
                    "Configure FIDO AI settings before translating UI languages."
                ),
                _("Add language"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return False
        return True

    def _run_ui_translation(
        self,
        *,
        code: str,
        native_name: str,
        display_name: str,
        direction: str = TEXT_DIRECTION_LTR,
        force: bool,
        title: str,
    ) -> bool:
        from .ai.explain import error_message_for_key
        from .i18n_ai import ensure_ui_translation

        cancel_event = threading.Event()
        dlg = wx.ProgressDialog(
            title,
            _("Preparing…"),
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE | wx.PD_CAN_ABORT,
        )
        result: dict = {"tr": None, "abort": False}

        def progress(msg: str) -> None:
            if result["abort"]:
                return
            wx.CallAfter(_pulse_ui_translate, dlg, msg, result, cancel_event)

        def worker() -> None:
            try:
                result["tr"] = ensure_ui_translation(
                    code=code,
                    native_name=native_name,
                    display_name=display_name,
                    direction=direction,
                    force=force,
                    progress=progress,
                    cancel_event=cancel_event,
                )
            except Exception as exc:  # noqa: BLE001
                from .i18n_ai import UiTranslationResult

                result["tr"] = UiTranslationResult(
                    error_key="provider_error", detail=str(exc)
                )

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while t.is_alive():
            wx.MilliSleep(50)
            wx.YieldIfNeeded()
            if dlg.WasCancelled():
                result["abort"] = True
                cancel_event.set()
        try:
            _clear_progress_announce(dlg)
            dlg.Destroy()
        except Exception:
            pass

        tr = result.get("tr")
        if tr is None:
            return False
        if tr.error_key == "cancelled":
            return False
        if not tr.ok:
            msg = error_message_for_key(tr.error_key, detail=tr.detail or "")
            if tr.error_key in ("builtin_code", "exists", "install_failed"):
                msg = _(
                    "Could not save the language catalog ({detail})."
                ).format(detail=tr.error_key)
            wx.MessageBox(msg, title, wx.OK | wx.ICON_ERROR, self)
            return False
        return True

    def on_add_language(self, _event: wx.CommandEvent | None = None) -> None:
        dlg = AddLanguageDialog(self)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            sel = dlg.selection()
        finally:
            dlg.Destroy()
        if not sel:
            wx.MessageBox(
                _("Enter a valid language code, menu name, and English name."),
                _("Add language"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        code, native, display, direction = sel
        existing = read_catalog(code)

        if is_language_hidden(code) and existing is not None:
            unhide_language(code)
            self._build_menubar()
            switch = wx.MessageBox(
                _(
                    "Restored “{name}”. Switch CheckMate to this language now?"
                ).format(name=existing.get("native_name") or native),
                _("Add language"),
                wx.YES_NO | wx.ICON_QUESTION,
                self,
            )
            if switch == wx.YES:
                set_language(code)
                self._apply_ui_language()
                self._maybe_show_ai_translation_warning(code)
            return

        if existing is not None and is_registered_language(code):
            answer = wx.MessageBox(
                _(
                    "“{name}” is already available. Update its translations "
                    "with AI?"
                ).format(name=existing.get("native_name") or native),
                _("Add language"),
                wx.YES_NO | wx.ICON_QUESTION,
                self,
            )
            if answer != wx.YES:
                return
            if not self._ai_required_for_language_tools():
                return
            if not self._run_ui_translation(
                code=code,
                native_name=str(existing.get("native_name") or native),
                display_name=str(existing.get("display_name") or display),
                direction=str(existing.get("direction") or direction),
                force=False,
                title=_("Updating translations"),
            ):
                return
            if get_language() == code:
                self._apply_ui_language()
            else:
                self._build_menubar()
            return

        if not self._ai_required_for_language_tools():
            return
        if not self._run_ui_translation(
            code=code,
            native_name=native,
            display_name=display,
            direction=direction,
            force=False,
            title=_("Adding language"),
        ):
            return
        set_language(code)
        self._apply_ui_language()
        self._maybe_show_ai_translation_warning(code)

    def on_edit_language_strings(self, lang: str) -> None:
        catalog = read_catalog(lang)
        if catalog is None:
            wx.MessageBox(
                _("No language catalog found for “{code}”.").format(code=lang),
                _("Edit strings"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        dlg = EditLanguageStringsDialog(self, catalog)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            updated = dlg.catalog()
        finally:
            dlg.Destroy()
        try:
            write_overlay_catalog(updated)
        except ValueError as exc:
            wx.MessageBox(
                _("Could not save the language catalog:\n{detail}").format(
                    detail=exc
                ),
                _("Edit strings"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        if get_language() == lang:
            self._apply_ui_language()
        else:
            self._build_menubar()
        wx.MessageBox(
            _("Saved string edits for {name}.").format(
                name=updated.get("native_name") or lang
            ),
            _("Edit strings"),
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def on_update_language(self, lang: str) -> None:
        if not self._ai_required_for_language_tools():
            return
        catalog = read_catalog(lang)
        if catalog is None:
            wx.MessageBox(
                _("No language catalog found for “{code}”.").format(code=lang),
                _("Update translations"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        if not self._run_ui_translation(
            code=lang,
            native_name=str(catalog.get("native_name") or lang),
            display_name=str(
                catalog.get("display_name") or language_display_name(lang)
            ),
            direction=str(catalog.get("direction") or TEXT_DIRECTION_LTR),
            force=False,
            title=_("Updating translations"),
        ):
            return
        if get_language() == lang:
            self._apply_ui_language()
        else:
            self._build_menubar()
        wx.MessageBox(
            _("Translations for {name} were updated.").format(
                name=catalog.get("native_name") or lang
            ),
            _("Update translations"),
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def on_regenerate_language(self, lang: str) -> None:
        if not self._ai_required_for_language_tools():
            return
        catalog = read_catalog(lang)
        if catalog is None:
            wx.MessageBox(
                _("No language catalog found for “{code}”.").format(code=lang),
                _("Regenerate translations"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        name = catalog.get("native_name") or lang
        answer = wx.MessageBox(
            _(
                "Regenerate all UI strings for “{name}” with AI? "
                "This overwrites existing translations for this language "
                "on this computer."
            ).format(name=name),
            _("Regenerate translations"),
            wx.YES_NO | wx.ICON_WARNING,
            self,
        )
        if answer != wx.YES:
            return
        if not self._run_ui_translation(
            code=lang,
            native_name=str(catalog.get("native_name") or lang),
            display_name=str(
                catalog.get("display_name") or language_display_name(lang)
            ),
            direction=str(catalog.get("direction") or TEXT_DIRECTION_LTR),
            force=True,
            title=_("Regenerating translations"),
        ):
            return
        if get_language() == lang:
            self._apply_ui_language()
        else:
            self._build_menubar()
        wx.MessageBox(
            _("Translations for {name} were regenerated.").format(name=name),
            _("Regenerate translations"),
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def on_export_language(self, lang: str) -> None:
        catalog = read_catalog(lang)
        if catalog is None:
            wx.MessageBox(
                _("No language catalog found for “{code}”.").format(code=lang),
                _("Export language"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return
        default_name = f"checkmate-ui-{lang}.json"
        with wx.FileDialog(
            self,
            _("Export language catalog"),
            defaultFile=default_name,
            wildcard=_("JSON files (*.json)|*.json"),
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
        try:
            export_language(lang, path)
        except (OSError, ValueError) as exc:
            wx.MessageBox(
                _("Could not export the language catalog:\n{detail}").format(
                    detail=exc
                ),
                _("Export language"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        wx.MessageBox(
            _("Exported {name} to:\n{path}").format(
                name=catalog.get("native_name") or lang, path=path
            ),
            _("Export language"),
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    def on_remove_language(self, lang: str) -> None:
        catalog = read_catalog(lang)
        name = (catalog or {}).get("native_name") or language_native_name(lang)
        answer = wx.MessageBox(
            _(
                "Remove “{name}” from the Language menu? "
                "You can add it again later without losing the catalog files."
            ).format(name=name),
            _("Remove language"),
            wx.YES_NO | wx.ICON_QUESTION,
            self,
        )
        if answer != wx.YES:
            return
        try:
            hide_language(lang)
        except ValueError as exc:
            wx.MessageBox(
                _("Could not remove the language:\n{detail}").format(detail=exc),
                _("Remove language"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        self._apply_ui_language()

    # Compatibility aliases for older bind names / tests.
    def on_update_custom_language(self, lang: str) -> None:
        self.on_update_language(lang)

    def on_export_custom_language(self, lang: str) -> None:
        self.on_export_language(lang)

    def on_remove_custom_language(self, lang: str) -> None:
        self.on_remove_language(lang)

    def on_import_language(self, _event: wx.CommandEvent | None = None) -> None:
        with wx.FileDialog(
            self,
            _("Import language catalog"),
            wildcard=_("JSON files (*.json)|*.json"),
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = Path(dlg.GetPath())
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            code = _normalize_lang_code(str((data or {}).get("code", "")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            wx.MessageBox(
                _("This file is not a valid CheckMate UI language catalog."),
                _("Import language"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        overwrite = False
        if code and read_catalog(code) is not None:
            answer = wx.MessageBox(
                _(
                    "A catalog for “{code}” already exists on this computer. "
                    "Overwrite the editable copy?"
                ).format(code=code),
                _("Import language"),
                wx.YES_NO | wx.ICON_QUESTION,
                self,
            )
            if answer != wx.YES:
                return
            overwrite = True
        try:
            installed = import_custom_language(path, overwrite=overwrite)
        except ValueError as exc:
            key = str(exc)
            if key == "exists":
                msg = _("A catalog for this language already exists.")
            elif key in (
                "invalid_format",
                "invalid_catalog",
                "invalid_version",
                "invalid_code",
                "missing_names",
                "empty_strings",
            ):
                msg = _(
                    "This file is not a valid CheckMate UI language catalog."
                )
            else:
                msg = _("Could not import the language catalog:\n{detail}").format(
                    detail=key
                )
            wx.MessageBox(msg, _("Import language"), wx.OK | wx.ICON_ERROR, self)
            return
        except (OSError, json.JSONDecodeError) as exc:
            wx.MessageBox(
                _("Could not import the language catalog:\n{detail}").format(
                    detail=exc
                ),
                _("Import language"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        unhide_language(installed)
        name = language_display_name(installed)
        catalog = read_catalog(installed)
        if catalog:
            name = str(catalog.get("native_name") or name)
        switch = wx.MessageBox(
            _(
                "Imported “{name}”. Switch CheckMate to this language now?"
            ).format(name=name),
            _("Import language"),
            wx.YES_NO | wx.ICON_QUESTION,
            self,
        )
        if switch == wx.YES:
            set_language(installed)
            self._apply_ui_language()
            self._maybe_show_ai_translation_warning(installed)
        else:
            self._build_menubar()

    def _apply_layout_direction(self) -> None:
        """Apply LTR/RTL layout from the active language catalog."""
        rtl = get_text_direction() == TEXT_DIRECTION_RTL
        direction = wx.Layout_RightToLeft if rtl else wx.Layout_LeftToRight
        try:
            self.SetLayoutDirection(direction)
        except Exception:
            pass
        try:
            app = wx.GetApp()
            if app is not None:
                app.SetLayoutDirection(direction)
        except Exception:
            pass
        try:
            if hasattr(self, "panel") and self.panel is not None:
                self.panel.SetLayoutDirection(direction)
        except Exception:
            pass

    def _apply_ui_language(self) -> None:
        """Refresh visible UI strings after a language change."""
        self._apply_layout_direction()
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
        self._update_result_status_icon()
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
        if self.ai_overview_btn is not None:
            self.ai_overview_btn.SetLabel(_("AI &overview"))
            self.ai_overview_btn.SetToolTip(
                _("Generate an AI overview of this report (Ctrl+Shift+A)")
            )
        self._update_show_issues_button()
        self.copy_btn.SetLabel(_("&Copy summary"))
        self.copy_btn.SetToolTip(_("Copy the result summary (Ctrl+Shift+C)"))
        self.report_btn.SetLabel(_("&Report…"))
        self.report_btn.SetToolTip(
            _(
                "View or save reports, copy the summary, or view the full log"
            )
        )
        self._size_result_action_buttons()
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
            self._show_result_text(_("CheckMate ready."), title=None)
        self._update_status_bar()
        self.panel.Layout()
        self.Layout()

    def _update_show_issues_button(self) -> None:
        has_issues = (
            self._last_result is not None and bool(self._last_result.issues)
        )
        if not has_issues and self._issues_visible:
            self._set_issues_panel_visible(False)
            return
        if has_issues and show_issues_always() and not self._issues_visible:
            self._set_issues_panel_visible(True)
            return
        self.show_issues_btn.Enable(has_issues)
        if self._issues_visible:
            self.show_issues_btn.SetLabel(_("Hide &issues"))
            self.show_issues_btn.SetToolTip(_("Hide the issues list"))
        else:
            self.show_issues_btn.SetLabel(_("Show &issues"))
            self.show_issues_btn.SetToolTip(_("Show the issues list"))

    def _set_issues_panel_visible(
        self, visible: bool, *, keep_center: bool = True
    ) -> None:
        """Show or hide the Issues frame, keeping the window vertically centred."""
        if visible == self._issues_visible:
            return

        old_rect = self.GetRect()
        non_client_h = old_rect.height - self.GetClientSize().height

        if not visible:
            # Measure how much height the issues block contributes before hiding.
            issues_h = self.issues_sizer.GetSize().height
            item = self.root_sizer.GetItem(self.issues_sizer)
            border = item.GetBorder() if item is not None else 10
            # LEFT|RIGHT|BOTTOM → bottom margin only adds to height.
            self._issues_height_delta = max(issues_h + border, 0)

        self.root_sizer.Show(self.issues_sizer, visible, recursive=True)
        self.issues_box.Show(visible)
        self._issues_visible = visible
        # Re-apply Source visibility after the flag changes: showing those
        # controls while Issues is collapsed parks them at the panel origin.
        self._apply_source_filter_visibility()
        self.panel.Layout()
        self.Layout()

        # Never shrink below the laid-out minimum — that was clipping the
        # result text box to three lines when Issues started collapsed.
        min_frame_h = non_client_h + max(self.root_sizer.GetMinSize().height, 1)
        if visible:
            new_h = max(old_rect.height + self._issues_height_delta, min_frame_h)
        else:
            new_h = max(old_rect.height - self._issues_height_delta, min_frame_h)

        if keep_center:
            new_y = old_rect.y + (old_rect.height - new_h) // 2
            if new_y < 0:
                new_y = 0
            self.SetSize(old_rect.x, new_y, old_rect.width, new_h)
        else:
            # Startup: Centre() runs immediately afterwards.
            self.SetSize(old_rect.width, new_h)

        self._update_show_issues_button()
        self._size_result_action_buttons()
        self.panel.Layout()
        self.Layout()
        # If the result text was still squeezed (common after collapsing Issues),
        # grow the frame by the shortfall so all four lines remain visible.
        self._ensure_result_text_height(keep_center=keep_center)

    def _ensure_result_text_height(self, *, keep_center: bool) -> None:
        """Grow the frame when the result text box is below its four-line min."""
        need = getattr(self, "_result_text_min_height", 0)
        if need <= 0 or not hasattr(self, "result_label"):
            return
        have = self.result_label.GetSize().height
        deficit = need - have
        if deficit <= 0:
            return
        rect = self.GetRect()
        new_h = rect.height + deficit
        if keep_center:
            new_y = rect.y - deficit // 2
            if new_y < 0:
                new_y = 0
            self.SetSize(rect.x, new_y, rect.width, new_h)
        else:
            self.SetSize(rect.width, new_h)
        min_w = self.GetMinSize().width
        self.SetMinSize((min_w, max(self.GetMinSize().height, new_h)))
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

                def progress(msg: str, *, announce: bool = True) -> None:
                    wx.PostEvent(
                        self, ProgressEvent(message=msg, announce=announce)
                    )

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
            self._show_result_text(_("CheckMate ready."), title=None)

    def _clear_to_launch_state(self) -> None:
        """Reset the UI to the same state as a fresh launch."""
        self._last_result = None
        self._pending_fix_verify = None
        self._displayed_issues = []
        self._displayed_counts = []
        self.path_ctrl.ChangeValue("")
        self._show_result_text(_("CheckMate ready."), title=None)
        self.issues_list.DeleteAllItems()
        self.filter_choice.SetSelection(0)
        self._source_filter_names = []
        self.source_choice.Set(list(source_filter_choices()))
        self.source_choice.SetSelection(0)
        self._set_source_filter_visible(False)
        self._update_report_actions_enabled()
        self._update_result_status_icon()
        self._update_status_bar()
        self._focus_select_button()

    def _set_busy(self, busy: bool, *, update_icon: bool = True) -> None:
        self._busy = busy
        self.select_file_btn.Enable(not busy)
        self.select_folder_btn.Enable(not busy)
        self.path_ctrl.Enable(not busy)
        if update_icon:
            self._update_result_status_icon()

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

        # WebView / AI chrome can take a moment; show feedback while building.
        progress = wx.ProgressDialog(
            _("Issue details"),
            _("Opening issue details…"),
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
        )
        try:
            _pulse_progress(progress, _("Opening issue details…"))
            wx.SafeYield(self, True)
            dlg = IssueDetailDialog(
                self,
                issue,
                count=count,
                check_result=self._last_result,
            )
            # Edge/WebKit WebView creation is the slow part — keep progress up.
            if getattr(dlg, "_show_ai", False):
                _pulse_progress(progress, _("Loading AI view…"))
                wx.SafeYield(self, True)
                dlg._realize_ai_html_view()
        finally:
            _clear_progress_announce(progress)
            try:
                progress.Destroy()
            except RuntimeError:
                pass

        result_code = dlg.ShowModal()
        pending = getattr(dlg, "applied_fix_verify", None)
        try:
            dlg.Destroy()
        except RuntimeError:
            pass
        # ProgressDialog / WebView modal teardown can leave the frame disabled.
        try:
            self.Enable(True)
            self.Raise()
            _win_force_foreground(self)
        except RuntimeError:
            pass
        if result_code == wx.ID_APPLY:
            # Apply fix succeeded — re-scan, then confirm resolution / offer revert.
            self._pending_fix_verify = pending
            wx.CallAfter(self.on_check, None)

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
        """Remember whether Source filter is wanted; show only if Issues is open."""
        self._source_filter_wanted = visible
        self._apply_source_filter_visibility()

    def _apply_source_filter_visibility(self) -> None:
        """Map Source filter wanted-state onto the screen.

        The Issues panel starts collapsed. Showing Source while that panel is
        hidden makes wx place the controls at the panel origin.
        """
        show = bool(self._source_filter_wanted and self._issues_visible)
        self.filter_row.Show(self.source_label, show)
        self.filter_row.Show(self.source_choice, show)
        self.source_label.Show(show)
        self.source_choice.Show(show)
        self.source_label.Enable(self._source_filter_wanted)
        self.source_choice.Enable(self._source_filter_wanted)
        if self._issues_visible:
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
        self._update_result_status_icon()
        self._update_status_bar()
        self._speak_status(result.announcement())
        self._announce_result_pane()
        try:
            from .sounds import play_completion_sound

            play_completion_sound(result.verdict)
        except Exception:
            pass
        try:
            from .telemetry import log_check

            log_check(result)
        except Exception:
            pass

    def _summary_text(self) -> str:
        result = self._last_result
        if result is None:
            return ""
        return format_text_report(result, include_full_log=False)

    # --- Events ---

    def on_result_icon_click(self, event: wx.MouseEvent) -> None:
        """Status icon acts as a shortcut for Select file…"""
        event.Skip()
        if self._busy:
            return
        self.on_browse_file(None)

    def on_browse_file(self, _event: wx.CommandEvent | None) -> None:
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
        # Quiet focus so screen readers announce "Checking…".
        self._show_result_text(
            _("Checking…"), title=_("Checking…"), focus=True
        )
        self.issues_list.DeleteAllItems()

        def worker() -> None:
            def progress(msg: str, *, announce: bool = True) -> None:
                wx.PostEvent(
                    self, ProgressEvent(message=msg, announce=announce)
                )

            result = run_check(path, exploded=None, progress=progress)
            wx.PostEvent(self, ResultEvent(result=result))

        threading.Thread(target=worker, daemon=True).start()

    def on_progress_event(self, event: ProgressEvent) -> None:
        # Keep the status bar on version info; show progress in the result area.
        if event.message == _("Ready"):
            self._update_status_bar()
            # Prefer starting a shell-opened publication over resetting to idle.
            if self._pending_open_paths:
                self._speak_status(_("Ready"))
                self._flush_pending_open_paths()
            elif not self._busy:
                self._restore_result_display()
                ready_text = self.result_label.GetValue().strip() or _("Ready")
                self._speak_status(ready_text)
            else:
                self._speak_status(_("Ready"))
            return
        announce = bool(getattr(event, "announce", True))
        if announce:
            # Milestones only (tool start / warm-up): quiet focus into Result.
            self._show_result_text(
                event.message, update_title=False, focus=True
            )
            return
        # Living updates (Ace stream / elapsed timer): refresh Result text and
        # speak with progress throttle — do not steal focus every tick.
        self.result_label.ChangeValue(event.message)
        self._set_result_accessible_name(event.message)
        self._speak_status(event.message, progress=True)

    def on_result_event(self, event: ResultEvent) -> None:
        # Defer icon refresh until _apply_result so we don't flash the previous
        # (or idle) status between clearing busy and loading the new verdict.
        self._set_busy(False, update_icon=False)
        pending = self._pending_fix_verify
        self._pending_fix_verify = None
        self._apply_result(event.result)
        self._flush_pending_open_paths()
        # Layout (e.g. auto-show Issues) can settle after apply; reclaim again.
        if _ANNOUNCE_MODE == "focus":
            wx.CallAfter(self._reclaim_focus_after_check)
        if pending is not None:
            wx.CallAfter(self._verify_applied_fix, pending, event.result)

    def _message_box_revert_or_keep(
        self,
        message: str,
        caption: str,
        *,
        default_revert: bool,
    ) -> bool:
        """Ask Revert vs Keep; return True when the user chooses Revert."""
        style = wx.YES_NO | wx.ICON_WARNING
        style |= wx.YES_DEFAULT if default_revert else wx.NO_DEFAULT
        dlg = wx.MessageDialog(self, message, caption, style)
        try:
            dlg.SetYesNoLabels(_("Revert"), _("Keep"))
        except Exception:
            # Older wx builds may lack SetYesNoLabels; fall back to Yes/No.
            pass
        try:
            return dlg.ShowModal() == wx.ID_YES
        finally:
            dlg.Destroy()

    def _verify_applied_fix(self, pending, result: CheckResult) -> None:
        """After Apply fix + re-check: confirm outcome, report side effects, offer revert."""
        from .ai.fix import PendingFixVerify, evaluate_fix_outcome
        from .models import Verdict

        if not isinstance(pending, PendingFixVerify):
            return

        backup = Path(pending.backup_path) if pending.backup_path else None
        has_backup = backup is not None and backup.is_file()

        if result.verdict == Verdict.ERROR:
            msg = _(
                "The publication was changed, but the re-check could not be completed.\n\n"
                "{detail}"
            ).format(detail=(result.error_message or "").strip() or _("Unknown error."))
            changelog = getattr(pending, "changelog_path", "") or ""
            if changelog:
                msg += "\n\n" + _("Edit log:\n{path}").format(path=changelog)
            if has_backup:
                msg += "\n\n" + _(
                    "Do you want to revert to the backup created before the fix?"
                )
                if self._message_box_revert_or_keep(
                    msg,
                    _("Re-check failed"),
                    default_revert=False,
                ):
                    self._revert_applied_fix(pending)
                else:
                    try:
                        from .edit_log import log_fix_validation

                        log_fix_validation(
                            target_path=pending.target_path,
                            issue=pending.issue,
                            backup_path=pending.backup_path,
                            outcome="recheck_failed_kept",
                            detail=result.error_message or "",
                        )
                    except OSError:
                        pass
            else:
                try:
                    from .edit_log import log_fix_validation

                    log_fix_validation(
                        target_path=pending.target_path,
                        issue=pending.issue,
                        backup_path=pending.backup_path,
                        outcome="recheck_failed_no_backup",
                        detail=result.error_message or "",
                    )
                except OSError:
                    pass
                wx.MessageBox(msg, _("Re-check failed"), wx.OK | wx.ICON_WARNING, self)
            return

        report = evaluate_fix_outcome(
            pending.issue,
            pending.before_result,
            result,
            batch_mode=bool(getattr(pending, "batch_mode", False)),
        )
        lines: list[str] = []

        if report.batch_mode:
            if report.target_resolved:
                lines.append(
                    _(
                        "All matching issues with code {code} appear to be resolved "
                        "({before} → {after})."
                    ).format(
                        code=pending.issue.code or _("(no code)"),
                        before=report.matched_before,
                        after=report.matched_after,
                    )
                )
            else:
                lines.append(
                    _(
                        "Matching issues with code {code}: {before} before, "
                        "{after} after the batch fix."
                    ).format(
                        code=pending.issue.code or _("(no code)"),
                        before=report.matched_before,
                        after=report.matched_after,
                    )
                )
            patch_count = int(getattr(pending, "patch_count", 0) or 0)
            if patch_count:
                lines.append(
                    _("Patches applied: {n}.").format(n=patch_count)
                )
        elif report.target_resolved:
            lines.append(
                _("The targeted issue appears to be resolved (code: {code}).").format(
                    code=pending.issue.code or _("(no code)")
                )
            )
        else:
            lines.append(
                _(
                    "The targeted issue is still reported after the fix was applied "
                    "(code: {code})."
                ).format(code=pending.issue.code or _("(no code)"))
            )

        lines.append("")
        lines.append(
            _(
                "Totals before: {fatals} fatal(s), {errors} error(s), "
                "{warnings} warning(s)."
            ).format(
                fatals=report.before_fatals,
                errors=report.before_errors,
                warnings=report.before_warnings,
            )
        )
        lines.append(
            _(
                "Totals after: {fatals} fatal(s), {errors} error(s), "
                "{warnings} warning(s)."
            ).format(
                fatals=report.after_fatals,
                errors=report.after_errors,
                warnings=report.after_warnings,
            )
        )
        if report.counts_reduced:
            lines.append(_("Overall errors/warnings decreased."))
        else:
            lines.append(
                _("Overall errors/warnings did not decrease after the fix.")
            )

        if report.fixed_ace_issue:
            lines.append("")
            if report.new_epubcheck_errors:
                lines.append(
                    _(
                        "Fixing this Ace issue introduced {n} new EPUBCheck "
                        "error(s) that were not present before:"
                    ).format(n=len(report.new_epubcheck_errors))
                )
                for issue in report.new_epubcheck_errors[:8]:
                    loc = f" — {issue.location}" if issue.location else ""
                    lines.append(f"• {issue.code}{loc}: {issue.message}")
                if len(report.new_epubcheck_errors) > 8:
                    lines.append(
                        _("…and {n} more.").format(
                            n=len(report.new_epubcheck_errors) - 8
                        )
                    )
            else:
                lines.append(
                    _("No new EPUBCheck errors were introduced by this Ace fix.")
                )

        body = "\n".join(lines)

        changelog = getattr(pending, "changelog_path", "") or ""
        if changelog:
            body += "\n\n" + _("Edit log:\n{path}").format(path=changelog)

        if not report.has_concerns:
            try:
                from .edit_log import log_fix_validation

                log_fix_validation(
                    target_path=pending.target_path,
                    issue=pending.issue,
                    backup_path=pending.backup_path,
                    outcome="confirmed",
                    detail=_("The targeted issue appears to be resolved."),
                )
            except OSError:
                pass
            wx.MessageBox(
                body,
                _("Fix confirmed"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        if not has_backup:
            try:
                from .edit_log import log_fix_validation

                log_fix_validation(
                    target_path=pending.target_path,
                    issue=pending.issue,
                    backup_path=pending.backup_path,
                    outcome="concerns_no_backup",
                    detail=body,
                )
            except OSError:
                pass
            wx.MessageBox(
                body
                + "\n\n"
                + _("No backup file was found to revert."),
                _("Fix not confirmed"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        if self._message_box_revert_or_keep(
            body
            + "\n\n"
            + _(
                "Do you want to revert to the backup?\n\n"
                "Backup:\n{backup}"
            ).format(backup=pending.backup_path),
            _("Fix not confirmed"),
            default_revert=True,
        ):
            self._revert_applied_fix(pending)
        else:
            try:
                from .edit_log import log_fix_validation

                log_fix_validation(
                    target_path=pending.target_path,
                    issue=pending.issue,
                    backup_path=pending.backup_path,
                    outcome="concerns_kept",
                    detail=body,
                )
            except OSError:
                pass

    def _revert_applied_fix(self, pending) -> None:
        from .epub_package import restore_from_backup

        try:
            restore_from_backup(pending.backup_path, pending.restore_to)
            for bak, restore_to in getattr(pending, "extra_backups", None) or []:
                restore_from_backup(bak, restore_to)
        except OSError as exc:
            wx.MessageBox(
                _("Could not revert to the backup:\n{detail}").format(detail=str(exc)),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        try:
            from .edit_log import log_fix_reverted

            log_fix_reverted(
                target_path=pending.target_path,
                issue=pending.issue,
                backup_path=pending.backup_path,
                restore_to=pending.restore_to,
            )
        except OSError:
            pass
        revert_msg = _("The publication was reverted to the backup.")
        changelog = getattr(pending, "changelog_path", "") or ""
        if changelog:
            revert_msg += "\n\n" + _("Edit log:\n{path}").format(path=changelog)
        wx.MessageBox(
            revert_msg,
            _("Reverted"),
            wx.OK | wx.ICON_INFORMATION,
            self,
        )
        wx.CallAfter(self.on_check, None)

    def on_filter_changed(self, _event: wx.CommandEvent) -> None:
        self._populate_issues()

    def on_unique_codes_changed(self, _event: wx.CommandEvent) -> None:
        update_settings(unique_codes=bool(self.unique_codes_cb.GetValue()))
        self._populate_issues()

    def on_settings(self, _event: wx.CommandEvent) -> None:
        from .settings_dialog import SettingsDialog

        prev_ai = ai_features_enabled()
        with SettingsDialog(self) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            dlg.apply()
        if ai_features_enabled() != prev_ai:
            self._apply_ai_features_visibility()
        else:
            self._update_show_issues_button()

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

    def on_show_issues(self, _event: wx.CommandEvent) -> None:
        self._set_issues_panel_visible(not self._issues_visible)

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
        view_html = menu.Append(wx.ID_ANY, _("View &HTML report in browser\tCtrl+H"))
        save_html = menu.Append(wx.ID_ANY, _("Save &HTML report…\tCtrl+S"))
        menu.AppendSeparator()
        view_text = menu.Append(wx.ID_ANY, _("View &text report\tCtrl+T"))
        save_text = menu.Append(wx.ID_ANY, _("Save &text report…\tCtrl+Shift+S"))
        menu.AppendSeparator()
        copy_item = menu.Append(wx.ID_ANY, _("&Copy summary\tCtrl+Shift+C"))
        clear_item = menu.Append(wx.ID_ANY, _("C&lear results\tCtrl+Shift+N"))
        menu.AppendSeparator()
        log_item = menu.Append(wx.ID_ANY, _("View full &log\tCtrl+L"))
        changelog_item = menu.Append(wx.ID_ANY, _("View edit &changelog…\tCtrl+Shift+G"))
        changelog_item.Enable(self._changelog_path_if_present() is not None)
        menu.Bind(wx.EVT_MENU, self.on_view_html_report, view_html)
        menu.Bind(wx.EVT_MENU, self.on_save_html_report, save_html)
        menu.Bind(wx.EVT_MENU, self.on_view_text_report, view_text)
        menu.Bind(wx.EVT_MENU, self.on_save_text_report, save_text)
        menu.Bind(wx.EVT_MENU, self.on_copy_summary, copy_item)
        menu.Bind(wx.EVT_MENU, self.on_clear_results, clear_item)
        menu.Bind(wx.EVT_MENU, self.on_view_full_log, log_item)
        menu.Bind(wx.EVT_MENU, self.on_view_changelog, changelog_item)
        _popup_menu_below(self, menu, self.report_btn)
        menu.Destroy()

    def _current_publication_path(self) -> Path | None:
        """Best path for the publication currently in focus (result or path field)."""
        if self._last_result is not None and self._last_result.target_path:
            path = Path(self._last_result.target_path).expanduser()
            if path.exists():
                return path
        path = self._current_path()
        if path is not None:
            path = path.expanduser()
            if path.exists():
                return path
        return None

    def _changelog_path_if_present(self) -> Path | None:
        from .edit_log import find_changelog

        return find_changelog(self._current_publication_path())

    def on_view_changelog(self, _event: wx.CommandEvent) -> None:
        path = self._changelog_path_if_present()
        if path is None:
            wx.MessageBox(
                _(
                    "No CheckMate edit changelog was found for this publication.\n\n"
                    "A changelog is created beside the file (or inside an exploded "
                    "folder) when you apply an AI fix."
                ),
                _("No changelog"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            wx.MessageBox(
                _("Could not read the changelog:\n{error}").format(error=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        from .changelog_dialog import ChangelogDialog

        dlg = ChangelogDialog(self, path=path, markdown_text=text)
        dlg.ShowModal()
        dlg.Destroy()

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

    def _close_overview_progress(self) -> None:
        timer = self._overview_progress_timer
        if timer is not None:
            try:
                if timer.IsRunning():
                    timer.Stop()
            except RuntimeError:
                pass
            self._overview_progress_timer = None
        dlg = self._overview_progress
        self._overview_progress = None
        _clear_progress_announce(dlg)
        if dlg is not None:
            try:
                dlg.Destroy()
            except RuntimeError:
                pass

    def _overview_status_callback(self, message: str) -> None:
        def update() -> None:
            if self._overview_progress is not None:
                cont = _pulse_progress(self._overview_progress, message)
                if not cont and self._overview_cancel is not None:
                    self._overview_cancel.set()

        wx.CallAfter(update)

    def _on_overview_progress_timer(self, _event: wx.TimerEvent) -> None:
        dlg = self._overview_progress
        cancel = self._overview_cancel
        if dlg is None or cancel is None:
            return
        cont = _pulse_progress(dlg)  # animation only — do not announce
        if not cont:
            cancel.set()
            _pulse_progress(dlg, _("Cancelling…"))
            self._close_overview_progress()

    def on_ai_overview(self, _event: wx.CommandEvent) -> None:
        if not ai_features_enabled():
            return
        if self._busy:
            wx.MessageBox(
                _("A check is already running. Wait for it to finish, then try again."),
                _("Busy"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if self._overview_progress is not None:
            return
        result = self._require_result(_("Nothing to overview"))
        if result is None:
            return

        cancel = threading.Event()
        self._overview_cancel = cancel
        self._overview_progress = wx.ProgressDialog(
            _("AI overview"),
            _("Loading AI libraries…"),
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT,
        )
        _present_progress_dialog(
            self._overview_progress, _("Loading AI libraries…")
        )
        self._overview_progress_timer = wx.Timer(self)
        self.Bind(
            wx.EVT_TIMER, self._on_overview_progress_timer, self._overview_progress_timer
        )
        self._overview_progress_timer.Start(200)
        status_cb = self._overview_status_callback

        def work() -> None:
            from .ai.explain import ExplainResult, error_message_for_key
            from .ai.litellm_client import preload_litellm
            from .ai.overview import explain_overview

            ok, detail = preload_litellm()
            if not ok:
                def fail() -> None:
                    self._close_overview_progress()
                    wx.MessageBox(
                        error_message_for_key("no_litellm", detail=detail),
                        _("AI overview"),
                        wx.OK | wx.ICON_ERROR,
                        self,
                    )

                wx.CallAfter(fail)
                return
            if cancel.is_set():
                return

            def _checking() -> None:
                if self._overview_progress is not None:
                    _pulse_progress(
                        self._overview_progress, _("Checking AI connection…")
                    )

            wx.CallAfter(_checking)
            try:
                out = explain_overview(
                    result,
                    cancel_event=cancel,
                    status_callback=status_cb,
                )
            except Exception as exc:
                out = ExplainResult(ok=False, error_key="provider_error", text=str(exc))
            if cancel.is_set():
                wx.CallAfter(self._close_overview_progress)
                return
            try:
                wx.PostEvent(self, OverviewAiEvent(result=out, check_result=result))
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _on_overview_ai_event(self, event: OverviewAiEvent) -> None:
        self._close_overview_progress()
        out = getattr(event, "result", None)
        check_result = getattr(event, "check_result", None) or self._last_result
        if out is None or check_result is None:
            return
        if not out.ok:
            from .ai.explain import error_message_for_key

            if out.error_key == "cancelled":
                self.SetStatusText(_("Cancelled."))
                wx.CallLater(4000, self._update_status_bar)
                return
            msg = error_message_for_key(out.error_key, detail=out.text or "")
            wx.MessageBox(msg, _("AI overview"), wx.OK | wx.ICON_ERROR, self)
            return

        try:
            from .telemetry import log_ai_overview

            log_ai_overview()
        except Exception:
            pass

        progress = wx.ProgressDialog(
            _("AI overview"),
            _("Loading AI view…"),
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
        )
        dlg = None
        try:
            _pulse_progress(progress, _("Loading AI view…"))
            wx.SafeYield(self, True)
            dlg = AiOverviewDialog(
                self,
                markdown_text=out.text or "",
                result=check_result,
                session=out.session,
            )
            dlg._realize_ai_html_view()
        finally:
            _clear_progress_announce(progress)
            try:
                progress.Destroy()
            except RuntimeError:
                pass
        if dlg is not None:
            dlg.ShowModal()
            try:
                dlg.Destroy()
            except RuntimeError:
                pass
            try:
                self.Enable(True)
                self.Raise()
                _win_force_foreground(self)
            except RuntimeError:
                pass

    def _close_alt_assess_progress(self) -> None:
        if self._alt_assess_progress_timer is not None:
            try:
                self._alt_assess_progress_timer.Stop()
            except RuntimeError:
                pass
            self._alt_assess_progress_timer = None
        if self._alt_assess_progress is not None:
            try:
                self._alt_assess_progress.Destroy()
            except RuntimeError:
                pass
            self._alt_assess_progress = None
        self._alt_assess_cancel = None

    def _alt_assess_status_callback(self, message: str) -> None:
        def update() -> None:
            if self._alt_assess_progress is not None:
                cont = _pulse_progress(self._alt_assess_progress, message)
                if not cont and self._alt_assess_cancel is not None:
                    self._alt_assess_cancel.set()

        wx.CallAfter(update)

    def _on_alt_assess_progress_timer(self, _event: wx.TimerEvent) -> None:
        dlg = self._alt_assess_progress
        cancel = self._alt_assess_cancel
        if dlg is None or cancel is None:
            return
        cont = _pulse_progress(dlg)
        if not cont:
            cancel.set()
            _pulse_progress(dlg, _("Cancelling…"))
            self._close_alt_assess_progress()

    def on_alt_assess_export(self, _event: wx.CommandEvent) -> None:
        if not ai_features_enabled():
            return
        if self._busy:
            wx.MessageBox(
                _("A check is already running. Wait for it to finish, then try again."),
                _("Busy"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        if self._alt_assess_progress is not None:
            return

        with wx.DirDialog(
            self,
            _("Select a Fido alt-text export folder"),
            style=wx.DD_DIR_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            folder = Path(dlg.GetPath())

        # Preflight: load CSV + confirm sample size
        try:
            from .ai.alt_export import load_alt_export
            from .ai.alt_sample import DEFAULT_SAMPLE_PERCENT, sample_choice_labels

            export = load_alt_export(folder)
        except FileNotFoundError as exc:
            from .ai.explain import error_message_for_key

            wx.MessageBox(
                error_message_for_key("bad_export", detail=str(exc)),
                _("Alt text assessment"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        except ValueError as exc:
            from .ai.explain import error_message_for_key

            wx.MessageBox(
                error_message_for_key("bad_export", detail=str(exc)),
                _("Alt text assessment"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        except Exception as exc:
            wx.MessageBox(
                _("Could not read the export folder:\n{error}").format(error=exc),
                _("Alt text assessment"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        counts = export.counts()
        choice_rows = sample_choice_labels(counts["total"])
        choices = [label for label, _mode, _pct in choice_rows]
        # Prefer 25% as the default selection when available.
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
            _("Alt text assessment"),
            choices,
        )
        try:
            choice_dlg.SetSelection(default_sel)
            if choice_dlg.ShowModal() != wx.ID_OK:
                return
            sel = choice_dlg.GetSelection()
            _label, mode, percent = choice_rows[sel]
            percent = percent if percent is not None else 100
        finally:
            choice_dlg.Destroy()

        self._start_alt_assess(folder, mode=mode, percent=percent, prior=None)

    def _start_alt_assess(
        self,
        folder: Path,
        *,
        mode: str,
        percent: int,
        prior,
        parent: wx.Window | None = None,
    ) -> None:
        """Run alt assessment on a worker thread and show/update the dialog."""
        if self._alt_assess_progress is not None:
            return
        cancel = threading.Event()
        self._alt_assess_cancel = cancel
        self._alt_assess_progress = wx.ProgressDialog(
            _("Alt text assessment"),
            _("Loading AI libraries…"),
            maximum=100,
            parent=parent or self,
            style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT,
        )
        _present_progress_dialog(
            self._alt_assess_progress, _("Loading AI libraries…")
        )
        self._alt_assess_progress_timer = wx.Timer(self)
        self.Bind(
            wx.EVT_TIMER,
            self._on_alt_assess_progress_timer,
            self._alt_assess_progress_timer,
        )
        self._alt_assess_progress_timer.Start(200)
        status_cb = self._alt_assess_status_callback

        def work() -> None:
            from .ai.alt_assess import AltAssessResult, assess_alt_export
            from .ai.explain import error_message_for_key
            from .ai.litellm_client import preload_litellm

            ok, detail = preload_litellm()
            if not ok:
                def fail() -> None:
                    self._close_alt_assess_progress()
                    wx.MessageBox(
                        error_message_for_key("no_litellm", detail=detail),
                        _("Alt text assessment"),
                        wx.OK | wx.ICON_ERROR,
                        parent or self,
                    )

                wx.CallAfter(fail)
                return
            if cancel.is_set():
                return
            try:
                out = assess_alt_export(
                    folder,
                    mode=mode,
                    percent=percent,
                    prior=prior,
                    cancel_event=cancel,
                    status_callback=status_cb,
                )
            except Exception as exc:
                out = AltAssessResult(
                    ok=False, error_key="provider_error", detail=str(exc)
                )
            if cancel.is_set():
                wx.CallAfter(self._close_alt_assess_progress)
                return
            try:
                wx.PostEvent(
                    self,
                    AltAssessAiEvent(result=out),
                )
            except RuntimeError:
                return

        threading.Thread(target=work, daemon=True).start()


    def _on_alt_assess_ai_event(self, event: AltAssessAiEvent) -> None:
        self._close_alt_assess_progress()
        out = getattr(event, "result", None)
        if out is None:
            return
        if not out.ok:
            from .ai.explain import error_message_for_key

            if out.error_key == "cancelled":
                self.SetStatusText(_("Cancelled."))
                wx.CallLater(4000, self._update_status_bar)
                return
            msg = error_message_for_key(
                out.error_key, detail=out.detail or out.text or ""
            )
            wx.MessageBox(msg, _("Alt text assessment"), wx.OK | wx.ICON_ERROR, self)
            return

        try:
            from .telemetry import log_ai_alt_assess

            log_ai_alt_assess()
        except Exception:
            pass

        from .ai.alt_dialog import AltAssessDialog

        dlg = AltAssessDialog(self, result=out)
        self._alt_assess_dialog = dlg
        dlg.ShowModal()
        self._alt_assess_dialog = None
        try:
            dlg.Destroy()
        except RuntimeError:
            pass
        try:
            self.Enable(True)
            self.Raise()
            _win_force_foreground(self)
        except RuntimeError:
            pass

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

                def progress(msg: str, *, announce: bool = True) -> None:
                    wx.PostEvent(
                        self, ProgressEvent(message=msg, announce=announce)
                    )

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

    def on_knowledge_base(self, _event: wx.CommandEvent) -> None:
        open_knowledge_base_home(self)

    def on_update_knowledge_base(self, _event: wx.CommandEvent) -> None:
        run_kb_update_with_progress(self)

    def on_about(self, _event: wx.CommandEvent) -> None:
        with AboutDialog(self) as dlg:
            dlg.ShowModal()

    def on_open_app_log(self, _event: wx.CommandEvent) -> None:
        from .logging_setup import log_file_path

        path = log_file_path()
        if not path.is_file():
            wx.MessageBox(
                _("No debugging log has been written yet."),
                _("Debugging log"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess

                subprocess.call(["open", str(path)])
            else:
                import subprocess

                subprocess.call(["xdg-open", str(path)])
        except OSError:
            wx.MessageBox(
                _("Could not open the debugging log:\n{path}").format(path=path),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )


def run_app(argv: list[str] | None = None) -> None:
    import os

    # Before any LiteLLM import (including via Explain/Fix threads).
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

    from .logging_setup import configure_logging

    configure_logging()
    paths = parse_launch_paths(argv)
    app = EBrailleApp(initial_paths=paths)
    app.MainLoop()
