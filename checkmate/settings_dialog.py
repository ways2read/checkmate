"""Settings dialog for general preferences and checker rule profiles."""

from __future__ import annotations

import wx

from .fido_settings import fido_settings_present
from .i18n import _
from .settings import (
    COLOR_THEME_LABELS,
    COLOR_THEMES,
    DEFAULT_COLOR_THEME,
    DEFAULT_EPUB_CHECKERS,
    DEFAULT_HTML_CHECKERS,
    DEFAULT_SOUND_SCHEME,
    DEFAULT_VERAPDF_FLAVOUR,
    EPUB_CHECKERS,
    EPUB_CHECKERS_LABELS,
    HTML_CHECKERS,
    HTML_CHECKERS_LABELS,
    SOUND_SCHEME_LABELS,
    SOUND_SCHEMES,
    VERAPDF_FLAVOUR_LABELS,
    VERAPDF_FLAVOURS,
    ai_features_enabled,
    ai_send_kb_article_body,
    color_theme,
    epub_checkers,
    html_checkers,
    html_follow_links,
    mathml_nordic_guidelines,
    show_issues_always,
    single_instance_enabled,
    sound_scheme,
    update_settings,
    verapdf_flavour,
)


class SettingsDialog(wx.Dialog):
    """Edit general prefs and checker profiles; save on OK."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            title=_("Settings"),
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        root = wx.BoxSizer(wx.VERTICAL)

        general = wx.StaticBoxSizer(wx.VERTICAL, self, _("General"))
        self.show_issues_cb = wx.CheckBox(self, label=_("Show issues always"))
        self.show_issues_cb.SetValue(show_issues_always())
        self.show_issues_cb.SetToolTip(
            _(
                "When checked, open the issues list automatically after a check "
                "that finds issues (instead of pressing Show issues)"
            )
        )
        general.Add(self.show_issues_cb, 0, wx.ALL, 6)

        sounds_hint = wx.StaticText(self, label=_("Sounds:"))
        general.Add(sounds_hint, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.sound_choice = wx.Choice(self)
        self._sound_values: list[str] = []
        current_sound = sound_scheme()
        select_sound = 0
        for i, code in enumerate(SOUND_SCHEMES):
            label = _(SOUND_SCHEME_LABELS.get(code, code))
            self.sound_choice.Append(label)
            self._sound_values.append(code)
            if code == current_sound:
                select_sound = i
        self.sound_choice.SetSelection(select_sound)
        self.sound_choice.SetName(_("Sounds"))
        self.sound_choice.SetToolTip(
            _(
                "Play short sounds when a check starts and when it finishes "
                "(different tones for passed and failed). "
                "Choose a scheme or turn sounds off."
            )
        )
        general.Add(self.sound_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        theme_hint = wx.StaticText(self, label=_("Color theme:"))
        general.Add(theme_hint, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.theme_choice = wx.Choice(self)
        self._theme_values: list[str] = []
        current_theme = color_theme()
        select_theme = 0
        for i, code in enumerate(COLOR_THEMES):
            label = _(COLOR_THEME_LABELS.get(code, code))
            self.theme_choice.Append(label)
            self._theme_values.append(code)
            if code == current_theme:
                select_theme = i
        self.theme_choice.SetSelection(select_theme)
        self.theme_choice.SetName(_("Color theme"))
        self.theme_choice.SetToolTip(
            _(
                "System follows your computer's light or dark setting. "
                "Light and Dark keep CheckMate on that theme."
            )
        )
        general.Add(self.theme_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.single_instance_cb = wx.CheckBox(
            self, label=_("Allow only one window")
        )
        self.single_instance_cb.SetValue(single_instance_enabled())
        self.single_instance_cb.SetToolTip(
            _(
                "When opening CheckMate again, focus the existing window "
                "instead of starting another. Files passed to the second "
                "launch open in that window. Helps avoid conflicting edits "
                "on the same publication."
            )
        )
        general.Add(self.single_instance_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.ai_cb = wx.CheckBox(self, label=_("Enable AI features"))
        ai_available = fido_settings_present()
        self.ai_cb.Enable(ai_available)
        self.ai_cb.SetValue(ai_available and ai_features_enabled())
        self.ai_cb.SetToolTip(
            _(
                "Show or hide AI features when FIDO AI is available "
                "(useful for training)"
            )
        )
        general.Add(self.ai_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.kb_body_cb = wx.CheckBox(
            self, label=_("Include Knowledge Base article text in AI prompts")
        )
        self.kb_body_cb.SetValue(bool(ai_send_kb_article_body()))
        self.kb_body_cb.SetToolTip(
            _(
                "When explaining or fixing issues with a DAISY KB article, "
                "send the offline article body to the model. Improves guidance "
                "but uses more tokens. Off by default."
            )
        )
        general.Add(self.kb_body_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        self.ai_cb.Bind(wx.EVT_CHECKBOX, self._on_ai_toggle)
        self._sync_kb_body_enabled()
        root.Add(general, 0, wx.EXPAND | wx.ALL, 12)

        epub_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("EPUB"))
        epub_hint = wx.StaticText(
            self,
            label=_("Checkers used when checking EPUB files:"),
        )
        epub_box.Add(epub_hint, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.epub_choice = wx.Choice(self)
        self._epub_values: list[str] = []
        current_epub = epub_checkers()
        select_epub = 0
        for i, code in enumerate(EPUB_CHECKERS):
            label = _(EPUB_CHECKERS_LABELS.get(code, code))
            self.epub_choice.Append(label)
            self._epub_values.append(code)
            if code == current_epub:
                select_epub = i
        self.epub_choice.SetSelection(select_epub)
        self.epub_choice.SetName(_("EPUB checkers"))
        self.epub_choice.SetToolTip(
            _(
                "EPUBCheck + Ace is the default. Choose EPUBCheck only or Ace "
                "only when you want a single tool. eBraille always uses the "
                "eBraille Checker."
            )
        )
        epub_box.Add(self.epub_choice, 0, wx.EXPAND | wx.ALL, 6)
        root.Add(epub_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        html_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("HTML"))
        html_hint = wx.StaticText(
            self,
            label=_("Checkers used when checking HTML files, folders, or URLs:"),
        )
        html_box.Add(html_hint, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.html_choice = wx.Choice(self)
        self._html_values: list[str] = []
        current_html = html_checkers()
        select_html = 0
        for i, code in enumerate(HTML_CHECKERS):
            label = _(HTML_CHECKERS_LABELS.get(code, code))
            self.html_choice.Append(label)
            self._html_values.append(code)
            if code == current_html:
                select_html = i
        self.html_choice.SetSelection(select_html)
        self.html_choice.SetName(_("HTML checkers"))
        self.html_choice.SetToolTip(
            _(
                "Nu HTML Checker + axe is the default. Choose Nu only or axe "
                "only when you want a single tool."
            )
        )
        html_box.Add(self.html_choice, 0, wx.EXPAND | wx.ALL, 6)
        self.html_follow_links_cb = wx.CheckBox(
            self,
            label=_("Also check linked pages on the same site (up to 25)"),
        )
        self.html_follow_links_cb.SetValue(html_follow_links())
        self.html_follow_links_cb.SetToolTip(
            _(
                "When checked, CheckMate follows same-site links from the "
                "starting page (skipping mailto, files, and other sites). "
                "When unchecked, only the page you opened is checked."
            )
        )
        html_box.Add(self.html_follow_links_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
        root.Add(html_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        math_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("MathML"))
        self.nordic_mathml_cb = wx.CheckBox(
            self,
            label=_("Check against the Nordic MathML Guidelines"),
        )
        self.nordic_mathml_cb.SetValue(mathml_nordic_guidelines())
        self.nordic_mathml_cb.SetToolTip(
            _(
                "Off by default. When on, after the Nu HTML Checker CheckMate "
                "flags MathML that likely breaks the Nordic MathML Guidelines "
                "(hyphen vs minus, missing invisible operators, mfenced, "
                "OCR-like tokens, and similar). Heuristic: some hits are "
                "false positives. Applies to MathML files, clipboard MathML, "
                "and local HTML that contains math."
            )
        )
        math_box.Add(self.nordic_mathml_cb, 0, wx.ALL, 6)
        root.Add(math_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        pdf_box = wx.StaticBoxSizer(wx.VERTICAL, self, _("PDF (veraPDF)"))
        pdf_hint = wx.StaticText(
            self,
            label=_("Validation profile used when checking PDF files:"),
        )
        pdf_box.Add(pdf_hint, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.verapdf_choice = wx.Choice(self)
        self._verapdf_values: list[str] = []
        current_flavour = verapdf_flavour()
        select_flavour = 0
        for i, code in enumerate(VERAPDF_FLAVOURS):
            label = VERAPDF_FLAVOUR_LABELS.get(code, code)
            self.verapdf_choice.Append(label)
            self._verapdf_values.append(code)
            if code == current_flavour:
                select_flavour = i
        self.verapdf_choice.SetSelection(select_flavour)
        self.verapdf_choice.SetName(_("PDF validation profile"))
        self.verapdf_choice.SetToolTip(
            _(
                "PDF/UA-2 is the default (accessibility). PDF/A profiles are "
                "archival conformance. If veraPDF hits an internal error on "
                "UA-2, CheckMate falls back to PDF/UA-1 automatically."
            )
        )
        pdf_box.Add(self.verapdf_choice, 0, wx.EXPAND | wx.ALL, 6)
        root.Add(pdf_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        if buttons is not None:
            root.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(root)
        self.Fit()
        self.CentreOnParent()

    def _on_ai_toggle(self, _event: wx.CommandEvent) -> None:
        self._sync_kb_body_enabled()

    def _sync_kb_body_enabled(self) -> None:
        ai_on = bool(self.ai_cb.IsEnabled() and self.ai_cb.GetValue())
        self.kb_body_cb.Enable(ai_on)

    def selected_color_theme(self) -> str:
        idx = self.theme_choice.GetSelection()
        if 0 <= idx < len(self._theme_values):
            return self._theme_values[idx]
        return DEFAULT_COLOR_THEME

    def selected_sound_scheme(self) -> str:
        idx = self.sound_choice.GetSelection()
        if 0 <= idx < len(self._sound_values):
            return self._sound_values[idx]
        return DEFAULT_SOUND_SCHEME

    def selected_epub_checkers(self) -> str:
        idx = self.epub_choice.GetSelection()
        if 0 <= idx < len(self._epub_values):
            return self._epub_values[idx]
        return DEFAULT_EPUB_CHECKERS

    def selected_html_checkers(self) -> str:
        idx = self.html_choice.GetSelection()
        if 0 <= idx < len(self._html_values):
            return self._html_values[idx]
        return DEFAULT_HTML_CHECKERS

    def selected_verapdf_flavour(self) -> str:
        idx = self.verapdf_choice.GetSelection()
        if 0 <= idx < len(self._verapdf_values):
            return self._verapdf_values[idx]
        return DEFAULT_VERAPDF_FLAVOUR

    def apply(self) -> None:
        """Persist dialog values to settings.json."""
        kwargs: dict = {
            "show_issues_always": bool(self.show_issues_cb.GetValue()),
            "sound_scheme": self.selected_sound_scheme(),
            "ui_color_theme": self.selected_color_theme(),
            "single_instance": bool(self.single_instance_cb.GetValue()),
            "epub_checkers": self.selected_epub_checkers(),
            "html_checkers": self.selected_html_checkers(),
            "html_follow_links": bool(self.html_follow_links_cb.GetValue()),
            "mathml_nordic_guidelines": bool(self.nordic_mathml_cb.GetValue()),
            "verapdf_flavour": self.selected_verapdf_flavour(),
        }
        if fido_settings_present():
            kwargs["ai_features_enabled"] = bool(self.ai_cb.GetValue())
            kwargs["ai_send_kb_article_body"] = bool(self.kb_body_cb.GetValue())
        update_settings(**kwargs)
        from .ui_appearance import apply_color_theme

        apply_color_theme(self.selected_color_theme())
