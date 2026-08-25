"""Native tiled Q&A list for Windows (no second Edge WebView2 host)."""

from __future__ import annotations

import sys

import wx

from ..i18n import _


def _bump_font(ctrl: wx.Window) -> None:
    try:
        font = ctrl.GetFont()
        if font.IsOk():
            font.SetPointSize(font.GetPointSize() + 1)
            ctrl.SetFont(font)
    except RuntimeError:
        pass


def _conversation_point_size() -> int:
    font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
    size = font.GetPointSize() if font.IsOk() else 10
    return max(9, size + 1)


def _hex_colour(value: str) -> wx.Colour:
    raw = (value or "").lstrip("#")
    if len(raw) != 6:
        return wx.NullColour
    try:
        return wx.Colour(int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return wx.NullColour


def _plain_fallback(fragment: str) -> str:
    from .markdown_html import _plain_from_html

    return _plain_from_html(fragment or "")


class _TurnAccessible(wx.Accessible):
    """One named utterance; hide inner StaticText from the a11y tree.

    ``wx.Accessible`` is implemented on Windows; Cocoa accepts the subclass
    but ignores most callbacks. Construction is still safe on macOS.
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    def GetName(self, childId):  # noqa: N802 — wx API
        if childId in (0, getattr(wx, "ACC_SELF", 0)):
            return (wx.ACC_OK, self._name)
        return (wx.ACC_OK, self._name)

    def GetRole(self, childId):  # noqa: N802 — wx API
        return (wx.ACC_OK, wx.ROLE_SYSTEM_TEXT)

    def GetState(self, childId):  # noqa: N802 — wx API
        flags = wx.ACC_STATE_SYSTEM_READONLY | wx.ACC_STATE_SYSTEM_FOCUSABLE
        return (wx.ACC_OK, flags)

    def GetChildCount(self):  # noqa: N802 — wx API
        return (wx.ACC_OK, 0)


class _TurnCard(wx.Panel):
    """Focusable Q&A turn so Tab walks the conversation on Windows."""

    def __init__(self, parent: wx.Window, *, kind: str, name: str) -> None:
        super().__init__(parent, style=wx.BORDER_SIMPLE)
        self.kind = kind
        self._utterance = name
        self.SetName(name)
        self.SetCanFocus(True)
        try:
            self.SetAccessible(_TurnAccessible(name))
        except Exception:
            pass
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def AcceptsFocus(self) -> bool:  # noqa: N802 — wx API
        return True

    def AcceptsFocusFromKeyboard(self) -> bool:  # noqa: N802 — wx API
        return True

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        key = event.GetKeyCode()
        parent = self.GetParent()
        move = getattr(parent, "focus_turn", None)
        if key == wx.WXK_DOWN and callable(move):
            if move(self, 1):
                return
        if key == wx.WXK_UP and callable(move):
            if move(self, -1):
                return
        event.Skip()


class ConversationScroller(wx.ScrolledWindow):
    """Native stacked Q&A cards. A second Edge host paints blank on Windows."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(
            parent,
            style=wx.BORDER_NONE | wx.TAB_TRAVERSAL | wx.VSCROLL,
        )
        self.SetName(_("Conversation"))
        self.SetScrollRate(0, 12)
        self._stack = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._stack)
        self._cards: list[_TurnCard] = []
        self._latest: _TurnCard | None = None
        self._wrap_targets: list[tuple[wx.StaticText, str, str]] = []
        self._html_bodies: list[wx.Window] = []
        self.Bind(wx.EVT_SIZE, self._on_size)

    def SetFocus(self) -> None:  # noqa: N802 — wx API
        target = self._latest or (self._cards[0] if self._cards else None)
        if target is not None:
            target.SetFocus()
            return
        super().SetFocus()

    def focus_turn(self, card: _TurnCard, delta: int) -> bool:
        try:
            idx = self._cards.index(card)
        except ValueError:
            return False
        nxt = idx + delta
        if nxt < 0 or nxt >= len(self._cards):
            return False
        self._cards[nxt].SetFocus()
        self._scroll_card_into_view(self._cards[nxt])
        return True

    def focus_latest(self) -> None:
        card = self._latest or (self._cards[-1] if self._cards else None)
        if card is None:
            return
        try:
            self._scroll_card_into_view(card)
            card.SetFocus()
        except RuntimeError:
            pass

    def scroll_to_latest(self) -> None:
        card = self._latest or (self._cards[-1] if self._cards else None)
        if card is None:
            return
        self._scroll_card_into_view(card)

    def _scroll_card_into_view(self, card: wx.Window) -> None:
        try:
            y = card.GetPosition().y
            _unitx, unity = self.GetScrollPixelsPerUnit()
            if unity:
                self.Scroll(0, max(0, y // unity))
        except Exception:
            pass

    def set_content(
        self,
        turns: list[tuple[str, str, str]] | list[tuple[str, str, str, str]],
        *,
        idle: str = "",
    ) -> None:
        from .markdown_html import conversation_chrome_colors

        colors = conversation_chrome_colors()
        paper = _hex_colour(colors["paper"])
        if paper.IsOk():
            self.SetBackgroundColour(paper)
        # Cocoa asserts or crashes if Freeze() runs on a window that is not
        # yet on screen (both dialogs paint the list from __init__).
        freeze = False
        try:
            freeze = bool(self.IsShownOnScreen())
        except (RuntimeError, AttributeError):
            freeze = False
        if freeze:
            self.Freeze()
        try:
            self._stack.Clear(delete_windows=True)
            self._cards.clear()
            self._wrap_targets.clear()
            self._html_bodies.clear()
            self._latest = None
            if not turns:
                self._add_idle(idle or "", colors)
            else:
                for i, turn in enumerate(turns):
                    kind, label, body = turn[0], turn[1], turn[2]
                    html_body = turn[3] if len(turn) > 3 else ""
                    self._add_turn(
                        kind,
                        label,
                        body,
                        colors,
                        html=html_body,
                        latest=i == len(turns) - 1,
                    )
            self._apply_wrap()
            self.FitInside()
        finally:
            if freeze:
                try:
                    self.Thaw()
                except RuntimeError:
                    pass
        try:
            wx.CallAfter(self._apply_wrap)
        except Exception:
            pass
        self.scroll_to_latest()

    def _add_idle(self, text: str, colors: dict[str, str]) -> None:
        utterance = (text or "").strip()
        card = _TurnCard(self, kind="idle", name=utterance)
        card._label_text = ""
        card._body_text = utterance
        card._chrome = (colors["card"], colors["muted"], colors["line"])
        inner = wx.BoxSizer(wx.VERTICAL)
        body = wx.StaticText(card, label=utterance, style=wx.ALIGN_CENTER)
        _bump_font(body)
        inner.Add(body, 0, wx.EXPAND | wx.ALL, 14)
        card.SetSizer(inner)
        self._paint_card(card, *card._chrome)
        self._wrap_targets.append((body, utterance, "idle"))
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.AddStretchSpacer(1)
        row.Add(card, 6, wx.ALIGN_CENTER | wx.TOP | wx.BOTTOM, 24)
        row.AddStretchSpacer(1)
        self._stack.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        self._cards.append(card)
        self._latest = card

    def _add_turn(
        self,
        kind: str,
        label: str,
        body: str,
        colors: dict[str, str],
        *,
        html: str = "",
        latest: bool,
    ) -> None:
        user = kind == "user"
        utterance = f"{label}: {body}".strip() if label else body
        card = _TurnCard(self, kind=kind, name=utterance)
        card._label_text = label
        card._body_text = body
        if user:
            card._chrome = (
                colors["user_bg"],
                colors["user_fg"],
                colors["user_border"],
            )
        elif kind == "assistant":
            card._chrome = (
                colors["assistant_bg"],
                colors["assistant_fg"],
                colors["assistant_border"],
            )
        else:
            card._chrome = (colors["card"], colors["ink"], colors["line"])
        inner = wx.BoxSizer(wx.VERTICAL)
        if label:
            heading = wx.StaticText(card, label=label)
            font = heading.GetFont()
            font.MakeBold()
            heading.SetFont(font)
            _bump_font(heading)
            inner.Add(heading, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
            self._wrap_targets.append((heading, label, kind))
        use_html = bool(html.strip()) and not user
        if use_html:
            body_ctrl = self._make_html_body(
                card, html, bg_hex=card._chrome[0], fg_hex=card._chrome[1]
            )
            inner.Add(
                body_ctrl,
                0,
                wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM | (0 if label else wx.TOP),
                8,
            )
        else:
            body_ctrl = wx.StaticText(card, label=body)
            _bump_font(body_ctrl)
            inner.Add(
                body_ctrl,
                0,
                wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM | (0 if label else wx.TOP),
                8,
            )
            self._wrap_targets.append((body_ctrl, body, kind))
        card.SetSizer(inner)
        self._paint_card(card, *card._chrome)
        row = wx.BoxSizer(wx.HORIZONTAL)
        if user:
            row.AddStretchSpacer(2)
            row.Add(card, 8, wx.EXPAND | wx.TOP | wx.BOTTOM, 6)
        else:
            row.Add(card, 10, wx.EXPAND | wx.TOP | wx.BOTTOM, 6)
            row.AddStretchSpacer(1)
        self._stack.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self._cards.append(card)
        if latest:
            self._latest = card

    def _make_html_body(
        self,
        parent: wx.Window,
        fragment: str,
        *,
        bg_hex: str,
        fg_hex: str,
    ) -> wx.Window:
        import webbrowser

        import wx.html

        from .markdown_html import conversation_card_page

        page = conversation_card_page(fragment, bg=bg_hex, fg=fg_hex)
        view = wx.html.HtmlWindow(parent, style=wx.html.HW_SCROLLBAR_NEVER)
        view.SetBorders(2)
        try:
            view.SetCanFocus(False)
        except Exception:
            pass
        try:
            view.SetStandardFonts(_conversation_point_size())
        except Exception:
            pass
        bg = _hex_colour(bg_hex)
        if bg.IsOk():
            view.SetBackgroundColour(bg)
        try:
            view.SetPage(page)
        except Exception:
            fallback = wx.StaticText(parent, label=_plain_fallback(fragment))
            _bump_font(fallback)
            return fallback

        def _on_link(event: wx.html.HtmlLinkEvent) -> None:
            href = ""
            try:
                href = (event.GetLinkInfo().GetHref() or "").strip()
            except Exception:
                href = ""
            if href.lower().startswith(("http://", "https://")):
                webbrowser.open(href)

        view.Bind(wx.html.EVT_HTML_LINK_CLICKED, _on_link)
        self._size_html_body(view)
        self._html_bodies.append(view)
        return view

    def _size_html_body(self, view: wx.Window) -> None:
        try:
            ir = view.GetInternalRepresentation()
            if ir is not None:
                height = max(48, int(ir.GetHeight()) + 16)
                view.SetMinSize((-1, height))
        except Exception:
            view.SetMinSize((-1, 80))

    def _paint_card(
        self, card: wx.Window, bg_hex: str, fg_hex: str, _border_hex: str
    ) -> None:
        bg, fg = _hex_colour(bg_hex), _hex_colour(fg_hex)
        if bg.IsOk():
            card.SetBackgroundColour(bg)
        if fg.IsOk():
            card.SetForegroundColour(fg)
        for child in card.GetChildren():
            if bg.IsOk():
                child.SetBackgroundColour(bg)
            if fg.IsOk():
                child.SetForegroundColour(fg)

    def _on_size(self, event: wx.SizeEvent) -> None:
        event.Skip()
        self._apply_wrap()

    def _apply_wrap(self) -> None:
        width = self.GetClientSize().GetWidth()
        if width < 80:
            return
        for ctrl, full, kind in self._wrap_targets:
            try:
                if not ctrl:
                    continue
            except RuntimeError:
                continue
            if kind == "user":
                wrap_at = max(80, int(width * 0.72) - 28)
            elif kind == "idle":
                wrap_at = max(80, int(width * 0.7) - 28)
            else:
                wrap_at = max(80, int(width * 0.86) - 28)
            try:
                ctrl.SetLabel(full)
                ctrl.Wrap(wrap_at)
            except RuntimeError:
                pass
        html_w = max(80, int(width * 0.86) - 40)
        for view in list(self._html_bodies):
            try:
                if not view:
                    continue
                view.SetMinSize((html_w, max(48, view.GetMinSize().GetHeight())))
                self._size_html_body(view)
            except RuntimeError:
                continue
        for card in self._cards:
            chrome = getattr(card, "_chrome", None)
            if chrome:
                self._paint_card(card, *chrome)
        try:
            self.Layout()
            self.FitInside()
        except RuntimeError:
            pass


def dialog_handles_composer_enter(
    event: wx.KeyEvent,
    ctrl: wx.Window | None,
    on_ask,
) -> bool:
    """True if Return was handled as Send (macOS default-button safe).

    Cocoa often delivers Return to the dialog CHAR_HOOK / default button
    instead of a child ``EVT_CHAR_HOOK`` on a multiline ``TextCtrl``.
    Shift+Return is left to the control so it can insert a newline.
    """
    if not callable(on_ask) or ctrl is None:
        return False
    if event.GetKeyCode() not in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
        return False
    if event.ShiftDown():
        return False
    try:
        focus = wx.Window.FindFocus()
    except RuntimeError:
        return False
    if focus is None:
        return False
    try:
        if focus is ctrl:
            on_ask(event)
            return True
        desc = getattr(ctrl, "IsDescendant", None)
        if callable(desc) and desc(focus):
            on_ask(event)
            return True
    except RuntimeError:
        return False
    return False


def make_chat_splitter(parent: wx.Window) -> wx.SplitterWindow:
    style = wx.SP_LIVE_UPDATE
    if sys.platform == "win32":
        style |= wx.SP_3DSASH
    splitter = wx.SplitterWindow(parent, style=style)
    splitter.SetMinimumPaneSize(160)
    splitter.SetSashGravity(1.0)
    return splitter


def current_chat_pane_width(splitter: wx.SplitterWindow) -> int:
    """Pixel width of the right (conversation) pane."""
    from ..settings import DEFAULT_CHAT_PANE_WIDTH, chat_pane_width

    try:
        total = max(splitter.GetClientSize().width, splitter.GetSize().width, 1)
        if splitter.IsSplit():
            return max(160, total - int(splitter.GetSashPosition()))
    except RuntimeError:
        pass
    try:
        return chat_pane_width()
    except Exception:
        return DEFAULT_CHAT_PANE_WIDTH


def sash_for_chat_width(splitter: wx.SplitterWindow, pane_w: int) -> int:
    """Sash position that leaves ``pane_w`` pixels for the conversation pane."""
    try:
        total = max(splitter.GetClientSize().width, splitter.GetSize().width, 0)
    except RuntimeError:
        total = 0
    if total < 400:
        total = 900
    pane = max(160, min(int(pane_w), total - 160))
    return max(160, total - pane)


def restore_chat_sash(splitter: wx.SplitterWindow) -> None:
    """Re-apply the saved conversation width without Unsplit/Hide."""
    from ..settings import chat_pane_width

    try:
        if not splitter.IsSplit():
            return
        splitter.SetSashPosition(sash_for_chat_width(splitter, chat_pane_width()))
    except RuntimeError:
        pass


def remember_chat_pane_width(splitter: wx.SplitterWindow | None) -> None:
    if splitter is None:
        return
    try:
        if not splitter.IsSplit():
            return
        from ..settings import set_chat_pane_width

        set_chat_pane_width(current_chat_pane_width(splitter))
    except Exception:
        pass


def bind_chat_sash_persist(splitter: wx.SplitterWindow) -> None:
    def _on_sash(event: wx.SplitterEvent) -> None:
        event.Skip()
        remember_chat_pane_width(splitter)

    splitter.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, _on_sash)


def set_chat_pane_shown(
    splitter: wx.SplitterWindow,
    report_host: wx.Window,
    chat_host: wx.Window,
    *,
    shown: bool,
    toggle: wx.Button | None = None,
) -> None:
    """Split or unsplit a native (non-Edge) conversation pane."""
    from ..settings import chat_pane_width

    try:
        if shown:
            chat_host.Show(True)
            sash = sash_for_chat_width(splitter, chat_pane_width())
            if not splitter.IsSplit():
                splitter.SplitVertically(report_host, chat_host, sash)
            else:
                splitter.SetSashPosition(sash)
            if toggle is not None:
                toggle.SetLabel(_("Hide chat"))
        else:
            remember_chat_pane_width(splitter)
            if splitter.IsSplit():
                splitter.Unsplit(chat_host)
            chat_host.Hide()
            if toggle is not None:
                toggle.SetLabel(_("Show chat"))
        splitter.UpdateSize()
    except RuntimeError:
        pass
