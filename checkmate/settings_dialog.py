"""Settings dialog for general preferences and checker rule profiles."""

from __future__ import annotations

import wx

from .fido_settings import fido_settings_present
from .i18n import _
from .settings import (
    DEFAULT_VERAPDF_FLAVOUR,
    VERAPDF_FLAVOUR_LABELS,
    VERAPDF_FLAVOURS,
    ai_features_enabled,
    ai_send_kb_article_body,
    show_issues_always,
    sounds_enabled,
    update_settings,
    verapdf_flavour,
)


class SettingsDialog(wx.Dialog):
    """Edit general prefs and PDF validation profile; save on OK."""

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

        self.sounds_cb = wx.CheckBox(self, label=_("Play completion sounds"))
        self.sounds_cb.SetValue(sounds_enabled())
        self.sounds_cb.SetToolTip(
            _(
                "Play a short sound when a check finishes "
                "(different tones for passed and failed)"
            )
        )
        general.Add(self.sounds_cb, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

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
                "PDF/UA-2 is the default. If veraPDF hits an internal error on "
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

    def selected_verapdf_flavour(self) -> str:
        idx = self.verapdf_choice.GetSelection()
        if 0 <= idx < len(self._verapdf_values):
            return self._verapdf_values[idx]
        return DEFAULT_VERAPDF_FLAVOUR

    def apply(self) -> None:
        """Persist dialog values to settings.json."""
        kwargs: dict = {
            "show_issues_always": bool(self.show_issues_cb.GetValue()),
            "sounds_enabled": bool(self.sounds_cb.GetValue()),
            "verapdf_flavour": self.selected_verapdf_flavour(),
        }
        if fido_settings_present():
            kwargs["ai_features_enabled"] = bool(self.ai_cb.GetValue())
            kwargs["ai_send_kb_article_body"] = bool(self.kb_body_cb.GetValue())
        update_settings(**kwargs)
