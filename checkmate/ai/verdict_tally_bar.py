"""Fido-style coloured AI-verdict tally pills for the image-report dialog."""

from __future__ import annotations

import wx

from .fido_image_report import (
    VERDICT_CODES,
    format_verdict_tally_spoken,
    verdict_tally_label,
    verdict_tally_pill_text,
    verdict_tally_short_label,
)

_FILL = {
    "ok": (6, 118, 71),
    "needs_attention": (180, 35, 24),
    "likely_ok_with_caveat": (202, 138, 12),
    "uncertain": (71, 84, 103),
    "unreviewed": (152, 162, 179),
}


class _TallyAccessible(wx.Accessible):
    def __init__(self, win: "VerdictTallyBar") -> None:
        super().__init__(win)
        self._win = win

    def GetName(self, childId):  # noqa: N802
        return (wx.ACC_OK, self._win.GetName())

    def GetRole(self, childId):  # noqa: N802
        return (wx.ACC_OK, wx.ACC_ROLE_STATICTEXT)

    def GetState(self, childId):  # noqa: N802
        flags = wx.ACC_STATE_SYSTEM_READONLY | wx.ACC_STATE_SYSTEM_FOCUSABLE
        try:
            if self._win.FindFocus() is self._win:
                flags |= wx.ACC_STATE_SYSTEM_FOCUSED
        except Exception:
            pass
        return (wx.ACC_OK, flags)

    def GetValue(self, childId):  # noqa: N802
        try:
            return (wx.ACC_OK, self._win.GetName())
        except Exception:
            return (wx.ACC_FALSE, "")


class VerdictTallyBar(wx.Panel):
    """Equal-width coloured pills: Not AI-reviewed, Likely OK, caveat, Needs attention."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, style=wx.BORDER_NONE)
        self._counts = {code: 0 for code in VERDICT_CODES}
        caption = format_verdict_tally_spoken(self._counts)
        self.SetCanFocus(True)
        self.SetName(caption)
        self.SetToolTip(caption)
        try:
            self.SetAccessible(_TallyAccessible(self))
        except Exception:
            pass
        self.SetMinSize((-1, int(self.FromDIP(28))))
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_left_down)
        self.Bind(wx.EVT_SET_FOCUS, self._on_focus_change)
        self.Bind(wx.EVT_KILL_FOCUS, self._on_focus_change)

    def AcceptsFocus(self) -> bool:  # noqa: N802
        return True

    def AcceptsFocusFromKeyboard(self) -> bool:  # noqa: N802
        return True

    def set_counts(self, counts: dict[str, int] | None) -> None:
        data = {code: 0 for code in VERDICT_CODES}
        for code, value in (counts or {}).items():
            if code in data:
                data[code] = int(value or 0)
        self._counts = data
        caption = format_verdict_tally_spoken(data)
        self.SetName(caption)
        self.SetToolTip(caption)
        try:
            self.SetAccessible(_TallyAccessible(self))
        except Exception:
            pass
        self.Refresh()

    set_counts = set_counts

    def _visible_codes(self) -> tuple[str, ...]:
        codes = [
            "unreviewed",
            "ok",
            "likely_ok_with_caveat",
            "needs_attention",
        ]
        if int(self._counts.get("uncertain") or 0) > 0:
            codes.append("uncertain")
        return tuple(codes)

    def _on_size(self, event) -> None:
        event.Skip()
        self.Refresh()

    def _on_left_down(self, event) -> None:
        self.SetFocus()
        event.Skip()

    def _on_focus_change(self, event) -> None:
        event.Skip()
        self.Refresh()

    def _on_paint(self, event) -> None:
        dc = wx.PaintDC(self)
        w, h = self.GetClientSize()
        if w < 8 or h < 8:
            return
        bg = self.GetBackgroundColour()
        dc.SetBackground(wx.Brush(bg))
        dc.Clear()
        codes = self._visible_codes()
        if not codes:
            return
        gap = int(self.FromDIP(4))
        pad = int(self.FromDIP(6))
        inner = max(w - gap * (len(codes) - 1), len(codes))
        pill_w = max(inner // len(codes), 1)
        font = self.GetFont()
        dc.SetFont(font)
        dc.SetTextForeground(wx.WHITE)
        gc = wx.GraphicsContext.Create(dc)
        x = 0
        for i, code in enumerate(codes):
            rgb = _FILL.get(code, (152, 162, 179))
            fill = wx.Colour(*rgb)
            count = int(self._counts.get(code) or 0)
            max_text = max(pill_w - pad * 2, 1)
            caption = verdict_tally_pill_text(
                full=verdict_tally_label(code),
                short=verdict_tally_short_label(code),
                count=count,
                max_width=max_text,
                measure=lambda text, _dc=dc: _dc.GetTextExtent(text)[0],
            )
            if gc is not None:
                gc.SetBrush(gc.CreateBrush(wx.Brush(fill)))
                gc.SetPen(wx.TRANSPARENT_PEN)
                radius = max(3.0, (h - 2) / 2.0)
                gc.DrawRoundedRectangle(x, 1, pill_w, max(h - 2, 1), radius)
            tw, th = dc.GetTextExtent(caption)
            tx = x + max(pad, (pill_w - tw) // 2)
            ty = max(0, (h - th) // 2)
            dc.DrawText(caption, tx, ty)
            x += pill_w + (gap if i < len(codes) - 1 else 0)
        if gc is not None:
            del gc
        if self.FindFocus() is self:
            try:
                wx.RendererNative.Get().DrawFocusRect(self, dc, self.GetClientRect())
            except Exception:
                dc.SetPen(wx.Pen(wx.WHITE, 1, wx.PENSTYLE_DOT))
                dc.SetBrush(wx.TRANSPARENT_BRUSH)
                dc.DrawRectangle(1, 1, max(w - 2, 1), max(h - 2, 1))
