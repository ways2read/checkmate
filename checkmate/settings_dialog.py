"""Settings dialog for general preferences and checker rule profiles."""

from __future__ import annotations

import wx

from .fido_settings import fido_settings_present
from .i18n import _
from .settings import (
    COLOR_THEME_LABELS,
    COLOR_THEMES,
    CODE_EDITORS,
    DEFAULT_COLOR_THEME,
    DEFAULT_EPUB_CHECKERS,
    DEFAULT_HTML_CHECKERS,
    DEFAULT_SOUND_SCHEME,
    DEFAULT_VERAPDF_FLAVOUR,
    EDITOR_NATIVE,
    EDITOR_SCINTILLA,
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
    autosave_edits,
    check_immediately,
    code_editor,
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


def _fill_choice(
    choice: wx.Choice,
    codes: tuple[str, ...],
    labels: dict[str, str],
    current: str,
    *,
    translate: bool = True,
) -> list[str]:
    """Append labelled options and select *current*; return the codes in order."""
    values: list[str] = []
    selected = 0
    for i, code in enumerate(codes):
        label = labels.get(code, code)
        choice.Append(_(label) if translate else label)
        values.append(code)
        if code == current:
            selected = i
    choice.SetSelection(selected)
    return values


class SettingsDialog(wx.Dialog):
    """Edit general prefs, checker profiles, editing, and AI on tabs; save on OK."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            title=_("Settings"),
            style=wx.DEFAULT_DIALOG_STYLE,
        )
        root = wx.BoxSizer(wx.VERTICAL)
        self.book = wx.Notebook(self)
        self.book.SetName(_("Settings"))
        self.book.AddPage(self._build_general_page(self.book), _("General"))
        self.book.AddPage(self._build_checkers_page(self.book), _("Checkers"))
        self.book.AddPage(self._build_editing_page(self.book), _("Editing"))
        self.book.AddPage(self._build_ai_page(self.book), _("AI"))
        root.Add(self.book, 1, wx.EXPAND | wx.ALL, 12)

        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        if buttons is not None:
            root.Add(buttons, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.SetSizer(root)
        self.Fit()
        self.CentreOnParent()

    # --- Pages ---

    def _build_general_page(self, book: wx.Notebook) -> wx.Panel:
        page = wx.Panel(book)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.check_immediately_cb = wx.CheckBox(
            page, label=_("Start checking immediately")
        )
        self.check_immediately_cb.SetValue(check_immediately())
        self.check_immediately_cb.SetToolTip(
            _(
                "When checked, CheckMate checks a file or folder as soon as you "
                "choose, drop, or open it. When unchecked, it is only loaded, so "
                "you can browse and edit its files first; press F5 to check."
            )
        )
        sizer.Add(self.check_immediately_cb, 0, wx.ALL, 8)

        self.show_issues_cb = wx.CheckBox(page, label=_("Show issues always"))
        self.show_issues_cb.SetValue(show_issues_always())
        self.show_issues_cb.SetToolTip(
            _(
                "When checked, open the issues list automatically after a check "
                "that finds issues (instead of pressing Show issues)"
            )
        )
        sizer.Add(self.show_issues_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.single_instance_cb = wx.CheckBox(page, label=_("Allow only one window"))
        self.single_instance_cb.SetValue(single_instance_enabled())
        self.single_instance_cb.SetToolTip(
            _(
                "When opening CheckMate again, focus the existing window "
                "instead of starting another. Files passed to the second "
                "launch open in that window. Helps avoid conflicting edits "
                "on the same publication."
            )
        )
        sizer.Add(self.single_instance_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sizer.Add(wx.StaticText(page, label=_("Sounds:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.sound_choice = wx.Choice(page)
        self._sound_values = _fill_choice(
            self.sound_choice, SOUND_SCHEMES, SOUND_SCHEME_LABELS, sound_scheme()
        )
        self.sound_choice.SetName(_("Sounds"))
        self.sound_choice.SetToolTip(
            _(
                "Play short sounds when a check starts and when it finishes "
                "(different tones for passed and failed). "
                "Choose a scheme or turn sounds off."
            )
        )
        sizer.Add(self.sound_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sizer.Add(
            wx.StaticText(page, label=_("Color theme:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8
        )
        self.theme_choice = wx.Choice(page)
        self._theme_values = _fill_choice(
            self.theme_choice, COLOR_THEMES, COLOR_THEME_LABELS, color_theme()
        )
        self.theme_choice.SetName(_("Color theme"))
        self.theme_choice.SetToolTip(
            _(
                "System follows your computer's light or dark setting. "
                "Light and Dark keep CheckMate on that theme."
            )
        )
        sizer.Add(self.theme_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        page.SetSizer(sizer)
        return page

    def _build_editing_page(self, book: wx.Notebook) -> wx.Panel:
        page = wx.Panel(book)
        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(
            wx.StaticText(page, label=_("Code &editor:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8
        )
        self.code_editor_choice = wx.Choice(page)
        self._code_editor_values = _fill_choice(
            self.code_editor_choice,
            CODE_EDITORS,
            {
                EDITOR_NATIVE: _("Standard text box"),
                EDITOR_SCINTILLA: _("Scintilla, with syntax highlighting"),
            },
            code_editor(),
            translate=False,
        )
        self.code_editor_choice.SetName(_("Code editor"))
        self.code_editor_choice.SetToolTip(
            _(
                "The editor on the Code tab of Browse and edit files. The standard "
                "text box works with all screen readers. Scintilla colours the code "
                "to make it easier to read, but not every screen reader can read it "
                "(Narrator cannot)."
            )
        )
        sizer.Add(self.code_editor_choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.autosave_edits_cb = wx.CheckBox(
            page, label=_("Save edits automatically when leaving a file")
        )
        self.autosave_edits_cb.SetValue(autosave_edits())
        self.autosave_edits_cb.SetToolTip(
            _(
                "In Browse and edit files, save your changes without asking when "
                "you move to another file or close the window. When unchecked, "
                "CheckMate asks whether to save them."
            )
        )
        sizer.Add(self.autosave_edits_cb, 0, wx.ALL, 8)

        page.SetSizer(sizer)
        return page

    def _build_checkers_page(self, book: wx.Notebook) -> wx.Panel:
        page = wx.Panel(book)
        sizer = wx.BoxSizer(wx.VERTICAL)

        epub_box = wx.StaticBoxSizer(wx.VERTICAL, page, _("EPUB"))
        epub_box.Add(
            wx.StaticText(
                epub_box.GetStaticBox(),
                label=_("Checkers used when checking EPUB files:"),
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.TOP,
            6,
        )
        self.epub_choice = wx.Choice(epub_box.GetStaticBox())
        self._epub_values = _fill_choice(
            self.epub_choice, EPUB_CHECKERS, EPUB_CHECKERS_LABELS, epub_checkers()
        )
        self.epub_choice.SetName(_("EPUB checkers"))
        self.epub_choice.SetToolTip(
            _(
                "EPUBCheck + Ace is the default. Choose EPUBCheck only or Ace "
                "only when you want a single tool. eBraille always uses the "
                "eBraille Checker."
            )
        )
        epub_box.Add(self.epub_choice, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(epub_box, 0, wx.EXPAND | wx.ALL, 8)

        html_box = wx.StaticBoxSizer(wx.VERTICAL, page, _("HTML"))
        html_parent = html_box.GetStaticBox()
        html_box.Add(
            wx.StaticText(
                html_parent,
                label=_("Checkers used when checking HTML files, folders, or URLs:"),
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.TOP,
            6,
        )
        self.html_choice = wx.Choice(html_parent)
        self._html_values = _fill_choice(
            self.html_choice, HTML_CHECKERS, HTML_CHECKERS_LABELS, html_checkers()
        )
        self.html_choice.SetName(_("HTML checkers"))
        self.html_choice.SetToolTip(
            _(
                "Nu HTML Checker + axe is the default. Choose Nu only or axe "
                "only when you want a single tool."
            )
        )
        html_box.Add(self.html_choice, 0, wx.EXPAND | wx.ALL, 6)
        self.html_follow_links_cb = wx.CheckBox(
            html_parent,
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
        sizer.Add(html_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        math_box = wx.StaticBoxSizer(wx.VERTICAL, page, _("MathML"))
        self.nordic_mathml_cb = wx.CheckBox(
            math_box.GetStaticBox(),
            label=_("Check against the Nordic MathML Guidelines"),
        )
        self.nordic_mathml_cb.SetValue(mathml_nordic_guidelines())
        self.nordic_mathml_cb.SetToolTip(
            _(
                "Off by default. When on, after the usual checkers CheckMate "
                "flags MathML that likely breaks the Nordic MathML Guidelines "
                "(hyphen vs minus, missing invisible operators, mfenced, "
                "OCR-like tokens, and similar). Heuristic: some hits are "
                "false positives. Applies to MathML files, clipboard MathML, "
                "local HTML and XHTML, and EPUB HTML/XHTML content documents."
            )
        )
        math_box.Add(self.nordic_mathml_cb, 0, wx.ALL, 6)
        sizer.Add(math_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        pdf_box = wx.StaticBoxSizer(wx.VERTICAL, page, _("PDF (veraPDF)"))
        pdf_box.Add(
            wx.StaticText(
                pdf_box.GetStaticBox(),
                label=_("Validation profile used when checking PDF files:"),
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.TOP,
            6,
        )
        self.verapdf_choice = wx.Choice(pdf_box.GetStaticBox())
        self._verapdf_values = _fill_choice(
            self.verapdf_choice,
            VERAPDF_FLAVOURS,
            VERAPDF_FLAVOUR_LABELS,
            verapdf_flavour(),
            translate=False,
        )
        self.verapdf_choice.SetName(_("PDF validation profile"))
        self.verapdf_choice.SetToolTip(
            _(
                "PDF/UA-2 is the default (accessibility). PDF/A profiles are "
                "archival conformance. If veraPDF hits an internal error on "
                "UA-2, CheckMate falls back to PDF/UA-1 automatically."
            )
        )
        pdf_box.Add(self.verapdf_choice, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(pdf_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        page.SetSizer(sizer)
        return page

    def _build_ai_page(self, book: wx.Notebook) -> wx.Panel:
        page = wx.Panel(book)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.ai_cb = wx.CheckBox(page, label=_("Enable AI features"))
        ai_available = fido_settings_present()
        self.ai_cb.Enable(ai_available)
        self.ai_cb.SetValue(ai_available and ai_features_enabled())
        self.ai_cb.SetToolTip(
            _(
                "Show or hide AI features when FIDO AI is available "
                "(useful for training)"
            )
        )
        sizer.Add(self.ai_cb, 0, wx.ALL, 8)

        self.kb_body_cb = wx.CheckBox(
            page, label=_("Include Knowledge Base article text in AI prompts")
        )
        self.kb_body_cb.SetValue(bool(ai_send_kb_article_body()))
        self.kb_body_cb.SetToolTip(
            _(
                "When explaining or fixing issues with a DAISY KB article, "
                "send the offline article body to the model. Improves guidance "
                "but uses more tokens. Off by default."
            )
        )
        sizer.Add(self.kb_body_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.ai_cb.Bind(wx.EVT_CHECKBOX, self._on_ai_toggle)
        self._sync_kb_body_enabled()

        page.SetSizer(sizer)
        return page

    # --- Events / values ---

    def _on_ai_toggle(self, _event: wx.CommandEvent) -> None:
        self._sync_kb_body_enabled()

    def _sync_kb_body_enabled(self) -> None:
        ai_on = bool(self.ai_cb.IsEnabled() and self.ai_cb.GetValue())
        self.kb_body_cb.Enable(ai_on)

    @staticmethod
    def _selected(choice: wx.Choice, values: list[str], default: str) -> str:
        idx = choice.GetSelection()
        if 0 <= idx < len(values):
            return values[idx]
        return default

    def selected_color_theme(self) -> str:
        return self._selected(self.theme_choice, self._theme_values, DEFAULT_COLOR_THEME)

    def selected_sound_scheme(self) -> str:
        return self._selected(self.sound_choice, self._sound_values, DEFAULT_SOUND_SCHEME)

    def selected_epub_checkers(self) -> str:
        return self._selected(self.epub_choice, self._epub_values, DEFAULT_EPUB_CHECKERS)

    def selected_html_checkers(self) -> str:
        return self._selected(self.html_choice, self._html_values, DEFAULT_HTML_CHECKERS)

    def selected_verapdf_flavour(self) -> str:
        return self._selected(
            self.verapdf_choice, self._verapdf_values, DEFAULT_VERAPDF_FLAVOUR
        )

    def apply(self) -> None:
        """Persist dialog values to settings.json."""
        kwargs: dict = {
            "check_immediately": bool(self.check_immediately_cb.GetValue()),
            "autosave_edits": bool(self.autosave_edits_cb.GetValue()),
            "code_editor": self._selected(
                self.code_editor_choice, self._code_editor_values, EDITOR_NATIVE
            ),
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
