"""OS appearance helpers for CheckMate wx chrome."""

from unittest.mock import patch

_WX_APP = None


def _ensure_wx_app() -> None:
    global _WX_APP
    import wx

    try:
        if wx.GetApp() is not None:
            return
    except Exception:
        pass
    _WX_APP = wx.App(False)


def test_prepare_process_appearance_sets_wx_msw_env() -> None:
    import os
    import sys

    from checkmate.ui_appearance import COLOR_THEME_SYSTEM, prepare_process_appearance

    if sys.platform != "win32":
        prepare_process_appearance()
        return
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("wx_msw_dark_mode", None)
        with patch(
            "checkmate.ui_appearance.get_color_theme", return_value=COLOR_THEME_SYSTEM
        ):
            prepare_process_appearance()
        assert os.environ.get("wx_msw_dark_mode") == "1"
        os.environ["wx_msw_dark_mode"] = "0"
        with patch(
            "checkmate.ui_appearance.get_color_theme", return_value=COLOR_THEME_SYSTEM
        ):
            prepare_process_appearance()
        assert os.environ.get("wx_msw_dark_mode") == "0"


def test_normalize_and_html_color_scheme() -> None:
    from checkmate.ui_appearance import (
        COLOR_THEME_DARK,
        COLOR_THEME_LIGHT,
        COLOR_THEME_SYSTEM,
        html_color_scheme,
        html_report_theme,
        normalize_color_theme,
        prefers_dark,
    )

    assert normalize_color_theme("Light") == COLOR_THEME_LIGHT
    assert normalize_color_theme("DARK") == COLOR_THEME_DARK
    assert normalize_color_theme("system") == COLOR_THEME_SYSTEM
    assert normalize_color_theme("auto") == COLOR_THEME_SYSTEM
    assert normalize_color_theme("") == COLOR_THEME_SYSTEM
    assert normalize_color_theme("nope") == COLOR_THEME_SYSTEM
    with patch("checkmate.ui_appearance.get_color_theme", return_value=COLOR_THEME_LIGHT):
        assert prefers_dark() is False
        assert html_color_scheme() == "only light"
        assert html_report_theme() == "light"
    with patch("checkmate.ui_appearance.get_color_theme", return_value=COLOR_THEME_DARK):
        assert prefers_dark() is True
        assert html_color_scheme() == "only dark"
        assert html_report_theme() == "dark"
    with patch(
        "checkmate.ui_appearance.get_color_theme", return_value=COLOR_THEME_SYSTEM
    ):
        assert html_color_scheme() == "light dark"
        assert html_report_theme() == "auto"


def test_wrap_os_dark_css_omits_media_when_light() -> None:
    from checkmate.ui_appearance import wrap_os_dark_css

    inner = "    body { color: red; }"
    with patch("checkmate.ui_appearance.get_color_theme", return_value="light"):
        assert wrap_os_dark_css(inner) == ""
    with patch("checkmate.ui_appearance.get_color_theme", return_value="dark"):
        out = wrap_os_dark_css(inner)
        assert "@media" not in out
        assert "body { color: red; }" in out
    with patch("checkmate.ui_appearance.get_color_theme", return_value="system"):
        out = wrap_os_dark_css(inner)
        assert "@media (prefers-color-scheme: dark)" in out
        assert "body { color: red; }" in out


def test_secondary_text_colour_is_readable() -> None:
    from checkmate.ui_appearance import _luma, secondary_text_colour

    _ensure_wx_app()
    colour = secondary_text_colour()
    assert colour.IsOk()
    luma = _luma(colour)
    assert 40 <= luma <= 220


def test_control_fill_and_primary_text_follow_theme() -> None:
    from checkmate.ui_appearance import (
        COLOR_THEME_DARK,
        COLOR_THEME_LIGHT,
        _luma,
        control_fill_colour,
        primary_text_colour,
    )

    _ensure_wx_app()
    with patch("checkmate.ui_appearance.get_color_theme", return_value=COLOR_THEME_DARK):
        assert _luma(control_fill_colour()) < 128
        assert _luma(primary_text_colour()) >= 160
    with patch("checkmate.ui_appearance.get_color_theme", return_value=COLOR_THEME_LIGHT):
        assert _luma(control_fill_colour()) >= 160
        assert _luma(primary_text_colour()) < 128


def test_windows_dark_theme_for_class() -> None:
    from checkmate.ui_appearance import _windows_dark_theme_for_class

    assert _windows_dark_theme_for_class("choice") == "DarkMode_CFD"
    assert _windows_dark_theme_for_class("combobox") == "DarkMode_CFD"
    assert _windows_dark_theme_for_class("textctrl") == "DarkMode_CFD"
    assert _windows_dark_theme_for_class("button") == "Explorer"
    assert _windows_dark_theme_for_class("listbox") == "DarkMode_Explorer"
    assert _windows_dark_theme_for_class("listctrl") == "DarkMode_Explorer"
    assert _windows_dark_theme_for_class("dataviewctrl") == "DarkMode_Explorer"
    assert _windows_dark_theme_for_class("statictext") is None
    from checkmate.ui_appearance import _windows_theme_for_native_class

    assert _windows_theme_for_native_class("SysHeader32") == "DarkMode_ItemsView"
    assert _windows_theme_for_native_class("Button") == "Explorer"
    assert _windows_theme_for_native_class("SysTabControl32") is None


def test_dark_tree_colours_textctrl() -> None:
    import wx

    from checkmate.ui_appearance import _luma, _paint_dark_tree

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        ctrl = wx.TextCtrl(frame, value="C:\\book.epub")
        ctrl.SetBackgroundColour(wx.Colour(255, 255, 255))
        ctrl.SetForegroundColour(wx.Colour(0, 0, 0))
        _paint_dark_tree(frame, only_explicit_light=False)
        assert _luma(ctrl.GetForegroundColour()) >= 160
        assert _luma(ctrl.GetBackgroundColour()) < 128
    finally:
        frame.Destroy()


def test_apply_window_appearance_recolours_hidden_textctrl() -> None:
    import wx

    from checkmate.ui_appearance import _luma, apply_window_appearance

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        panel = wx.Panel(frame)
        ctrl = wx.TextCtrl(panel, value="")
        ctrl.Enable(False)
        panel.Hide()
        ctrl.SetBackgroundColour(wx.Colour(255, 255, 255))
        ctrl.SetForegroundColour(wx.Colour(0, 0, 0))
        panel.Show()
        with patch("checkmate.ui_appearance.prefers_dark", return_value=True):
            apply_window_appearance(panel)
        assert _luma(ctrl.GetForegroundColour()) >= 160
        assert _luma(ctrl.GetBackgroundColour()) < 128
    finally:
        frame.Destroy()


def test_dark_tree_colours_disabled_textctrl() -> None:
    import sys

    import wx

    from checkmate.ui_appearance import (
        _ctlcolor_parent_hwnds,
        _luma,
        _paint_dark_tree,
        _windows_hwnd,
    )

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        ctrl = wx.TextCtrl(frame, value="")
        ctrl.Enable(False)
        ctrl.SetBackgroundColour(wx.Colour(255, 255, 255))
        ctrl.SetForegroundColour(wx.Colour(0, 0, 0))
        _paint_dark_tree(frame, only_explicit_light=False)
        assert _luma(ctrl.GetForegroundColour()) >= 160
        assert _luma(ctrl.GetBackgroundColour()) < 128
        if sys.platform == "win32":
            assert _windows_hwnd(frame) in _ctlcolor_parent_hwnds
    finally:
        frame.Destroy()


def test_dark_tree_colours_notebook_tabs() -> None:
    import sys

    import wx

    from checkmate.ui_appearance import (
        _luma,
        _notebook_dark_hwnds,
        _paint_dark_tree,
        _windows_hwnd,
    )

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        notebook = wx.Notebook(frame)
        notebook.AddPage(wx.Panel(notebook), "Issue")
        notebook.AddPage(wx.Panel(notebook), "Explain with AI")
        notebook.SetBackgroundColour(wx.Colour(255, 255, 255))
        notebook.SetForegroundColour(wx.Colour(0, 0, 0))
        _paint_dark_tree(frame, only_explicit_light=False)
        assert _luma(notebook.GetForegroundColour()) >= 160
        assert _luma(notebook.GetBackgroundColour()) < 128
        if sys.platform == "win32":
            assert _windows_hwnd(notebook) in _notebook_dark_hwnds
    finally:
        frame.Destroy()


def test_listctrl_header_attr_is_light_on_dark() -> None:
    import wx

    from checkmate.ui_appearance import _DARK_FG, _apply_list_header_colours, _luma

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        listing = wx.ListCtrl(frame, style=wx.LC_REPORT)
        listing.InsertColumn(0, "Severity")
        _apply_list_header_colours(listing, dark=True)
        getter = getattr(listing, "GetHeaderAttr", None)
        if callable(getter):
            attr = getter()
            if attr is not None and attr.HasTextColour():
                assert _luma(attr.GetTextColour()) >= 160
            elif attr is not None:
                assert attr.GetTextColour().Red() == _DARK_FG[0]
    finally:
        frame.Destroy()


def test_dark_tree_skips_preserved_colours() -> None:
    import wx

    from checkmate.ui_appearance import _paint_dark_tree

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        ctrl = wx.TextCtrl(frame, value="Checking…")
        ctrl.SetBackgroundColour(wx.Colour(20, 83, 45))
        ctrl.SetForegroundColour(wx.Colour(134, 239, 172))
        ctrl._checkmate_preserve_colours = True
        _paint_dark_tree(frame, only_explicit_light=False)
        bg = ctrl.GetBackgroundColour()
        fg = ctrl.GetForegroundColour()
        assert (bg.Red(), bg.Green(), bg.Blue()) == (20, 83, 45)
        assert (fg.Red(), fg.Green(), fg.Blue()) == (134, 239, 172)
    finally:
        frame.Destroy()


def test_dark_mode_status_icons_exist() -> None:
    from checkmate.paths import application_dir

    images = application_dir() / "images"
    for name in (
        "checkmate-dark.png",
        "checkmate-down-dark.png",
        "checkmate-wait-dark.png",
        "checkmate-x-dark.png",
    ):
        assert (images / name).is_file(), name


def test_ui_appearance_imports_on_non_windows() -> None:
    """Win32 callback types must not be built at import time."""
    import sys

    from checkmate import ui_appearance as ua

    if sys.platform == "win32":
        assert ua._SUBCLASSPROC is not None
    else:
        assert ua._SUBCLASSPROC is None


def test_clear_forced_colours_is_safe() -> None:
    """macOS cannot Set*Colour(NullColour); startup light theme must not assert."""
    import wx

    from checkmate.ui_appearance import _clear_forced_colours, _paint_dark_tree

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        wx.TextCtrl(frame, value="x")
        wx.StaticText(frame, label="hi")
        wx.Notebook(frame)
        frame.Show()
        _paint_dark_tree(frame, only_explicit_light=False)
        _clear_forced_colours(frame)
    finally:
        frame.Destroy()


def test_menubar_dark_hook_is_safe() -> None:
    import sys

    import wx

    from checkmate.ui_appearance import _windows_set_menubar_dark

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        bar = wx.MenuBar()
        bar.Append(wx.Menu(), "File")
        frame.SetMenuBar(bar)
        frame.Show()
        _windows_set_menubar_dark(frame, True)
        from checkmate.ui_appearance import (
            _menu_font_handle,
            _uah_repaint_menubar,
            _windows_hwnd,
        )

        hwnd = _windows_hwnd(frame)
        if sys.platform == "win32" and hwnd:
            _uah_repaint_menubar(hwnd)
            assert _menu_font_handle() != 0
        _windows_set_menubar_dark(frame, False)
    finally:
        frame.Destroy()


def test_enable_app_appearance_patches_progress_dialog() -> None:
    import wx

    from checkmate.ui_appearance import enable_app_appearance, prefers_dark

    _ensure_wx_app()
    enable_app_appearance()
    assert getattr(wx.ProgressDialog, "_checkmate_appearance_patched", False)
    generic = getattr(wx, "GenericProgressDialog", None)
    if prefers_dark():
        assert generic is not None
        assert wx.ProgressDialog is generic
    elif generic is not None and generic is not wx.ProgressDialog:
        assert getattr(generic, "_checkmate_appearance_patched", False)


def test_dark_tree_sets_light_static_text() -> None:
    import sys

    import wx

    from checkmate.ui_appearance import _luma, _paint_dark_tree

    _ensure_wx_app()
    frame = wx.Frame(None)
    try:
        label = wx.StaticText(frame, label="Working…")
        label.SetBackgroundColour(wx.Colour(32, 32, 32))
        label.SetForegroundColour(wx.Colour(0, 0, 0))
        _paint_dark_tree(frame, only_explicit_light=False)
        fg = label.GetForegroundColour()
        assert fg.IsOk()
        assert _luma(fg) >= 160
        if sys.platform == "win32":
            assert not label.GetThemeEnabled()
    finally:
        frame.Destroy()


def test_luma_and_light_fill() -> None:
    import wx

    from checkmate.ui_appearance import _is_light_fill, _luma

    white = wx.Colour(255, 255, 255)
    black = wx.Colour(0, 0, 0)
    grey = wx.Colour(240, 240, 240)
    dark = wx.Colour(32, 32, 32)
    assert _luma(white) > _luma(black)
    assert _is_light_fill(white)
    assert _is_light_fill(grey)
    assert not _is_light_fill(dark)
    assert _is_light_fill(None)
    assert _is_light_fill(wx.NullColour)


def test_color_theme_persists(tmp_path, monkeypatch) -> None:
    from checkmate import settings as settings_mod
    from checkmate.ui_appearance import (
        COLOR_THEME_DARK,
        get_color_theme,
        set_color_theme,
    )

    monkeypatch.setattr(settings_mod, "app_data_dir", lambda: tmp_path)
    set_color_theme(COLOR_THEME_DARK)
    assert get_color_theme() == COLOR_THEME_DARK
    assert settings_mod.color_theme() == COLOR_THEME_DARK
    data = settings_mod.read_settings()
    assert data.get("ui_color_theme") == COLOR_THEME_DARK
