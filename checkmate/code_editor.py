"""Code editor for the Edit files workspace: the native text box or Scintilla.

Both wrap long lines and report 1-based line/column in characters of the
file's logical lines (never the wrapped screen lines), so issue locations
and Go to line agree whichever editor is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import wx
import wx.stc as stc

from .settings import EDITOR_SCINTILLA

_CSS_SUFFIXES = frozenset({".css"})
_MARKUP_SUFFIXES = frozenset(
    {".xhtml", ".html", ".htm", ".opf", ".ncx", ".xml", ".svg", ".smil", ".mml", ".pls"}
)

# Highlighting colours (foreground, bold) per theme.
_LIGHT = {
    "fg": "#1e1e1e",
    "bg": "#ffffff",
    "caret": "#000000",
    "selection": "#add6ff",
    "margin_fg": "#6e7681",
    "margin_bg": "#f3f3f3",
    "tag": ("#800000", False),
    "attribute": ("#e50000", False),
    "string": ("#0000ff", False),
    "comment": ("#008000", False),
    "entity": ("#a31515", False),
    "declaration": ("#795e26", False),
    "selector": ("#800000", False),
    "property": ("#e50000", False),
    "value": ("#0451a5", False),
    "directive": ("#af00db", False),
    "important": ("#af00db", True),
}
_DARK = {
    "fg": "#d4d4d4",
    "bg": "#1e1e1e",
    "caret": "#ffffff",
    "selection": "#264f78",
    "margin_fg": "#858585",
    "margin_bg": "#252526",
    "tag": ("#569cd6", False),
    "attribute": ("#9cdcfe", False),
    "string": ("#ce9178", False),
    "comment": ("#6a9955", False),
    "entity": ("#d7ba7d", False),
    "declaration": ("#c586c0", False),
    "selector": ("#d7ba7d", False),
    "property": ("#9cdcfe", False),
    "value": ("#ce9178", False),
    "directive": ("#c586c0", False),
    "important": ("#c586c0", True),
}

_MARKUP_STYLES = {
    "tag": (stc.STC_H_TAG, stc.STC_H_TAGUNKNOWN, stc.STC_H_TAGEND),
    "attribute": (stc.STC_H_ATTRIBUTE, stc.STC_H_ATTRIBUTEUNKNOWN),
    "string": (stc.STC_H_DOUBLESTRING, stc.STC_H_SINGLESTRING, stc.STC_H_VALUE),
    "comment": (stc.STC_H_COMMENT, stc.STC_H_XCCOMMENT),
    "entity": (stc.STC_H_ENTITY,),
    "declaration": (stc.STC_H_XMLSTART, stc.STC_H_XMLEND, stc.STC_H_CDATA, stc.STC_H_SGML_DEFAULT),
}
_CSS_STYLES = {
    "selector": (
        stc.STC_CSS_TAG,
        stc.STC_CSS_CLASS,
        stc.STC_CSS_ID,
        stc.STC_CSS_PSEUDOCLASS,
        stc.STC_CSS_UNKNOWN_PSEUDOCLASS,
        stc.STC_CSS_PSEUDOELEMENT,
        stc.STC_CSS_EXTENDED_PSEUDOCLASS,
        stc.STC_CSS_EXTENDED_PSEUDOELEMENT,
        stc.STC_CSS_ATTRIBUTE,
    ),
    # Without keyword lists every property is "unknown"; colour them all alike.
    "property": (
        stc.STC_CSS_IDENTIFIER,
        stc.STC_CSS_IDENTIFIER2,
        stc.STC_CSS_IDENTIFIER3,
        stc.STC_CSS_UNKNOWN_IDENTIFIER,
        stc.STC_CSS_EXTENDED_IDENTIFIER,
    ),
    "value": (stc.STC_CSS_VALUE,),
    "string": (stc.STC_CSS_DOUBLESTRING, stc.STC_CSS_SINGLESTRING),
    "comment": (stc.STC_CSS_COMMENT,),
    "directive": (stc.STC_CSS_DIRECTIVE, stc.STC_CSS_MEDIA),
    "important": (stc.STC_CSS_IMPORTANT,),
}


def _code_font() -> wx.Font:
    return wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE))


def line_column_of(text_before: str) -> tuple[int, int]:
    """1-based line and column just after *text_before* (the text up to the caret)."""
    line = text_before.count("\n") + 1
    return line, len(text_before) - (text_before.rfind("\n") + 1) + 1


def offset_of(text: str, line: int, column: int) -> int:
    """Character offset of 1-based *line*/*column* in *text*, clamped to the text."""
    start = 0
    for _ in range(max(1, line) - 1):
        found = text.find("\n", start)
        if found < 0:
            return len(text)
        start = found + 1
    end = text.find("\n", start)
    if end < 0:
        end = len(text)
    return min(start + max(1, column) - 1, end)


class CodeEditor:
    """Common interface over the two editor controls."""

    ctrl: wx.Window

    def set_text(self, text: str, member: str = "") -> None:
        """Replace the text without change events; clears undo history."""
        raise NotImplementedError

    def get_text(self) -> str:
        raise NotImplementedError

    def replace_all(self, text: str) -> None:
        """Replace the whole text as one edit the user can undo (fires change events)."""
        raise NotImplementedError

    def replace_range(self, start: int, end: int, text: str) -> None:
        """Replace characters *start*..*end* as one undoable edit (character offsets)."""
        raise NotImplementedError

    def select_range(self, start: int, end: int) -> None:
        """Select characters *start*..*end* (character offsets) and scroll to them."""
        raise NotImplementedError

    def set_editable(self, editable: bool) -> None:
        raise NotImplementedError

    def is_editable(self) -> bool:
        raise NotImplementedError

    def line_column(self) -> tuple[int, int]:
        raise NotImplementedError

    def place_caret(self, line: int, column: int) -> None:
        raise NotImplementedError

    def bind_changed(self, handler: Callable[[], None]) -> None:
        raise NotImplementedError

    def bind_caret(self, handler: Callable[[], None]) -> None:
        raise NotImplementedError

    def apply_theme(self) -> None:
        """Re-colour after a theme change (Scintilla only)."""

    def mark_saved(self) -> None:
        """The text now matches the file on disk."""

    def set_name(self, name: str) -> None:
        self.ctrl.SetName(name)


class NativeCodeEditor(CodeEditor):
    """The platform text box (RichEdit on Windows): the most screen-reader friendly."""

    def __init__(self, parent: wx.Window, name: str) -> None:
        self.ctrl = wx.TextCtrl(
            parent, style=wx.TE_MULTILINE | wx.TE_RICH2 | wx.TE_NOHIDESEL, name=name
        )
        font = _code_font()
        if font.IsOk():
            self.ctrl.SetFont(font)
        self.ctrl.SetEditable(False)

    def set_text(self, text: str, member: str = "") -> None:
        self.ctrl.ChangeValue(text)

    def get_text(self) -> str:
        return self.ctrl.GetValue()

    def replace_all(self, text: str) -> None:
        # Replace() records a delete and an insert (two undo steps); one write is one.
        self.ctrl.SetSelection(-1, -1)
        self.ctrl.WriteText(text)

    def _pos(self, text: str, offset: int) -> int:
        if wx.Platform == "__WXMSW__":
            # RichEdit counts UTF-16 units: characters past U+FFFF take two.
            return offset + sum(1 for ch in text[:offset] if ord(ch) > 0xFFFF)
        return offset

    def replace_range(self, start: int, end: int, text: str) -> None:
        current = self.ctrl.GetValue()
        self.ctrl.SetSelection(self._pos(current, start), self._pos(current, end))
        self.ctrl.WriteText(text)

    def select_range(self, start: int, end: int) -> None:
        current = self.ctrl.GetValue()
        first, last = self._pos(current, start), self._pos(current, end)
        self.ctrl.ShowPosition(last)
        self.ctrl.ShowPosition(first)
        self.ctrl.SetSelection(first, last)

    def set_editable(self, editable: bool) -> None:
        self.ctrl.SetEditable(editable)

    def is_editable(self) -> bool:
        return self.ctrl.IsEditable()

    def line_column(self) -> tuple[int, int]:
        # Wrapped lines make PositionToXY count screen lines, so count in the text.
        return line_column_of(self.ctrl.GetRange(0, self.ctrl.GetInsertionPoint()))

    def place_caret(self, line: int, column: int) -> None:
        text = self.ctrl.GetValue()
        pos = self._pos(text, offset_of(text, line, column))
        self.ctrl.SetInsertionPoint(pos)
        self.ctrl.ShowPosition(pos)

    def bind_changed(self, handler: Callable[[], None]) -> None:
        def on_text(event: wx.CommandEvent) -> None:
            event.Skip()
            handler()

        self.ctrl.Bind(wx.EVT_TEXT, on_text)

    def bind_caret(self, handler: Callable[[], None]) -> None:
        def on_event(event: wx.Event) -> None:
            event.Skip()
            wx.CallAfter(handler)

        for evt in (wx.EVT_KEY_UP, wx.EVT_LEFT_UP, wx.EVT_SET_FOCUS):
            self.ctrl.Bind(evt, on_event)


class ScintillaCodeEditor(CodeEditor):
    """Scintilla with syntax highlighting for markup and CSS."""

    def __init__(self, parent: wx.Window, name: str) -> None:
        self.ctrl = stc.StyledTextCtrl(parent, name=name)
        self._setting = False
        self._lexer = stc.STC_LEX_NULL
        c = self.ctrl
        c.SetWrapMode(stc.STC_WRAP_WORD)
        c.SetWrapVisualFlags(stc.STC_WRAPVISUALFLAG_END)
        c.SetEOLMode(stc.STC_EOL_LF)
        c.SetUseTabs(False)
        c.SetTabWidth(2)
        c.SetIndent(2)
        c.SetMarginWidth(1, 0)
        c.SetMarginType(0, stc.STC_MARGIN_NUMBER)
        # Keep Tab for moving between controls, as in the native text box.
        c.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        self.apply_theme()
        c.SetReadOnly(True)

    def _on_key_down(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_TAB and not event.ControlDown() and not event.AltDown():
            self.ctrl.Navigate(
                wx.NavigationKeyEvent.IsBackward
                if event.ShiftDown()
                else wx.NavigationKeyEvent.IsForward
            )
            return
        event.Skip()

    def apply_theme(self) -> None:
        from .ui_appearance import prefers_dark

        palette = _DARK if prefers_dark() else _LIGHT
        c = self.ctrl
        font = _code_font()
        c.StyleResetDefault()
        if font.IsOk():
            c.StyleSetFont(stc.STC_STYLE_DEFAULT, font)
        c.StyleSetForeground(stc.STC_STYLE_DEFAULT, wx.Colour(palette["fg"]))
        c.StyleSetBackground(stc.STC_STYLE_DEFAULT, wx.Colour(palette["bg"]))
        c.StyleClearAll()
        c.StyleSetForeground(stc.STC_STYLE_LINENUMBER, wx.Colour(palette["margin_fg"]))
        c.StyleSetBackground(stc.STC_STYLE_LINENUMBER, wx.Colour(palette["margin_bg"]))
        c.SetCaretForeground(wx.Colour(palette["caret"]))
        c.SetSelBackground(True, wx.Colour(palette["selection"]))
        styles = (
            _CSS_STYLES
            if self._lexer == stc.STC_LEX_CSS
            else _MARKUP_STYLES
            if self._lexer == stc.STC_LEX_XML
            else {}
        )
        for role, ids in styles.items():
            colour, bold = palette[role]
            for style in ids:
                c.StyleSetForeground(style, wx.Colour(colour))
                c.StyleSetBold(style, bold)
        c.SetMarginWidth(0, c.TextWidth(stc.STC_STYLE_LINENUMBER, "_99999"))
        c.Colourise(0, -1)

    def _set_lexer(self, member: str) -> None:
        suffix = Path(member).suffix.lower()
        if suffix in _CSS_SUFFIXES:
            lexer = stc.STC_LEX_CSS
        elif suffix in _MARKUP_SUFFIXES:
            lexer = stc.STC_LEX_XML
        else:
            lexer = stc.STC_LEX_NULL
        if lexer != self._lexer:
            self._lexer = lexer
            self.ctrl.SetLexer(lexer)
            self.apply_theme()

    def set_text(self, text: str, member: str = "") -> None:
        c = self.ctrl
        read_only = c.GetReadOnly()
        self._setting = True
        try:
            self._set_lexer(member)
            c.SetReadOnly(False)
            c.SetText(text)
            c.EmptyUndoBuffer()
            c.SetSavePoint()
        finally:
            c.SetReadOnly(read_only)
            self._setting = False

    def get_text(self) -> str:
        return self.ctrl.GetText()

    def mark_saved(self) -> None:
        self.ctrl.SetSavePoint()

    def replace_all(self, text: str) -> None:
        self._replace_bytes(0, self.ctrl.GetLength(), text)

    def _replace_bytes(self, start: int, end: int, text: str) -> None:
        c = self.ctrl
        c.BeginUndoAction()
        try:
            c.SetTargetStart(start)
            c.SetTargetEnd(end)
            c.ReplaceTarget(text)
        finally:
            c.EndUndoAction()

    @staticmethod
    def _pos(text: str, offset: int) -> int:
        # Scintilla positions are UTF-8 bytes.
        return len(text[:offset].encode("utf-8"))

    def replace_range(self, start: int, end: int, text: str) -> None:
        current = self.ctrl.GetText()
        self._replace_bytes(self._pos(current, start), self._pos(current, end), text)

    def select_range(self, start: int, end: int) -> None:
        current = self.ctrl.GetText()
        self.ctrl.SetSelection(self._pos(current, start), self._pos(current, end))
        self.ctrl.EnsureCaretVisible()

    def set_editable(self, editable: bool) -> None:
        self.ctrl.SetReadOnly(not editable)

    def is_editable(self) -> bool:
        return not self.ctrl.GetReadOnly()

    def line_column(self) -> tuple[int, int]:
        c = self.ctrl
        pos = c.GetCurrentPos()
        line = c.LineFromPosition(pos)
        # Positions are UTF-8 bytes; count characters instead.
        return line + 1, len(c.GetTextRange(c.PositionFromLine(line), pos)) + 1

    def place_caret(self, line: int, column: int) -> None:
        c = self.ctrl
        index = min(max(1, line), c.GetLineCount()) - 1
        start = c.PositionFromLine(index)
        end = c.GetLineEndPosition(index)
        pos = start
        for _ in range(max(1, column) - 1):
            if pos >= end:
                break
            pos = c.PositionAfter(pos)
        c.GotoPos(min(pos, end))
        c.EnsureCaretVisible()

    def bind_changed(self, handler: Callable[[], None]) -> None:
        def on_change(event: wx.Event) -> None:
            event.Skip()
            # Change events can arrive after set_text returns; only real edits
            # since the last load or save count.
            if not self._setting and self.ctrl.GetModify():
                handler()

        self.ctrl.Bind(stc.EVT_STC_CHANGE, on_change)

    def bind_caret(self, handler: Callable[[], None]) -> None:
        def on_update(event: wx.Event) -> None:
            event.Skip()
            handler()

        self.ctrl.Bind(stc.EVT_STC_UPDATEUI, on_update)


def create_code_editor(parent: wx.Window, kind: str, name: str) -> CodeEditor:
    if kind == EDITOR_SCINTILLA:
        return ScintillaCodeEditor(parent, name)
    return NativeCodeEditor(parent, name)
