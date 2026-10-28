"""Edit files workspace: browse a publication's files, edit text, preview, save, and re-check."""

from __future__ import annotations

import html
import logging
import shutil
import sys
import tempfile
import time
import webbrowser
import zipfile
from pathlib import Path

import wx
import wx.dataview as dv

from .i18n import _
from .models import CheckResult
from .workspace_files import (
    READ_ONLY_TOO_LARGE,
    READ_ONLY_UNDECODABLE,
    TextDocument,
    WorkspaceSource,
    decode_text,
    is_preview_member,
    is_rendered_member,
    is_text_member,
    load_text_document,
    member_disk_path,
    read_member_bytes,
    resolve_member,
    revert_member,
    save_member_text,
    source_mtime_ns,
)

logger = logging.getLogger(__name__)

# Native tree per platform for screen readers (mirrors the issues list choice).
_USE_DATAVIEW_TREE = sys.platform != "win32"

_PAGE_TEXT = 0
_PAGE_PREVIEW = 1
# Wait for arrow-key movement in the tree to settle before loading a preview.
_PREVIEW_DELAY_MS = 250
# Source previews of very large text files are cut here.
_MAX_SOURCE_PREVIEW_CHARS = 400_000

_SOURCE_PREVIEW_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ margin: 0; padding: 8px; font: 13px/1.45 Consolas, "Cascadia Mono", Menlo, monospace; }}
pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
</style></head>
<body><pre>{body}</pre></body></html>
"""

# Raster images get a themed page; Edge's own image viewer paints the margins black.
_RASTER_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif"})
_IMAGE_PREVIEW_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
:root {{ color-scheme: light dark; }}
html, body {{ height: 100%; margin: 0; }}
body {{ display: flex; align-items: center; justify-content: center; background: Canvas; }}
img {{ max-width: calc(100% - 16px); max-height: calc(100% - 16px); object-fit: contain; }}
</style></head>
<body><img src="{src}" alt="{title}"></body></html>
"""


def _speak(text: str) -> None:
    try:
        from .accessibility import speak

        speak(text, interrupt=True)
    except Exception:
        pass


def _severity_summary(result: CheckResult) -> str:
    parts = CheckResult._severity_parts(result.fatals, result.errors, result.warnings)
    return ", ".join(parts) if parts else _("no errors or warnings")


class _FileTree:
    """Folder/file tree over the platform-native control."""

    def __init__(self, parent: wx.Window, name: str) -> None:
        self._dataview = _USE_DATAVIEW_TREE
        if self._dataview:
            self.ctrl: wx.Window = dv.DataViewTreeCtrl(
                parent, style=dv.DV_SINGLE | wx.BORDER_SUNKEN
            )
        else:
            self.ctrl = wx.TreeCtrl(
                parent,
                style=wx.TR_HAS_BUTTONS
                | wx.TR_HIDE_ROOT
                | wx.TR_LINES_AT_ROOT
                | wx.TR_SINGLE
                | wx.BORDER_SUNKEN,
            )
        self.ctrl.SetName(name)
        self._member_by_item: dict = {}
        self._item_by_member: dict = {}
        # True while code (not the user) changes the selection.
        self.programmatic = False

    def populate(self, members: tuple[str, ...]) -> None:
        folders: dict[str, object] = {}
        containers: list[object] = []
        self.ctrl.DeleteAllItems()
        if self._dataview:
            root = dv.NullDataViewItem
        else:
            root = self.ctrl.AddRoot(_("Files"))
        self._member_by_item.clear()
        self._item_by_member.clear()
        for member in members:
            parts = member.split("/")
            parent = root
            for depth in range(1, len(parts)):
                key = "/".join(parts[:depth])
                if key not in folders:
                    if self._dataview:
                        folders[key] = self.ctrl.AppendContainer(parent, parts[depth - 1])
                    else:
                        folders[key] = self.ctrl.AppendItem(parent, parts[depth - 1])
                    containers.append(folders[key])
                parent = folders[key]
            item = self.ctrl.AppendItem(parent, parts[-1])
            self._member_by_item[item] = member
            self._item_by_member[member] = item
        for item in containers:
            self.ctrl.Expand(item)

    def selected_member(self) -> str | None:
        item = self.ctrl.GetSelection()
        if not item.IsOk():
            return None
        return self._member_by_item.get(item)

    def select_member(self, member: str) -> None:
        item = self._item_by_member.get(member)
        if item is None:
            return
        self.programmatic = True
        try:
            if self._dataview:
                self.ctrl.Select(item)
            else:
                self.ctrl.SelectItem(item)
            self.ctrl.EnsureVisible(item)
        finally:
            self.programmatic = False

    def focus_first_file(self) -> None:
        if self._item_by_member:
            self.select_member(next(iter(self._item_by_member)))
        self.ctrl.SetFocus()

    def bind_activate(self, handler) -> None:
        if self._dataview:
            self.ctrl.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, handler)
        else:
            self.ctrl.Bind(wx.EVT_TREE_ITEM_ACTIVATED, handler)

    def bind_selection(self, handler) -> None:
        if self._dataview:
            self.ctrl.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, handler)
        else:
            self.ctrl.Bind(wx.EVT_TREE_SEL_CHANGED, handler)


class PublicationWorkspace(wx.Frame):
    """Modeless window owned by the main frame; re-checks run in the main window."""

    def __init__(self, main_frame: wx.Frame, source: WorkspaceSource) -> None:
        super().__init__(main_frame, title=_("Edit files"), style=wx.DEFAULT_FRAME_STYLE)
        from . import main as main_mod

        self._main = main_frame
        self._main_mod = main_mod
        self._source = source
        self._member: str | None = None
        self._doc: TextDocument | None = None
        self._dirty = False
        self._loading = False
        self._disk_mtime: int | None = None
        self._session_backups: dict[str, str] = {}
        self._checking = False
        self._preview_view: wx.Window | None = None
        self._preview_is_webview = False
        self._preview_url = ""
        # Extracted copy of a packaged publication, so relative CSS/images load.
        self._preview_dir: Path | None = None
        # File shown on the Preview tab: the tree selection (or the open file).
        self._preview_member: str | None = None
        self._preview_timer: wx.CallLater | None = None
        # Temporary folder for generated preview pages (never the publication).
        self._preview_pages_dir: Path | None = None
        self._image_page_count = 0
        self._keep_tree_focus_until = 0.0
        self._closing = False
        try:
            self.SetIcons(main_frame.GetIcons())
        except Exception:
            pass

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(panel, label=_("Publication: {path}", path=source.target))
        root.Add(heading, 0, wx.ALL, 8)

        self._splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        self._splitter.SetMinimumPaneSize(160)

        left = wx.Panel(self._splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        left_sizer.Add(wx.StaticText(left, label=_("&Files")), 0, wx.BOTTOM, 4)
        self._tree = _FileTree(left, name=_("Files"))
        self._tree.populate(source.members)
        self._tree.bind_activate(self._on_tree_activated)
        self._tree.bind_selection(self._on_tree_selection)
        self._tree.ctrl.Bind(wx.EVT_CHAR_HOOK, self._on_tree_char_hook)
        left_sizer.Add(self._tree.ctrl, 1, wx.EXPAND)
        left.SetSizer(left_sizer)

        right = wx.Panel(self._splitter)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        self._book = wx.Notebook(right)
        self._book.SetName(_("Text and preview"))

        text_page = wx.Panel(self._book)
        text_sizer = wx.BoxSizer(wx.VERTICAL)
        self._file_label = wx.StaticText(text_page, label=_("&Text (no file open)"))
        text_sizer.Add(self._file_label, 0, wx.ALL, 4)
        self._editor = wx.TextCtrl(
            text_page,
            style=wx.TE_MULTILINE | wx.TE_RICH2 | wx.TE_DONTWRAP | wx.TE_NOHIDESEL,
            name=_("File text"),
        )
        font = wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE))
        if font.IsOk():
            self._editor.SetFont(font)
        self._editor.SetEditable(False)
        self._editor.Bind(wx.EVT_TEXT, self._on_text_changed)
        for evt in (wx.EVT_KEY_UP, wx.EVT_LEFT_UP, wx.EVT_SET_FOCUS):
            self._editor.Bind(evt, self._on_caret_event)
        text_sizer.Add(self._editor, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 4)
        info_row = wx.BoxSizer(wx.HORIZONTAL)
        self._position_label = wx.StaticText(text_page, label="")
        self._notice_label = wx.StaticText(text_page, label="")
        info_row.Add(self._position_label, 0, wx.RIGHT, 16)
        info_row.Add(self._notice_label, 1)
        text_sizer.Add(info_row, 0, wx.EXPAND | wx.ALL, 4)
        text_page.SetSizer(text_sizer)

        preview_page = wx.Panel(self._book)
        preview_sizer = wx.BoxSizer(wx.VERTICAL)
        self._preview_label = wx.StaticText(preview_page, label=_("Preview"))
        preview_sizer.Add(self._preview_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self._preview_notice = wx.StaticText(preview_page, label="")
        preview_sizer.Add(self._preview_notice, 0, wx.EXPAND | wx.ALL, 4)
        self._preview_host = main_mod._AiHtmlHostPanel(preview_page, name=_("Preview"))
        self._preview_host.SetSizer(wx.BoxSizer(wx.VERTICAL))
        main_mod._win_clear_tab_stop(self._preview_host)
        preview_sizer.Add(self._preview_host, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        preview_page.SetSizer(preview_sizer)

        self._book.AddPage(text_page, _("Text"))
        self._book.AddPage(preview_page, _("Preview"))
        self._book.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self._on_page_changed)
        right_sizer.Add(self._book, 1, wx.EXPAND)
        right.SetSizer(right_sizer)

        self._splitter.SplitVertically(left, right, 280)
        root.Add(self._splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self._status = wx.StaticText(panel, label="")
        root.Add(self._status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self._save_btn = wx.Button(panel, label=_("&Save"))
        self._recheck_btn = wx.Button(panel, label=_("Save and &re-check"))
        self._revert_btn = wx.Button(panel, label=_("Re&vert file"))
        self._external_btn = wx.Button(panel, label=_("Open in &default app"))
        close_btn = wx.Button(panel, wx.ID_CLOSE, label=_("&Close"))
        self._save_btn.SetToolTip(_("Save this file (Ctrl+S)"))
        self._recheck_btn.SetToolTip(_("Save, then validate the publication again (F5)"))
        self._revert_btn.SetToolTip(
            _("Restore this file to how it was before this editing session")
        )
        self._external_btn.SetToolTip(_("Open this file in its default application"))
        for btn in (self._save_btn, self._recheck_btn, self._revert_btn, self._external_btn):
            btns.Add(btn, 0, wx.RIGHT, 8)
        btns.AddStretchSpacer(1)
        btns.Add(close_btn, 0)
        root.Add(btns, 0, wx.EXPAND | wx.ALL, 8)

        self._save_btn.Bind(wx.EVT_BUTTON, lambda _e: self.save())
        self._recheck_btn.Bind(wx.EVT_BUTTON, lambda _e: self.save_and_recheck())
        self._revert_btn.Bind(wx.EVT_BUTTON, lambda _e: self.revert_file())
        self._external_btn.Bind(wx.EVT_BUTTON, lambda _e: self.open_external())
        close_btn.Bind(wx.EVT_BUTTON, lambda _e: self.Close())
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_ACTIVATE, self._on_activate)

        actions = {
            "save": self.save,
            "recheck": self.save_and_recheck,
            "goto": self.go_to_line,
            "close": self.Close,
            "pane": self._toggle_pane,
            "prev_page": lambda: self._switch_page(_PAGE_TEXT),
            "next_page": lambda: self._switch_page(_PAGE_PREVIEW),
        }
        ids = {name: wx.NewIdRef() for name in actions}
        for name, action in actions.items():
            self.Bind(wx.EVT_MENU, lambda _e, fn=action: fn(), id=ids[name])
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [
                    (wx.ACCEL_CTRL, ord("S"), ids["save"]),
                    (wx.ACCEL_NORMAL, wx.WXK_F5, ids["recheck"]),
                    (wx.ACCEL_CTRL, ord("G"), ids["goto"]),
                    (wx.ACCEL_CTRL, ord("W"), ids["close"]),
                    (wx.ACCEL_NORMAL, wx.WXK_F6, ids["pane"]),
                    (wx.ACCEL_CTRL, wx.WXK_PAGEUP, ids["prev_page"]),
                    (wx.ACCEL_CTRL, wx.WXK_PAGEDOWN, ids["next_page"]),
                ]
            )
        )

        panel.SetSizer(root)
        self._update_title()
        self._update_buttons()
        self._fit_to_screen()

    def _fit_to_screen(self) -> None:
        """Size to the work area of the main window's display, never past its edges."""
        index = wx.Display.GetFromWindow(self._main)
        display = wx.Display(index if index != wx.NOT_FOUND else 0)
        area = display.GetClientArea()
        want = self.FromDIP(wx.Size(1040, 720))
        width = min(want.width, int(area.width * 0.92))
        height = min(want.height, int(area.height * 0.88))
        self.SetMinSize(wx.Size(min(self.FromDIP(560), width), min(self.FromDIP(380), height)))
        self.SetSize(wx.Size(width, height))
        self._splitter.SetSashPosition(min(self.FromDIP(280), width // 3))
        self.CentreOnParent()
        x, y = self.GetPosition()
        x = max(area.x, min(x, area.x + area.width - width))
        y = max(area.y, min(y, area.y + area.height - height))
        self.SetPosition(wx.Point(x, y))

    # --- Public API used by the main window ---

    @property
    def target(self) -> Path:
        return self._source.target

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    def focus_files(self) -> None:
        if self._member is None:
            self._tree.focus_first_file()
            self._schedule_preview()
            return
        self._tree.select_member(self._member)
        self._tree.ctrl.SetFocus()

    def open_location(self, member: str | None, line: int | None, column: int | None) -> bool:
        """Open the member a checker reported, with the caret at line/column."""
        if not member:
            self._set_status(_("This issue does not point to a file."))
            self.focus_files()
            return False
        resolved = resolve_member(self._source, member)
        if resolved is None:
            self._set_status(_("Could not find {file} in this publication.", file=member))
            self.focus_files()
            return False
        self._switch_page(_PAGE_TEXT, focus=False)
        return self.open_member(resolved, line=line, column=column)

    def open_member(
        self, member: str, *, line: int | None = None, column: int | None = None
    ) -> bool:
        """Open a text file for editing. Media and other non-text files stay preview-only."""
        self._cancel_scheduled_preview()
        if not is_text_member(member):
            self._preview_member = member
            self._tree.select_member(member)
            self._switch_page(_PAGE_PREVIEW, focus=False)
            self._set_status(
                _("{file} is not a text file, so it can only be previewed.", file=member)
            )
            return False
        if member != self._member:
            if not self.confirm_unsaved():
                return False
            if not self._load(member):
                return False
        self._preview_member = member
        self._tree.select_member(member)
        self._switch_page(_PAGE_TEXT, focus=False)
        self._place_caret(line, column)
        self._editor.SetFocus()
        return True

    def confirm_unsaved(self) -> bool:
        """Ask Save / Don't save / Cancel when there are unsaved edits. True = proceed."""
        if not self._dirty:
            return True
        dlg = wx.MessageDialog(
            self,
            _("Save changes to {file}?", file=self._member or ""),
            _("Edit files"),
            wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
        )
        try:
            dlg.SetYesNoCancelLabels(_("&Save"), _("Do&n't save"), _("Cancel"))
        except Exception:
            pass
        try:
            answer = dlg.ShowModal()
        finally:
            dlg.Destroy()
        if answer == wx.ID_YES:
            return self.save()
        if answer == wx.ID_NO:
            self._dirty = False
            self._update_title()
            return True
        return False

    def on_recheck_finished(
        self, before: CheckResult | None, result: CheckResult
    ) -> None:
        """Called by the main window when a re-check this window asked for ends."""
        self._checking = False
        self._update_buttons()
        lines = [_("Re-check finished. {headline}.", headline=result.headline)]
        if before is not None:
            lines.append(_("Before: {summary}.", summary=_severity_summary(before)))
        if self._member:
            count = self._issue_count_for_member(result, self._member)
            lines.append(_("Issues in this file: {n}.", n=count))
        self._set_status(" ".join(lines))

    # --- Loading / caret ---

    def _load(self, member: str) -> bool:
        try:
            doc = load_text_document(self._source, member)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            wx.MessageBox(
                _("Could not open {file}:\n{error}", file=member, error=exc),
                _("Edit files"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return False
        self._loading = True
        try:
            self._editor.ChangeValue(doc.text)
        finally:
            self._loading = False
        self._member = member
        self._doc = doc
        self._dirty = False
        self._disk_mtime = source_mtime_ns(self._source, member)
        self._editor.SetEditable(doc.editable)
        self._editor.SetName(member)
        self._file_label.SetLabel(_("&Text of {file}", file=member))
        self._notice_label.SetLabel(self._read_only_message(doc))
        self._place_caret(1, 1)
        self._update_title()
        self._update_buttons()
        self._editor.GetParent().Layout()
        return True

    def _read_only_message(self, doc: TextDocument) -> str:
        if doc.read_only_reason == READ_ONLY_UNDECODABLE:
            return _(
                "Read-only: this file is not valid {encoding} text, so editing "
                "could damage it.",
                encoding=doc.encoding.upper(),
            )
        if doc.read_only_reason == READ_ONLY_TOO_LARGE:
            return _("Read-only: this file is too large to edit here.")
        return ""

    def _place_caret(self, line: int | None, column: int | None) -> None:
        line = max(1, line or 1)
        column = max(1, column or 1)
        pos = self._editor.XYToPosition(column - 1, line - 1)
        if pos < 0:
            pos = self._editor.XYToPosition(0, line - 1)
        if pos < 0:
            pos = self._editor.GetLastPosition() if line > 1 else 0
        self._editor.SetInsertionPoint(pos)
        self._editor.ShowPosition(pos)
        self._update_position()

    def _caret_line_column(self) -> tuple[int, int] | None:
        found = self._editor.PositionToXY(self._editor.GetInsertionPoint())
        if len(found) == 3:
            ok, x, y = found
            return (y + 1, x + 1) if ok else None
        x, y = found
        return y + 1, x + 1

    def _update_position(self) -> None:
        if self._member is None:
            self._position_label.SetLabel("")
            return
        where = self._caret_line_column()
        if where is not None:
            self._position_label.SetLabel(
                _("Line {line}, column {column}", line=where[0], column=where[1])
            )

    def go_to_line(self) -> None:
        if self._member is None:
            return
        with wx.TextEntryDialog(
            self,
            _("Line number (or line:column):"),
            _("Go to line"),
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            text = dlg.GetValue().strip()
        parts = text.replace(",", ":").split(":")
        try:
            line = int(parts[0])
            column = int(parts[1]) if len(parts) > 1 and parts[1].strip() else 1
        except ValueError:
            return
        self._switch_page(_PAGE_TEXT, focus=False)
        self._place_caret(line, column)
        self._editor.SetFocus()

    # --- Preview ---

    def _switch_page(self, page: int, *, focus: bool = True) -> None:
        # ChangeSelection: no page-changed event, so the preview loads once here.
        if self._book.GetSelection() != page:
            self._book.ChangeSelection(page)
        if page == _PAGE_PREVIEW:
            self._refresh_preview()
        if not focus:
            return
        if page == _PAGE_TEXT:
            self._editor.SetFocus()
        else:
            self._preview_host.SetFocus()

    def _ensure_preview_view(self) -> bool:
        """Create the preview WebView on first use. False when no backend works."""
        if self._preview_view is not None:
            return self._preview_is_webview
        mm = self._main_mod
        view, is_webview = mm._create_ai_html_view(self._preview_host, name=_("Preview"))
        self._preview_host.GetSizer().Add(view, 1, wx.EXPAND)
        mm._wire_ai_html_host(self._preview_host, view, is_webview=is_webview)
        self._preview_view = view
        self._preview_is_webview = is_webview
        if is_webview:
            import wx.html2 as html2

            view.Bind(html2.EVT_WEBVIEW_NAVIGATING, self._on_preview_navigating)
            view.Bind(html2.EVT_WEBVIEW_LOADED, self._on_preview_loaded)
            tip = _(
                "Preview: press Enter to browse the page. Inside the page, Escape "
                "returns to the file list, and Ctrl+Tab leaves the page."
            )
            for win in (self._preview_host, view):
                try:
                    win.SetToolTip(tip)
                except Exception:
                    pass
        else:
            view.Hide()
        self._preview_host.Layout()
        return is_webview

    def _preview_root(self) -> Path | None:
        if not self._source.packaged:
            return self._source.root
        if self._preview_dir is None:
            from .epub_package import extract_epub

            folder = Path(tempfile.mkdtemp(prefix="checkmate-preview-"))
            try:
                extract_epub(self._source.target, folder)
            except (OSError, zipfile.BadZipFile):
                logger.debug("Could not extract package for preview", exc_info=True)
                shutil.rmtree(folder, ignore_errors=True)
                return None
            self._preview_dir = folder
        return self._preview_dir

    def _sync_preview_copy(self, member: str) -> None:
        """Mirror a packaged save/revert into the extracted preview copy."""
        if self._preview_dir is None:
            return
        dest = (self._preview_dir / member).resolve()
        try:
            dest.relative_to(self._preview_dir.resolve())
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(read_member_bytes(self._source, member))
        except (ValueError, OSError, KeyError, zipfile.BadZipFile):
            logger.debug("Could not refresh preview copy of %s", member, exc_info=True)

    def _refresh_preview(self) -> None:
        if self._book.GetSelection() != _PAGE_PREVIEW:
            return
        # Only the item selected in the tree is ever previewed.
        member = self._preview_member
        notice = ""
        uri = ""
        page = ""
        fresh_view = self._preview_view is None
        if member is None:
            notice = _("Select a file in the list to preview it.")
        elif not is_preview_member(member):
            notice = _("There is no preview for this type of file.")
        elif not self._ensure_preview_view():
            notice = _("Preview is not available on this computer.")
        elif is_rendered_member(member):
            root = self._preview_root()
            path = root / member if root is not None else None
            if path is None or not path.is_file():
                notice = _("Could not prepare the preview.")
            elif path.suffix.lower() in _RASTER_SUFFIXES:
                uri = self._image_preview_uri(path, member)
                if not uri:
                    notice = _("Could not prepare the preview.")
            else:
                uri = path.as_uri()
        else:
            page = self._source_preview_page(member)
            if not page:
                notice = _("Could not prepare the preview.")
        if member is not None and member == self._member and self._dirty:
            notice = _("Preview shows the last saved version. Save to update it.")
        self._preview_label.SetLabel(
            _("Preview of {file}", file=member) if member else _("Preview")
        )
        if self._preview_is_webview and self._preview_view is not None:
            if page:
                show = lambda: self._show_preview_page(page, member or "")  # noqa: E731
            else:
                show = lambda: self._show_preview_uri(uri or "about:blank")  # noqa: E731
            if fresh_view:
                # A just-created Edge view paints blank if loaded immediately.
                wx.CallLater(200, lambda: None if self._closing else show())
            else:
                show()
        self._preview_notice.SetLabel(notice)
        self._preview_notice.Show(bool(notice))
        self._preview_notice.GetParent().Layout()

    def _source_preview_page(self, member: str) -> str:
        """Read-only source view for CSS, OPF, XML, and other non-rendered text."""
        try:
            text = decode_text(read_member_bytes(self._source, member)).text
        except (OSError, KeyError, zipfile.BadZipFile):
            return ""
        if len(text) > _MAX_SOURCE_PREVIEW_CHARS:
            text = text[:_MAX_SOURCE_PREVIEW_CHARS] + "\n…"
        return _SOURCE_PREVIEW_PAGE.format(
            title=html.escape(member), body=html.escape(text)
        )

    def _image_preview_uri(self, image: Path, member: str) -> str:
        """Write a page that shows *image* centred on the theme background."""
        try:
            if self._preview_pages_dir is None:
                self._preview_pages_dir = Path(
                    tempfile.mkdtemp(prefix="checkmate-preview-pages-")
                )
            # A fresh name per image avoids Edge re-showing a cached page.
            previous = self._preview_pages_dir / f"image-{self._image_page_count}.html"
            self._image_page_count += 1
            page = self._preview_pages_dir / f"image-{self._image_page_count}.html"
            page.write_text(
                _IMAGE_PREVIEW_PAGE.format(
                    title=html.escape(member), src=html.escape(image.as_uri(), quote=True)
                ),
                encoding="utf-8",
            )
            previous.unlink(missing_ok=True)
            return page.as_uri()
        except OSError:
            logger.debug("Could not write image preview page", exc_info=True)
            return ""

    def _clear_preview(self, notice: str = "") -> None:
        """Show nothing: the previewed item is no longer the one selected."""
        self._preview_member = None
        self._preview_label.SetLabel(_("Preview"))
        self._preview_notice.SetLabel(notice)
        self._preview_notice.Show(bool(notice))
        self._preview_notice.GetParent().Layout()
        if (
            self._preview_is_webview
            and self._preview_view is not None
            and self._preview_url not in ("", "about:blank")
        ):
            self._show_preview_uri("about:blank")

    def _show_preview_page(self, page: str, member: str) -> None:
        view = self._preview_view
        if view is None:
            return
        try:
            view.SetPage(page, "")
            self._preview_url = f"source:{member}"
        except Exception:
            logger.debug("Preview SetPage failed for %s", member, exc_info=True)

    def _show_preview_uri(self, uri: str) -> None:
        view = self._preview_view
        if view is None:
            return
        try:
            if uri == self._preview_url and uri != "about:blank":
                import wx.html2 as html2

                view.Reload(html2.WEBVIEW_RELOAD_NO_CACHE)
            else:
                view.LoadURL(uri)
            self._preview_url = uri
        except Exception:
            logger.debug("Preview load failed for %s", uri, exc_info=True)

    def _schedule_preview(self) -> None:
        """Preview the tree selection once arrow-key movement settles."""
        self._cancel_scheduled_preview()
        self._preview_timer = wx.CallLater(_PREVIEW_DELAY_MS, self._preview_selection)

    def _cancel_scheduled_preview(self) -> None:
        if self._preview_timer is not None:
            self._preview_timer.Stop()
            self._preview_timer = None

    def _preview_selection(self) -> None:
        self._preview_timer = None
        if self._closing:
            return
        member = self._tree.selected_member()
        if member is None:
            self._clear_preview(_("Select a file in the list to preview it."))
            return
        if member == self._preview_member:
            return
        keep_tree_focus = self._focus_in_tree()
        self._preview_member = member
        self._switch_page(_PAGE_PREVIEW, focus=False)
        if keep_tree_focus:
            # Page switches and Edge loads can take focus; browsing stays in the list.
            self._keep_tree_focus_until = time.monotonic() + 2.0
            self._restore_tree_focus()
            wx.CallLater(350, self._restore_tree_focus)

    def _focus_in_tree(self) -> bool:
        focus = wx.Window.FindFocus()
        while focus is not None:
            if focus is self._tree.ctrl:
                return True
            focus = focus.GetParent()
        return False

    def _restore_tree_focus(self) -> None:
        if self._closing or time.monotonic() > self._keep_tree_focus_until:
            return
        if not self._focus_in_tree():
            self._main_mod._try_set_focus(self._tree.ctrl)

    def _on_page_changed(self, event: wx.BookCtrlEvent) -> None:
        event.Skip()
        if event.GetSelection() == _PAGE_PREVIEW:
            wx.CallAfter(self._refresh_preview)

    def _on_preview_loaded(self, event) -> None:
        event.Skip()
        from .ai.markdown_html import _WEBVIEW_TAB_EXIT_JS

        if self._preview_view is not None:
            self._main_mod._webview_run_script(self._preview_view, _WEBVIEW_TAB_EXIT_JS)
        self._restore_tree_focus()

    def _on_preview_navigating(self, event) -> None:
        url = (event.GetURL() or "").strip()
        action = self._main_mod._webview_host_action(url)
        if action is not None:
            event.Veto()
            if action == "close":
                wx.CallAfter(self._tree.ctrl.SetFocus)
            elif action in ("page_prev", "page_next"):
                wx.CallAfter(self._switch_page, _PAGE_TEXT)
            elif action == "next":
                wx.CallAfter(self._recheck_btn.SetFocus)
            elif action == "prev":
                wx.CallAfter(self._tree.ctrl.SetFocus)
            return
        if url.lower().startswith(("http://", "https://", "mailto:")):
            event.Veto()
            try:
                webbrowser.open(url)
            except OSError:
                pass
            return
        event.Skip()

    # --- Save / revert / external ---

    def save(self) -> bool:
        """Save the open file. Returns False if the save failed or was cancelled."""
        if not self._dirty or self._member is None or self._doc is None:
            return True
        disk = source_mtime_ns(self._source, self._member)
        if disk is not None and self._disk_mtime is not None and disk != self._disk_mtime:
            answer = wx.MessageBox(
                _(
                    "{file} was changed outside this window since you opened it.\n\n"
                    "Overwrite it with your edits?",
                    file=self._member,
                ),
                _("Edit files"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
                self,
            )
            if answer != wx.YES:
                return False
        try:
            outcome = save_member_text(
                self._source,
                self._member,
                self._doc,
                self._editor.GetValue(),
                session_backups=self._session_backups,
            )
        except UnicodeEncodeError as exc:
            wx.MessageBox(
                _(
                    "This file uses the {encoding} encoding, which cannot store some "
                    "of the characters you typed.\n\n{error}",
                    encoding=self._doc.encoding.upper(),
                    error=exc,
                ),
                _("Could not save"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return False
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            wx.MessageBox(
                _("Could not save {file}:\n{error}", file=self._member, error=exc),
                _("Could not save"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return False
        try:
            from .edit_log import log_manual_edit

            log_manual_edit(
                target_path=self._source.target,
                member=self._member,
                backup_path=outcome.backup_path,
            )
        except OSError:
            logger.debug("Could not write edit changelog", exc_info=True)
        self._dirty = False
        self._disk_mtime = source_mtime_ns(self._source, self._member)
        self._sync_preview_copy(self._member)
        self._update_title()
        self._update_buttons()
        self._refresh_preview()
        self._set_status(_("Saved {file}.", file=self._member))
        return True

    def save_and_recheck(self) -> None:
        if self._checking:
            return
        if not self.save():
            return
        if not self._main.start_workspace_recheck(self):
            self._set_status(
                _("A check is already running. Try again when it finishes.")
            )
            return
        self._checking = True
        self._update_buttons()
        self._set_status(_("Checking…"))

    def revert_file(self) -> None:
        member = self._member
        if member is None or member not in self._session_backups:
            return
        answer = wx.MessageBox(
            _(
                "Restore {file} to how it was before this editing session?\n\n"
                "Any unsaved changes are discarded.",
                file=member,
            ),
            _("Revert file"),
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
            self,
        )
        if answer != wx.YES:
            return
        backup = self._session_backups[member]
        try:
            revert_member(self._source, member, self._session_backups)
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            wx.MessageBox(
                _("Could not revert to the backup:\n{detail}", detail=exc),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        try:
            from .edit_log import log_manual_revert

            log_manual_revert(
                target_path=self._source.target, member=member, backup_path=backup
            )
        except OSError:
            logger.debug("Could not write edit changelog", exc_info=True)
        self._dirty = False
        self._member = None
        self._sync_preview_copy(member)
        self._load(member)
        self._refresh_preview()
        self._set_status(_("Reverted {file}.", file=member))

    def open_external(self) -> None:
        """Open the selected file (or the one being edited) in its default application."""
        member = self._tree.selected_member() or self._member
        if member is None:
            return
        path = member_disk_path(self._source, member)
        if path is None or self._source.packaged:
            return
        if member == self._member and self._dirty and not self.confirm_unsaved():
            return
        if not wx.LaunchDefaultApplication(str(path)):
            wx.MessageBox(
                _("Could not open {file} in its default application.", file=member),
                _("Edit files"),
                wx.OK | wx.ICON_WARNING,
                self,
            )

    # --- Helpers ---

    def _issue_count_for_member(self, result: CheckResult, member: str) -> int:
        from .ai.context import issue_member_position

        cache: dict[str, str | None] = {}
        count = 0
        for issue in result.issues:
            found, _line, _col = issue_member_position(
                self._source.target, issue, cache=cache
            )
            if found and resolve_member(self._source, found) == member:
                count += 1
        return count

    def _set_status(self, message: str) -> None:
        self._status.SetLabel(message)
        self._status.Wrap(max(400, self.GetClientSize().width - 24))
        self.Layout()
        _speak(message)

    def _update_title(self) -> None:
        mark = "*" if self._dirty else ""
        parts = [
            f"{mark}{self._member}" if self._member else None,
            _("Edit files"),
            self._source.target.name,
        ]
        self.SetTitle(" — ".join(p for p in parts if p))

    def _update_buttons(self) -> None:
        editable = self._doc is not None and self._doc.editable
        self._save_btn.Enable(editable and self._dirty)
        self._recheck_btn.Enable(not self._checking)
        self._revert_btn.Enable(
            self._member is not None and self._member in self._session_backups
        )
        self._external_btn.Enable(bool(self._source.members) and not self._source.packaged)

    def _toggle_pane(self) -> None:
        if self._tree.ctrl.HasFocus():
            if self._book.GetSelection() == _PAGE_TEXT:
                self._editor.SetFocus()
            else:
                self._preview_host.SetFocus()
        else:
            self._tree.ctrl.SetFocus()

    # --- Events ---

    def _on_tree_selection(self, event: wx.Event) -> None:
        event.Skip()
        # Destroying the tree and late native notifications also land here.
        if self._closing or self._tree.programmatic:
            return
        if self._tree.selected_member() != self._preview_member:
            # Drop the old preview at once; the new one loads when movement settles.
            self._clear_preview()
            self._schedule_preview()

    def _on_tree_activated(self, _event: wx.Event) -> None:
        member = self._tree.selected_member()
        if member is not None:
            self.open_member(member)

    def _on_tree_char_hook(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            member = self._tree.selected_member()
            if member is not None:
                self.open_member(member)
                return
        event.Skip()

    def _on_text_changed(self, event: wx.CommandEvent) -> None:
        event.Skip()
        if self._loading or self._dirty or not self._editor.IsEditable():
            return
        self._dirty = True
        self._update_title()
        self._update_buttons()

    def _on_caret_event(self, event: wx.Event) -> None:
        event.Skip()
        wx.CallAfter(self._update_position)

    def _on_activate(self, event: wx.ActivateEvent) -> None:
        event.Skip()
        if not event.GetActive() or self._member is None or self._dirty:
            return
        disk = source_mtime_ns(self._source, self._member)
        if disk is None or self._disk_mtime is None or disk == self._disk_mtime:
            return
        member = self._member
        where = self._caret_line_column()
        self._member = None
        if self._load(member) and where is not None:
            self._place_caret(*where)
        self._sync_preview_copy(member)
        self._refresh_preview()
        self._set_status(_("Reloaded {file} because it changed on disk.", file=member))

    def _on_close(self, event: wx.CloseEvent) -> None:
        if event.CanVeto() and not self.confirm_unsaved():
            event.Veto()
            return
        self._closing = True
        self._cancel_scheduled_preview()
        try:
            self._main.on_workspace_closed(self)
        except Exception:
            logger.debug("Main window did not accept workspace close", exc_info=True)
        temp_dirs = [d for d in (self._preview_dir, self._preview_pages_dir) if d is not None]
        if self._preview_view is not None and self._preview_is_webview:
            # Edge must drop its HWND before the frame goes; never Stop/Hide the view.
            try:
                import wx.html2 as html2

                self._preview_view.Unbind(html2.EVT_WEBVIEW_LOADED)
                self._preview_view.Unbind(html2.EVT_WEBVIEW_NAVIGATING)
            except Exception:
                pass
            from .ai.alt_inventory_dialog import schedule_webview_window_destroy

            self.Hide()
            schedule_webview_window_destroy(self)
        else:
            self.Destroy()
        for folder in temp_dirs:
            wx.CallLater(3000, shutil.rmtree, folder, True)
