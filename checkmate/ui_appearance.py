"""Follow the OS light/dark appearance in CheckMate's wx UI.

macOS and GTK already follow the system appearance; leftover hardcoded light
fills are still corrected. Windows wxPython 4.2 reports ``IsDark()`` correctly
but keeps light ``SYS_COLOUR_*`` values, so client areas are darkened there.
wx 4.3+ can opt in via ``SetAppearance`` / ``MSWEnableDarkMode``.

Native Windows ``ProgressDialog`` (Task Dialog) keeps black text on a dark
chrome — we switch to ``GenericProgressDialog`` and paint the labels.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

import wx

_FILTER: wx.EventFilter | None = None
_edit_hint_hwnds: set[int] = set()
_edit_hint_text: dict[int, str] = {}
_list_header_dark_hwnds: set[int] = set()
_notebook_dark_hwnds: set[int] = set()
_notebook_hot: dict[int, int] = {}
_ctlcolor_parent_hwnds: set[int] = set()
_control_subclassed: set[int] = set()

COLOR_THEME_SETTING = "ui_color_theme"
COLOR_THEME_SYSTEM = "system"
COLOR_THEME_LIGHT = "light"
COLOR_THEME_DARK = "dark"
COLOR_THEME_DEFAULT = COLOR_THEME_SYSTEM

_DARK_BG = (32, 32, 32)
_DARK_CTRL = (43, 43, 43)
_DARK_FG = (245, 245, 245)
_DARK_MUTED = (176, 176, 176)
_LIGHT_BG = (255, 255, 255)
_LIGHT_FG = (20, 20, 20)
_LIGHT_MUTED = (70, 70, 70)
_LIGHT_WELL = (200, 200, 200)


def prepare_process_appearance() -> None:
    """Call before ``import wx`` / ``wx.App()`` so wx 3.3+ can follow the OS.

    Windows-only environment opt-in. macOS and Linux already use the system
    appearance by default. Light theme opts out of wx's dark-mode hint.
    """
    if sys.platform != "win32":
        return
    if get_color_theme() == COLOR_THEME_LIGHT:
        os.environ["wx_msw_dark_mode"] = "0"
    else:
        os.environ.setdefault("wx_msw_dark_mode", "1")


def normalize_color_theme(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {COLOR_THEME_LIGHT, COLOR_THEME_DARK, COLOR_THEME_SYSTEM}:
        return raw
    if raw in {"auto", "default", "os", "system settings", "use system setting"}:
        return COLOR_THEME_SYSTEM
    return COLOR_THEME_DEFAULT


def get_color_theme() -> str:
    try:
        from .settings import read_settings

        return normalize_color_theme(
            read_settings().get(COLOR_THEME_SETTING, COLOR_THEME_DEFAULT)
        )
    except Exception:
        return COLOR_THEME_DEFAULT


def set_color_theme(theme: object) -> str:
    value = normalize_color_theme(theme)
    from .settings import update_settings

    update_settings(**{COLOR_THEME_SETTING: value})
    return value


def os_prefers_dark() -> bool:
    """True when the OS appearance is dark (ignores CheckMate's color theme)."""
    try:
        appearance = wx.SystemSettings.GetAppearance()
        if appearance is not None:
            is_dark = getattr(appearance, "IsDark", None)
            if callable(is_dark) and bool(is_dark()):
                return True
            using_dark = getattr(appearance, "IsUsingDarkBackground", None)
            if callable(using_dark) and bool(using_dark()):
                return True
    except Exception:
        pass
    try:
        colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
        if colour is not None and colour.IsOk():
            return _luma(colour) < 128
    except Exception:
        return False
    return False


def prefers_dark() -> bool:
    """True when CheckMate should use a dark chrome (theme setting, then OS)."""
    theme = get_color_theme()
    if theme == COLOR_THEME_DARK:
        return True
    if theme == COLOR_THEME_LIGHT:
        return False
    return os_prefers_dark()


def system_prefers_dark() -> bool:
    """Alias for :func:`prefers_dark` (includes the user's color theme)."""
    return prefers_dark()


def html_color_scheme() -> str:
    """Value for HTML ``color-scheme`` (``only`` stops WebView2 using the OS)."""
    theme = get_color_theme()
    if theme == COLOR_THEME_DARK:
        return "only dark"
    if theme == COLOR_THEME_LIGHT:
        return "only light"
    return "light dark"


def html_report_theme() -> str:
    """``auto``, ``light``, or ``dark`` for baked HTML/CSS tokens."""
    theme = get_color_theme()
    if theme in {COLOR_THEME_LIGHT, COLOR_THEME_DARK}:
        return theme
    return "auto"


def html_root_class() -> str:
    """``checkmate-theme-light``, ``checkmate-theme-dark``, or ``checkmate-theme-system``."""
    kind = html_report_theme()
    return f"checkmate-theme-{kind if kind != 'auto' else 'system'}"


def wrap_os_dark_css(inner: str) -> str:
    """Dark CSS for System (OS media query), Dark (always), or Light (omit).

    WebView2's ``prefers-color-scheme`` follows Windows, not the page, so Light
    must not emit a dark media query at all.
    """
    body = (inner or "").strip("\n")
    if not body:
        return ""
    kind = html_report_theme()
    if kind == COLOR_THEME_LIGHT:
        return ""
    if kind == COLOR_THEME_DARK:
        return body + "\n"
    return f"@media (prefers-color-scheme: dark) {{\n{body}\n}}\n"


def wx_system_colours_are_dark() -> bool:
    """True when wx has remapped ``SYS_COLOUR_WINDOW`` to a dark fill."""
    try:
        colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
        return bool(colour and colour.IsOk() and _luma(colour) < 128)
    except Exception:
        return False


def window_background_colour() -> wx.Colour:
    colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
    if prefers_dark():
        if _is_light_fill(colour):
            return wx.Colour(*_DARK_BG)
        if colour is not None and colour.IsOk():
            return colour
        return wx.Colour(*_DARK_BG)
    if colour is not None and colour.IsOk() and _luma(colour) < 128:
        return wx.Colour(*_LIGHT_BG)
    if colour is not None and colour.IsOk():
        return colour
    return wx.Colour(*_LIGHT_BG)


def panel_background_colour() -> wx.Colour:
    colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE)
    if prefers_dark():
        if _is_light_fill(colour):
            return wx.Colour(*_DARK_BG)
        if colour is not None and colour.IsOk():
            return colour
        return window_background_colour()
    if colour is not None and colour.IsOk() and _luma(colour) < 128:
        return wx.Colour(*_LIGHT_BG)
    if colour is not None and colour.IsOk():
        return colour
    return window_background_colour()


def well_background_colour() -> wx.Colour:
    """Slightly inset panel (preview well)."""
    window = window_background_colour()
    if prefers_dark():
        if window.IsOk() and _luma(window) < 128:
            return wx.Colour(
                min(255, window.Red() + 16),
                min(255, window.Green() + 16),
                min(255, window.Blue() + 16),
            )
        return wx.Colour(*_DARK_CTRL)
    return wx.Colour(*_LIGHT_WELL)


def control_fill_colour() -> wx.Colour:
    """Fill for text fields, choices, and list-like controls."""
    colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
    if prefers_dark():
        if colour is not None and colour.IsOk() and _luma(colour) < 128:
            return colour
        return wx.Colour(*_DARK_CTRL)
    if colour is not None and colour.IsOk() and _luma(colour) < 128:
        return wx.Colour(*_LIGHT_BG)
    if colour is not None and colour.IsOk():
        return colour
    return wx.Colour(*_LIGHT_BG)


def primary_text_colour() -> wx.Colour:
    """Main control text that stays readable on :func:`control_fill_colour`."""
    colour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)
    if prefers_dark():
        if colour is not None and colour.IsOk() and _luma(colour) >= 160:
            return colour
        return wx.Colour(*_DARK_FG)
    if colour is None or not colour.IsOk() or _luma(colour) >= 160:
        return wx.Colour(*_LIGHT_FG)
    return colour


def secondary_text_colour() -> wx.Colour:
    """Muted label colour that stays readable on light and dark backgrounds."""
    gray = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
    if gray is not None and gray.IsOk():
        luma = _luma(gray)
        if prefers_dark() and luma < 90:
            return wx.Colour(*_DARK_MUTED)
        if not prefers_dark() and luma > 180:
            return wx.Colour(*_LIGHT_MUTED)
        return gray
    return wx.Colour(*(_DARK_MUTED if prefers_dark() else _LIGHT_MUTED))


def enable_app_appearance(app: wx.App | None = None) -> None:
    """Opt this process into the chosen light/dark theme. Safe to call more than once."""
    global _FILTER
    app = app or wx.GetApp()
    if app is None:
        return
    prepare_process_appearance()
    _windows_allow_dark_mode_for_app()
    _wx_set_system_appearance(app)
    _patch_progress_dialogs()
    if _FILTER is None:
        filt = _AppearanceFilter()
        try:
            app.AddFilter(filt)
        except Exception:
            return
        _FILTER = filt


def apply_color_theme(theme: object | None = None, app: wx.App | None = None) -> str:
    """Persist *theme* (if given) and restyle every top-level window."""
    value = get_color_theme() if theme is None else set_color_theme(theme)
    enable_app_appearance(app)
    try:
        windows = list(wx.GetTopLevelWindows())
    except Exception:
        windows = []
    for win in windows:
        apply_toplevel_appearance(win)
    return value


def apply_webview_appearance(view: wx.Window) -> None:
    """Make an Edge/WebKit view follow CheckMate's Light/Dark/System setting."""
    if view is None:
        return
    try:
        view.SetBackgroundColour(window_background_colour())
    except Exception:
        pass
    script = _webview_theme_script()
    if not script:
        return
    if not getattr(view, "_checkmate_theme_script_added", False):
        add = getattr(view, "AddUserScript", None)
        if callable(add):
            try:
                add(script)
                view._checkmate_theme_script_added = True
            except TypeError:
                try:
                    add(javascript=script)
                    view._checkmate_theme_script_added = True
                except Exception:
                    pass
            except Exception:
                pass
    run = getattr(view, "RunScript", None)
    if callable(run):
        try:
            with wx.LogNull():
                run(script)
        except Exception:
            pass


def _webview_theme_script() -> str:
    kind = html_report_theme()
    if kind == "auto":
        return (
            "(function(){var h=document.documentElement;if(!h)return;"
            "h.classList.add('checkmate-theme-system');"
            "h.style.colorScheme='light dark';})();"
        )
    scheme = "only light" if kind == "light" else "only dark"
    css = _webview_force_theme_css(kind)
    payload = json.dumps({"kind": kind, "scheme": scheme, "css": css})
    return (
        "(function(){"
        f"var t={payload};"
        "function apply(){"
        "var h=document.documentElement;if(!h)return;"
        "h.classList.add('checkmate-theme-'+t.kind);"
        "h.style.colorScheme=t.scheme;"
        "var metas=document.querySelectorAll('meta[name=\"color-scheme\"]');"
        "for(var i=0;i<metas.length;i++){metas[i].setAttribute('content',t.scheme);}"
        "if(!t.css||!document.head)return;"
        "var s=document.getElementById('checkmate-force-theme');"
        "if(!s){s=document.createElement('style');s.id='checkmate-force-theme';"
        "document.head.appendChild(s);}s.textContent=t.css;"
        "}"
        "apply();"
        "if(document.readyState==='loading'){"
        "document.addEventListener('DOMContentLoaded',apply);}"
        "})();"
    )


def _webview_force_theme_css(kind: str) -> str:
    if kind == "light":
        return """
@media (prefers-color-scheme: dark) {
  html { color-scheme: only light !important; }
  :root {
    --ink: #0f172a !important;
    --muted: #475569 !important;
    --paper: #eef5fb !important;
    --card: #ffffff !important;
    --line: #c9d8e8 !important;
    --line-strong: #8aa0b8 !important;
    --focus: #0f766e !important;
    --focus-ring: #5eead4 !important;
    --link: #0f766e !important;
    --link-visited: #115e59 !important;
    --note-fg: #9a3412 !important;
    --note-bg: #ffedd5 !important;
    --note-border: #fdba74 !important;
    --fix-fg: #14532d !important;
    --fix-border: #86efac !important;
    --chat-user-bg: #dbeafe !important;
    --chat-user-fg: #0f172a !important;
    --chat-user-border: #93c5fd !important;
    --code-bg: #e8f0f8 !important;
    --shadow: 0 1px 2px rgb(15 23 42 / 8%) !important;
    --fatal-fg: #7f1d1d !important;
    --fatal-bg: #fef2f2 !important;
    --error-fg: #991b1b !important;
    --error-bg: #fef2f2 !important;
    --warning-fg: #9a3412 !important;
    --warning-bg: #fff7ed !important;
    --info-fg: #1e3a8a !important;
    --info-bg: #eff6ff !important;
    --usage-fg: #334155 !important;
    --usage-bg: #e8eef5 !important;
  }
  body { background: var(--paper, #eef5fb) !important; color: var(--ink, #0f172a) !important; }
}
"""
    if kind == "dark":
        return """
html { color-scheme: only dark !important; }
:root {
  --ink: #f1f5f9 !important;
  --muted: #94a3b8 !important;
  --paper: #0f172a !important;
  --card: #1e293b !important;
  --line: #334155 !important;
  --line-strong: #64748b !important;
  --focus: #2dd4bf !important;
  --focus-ring: #0f766e !important;
  --link: #5eead4 !important;
  --link-visited: #99f6e4 !important;
  --note-fg: #fed7aa !important;
  --note-bg: #7c2d12 !important;
  --note-border: #c2410c !important;
  --fix-fg: #bbf7d0 !important;
  --fix-border: #166534 !important;
  --chat-user-bg: #1e3a5f !important;
  --chat-user-fg: #eff6ff !important;
  --chat-user-border: #3b82f6 !important;
  --code-bg: #0f172a !important;
  --shadow: 0 1px 3px rgb(0 0 0 / 35%) !important;
  --fatal-fg: #fecaca !important;
  --fatal-bg: #7f1d1d !important;
  --error-fg: #fecaca !important;
  --error-bg: #7f1d1d !important;
  --warning-fg: #fed7aa !important;
  --warning-bg: #7c2d12 !important;
  --info-fg: #bfdbfe !important;
  --info-bg: #1e3a8a !important;
  --usage-fg: #cbd5e1 !important;
  --usage-bg: #334155 !important;
}
body { background: #0f172a !important; color: #e2e8f0 !important; }
"""
    return ""


def apply_toplevel_appearance(win: wx.Window) -> None:
    """Title bar (Windows) plus leftover fills on every platform."""
    if win is None:
        return
    try:
        if not win:
            return
    except RuntimeError:
        return
    dark = prefers_dark()
    if sys.platform == "win32":
        _windows_set_titlebar_dark(win, dark)
        _windows_set_menubar_dark(win, dark)
    if dark:
        only_explicit = (
            wx_system_colours_are_dark() and get_color_theme() == COLOR_THEME_SYSTEM
        )
        _paint_dark_tree(win, only_explicit_light=only_explicit)
    else:
        _clear_forced_colours(win)
        if wx_system_colours_are_dark():
            _paint_light_tree(win)
    try:
        win.Refresh()
    except RuntimeError:
        pass


def apply_window_appearance(win: wx.Window) -> None:
    """Restyle *win* and descendants after Show/Hide (not title bar / menu)."""
    if win is None:
        return
    try:
        if not win:
            return
    except RuntimeError:
        return
    if _skip_dark_paint(win):
        return
    if prefers_dark():
        only_explicit = (
            wx_system_colours_are_dark() and get_color_theme() == COLOR_THEME_SYSTEM
        )
        _paint_dark_tree(win, only_explicit_light=only_explicit)
    else:
        _clear_forced_colours(win)
        if wx_system_colours_are_dark():
            _paint_light_tree(win)
    try:
        win.Refresh()
    except RuntimeError:
        pass


def _patch_progress_dialogs() -> None:
    """Use wx labels we can colour; native Task Dialog text stays black."""
    generic = getattr(wx, "GenericProgressDialog", None)
    if (
        generic is not None
        and wx.ProgressDialog is not generic
        and prefers_dark()
    ):
        wx.ProgressDialog = generic

    seen: set[type] = set()
    for name in ("ProgressDialog", "GenericProgressDialog"):
        cls = getattr(wx, name, None)
        if cls is None or cls in seen or getattr(cls, "_checkmate_appearance_patched", False):
            continue
        seen.add(cls)
        original = cls.__init__

        def _init(self, *args, _original=original, **kwargs):
            _original(self, *args, **kwargs)
            try:
                apply_toplevel_appearance(self)
            except Exception:
                pass
            try:
                wx.CallAfter(apply_toplevel_appearance, self)
            except Exception:
                pass

        cls.__init__ = _init
        cls._checkmate_appearance_patched = True


def _wx_set_system_appearance(app: wx.App) -> None:
    appearance_ns = getattr(wx, "Appearance", None)
    set_fn = getattr(app, "SetAppearance", None)
    theme = get_color_theme()
    if appearance_ns is not None and callable(set_fn):
        attr = {
            COLOR_THEME_LIGHT: "Light",
            COLOR_THEME_DARK: "Dark",
        }.get(theme, "System")
        value = getattr(appearance_ns, attr, None)
        if value is not None:
            try:
                set_fn(value)
                return
            except Exception:
                pass
    msw = getattr(app, "MSWEnableDarkMode", None)
    if callable(msw) and theme != COLOR_THEME_LIGHT:
        flags = getattr(type(app), "DarkMode_Auto", 0)
        if theme == COLOR_THEME_DARK:
            flags = getattr(type(app), "DarkMode_Always", flags)
        try:
            msw(flags)
        except TypeError:
            try:
                msw()
            except Exception:
                pass
        except Exception:
            pass


def _luma(colour: wx.Colour) -> float:
    return (299 * colour.Red() + 587 * colour.Green() + 114 * colour.Blue()) / 1000


def _is_light_fill(colour: wx.Colour | None) -> bool:
    if colour is None or not colour.IsOk():
        return True
    return _luma(colour) >= 160


def _skip_dark_paint(win: wx.Window) -> bool:
    return "webview" in type(win).__name__.lower()


def _is_text_label(win: wx.Window) -> bool:
    return type(win).__name__.lower() in {"statictext", "staticbox"}


# Native Windows dark themes: CFD for edit/combo, Explorer for lists/scrollbars.
_CFD_CLASSES = frozenset(
    {
        "textctrl",
        "combobox",
        "choice",
        "datepickerctrl",
        "spinctrl",
        "spinctrldouble",
    }
)
_BUTTON_CLASSES = frozenset(
    {
        "button",
        "bitmapbutton",
        "togglebutton",
        "commandlinkbutton",
    }
)
_ITEMSVIEW_CLASSES = frozenset(
    {
        "listctrl",
        "dataviewctrl",
        "dataviewlistctrl",
        "treectrl",
    }
)
_EXPLORER_CLASSES = frozenset(
    {
        "listbox",
        "checklistbox",
        "gauge",
        "scrollbar",
    }
) | _BUTTON_CLASSES | _ITEMSVIEW_CLASSES
_OWNER_FILL_CLASSES = _CFD_CLASSES | _EXPLORER_CLASSES


def _windows_dark_theme_for_class(cls: str) -> str | None:
    if cls in _CFD_CLASSES:
        return "DarkMode_CFD"
    if cls in _BUTTON_CLASSES:
        # Explorer + AllowDarkMode paints idle and hot; DarkMode_Explorer can
        # leave a light hover fill under our custom text colour.
        return "Explorer"
    if cls in _ITEMSVIEW_CLASSES:
        # Body uses Explorer; SysHeader32 is themed separately as ItemsView.
        return "DarkMode_Explorer"
    if cls in _EXPLORER_CLASSES:
        return "DarkMode_Explorer"
    return None


def _windows_theme_for_native_class(class_name: str) -> str | None:
    """Dark uxtheme class for a Win32 window class (e.g. SysHeader32)."""
    key = (class_name or "").strip().lower()
    if key == "sysheader32":
        return "DarkMode_ItemsView"
    if key in {"syslistview32", "systreeview32"}:
        return "DarkMode_Explorer"
    if key in {"edit", "combobox", "comboboxex32"}:
        return "DarkMode_CFD"
    if key == "button":
        return "Explorer"
    if key == "listbox":
        return "DarkMode_Explorer"
    return None


def _paint_dark_tree(win: wx.Window, *, only_explicit_light: bool = False) -> None:
    if _skip_dark_paint(win):
        return
    try:
        bg = win.GetBackgroundColour()
    except RuntimeError:
        return
    cls = type(win).__name__.lower()
    is_label = _is_text_label(win)
    if sys.platform == "win32" and cls == "textctrl":
        # Visual styles paint a light hot/focus fill. Owner colours + no theme
        # keep the field dark; we draw the cue banner ourselves in muted light.
        try:
            win.SetThemeEnabled(False)
        except Exception:
            pass
        hwnd = _windows_hwnd(win)
        if hwnd:
            get_hint = getattr(win, "GetHint", None)
            if callable(get_hint):
                try:
                    hint = get_hint() or ""
                    if hint:
                        _edit_hint_text[hwnd] = hint
                except Exception:
                    pass
            _edit_hint_hwnds.add(hwnd)
            _windows_disable_visual_styles(hwnd)
            _windows_ensure_control_subclass(hwnd)
            # Disabled/read-only EDIT sends WM_CTLCOLORSTATIC to the parent,
            # which otherwise returns a white brush (Ask box, Model field).
            parent = win.GetParent()
            phwnd = _windows_hwnd(parent) if parent is not None else 0
            if phwnd:
                _ctlcolor_parent_hwnds.add(phwnd)
                _windows_ensure_control_subclass(phwnd)
    elif sys.platform == "win32" and cls == "notebook":
        # SysTabControl32 has no dark uxtheme class; we owner-paint labels
        # with the control's WM_GETFONT so hover does not change face/size.
        try:
            win.SetThemeEnabled(False)
        except Exception:
            pass
        hwnd = _windows_hwnd(win)
        if hwnd:
            native = _windows_native_class_name(hwnd).lower()
            if native != "systabcontrol32":
                try:
                    child = ctypes.windll.user32.GetWindow(hwnd, 5)
                    while child:
                        ch = int(child)
                        if _windows_native_class_name(ch).lower() == "systabcontrol32":
                            hwnd = ch
                            break
                        child = ctypes.windll.user32.GetWindow(ch, 2)
                except Exception:
                    pass
            _windows_allow_dark_mode_for_hwnd(hwnd, True)
            try:
                ctypes.windll.uxtheme.SetWindowTheme(hwnd, "", "")
            except Exception:
                pass
            _notebook_dark_hwnds.add(hwnd)
            _windows_ensure_control_subclass(hwnd)
            try:
                ctypes.windll.user32.InvalidateRect(hwnd, None, True)
            except Exception:
                pass
    elif sys.platform == "win32" and not is_label:
        _windows_apply_dark_control_theme(win)
        if cls in _ITEMSVIEW_CLASSES:
            _apply_list_header_colours(win, dark=True)
            hwnd = _windows_hwnd(win)
            if hwnd:
                _list_header_dark_hwnds.add(hwnd)
                _windows_ensure_control_subclass(hwnd)
    preserve = bool(getattr(win, "_checkmate_preserve_colours", False))
    if preserve:
        try:
            children = win.GetChildren()
        except RuntimeError:
            return
        for child in children:
            _paint_dark_tree(child, only_explicit_light=only_explicit_light)
        return
    explicit_light = bool(bg is not None and bg.IsOk() and _luma(bg) >= 160)
    already_dark = bool(bg is not None and bg.IsOk() and _luma(bg) < 128)
    should_paint = explicit_light if only_explicit_light else _is_light_fill(bg)
    # Owner-colouring a BUTTON keeps our light text while hover paints a light
    # hot-fill, so the label vanishes. DarkMode Explorer paints idle and hot.
    skip_owner = sys.platform == "win32" and cls in _BUTTON_CLASSES
    if cls == "textctrl":
        try:
            win.SetBackgroundColour(wx.Colour(*_DARK_CTRL))
            win.SetForegroundColour(wx.Colour(*_DARK_FG))
        except RuntimeError:
            return
    elif cls == "notebook":
        try:
            win.SetBackgroundColour(wx.Colour(*_DARK_BG))
            win.SetForegroundColour(wx.Colour(*_DARK_FG))
        except RuntimeError:
            return
    elif (should_paint or (is_label and already_dark)) and not skip_owner:
        if is_label and sys.platform == "win32":
            try:
                win.SetThemeEnabled(False)
            except Exception:
                pass
        fill = _DARK_CTRL if cls in _OWNER_FILL_CLASSES else _DARK_BG
        try:
            if should_paint:
                win.SetBackgroundColour(wx.Colour(*fill))
            win.SetForegroundColour(wx.Colour(*_DARK_FG))
        except RuntimeError:
            return
    try:
        children = win.GetChildren()
    except RuntimeError:
        return
    for child in children:
        _paint_dark_tree(child, only_explicit_light=only_explicit_light)


def _clear_forced_colours(win: wx.Window) -> None:
    if _skip_dark_paint(win):
        return
    try:
        cls = type(win).__name__.lower()
        if sys.platform == "win32" and (
            _is_text_label(win) or cls in {"textctrl", "notebook"}
        ):
            win.SetThemeEnabled(True)
        if cls in _ITEMSVIEW_CLASSES:
            _apply_list_header_colours(win, dark=False)
        hwnd = _windows_hwnd(win) if sys.platform == "win32" else 0
        if hwnd:
            _edit_hint_hwnds.discard(hwnd)
            _edit_hint_text.pop(hwnd, None)
            _list_header_dark_hwnds.discard(hwnd)
            _notebook_dark_hwnds.discard(hwnd)
            _notebook_hot.pop(hwnd, None)
            _ctlcolor_parent_hwnds.discard(hwnd)
            _windows_release_control_subclass(hwnd)
        win.SetBackgroundColour(wx.NullColour)
        win.SetForegroundColour(wx.NullColour)
        if sys.platform == "win32":
            _windows_clear_window_theme(win)
    except RuntimeError:
        return
    try:
        children = win.GetChildren()
    except RuntimeError:
        return
    for child in children:
        _clear_forced_colours(child)


def _paint_light_tree(win: wx.Window) -> None:
    if _skip_dark_paint(win):
        return
    try:
        bg = win.GetBackgroundColour()
    except RuntimeError:
        return
    if bg is not None and bg.IsOk() and _luma(bg) < 128:
        try:
            if _is_text_label(win) or type(win).__name__.lower() in {
                "textctrl",
                "notebook",
            }:
                if sys.platform == "win32":
                    win.SetThemeEnabled(True)
            win.SetBackgroundColour(wx.Colour(*_LIGHT_BG))
            win.SetForegroundColour(wx.Colour(*_LIGHT_FG))
        except RuntimeError:
            return
    try:
        children = win.GetChildren()
    except RuntimeError:
        return
    for child in children:
        _paint_light_tree(child)


def _windows_allow_dark_mode_for_app() -> None:
    if sys.platform != "win32":
        return
    theme = get_color_theme()
    # uxtheme PreferredAppMode: Default=0, AllowDark=1, ForceDark=2, ForceLight=3
    mode = 1
    if theme == COLOR_THEME_DARK:
        mode = 2
    elif theme == COLOR_THEME_LIGHT:
        mode = 3
    try:
        uxtheme = ctypes.WinDLL("uxtheme")
        try:
            set_pref = uxtheme[135]
            set_pref.argtypes = [ctypes.c_int]
            set_pref.restype = ctypes.c_int
            set_pref(mode)
        except Exception:
            pass
        try:
            uxtheme[136]()
        except Exception:
            pass
    except Exception:
        pass


def _windows_set_titlebar_dark(win: wx.Window, enabled: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = int(win.GetHandle() or 0)
    except Exception:
        return
    if not hwnd:
        return
    try:
        value = ctypes.c_int(1 if enabled else 0)
        setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
        setter.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        for attr in (20, 19):
            setter(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def _windows_clear_window_theme(win: wx.Window) -> None:
    if sys.platform != "win32":
        return
    hwnd = _windows_hwnd(win)
    if not hwnd:
        return
    _windows_allow_dark_mode_for_hwnd(hwnd, False)
    try:
        ctypes.windll.uxtheme.SetWindowTheme(hwnd, None, None)
    except Exception:
        pass
    try:
        ctypes.windll.user32.SendMessageW(hwnd, 0x031A, 0, 0)
    except Exception:
        pass


def _windows_hwnd(win: wx.Window) -> int:
    try:
        return int(win.GetHandle() or 0)
    except Exception:
        return 0


def _windows_allow_dark_mode_for_hwnd(hwnd: int, allow: bool = True) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    try:
        fn = ctypes.WinDLL("uxtheme")[133]  # AllowDarkModeForWindow
        fn.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        fn.restype = ctypes.c_bool
        fn(hwnd, allow)
    except Exception:
        pass


def _windows_set_window_theme(hwnd: int, theme: str) -> None:
    if sys.platform != "win32" or not hwnd:
        return
    try:
        ctypes.windll.uxtheme.SetWindowTheme(hwnd, theme, None)
    except Exception:
        pass
    try:
        ctypes.windll.user32.SendMessageW(hwnd, 0x031A, 0, 0)  # WM_THEMECHANGED
    except Exception:
        pass


def _windows_disable_visual_styles(hwnd: int) -> None:
    """Empty theme so uxtheme cannot paint a light EDIT/tab fill over us."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        ctypes.windll.uxtheme.SetWindowTheme(hwnd, "", "")
    except Exception:
        pass


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _COMBOBOXINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("rcItem", _RECT),
        ("rcButton", _RECT),
        ("stateButton", ctypes.c_uint),
        ("hwndCombo", ctypes.c_void_p),
        ("hwndItem", ctypes.c_void_p),
        ("hwndList", ctypes.c_void_p),
    ]


def _windows_theme_combobox_parts(hwnd: int) -> None:
    """Theme the inner EDIT and dropdown list of a ComboBox / wx.Choice."""
    try:
        info = _COMBOBOXINFO()
        info.cbSize = ctypes.sizeof(info)
        ok = ctypes.windll.user32.GetComboBoxInfo(hwnd, ctypes.byref(info))
        if not ok:
            return
        if info.hwndItem:
            child = int(info.hwndItem)
            _windows_allow_dark_mode_for_hwnd(child, True)
            _windows_set_window_theme(child, "DarkMode_CFD")
        if info.hwndList:
            drop = int(info.hwndList)
            _windows_allow_dark_mode_for_hwnd(drop, True)
            _windows_set_window_theme(drop, "DarkMode_Explorer")
    except Exception:
        pass


def _windows_native_class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        n = ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value if n else ""
    except Exception:
        return ""


def _windows_theme_hwnd(hwnd: int, theme: str) -> None:
    _windows_allow_dark_mode_for_hwnd(hwnd, True)
    _windows_set_window_theme(hwnd, theme)


def _windows_theme_list_header(hwnd: int) -> None:
    """SysHeader32 stays light unless it gets DarkMode_ItemsView."""
    try:
        header = int(ctypes.windll.user32.SendMessageW(hwnd, 0x101F, 0, 0) or 0)
    except Exception:
        header = 0
    if header:
        _windows_theme_hwnd(header, "DarkMode_ItemsView")
    native = _windows_native_class_name(hwnd)
    if native.lower() == "sysheader32":
        _windows_theme_hwnd(hwnd, "DarkMode_ItemsView")
    try:
        child = ctypes.windll.user32.GetWindow(hwnd, 5)  # GW_CHILD
        while child:
            ch = int(child)
            if _windows_native_class_name(ch).lower() == "sysheader32":
                _windows_theme_hwnd(ch, "DarkMode_ItemsView")
            child = ctypes.windll.user32.GetWindow(ch, 2)  # GW_HWNDNEXT
    except Exception:
        pass


def _windows_apply_dark_control_theme(win: wx.Window) -> None:
    """Apply the matching Windows dark theme so combos/edits paint before focus."""
    if sys.platform != "win32":
        return
    cls = type(win).__name__.lower()
    if cls == "textctrl":
        return
    theme = _windows_dark_theme_for_class(cls)
    hwnd = _windows_hwnd(win)
    if hwnd:
        native_theme = _windows_theme_for_native_class(_windows_native_class_name(hwnd))
        theme = native_theme or theme
    if theme is None or not hwnd:
        return
    _windows_allow_dark_mode_for_hwnd(hwnd, True)
    try:
        win.SetThemeEnabled(True)
    except Exception:
        pass
    _windows_set_window_theme(hwnd, theme)
    if cls in {"choice", "combobox"}:
        _windows_theme_combobox_parts(hwnd)
    if cls in _ITEMSVIEW_CLASSES or cls in {"listctrl"}:
        _windows_theme_list_header(hwnd)
    try:
        for child in win.GetChildren():
            ch = _windows_hwnd(child)
            if not ch:
                continue
            child_theme = (
                _windows_theme_for_native_class(_windows_native_class_name(ch))
                or theme
            )
            _windows_theme_hwnd(ch, child_theme)
            if child_theme == "DarkMode_Explorer" or type(child).__name__.lower() in _ITEMSVIEW_CLASSES:
                _windows_theme_list_header(ch)
    except Exception:
        pass


_CONTROL_SUBCLASS_ID = 0x434D4354  # CMCT
_WM_PAINT = 0x000F
_WM_ERASEBKGND = 0x0014
_WM_ENABLE = 0x000A
_WM_SHOWWINDOW = 0x0018
_WM_SETFOCUS = 0x0007
_WM_KILLFOCUS = 0x0008
_WM_MOUSEMOVE = 0x0200
_WM_MOUSELEAVE = 0x02A3
_WM_NOTIFY = 0x004E
_WM_CTLCOLOREDIT = 0x0133
_WM_CTLCOLORSTATIC = 0x0138
_NM_CUSTOMDRAW = -12
_CDDS_PREPAINT = 0x00000001
_CDDS_ITEMPREPAINT = 0x00010001
_CDRF_NOTIFYITEMDRAW = 0x00000020
_CDRF_NEWFONT = 0x00000002
_EM_GETRECT = 0x00B2
_EM_GETCUEBANNER = 0x1502
_WM_GETFONT = 0x0031
_DT_LEFT = 0x00000000
_DT_CENTER = 0x00000001
_DT_VCENTER = 0x00000004
_DT_SINGLELINE = 0x00000020
_DT_NOPREFIX = 0x00000800
_DT_EDITCONTROL = 0x00002000
_TCM_GETITEMCOUNT = 0x1304
_TCM_GETITEMRECT = 0x130A
_TCM_GETCURSEL = 0x130B
_TCM_GETITEMW = 0x133C
_TCM_ADJUSTRECT = 0x1328
_TCM_HITTEST = 0x130D
_TCIF_TEXT = 0x0001
_TCS_BOTTOM = 0x0002
_GWL_STYLE = -16
_TME_LEAVE = 0x00000002


class _NMHDR(ctypes.Structure):
    _fields_ = [
        ("hwndFrom", ctypes.c_void_p),
        ("idFrom", ctypes.c_size_t),
        ("code", ctypes.c_uint),
    ]


class _NMCUSTOMDRAW(ctypes.Structure):
    _fields_ = [
        ("hdr", _NMHDR),
        ("dwDrawStage", ctypes.c_uint),
        ("hdc", ctypes.c_void_p),
        ("rc", _RECT),
        ("dwItemSpec", ctypes.c_size_t),
        ("uItemState", ctypes.c_uint),
        ("lItemlParam", ctypes.c_ssize_t),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", ctypes.c_void_p),
        ("fErase", ctypes.c_int),
        ("rcPaint", _RECT),
        ("fRestore", ctypes.c_int),
        ("fIncUpdate", ctypes.c_int),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class _TCITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", ctypes.c_uint),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_ssize_t),
    ]


class _TCHITTESTINFO(ctypes.Structure):
    _fields_ = [
        ("pt", _POINT),
        ("flags", ctypes.c_uint),
    ]


class _TRACKMOUSEEVENT_CLIENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("hwndTrack", ctypes.c_void_p),
        ("dwHoverTime", ctypes.c_uint),
    ]


def _apply_list_header_colours(win: wx.Window, *, dark: bool) -> None:
    setter = getattr(win, "SetHeaderAttr", None)
    if not callable(setter):
        return
    try:
        attr = wx.ItemAttr()
        if dark:
            attr.SetTextColour(wx.Colour(*_DARK_FG))
            attr.SetBackgroundColour(wx.Colour(*_DARK_CTRL))
        setter(attr)
    except Exception:
        pass


def _header_custom_draw_result(list_hwnd: int, lparam: int) -> int | None:
    addr = _lparam_addr(lparam)
    if not addr:
        return None
    hdr = _NMHDR.from_address(addr)
    if ctypes.c_int(hdr.code).value != _NM_CUSTOMDRAW:
        return None
    try:
        header = int(ctypes.windll.user32.SendMessageW(list_hwnd, 0x101F, 0, 0) or 0)
    except Exception:
        header = 0
    if not header or int(hdr.hwndFrom or 0) != header:
        return None
    nmcd = _NMCUSTOMDRAW.from_address(addr)
    stage = int(nmcd.dwDrawStage)
    if stage == _CDDS_PREPAINT:
        return _CDRF_NOTIFYITEMDRAW
    if stage == _CDDS_ITEMPREPAINT and nmcd.hdc:
        ctypes.windll.gdi32.SetTextColor(int(nmcd.hdc), _colorref(_DARK_FG))
        return _CDRF_NEWFONT
    return None


def _edit_draw_hint(hwnd: int) -> None:
    try:
        if int(ctypes.windll.user32.GetWindowTextLengthW(hwnd) or 0) > 0:
            return
        if int(ctypes.windll.user32.GetFocus() or 0) == hwnd:
            return
    except Exception:
        return
    text = _edit_hint_text.get(hwnd, "")
    buf = ctypes.create_unicode_buffer(512)
    try:
        ok = ctypes.windll.user32.SendMessageW(hwnd, _EM_GETCUEBANNER, buf, 512)
        if ok and buf.value:
            text = buf.value
    except Exception:
        pass
    if not text:
        return
    hdc = ctypes.windll.user32.GetDC(hwnd)
    if not hdc:
        return
    hdc = int(hdc)
    font = int(ctypes.windll.user32.SendMessageW(hwnd, _WM_GETFONT, 0, 0) or 0)
    old = ctypes.windll.gdi32.SelectObject(hdc, font) if font else 0
    try:
        rc = _RECT()
        ctypes.windll.user32.SendMessageW(
            hwnd, _EM_GETRECT, 0, ctypes.byref(rc)
        )
        ctypes.windll.gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
        ctypes.windll.gdi32.SetTextColor(hdc, _colorref(_DARK_MUTED))
        flags = (
            _DT_LEFT
            | _DT_SINGLELINE
            | _DT_VCENTER
            | _DT_NOPREFIX
            | _DT_EDITCONTROL
        )
        ctypes.windll.user32.DrawTextW(
            hdc, text, -1, ctypes.byref(rc), flags
        )
    finally:
        if old:
            ctypes.windll.gdi32.SelectObject(hdc, old)
        ctypes.windll.user32.ReleaseDC(hwnd, hdc)


def _lparam_client_pt(lparam) -> tuple[int, int]:
    raw = int(lparam)
    x = ctypes.c_short(raw & 0xFFFF).value
    y = ctypes.c_short((raw >> 16) & 0xFFFF).value
    return x, y


def _edit_ctlcolor_result(child: int, hdc: int) -> int | None:
    if child not in _edit_hint_hwnds or not hdc:
        return None
    enabled = True
    try:
        enabled = bool(ctypes.windll.user32.IsWindowEnabled(child))
    except Exception:
        pass
    fg = _DARK_FG if enabled else _DARK_MUTED
    gdi = ctypes.windll.gdi32
    gdi.SetTextColor(hdc, _colorref(fg))
    gdi.SetBkColor(hdc, _colorref(_DARK_CTRL))
    gdi.SetBkMode(hdc, 2)  # OPAQUE: pair with SetBkColor so white cannot show through
    return _gdi_brush(_DARK_CTRL)


def _notebook_item_label(hwnd: int, index: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    item = _TCITEMW()
    item.mask = _TCIF_TEXT
    item.pszText = ctypes.cast(buf, ctypes.c_void_p)
    item.cchTextMax = 255
    ctypes.windll.user32.SendMessageW(
        hwnd, _TCM_GETITEMW, index, ctypes.byref(item)
    )
    return buf.value


def _notebook_draw(hwnd: int, hdc: int) -> None:
    """Owner-paint tab labels; always use the tab control's WM_GETFONT."""
    user = ctypes.windll.user32
    gdi = ctypes.windll.gdi32
    client = _RECT()
    if not user.GetClientRect(hwnd, ctypes.byref(client)):
        return
    display = _RECT()
    display.left, display.top = client.left, client.top
    display.right, display.bottom = client.right, client.bottom
    user.SendMessageW(hwnd, _TCM_ADJUSTRECT, False, ctypes.byref(display))
    user.FillRect(hdc, ctypes.byref(client), _gdi_brush(_DARK_BG))

    style = 0
    try:
        style = int(user.GetWindowLongW(hwnd, _GWL_STYLE) or 0)
    except Exception:
        pass
    tabs_bottom = bool(style & _TCS_BOTTOM)

    count = int(user.SendMessageW(hwnd, _TCM_GETITEMCOUNT, 0, 0) or 0)
    cur = int(user.SendMessageW(hwnd, _TCM_GETCURSEL, 0, 0) or 0)
    hot = _notebook_hot.get(hwnd, -1)
    font = int(user.SendMessageW(hwnd, _WM_GETFONT, 0, 0) or 0)
    old_font = int(gdi.SelectObject(hdc, font) or 0) if font else 0
    try:
        gdi.SetBkMode(hdc, 1)  # TRANSPARENT
        for i in range(count):
            rc = _RECT()
            if not user.SendMessageW(hwnd, _TCM_GETITEMRECT, i, ctypes.byref(rc)):
                continue
            selected = i == cur
            fill = (58, 58, 58) if selected else (
                (48, 48, 48) if i == hot else _DARK_BG
            )
            user.FillRect(hdc, ctypes.byref(rc), _gdi_brush(fill))
            text = _notebook_item_label(hwnd, i)
            if not text:
                continue
            gdi.SetTextColor(hdc, _colorref(_DARK_FG))
            user.DrawTextW(
                hdc,
                text,
                -1,
                ctypes.byref(rc),
                _DT_CENTER | _DT_VCENTER | _DT_SINGLELINE,
            )
        edge = _RECT()
        edge.left = client.left
        edge.right = client.right
        if tabs_bottom:
            edge.top = display.bottom
            edge.bottom = display.bottom + 1
        else:
            edge.top = max(client.top, display.top - 1)
            edge.bottom = display.top
        user.FillRect(hdc, ctypes.byref(edge), _gdi_brush(_DARK_BG))
    finally:
        if old_font:
            gdi.SelectObject(hdc, old_font)


def _notebook_handle_paint(hwnd: int) -> bool:
    ps = _PAINTSTRUCT()
    hdc_obj = ctypes.windll.user32.BeginPaint(hwnd, ctypes.byref(ps))
    if not hdc_obj:
        return False
    try:
        _notebook_draw(hwnd, int(hdc_obj))
    finally:
        ctypes.windll.user32.EndPaint(hwnd, ctypes.byref(ps))
    return True


def _notebook_track_hot(hwnd: int, lparam) -> None:
    x, y = _lparam_client_pt(lparam)
    info = _TCHITTESTINFO()
    info.pt.x = x
    info.pt.y = y
    index = int(
        ctypes.c_int(
            ctypes.windll.user32.SendMessageW(
                hwnd, _TCM_HITTEST, 0, ctypes.byref(info)
            )
        ).value
    )
    prev = _notebook_hot.get(hwnd, -1)
    if index == prev:
        tme = _TRACKMOUSEEVENT_CLIENT()
        tme.cbSize = ctypes.sizeof(tme)
        tme.dwFlags = _TME_LEAVE
        tme.hwndTrack = hwnd
        try:
            ctypes.windll.user32.TrackMouseEvent(ctypes.byref(tme))
        except Exception:
            pass
        return
    _notebook_hot[hwnd] = index
    ctypes.windll.user32.InvalidateRect(hwnd, None, False)
    tme = _TRACKMOUSEEVENT_CLIENT()
    tme.cbSize = ctypes.sizeof(tme)
    tme.dwFlags = _TME_LEAVE
    tme.hwndTrack = hwnd
    try:
        ctypes.windll.user32.TrackMouseEvent(ctypes.byref(tme))
    except Exception:
        pass


def _control_subclass_callback(hwnd, msg, wparam, lparam, _uid, _ref):
    comctl = _windows_comctl32()
    ih = int(hwnd)
    try:
        if ih in _ctlcolor_parent_hwnds and msg in (
            _WM_CTLCOLOREDIT,
            _WM_CTLCOLORSTATIC,
        ):
            brush = _edit_ctlcolor_result(int(lparam), int(wparam))
            if brush:
                return brush
        if ih in _notebook_dark_hwnds:
            if msg == _WM_ERASEBKGND:
                hdc = int(wparam or 0)
                if hdc:
                    rc = _RECT()
                    ctypes.windll.user32.GetClientRect(ih, ctypes.byref(rc))
                    ctypes.windll.user32.FillRect(
                        hdc, ctypes.byref(rc), _gdi_brush(_DARK_BG)
                    )
                    return 1
            if msg == _WM_PAINT and _notebook_handle_paint(ih):
                return 0
            if msg == _WM_MOUSEMOVE:
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                _notebook_track_hot(ih, lparam)
                return result
            if msg == _WM_MOUSELEAVE:
                if _notebook_hot.pop(ih, -1) != -1:
                    ctypes.windll.user32.InvalidateRect(hwnd, None, False)
                return comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
        if ih in _list_header_dark_hwnds and msg == _WM_NOTIFY:
            drawn = _header_custom_draw_result(ih, lparam)
            if drawn is not None:
                return drawn
        if ih in _edit_hint_hwnds:
            if msg == _WM_PAINT:
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                _edit_draw_hint(ih)
                return result
            if msg == _WM_SHOWWINDOW:
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                if wparam:
                    _windows_disable_visual_styles(ih)
                    ctypes.windll.user32.InvalidateRect(hwnd, None, True)
                return result
            if msg in (_WM_SETFOCUS, _WM_KILLFOCUS, _WM_ENABLE):
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                ctypes.windll.user32.InvalidateRect(hwnd, None, True)
                return result
    except Exception:
        pass
    return comctl.DefSubclassProc(hwnd, msg, wparam, lparam)


_control_subclass_proc = None


def _windows_control_subclass_proc():
    global _control_subclass_proc
    if _control_subclass_proc is None:
        _control_subclass_proc = _SUBCLASSPROC(_control_subclass_callback)
    return _control_subclass_proc


def _windows_ensure_control_subclass(hwnd: int) -> None:
    if not hwnd or hwnd in _control_subclassed:
        return
    try:
        ok = _windows_comctl32().SetWindowSubclass(
            hwnd, _windows_control_subclass_proc(), _CONTROL_SUBCLASS_ID, 0
        )
        if ok:
            _control_subclassed.add(hwnd)
    except Exception:
        pass


def _windows_release_control_subclass(hwnd: int) -> None:
    if not hwnd or hwnd not in _control_subclassed:
        return
    if hwnd in _edit_hint_hwnds or hwnd in _list_header_dark_hwnds:
        return
    if hwnd in _notebook_dark_hwnds or hwnd in _ctlcolor_parent_hwnds:
        return
    try:
        _windows_comctl32().RemoveWindowSubclass(
            hwnd, _windows_control_subclass_proc(), _CONTROL_SUBCLASS_ID
        )
    except Exception:
        pass
    _control_subclassed.discard(hwnd)


def _windows_set_dark_explorer_theme(win: wx.Window) -> None:
    _windows_apply_dark_control_theme(win)


# --- Dark menu bar (Win32 has no uxtheme class for the bar; popups are fine) ---

_WM_UAHDRAWMENU = 0x0091
_WM_UAHDRAWMENUITEM = 0x0092
_WM_NCPAINT = 0x0085
_WM_NCACTIVATE = 0x0086
_WM_NCMOUSEMOVE = 0x00A0
_WM_NCMOUSELEAVE = 0x02A2
_WM_SETTINGCHANGE = 0x001A
_WM_THEMECHANGED = 0x031A
_SPI_GETNONCLIENTMETRICS = 0x0029
_LF_FACESIZE = 32
_TME_LEAVE = 0x00000002
_TME_NONCLIENT = 0x00000010
_MIIM_STATE = 0x00000001
_MF_GRAYED = 0x0001
_MF_DISABLED = 0x0002
_OBJID_MENU = 0xFFFFFFFD
_MIIM_STRING = 0x00000040
_ODS_SELECTED = 0x0001
_ODS_GRAYED = 0x0002
_ODS_DISABLED = 0x0004
_ODS_HOTLIGHT = 0x0040
_ODS_NOACCEL = 0x0100
_DT_CENTER = 0x00000001
_DT_VCENTER = 0x00000004
_DT_SINGLELINE = 0x00000020
_DT_HIDEPREFIX = 0x00100000
_TRANSPARENT = 1
_MENUBAR_SUBCLASS_ID = 0x434D444B  # CMDK

_menubar_dark_enabled = False
_menubar_subclassed: set[int] = set()
_menubar_tracking: set[int] = set()
_menubar_brushes: dict[tuple[int, int, int], int] = {}
_menubar_font = 0
_SUBCLASSPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_void_p,
    ctypes.c_uint,
    ctypes.c_size_t,
    ctypes.c_ssize_t,
    ctypes.c_size_t,
    ctypes.c_size_t,
)
_menubar_subclass_proc = None
_comctl32 = None


def _windows_comctl32():
    global _comctl32
    if _comctl32 is not None:
        return _comctl32
    dll = ctypes.WinDLL("comctl32")
    dll.SetWindowSubclass.argtypes = [
        ctypes.c_void_p,
        _SUBCLASSPROC,
        ctypes.c_size_t,
        ctypes.c_size_t,
    ]
    dll.SetWindowSubclass.restype = ctypes.c_bool
    dll.RemoveWindowSubclass.argtypes = [
        ctypes.c_void_p,
        _SUBCLASSPROC,
        ctypes.c_size_t,
    ]
    dll.RemoveWindowSubclass.restype = ctypes.c_bool
    dll.DefSubclassProc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    dll.DefSubclassProc.restype = ctypes.c_ssize_t
    _comctl32 = dll
    return dll


def _lparam_addr(lparam) -> int:
    value = ctypes.c_void_p(int(lparam)).value
    return int(value) if value else 0


class _DRAWITEMSTRUCT(ctypes.Structure):
    _fields_ = [
        ("CtlType", ctypes.c_uint),
        ("CtlID", ctypes.c_uint),
        ("itemID", ctypes.c_uint),
        ("itemAction", ctypes.c_uint),
        ("itemState", ctypes.c_uint),
        ("hwndItem", ctypes.c_void_p),
        ("hDC", ctypes.c_void_p),
        ("rcItem", _RECT),
        ("itemData", ctypes.c_size_t),
    ]


class _UAHMENU(ctypes.Structure):
    _fields_ = [
        ("hmenu", ctypes.c_void_p),
        ("hdc", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
    ]


class _UAHMENUITEMMETRICS(ctypes.Structure):
    _fields_ = [("data", ctypes.c_uint * 8)]  # union; unused


class _UAHMENUPOPUPMETRICS(ctypes.Structure):
    _fields_ = [
        ("rgcx", ctypes.c_uint * 4),
        ("fUpdateMaxWidths", ctypes.c_uint),
    ]


class _UAHMENUITEM(ctypes.Structure):
    _fields_ = [
        ("iPosition", ctypes.c_int),
        ("umim", _UAHMENUITEMMETRICS),
        ("umpm", _UAHMENUPOPUPMETRICS),
    ]


class _UAHDRAWMENUITEM(ctypes.Structure):
    _fields_ = [
        ("dis", _DRAWITEMSTRUCT),
        ("um", _UAHMENU),
        ("umi", _UAHMENUITEM),
    ]


class _MENUBARINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("rcBar", _RECT),
        ("hMenu", ctypes.c_void_p),
        ("hwndMenu", ctypes.c_void_p),
        ("flags", ctypes.c_uint),
    ]


class _MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("fMask", ctypes.c_uint),
        ("fType", ctypes.c_uint),
        ("fState", ctypes.c_uint),
        ("wID", ctypes.c_uint),
        ("hSubMenu", ctypes.c_void_p),
        ("hbmpChecked", ctypes.c_void_p),
        ("hbmpUnchecked", ctypes.c_void_p),
        ("dwItemData", ctypes.c_size_t),
        ("dwTypeData", ctypes.c_void_p),
        ("cch", ctypes.c_uint),
        ("hbmpItem", ctypes.c_void_p),
    ]


class _LOGFONTW(ctypes.Structure):
    _fields_ = [
        ("lfHeight", ctypes.c_long),
        ("lfWidth", ctypes.c_long),
        ("lfEscapement", ctypes.c_long),
        ("lfOrientation", ctypes.c_long),
        ("lfWeight", ctypes.c_long),
        ("lfItalic", ctypes.c_byte),
        ("lfUnderline", ctypes.c_byte),
        ("lfStrikeOut", ctypes.c_byte),
        ("lfCharSet", ctypes.c_byte),
        ("lfOutPrecision", ctypes.c_byte),
        ("lfClipPrecision", ctypes.c_byte),
        ("lfQuality", ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte),
        ("lfFaceName", ctypes.c_wchar * _LF_FACESIZE),
    ]


class _NONCLIENTMETRICSW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("iBorderWidth", ctypes.c_int),
        ("iScrollWidth", ctypes.c_int),
        ("iScrollHeight", ctypes.c_int),
        ("iCaptionWidth", ctypes.c_int),
        ("iCaptionHeight", ctypes.c_int),
        ("lfCaptionFont", _LOGFONTW),
        ("iSmCaptionWidth", ctypes.c_int),
        ("iSmCaptionHeight", ctypes.c_int),
        ("lfSmCaptionFont", _LOGFONTW),
        ("iMenuWidth", ctypes.c_int),
        ("iMenuHeight", ctypes.c_int),
        ("lfMenuFont", _LOGFONTW),
        ("lfStatusFont", _LOGFONTW),
        ("lfMessageFont", _LOGFONTW),
        ("iPaddedBorderWidth", ctypes.c_int),
    ]


def _colorref(rgb: tuple[int, int, int]) -> int:
    return int(rgb[0]) | (int(rgb[1]) << 8) | (int(rgb[2]) << 16)


def _gdi_brush(rgb: tuple[int, int, int]) -> int:
    brush = _menubar_brushes.get(rgb)
    if brush:
        return brush
    handle = int(ctypes.windll.gdi32.CreateSolidBrush(_colorref(rgb)))
    _menubar_brushes[rgb] = handle
    return handle


def _invalidate_menu_font() -> None:
    global _menubar_font
    if _menubar_font:
        try:
            ctypes.windll.gdi32.DeleteObject(_menubar_font)
        except Exception:
            pass
        _menubar_font = 0


def _menu_font_handle() -> int:
    """Cached HFONT for NONCLIENTMETRICS.lfMenuFont (system menu bar face/size)."""
    global _menubar_font
    if _menubar_font:
        return _menubar_font
    user = ctypes.windll.user32
    gdi = ctypes.windll.gdi32
    padded = ctypes.sizeof(ctypes.c_int)
    full = ctypes.sizeof(_NONCLIENTMETRICSW)
    for cb in (full, full - padded):
        ncm = _NONCLIENTMETRICSW()
        ncm.cbSize = cb
        try:
            ok = user.SystemParametersInfoW(
                _SPI_GETNONCLIENTMETRICS, cb, ctypes.byref(ncm), 0
            )
        except Exception:
            ok = False
        if not ok:
            continue
        try:
            handle = int(gdi.CreateFontIndirectW(ctypes.byref(ncm.lfMenuFont)) or 0)
        except Exception:
            handle = 0
        if handle:
            _menubar_font = handle
            return handle
    return 0


def _select_menu_font(hdc: int) -> int:
    handle = _menu_font_handle()
    if not handle or not hdc:
        return 0
    try:
        return int(ctypes.windll.gdi32.SelectObject(hdc, handle) or 0)
    except Exception:
        return 0


def _restore_gdi_object(hdc: int, old: int) -> None:
    if old and hdc:
        try:
            ctypes.windll.gdi32.SelectObject(hdc, old)
        except Exception:
            pass


def _uah_menu_bar_rect(hwnd: int) -> _RECT | None:
    info = _MENUBARINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetMenuBarInfo(hwnd, _OBJID_MENU, 0, ctypes.byref(info)):
        return None
    return _uah_screen_rect_to_window(hwnd, info.rcBar)


def _uah_screen_rect_to_window(hwnd: int, rc_screen: _RECT) -> _RECT:
    window = _RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(window))
    rc = _RECT()
    rc.left = rc_screen.left - window.left
    rc.right = rc_screen.right - window.left
    rc.top = rc_screen.top - window.top
    rc.bottom = rc_screen.bottom - window.top
    return rc


def _lparam_screen_pt(lparam) -> tuple[int, int]:
    raw = int(lparam)
    x = ctypes.c_short(raw & 0xFFFF).value
    y = ctypes.c_short((raw >> 16) & 0xFFFF).value
    return x, y


class _TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("hwndTrack", ctypes.c_void_p),
        ("dwHoverTime", ctypes.c_uint),
    ]


def _track_nc_mouse_leave(hwnd: int) -> None:
    if hwnd in _menubar_tracking:
        return
    tme = _TRACKMOUSEEVENT()
    tme.cbSize = ctypes.sizeof(tme)
    tme.dwFlags = _TME_LEAVE | _TME_NONCLIENT
    tme.hwndTrack = hwnd
    tme.dwHoverTime = 0
    try:
        if ctypes.windll.user32.TrackMouseEvent(ctypes.byref(tme)):
            _menubar_tracking.add(hwnd)
    except Exception:
        pass


def _uah_menu_item_label(hmenu: int, index: int) -> tuple[str, bool]:
    buf = ctypes.create_unicode_buffer(256)
    mii = _MENUITEMINFOW()
    mii.cbSize = ctypes.sizeof(mii)
    mii.fMask = _MIIM_STRING | _MIIM_STATE
    mii.dwTypeData = ctypes.cast(buf, ctypes.c_void_p)
    mii.cch = 255
    ctypes.windll.user32.GetMenuItemInfoW(hmenu, index, True, ctypes.byref(mii))
    disabled = bool(mii.fState & (_MF_GRAYED | _MF_DISABLED))
    return buf.value, disabled


def _uah_repaint_menubar(hwnd: int, hot_pt: tuple[int, int] | None = None) -> None:
    """Paint the whole bar. Windows' NC hot-track is a light fill we must cover."""
    bar = _uah_menu_bar_rect(hwnd)
    if bar is None:
        return
    hdc_obj = ctypes.windll.user32.GetWindowDC(hwnd)
    if not hdc_obj:
        return
    hdc = int(hdc_obj)
    try:
        ctypes.windll.user32.FillRect(
            hdc, ctypes.byref(bar), _gdi_brush(_DARK_BG)
        )
        hmenu = int(ctypes.windll.user32.GetMenu(hwnd) or 0)
        i = 1
        while hmenu:
            info = _MENUBARINFO()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetMenuBarInfo(
                hwnd, _OBJID_MENU, i, ctypes.byref(info)
            ):
                break
            rc = _uah_screen_rect_to_window(hwnd, info.rcBar)
            hot = bool(info.flags & 2)
            if hot_pt is not None:
                sx, sy = hot_pt
                hot = (
                    info.rcBar.left <= sx < info.rcBar.right
                    and info.rcBar.top <= sy < info.rcBar.bottom
                )
            fill = (58, 58, 58) if hot else _DARK_BG
            ctypes.windll.user32.FillRect(
                hdc, ctypes.byref(rc), _gdi_brush(fill)
            )
            text, disabled = _uah_menu_item_label(hmenu, i - 1)
            if text:
                fg = _DARK_MUTED if disabled else _DARK_FG
                old_font = _select_menu_font(hdc)
                try:
                    ctypes.windll.gdi32.SetBkMode(hdc, _TRANSPARENT)
                    ctypes.windll.gdi32.SetTextColor(hdc, _colorref(fg))
                    ctypes.windll.user32.DrawTextW(
                        hdc,
                        text,
                        -1,
                        ctypes.byref(rc),
                        _DT_CENTER | _DT_SINGLELINE | _DT_VCENTER,
                    )
                finally:
                    _restore_gdi_object(hdc, old_font)
            i += 1
        line = _RECT()
        line.left = bar.left
        line.right = bar.right
        line.top = bar.bottom
        line.bottom = bar.bottom + 1
        ctypes.windll.user32.FillRect(
            hdc, ctypes.byref(line), _gdi_brush(_DARK_BG)
        )
    finally:
        ctypes.windll.user32.ReleaseDC(hwnd, hdc_obj)


def _uah_draw_menu_bar(hwnd: int, lparam: int) -> bool:
    addr = _lparam_addr(lparam)
    if not addr:
        return False
    menu = _UAHMENU.from_address(addr)
    rc = _uah_menu_bar_rect(hwnd)
    if rc is None or not menu.hdc:
        return False
    ctypes.windll.user32.FillRect(int(menu.hdc), ctypes.byref(rc), _gdi_brush(_DARK_BG))
    return True


def _uah_draw_menu_item(lparam: int) -> bool:
    addr = _lparam_addr(lparam)
    if not addr:
        return False
    item = _UAHDRAWMENUITEM.from_address(addr)
    state = int(item.dis.itemState)
    if state & _ODS_SELECTED:
        fill = (64, 64, 64)
    elif state & _ODS_HOTLIGHT:
        fill = (58, 58, 58)
    else:
        fill = _DARK_BG
    hdc = int(item.um.hdc or 0)
    if not hdc:
        return False
    ctypes.windll.user32.FillRect(hdc, ctypes.byref(item.dis.rcItem), _gdi_brush(fill))
    buf = ctypes.create_unicode_buffer(256)
    mii = _MENUITEMINFOW()
    mii.cbSize = ctypes.sizeof(mii)
    mii.fMask = _MIIM_STRING
    mii.dwTypeData = ctypes.cast(buf, ctypes.c_void_p)
    mii.cch = 255
    hmenu = int(item.um.hmenu or 0)
    if hmenu:
        ctypes.windll.user32.GetMenuItemInfoW(
            hmenu, item.umi.iPosition, True, ctypes.byref(mii)
        )
    text = buf.value
    if text:
        disabled = bool(state & (_ODS_GRAYED | _ODS_DISABLED))
        fg = _DARK_MUTED if disabled else _DARK_FG
        gdi = ctypes.windll.gdi32
        old_font = _select_menu_font(hdc)
        try:
            gdi.SetBkMode(hdc, _TRANSPARENT)
            gdi.SetTextColor(hdc, _colorref(fg))
            flags = _DT_CENTER | _DT_SINGLELINE | _DT_VCENTER
            if state & _ODS_NOACCEL:
                flags |= _DT_HIDEPREFIX
            ctypes.windll.user32.DrawTextW(
                hdc, text, -1, ctypes.byref(item.dis.rcItem), flags
            )
        finally:
            _restore_gdi_object(hdc, old_font)
    return True


def _uah_draw_menu_nc_line(hwnd: int) -> None:
    rc = _uah_menu_bar_rect(hwnd)
    if rc is None:
        return
    line = _RECT()
    line.left = rc.left
    line.right = rc.right
    line.top = rc.bottom
    line.bottom = rc.bottom + 1
    hdc = ctypes.windll.user32.GetWindowDC(hwnd)
    if not hdc:
        return
    try:
        ctypes.windll.user32.FillRect(int(hdc), ctypes.byref(line), _gdi_brush(_DARK_BG))
    finally:
        ctypes.windll.user32.ReleaseDC(hwnd, hdc)


def _menubar_subclass_callback(hwnd, msg, wparam, lparam, _uid, _ref):
    comctl = _windows_comctl32()
    try:
        if _menubar_dark_enabled:
            ih = int(hwnd)
            if msg == _WM_UAHDRAWMENU and _uah_draw_menu_bar(ih, lparam):
                return 0
            if msg == _WM_UAHDRAWMENUITEM and _uah_draw_menu_item(lparam):
                return 0
            if msg == _WM_NCMOUSEMOVE:
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                _track_nc_mouse_leave(ih)
                _uah_repaint_menubar(ih, _lparam_screen_pt(lparam))
                return result
            if msg == _WM_NCMOUSELEAVE:
                _menubar_tracking.discard(ih)
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                _uah_repaint_menubar(ih)
                return result
            if msg in (_WM_NCPAINT, _WM_NCACTIVATE):
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                _uah_repaint_menubar(ih)
                return result
            if msg in (_WM_SETTINGCHANGE, _WM_THEMECHANGED):
                _invalidate_menu_font()
                result = comctl.DefSubclassProc(hwnd, msg, wparam, lparam)
                _uah_repaint_menubar(ih)
                return result
    except Exception:
        pass
    return comctl.DefSubclassProc(hwnd, msg, wparam, lparam)


def _windows_menubar_subclass_proc():
    global _menubar_subclass_proc
    if _menubar_subclass_proc is None:
        _menubar_subclass_proc = _SUBCLASSPROC(_menubar_subclass_callback)
    return _menubar_subclass_proc


def _windows_set_menubar_dark(win: wx.Window, enabled: bool) -> None:
    """Paint the frame menu bar dark; popups follow SetPreferredAppMode."""
    global _menubar_dark_enabled
    if sys.platform != "win32":
        return
    hwnd = _windows_hwnd(win)
    if not hwnd:
        return
    _menubar_dark_enabled = bool(enabled)
    proc = _windows_menubar_subclass_proc()
    comctl = _windows_comctl32()
    try:
        if enabled:
            if hwnd not in _menubar_subclassed:
                comctl.SetWindowSubclass(hwnd, proc, _MENUBAR_SUBCLASS_ID, 0)
                _menubar_subclassed.add(hwnd)
        elif hwnd in _menubar_subclassed:
            comctl.RemoveWindowSubclass(hwnd, proc, _MENUBAR_SUBCLASS_ID)
            _menubar_subclassed.discard(hwnd)
        ctypes.windll.user32.DrawMenuBar(hwnd)
        if enabled:
            try:
                ctypes.WinDLL("uxtheme")[136]()
            except Exception:
                pass
            try:
                wx.CallAfter(_uah_repaint_menubar, hwnd)
            except Exception:
                _uah_repaint_menubar(hwnd)
    except Exception:
        pass


class _AppearanceFilter(wx.EventFilter):
    def __init__(self) -> None:
        super().__init__()

    def FilterEvent(self, event):  # noqa: N802 - wx API
        try:
            if event.GetEventType() == wx.wxEVT_SHOW:
                obj = event.GetEventObject()
                shown = True
                is_shown = getattr(event, "IsShown", None)
                if callable(is_shown):
                    shown = bool(is_shown())
                if shown and (
                    isinstance(obj, wx.TopLevelWindow)
                    or type(obj).__name__ in ("ProgressDialog", "GenericProgressDialog")
                ):
                    apply_toplevel_appearance(obj)
                elif shown and isinstance(obj, wx.Window):
                    # Show() of a hidden panel (Explain / Fix follow-up) reattaches
                    # visual styles on child EDITs; re-apply after that happens.
                    apply_window_appearance(obj)
        except Exception:
            pass
        return getattr(self, "Event_Skip", getattr(wx.EventFilter, "Event_Skip", -1))
