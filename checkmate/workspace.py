"""Edit files workspace: browse a publication's files, edit text, save, and re-check."""

from __future__ import annotations

import logging
import sys
import zipfile
from pathlib import Path

import wx
import wx.dataview as dv

from .i18n import _
from .models import CheckResult
from .workspace_files import (
    READ_ONLY_PACKAGED,
    READ_ONLY_TOO_LARGE,
    READ_ONLY_UNDECODABLE,
    TextDocument,
    WorkspaceSource,
    is_text_member,
    load_text_document,
    member_disk_path,
    resolve_member,
    revert_member,
    save_member_text,
)

logger = logging.getLogger(__name__)

# Native tree per platform for screen readers (mirrors the issues list choice).
_USE_DATAVIEW_TREE = sys.platform != "win32"


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

    def populate(self, members: tuple[str, ...]) -> None:
        folders: dict[str, object] = {}
        containers: list[object] = []
        if self._dataview:
            assert isinstance(self.ctrl, dv.DataViewTreeCtrl)
            self.ctrl.DeleteAllItems()
            root = dv.NullDataViewItem
        else:
            assert isinstance(self.ctrl, wx.TreeCtrl)
            self.ctrl.DeleteAllItems()
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
        if self._dataview:
            self.ctrl.Select(item)
            self.ctrl.EnsureVisible(item)
        else:
            self.ctrl.SelectItem(item)
            self.ctrl.EnsureVisible(item)

    def focus_first_file(self) -> None:
        if not self._item_by_member:
            self.ctrl.SetFocus()
            return
        first = next(iter(self._item_by_member))
        self.select_member(first)
        self.ctrl.SetFocus()

    def bind_activate(self, handler) -> None:
        if self._dataview:
            self.ctrl.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, handler)
        else:
            self.ctrl.Bind(wx.EVT_TREE_ITEM_ACTIVATED, handler)


class PublicationWorkspace(wx.Frame):
    """Modeless window owned by the main frame; re-checks run in the main window."""

    def __init__(self, main_frame: wx.Frame, source: WorkspaceSource) -> None:
        super().__init__(
            main_frame,
            title=_("Edit files"),
            size=(1040, 720),
            style=wx.DEFAULT_FRAME_STYLE,
        )
        self._main = main_frame
        self._source = source
        self._member: str | None = None
        self._doc: TextDocument | None = None
        self._dirty = False
        self._loading = False
        self._disk_mtime: int | None = None
        self._session_backups: dict[str, str] = {}
        self._checking = False
        try:
            self.SetIcons(main_frame.GetIcons())
        except Exception:
            pass

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        heading = wx.StaticText(panel, label=_("Publication: {path}", path=source.target))
        root.Add(heading, 0, wx.ALL, 8)
        if source.packaged:
            notice = wx.StaticText(
                panel,
                label=_(
                    "Files inside a packaged publication are read-only here. "
                    "To edit them, extract the publication to a folder and open the folder."
                ),
            )
            notice.Wrap(980)
            root.Add(notice, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        self._splitter.SetMinimumPaneSize(160)

        left = wx.Panel(self._splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        left_sizer.Add(wx.StaticText(left, label=_("&Files")), 0, wx.BOTTOM, 4)
        self._tree = _FileTree(left, name=_("Files"))
        self._tree.populate(source.members)
        self._tree.bind_activate(self._on_tree_activated)
        self._tree.ctrl.Bind(wx.EVT_CHAR_HOOK, self._on_tree_char_hook)
        left_sizer.Add(self._tree.ctrl, 1, wx.EXPAND)
        left.SetSizer(left_sizer)

        right = wx.Panel(self._splitter)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        self._file_label = wx.StaticText(right, label=_("&Text (no file open)"))
        right_sizer.Add(self._file_label, 0, wx.BOTTOM, 4)
        self._editor = wx.TextCtrl(
            right,
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
        right_sizer.Add(self._editor, 1, wx.EXPAND)
        info_row = wx.BoxSizer(wx.HORIZONTAL)
        self._position_label = wx.StaticText(right, label="")
        self._notice_label = wx.StaticText(right, label="")
        info_row.Add(self._position_label, 0, wx.RIGHT, 16)
        info_row.Add(self._notice_label, 1)
        right_sizer.Add(info_row, 0, wx.EXPAND | wx.TOP, 4)
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

        ids = {name: wx.NewIdRef() for name in ("save", "recheck", "goto", "close", "pane")}
        self.Bind(wx.EVT_MENU, lambda _e: self.save(), id=ids["save"])
        self.Bind(wx.EVT_MENU, lambda _e: self.save_and_recheck(), id=ids["recheck"])
        self.Bind(wx.EVT_MENU, lambda _e: self.go_to_line(), id=ids["goto"])
        self.Bind(wx.EVT_MENU, lambda _e: self.Close(), id=ids["close"])
        self.Bind(wx.EVT_MENU, lambda _e: self._toggle_pane(), id=ids["pane"])
        self.SetAcceleratorTable(
            wx.AcceleratorTable(
                [
                    (wx.ACCEL_CTRL, ord("S"), ids["save"]),
                    (wx.ACCEL_NORMAL, wx.WXK_F5, ids["recheck"]),
                    (wx.ACCEL_CTRL, ord("G"), ids["goto"]),
                    (wx.ACCEL_CTRL, ord("W"), ids["close"]),
                    (wx.ACCEL_NORMAL, wx.WXK_F6, ids["pane"]),
                ]
            )
        )

        panel.SetSizer(root)
        self._update_title()
        self._update_buttons()
        self.CentreOnParent()

    # --- Public API used by the main window ---

    @property
    def target(self) -> Path:
        return self._source.target

    def has_unsaved_changes(self) -> bool:
        return self._dirty

    def focus_files(self) -> None:
        if self._member is None:
            self._tree.focus_first_file()
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
            self._set_status(
                _("Could not find {file} in this publication.", file=member or "?")
            )
            self.focus_files()
            return False
        return self.open_member(resolved, line=line, column=column)

    def open_member(
        self, member: str, *, line: int | None = None, column: int | None = None
    ) -> bool:
        if member != self._member:
            if not self.confirm_unsaved():
                return False
            if not self._load(member):
                return False
        self._tree.select_member(member)
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
        message = " ".join(lines)
        self._set_status(message)

    # --- Loading / caret ---

    def _load(self, member: str) -> bool:
        doc: TextDocument | None = None
        notice = ""
        try:
            if is_text_member(member):
                doc = load_text_document(self._source, member)
            else:
                notice = _("This is not a text file.")
                if not self._source.packaged:
                    notice += " " + _("Use Open in default app to view it.")
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            wx.MessageBox(
                _("Could not open {file}:\n{error}", file=member, error=exc),
                _("Edit files"),
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return False
        if doc is not None and not doc.editable:
            notice = self._read_only_message(doc)
        self._loading = True
        try:
            self._editor.ChangeValue(doc.text if doc is not None else "")
        finally:
            self._loading = False
        self._member = member
        self._doc = doc
        self._dirty = False
        self._disk_mtime = self._current_mtime()
        editable = doc is not None and doc.editable
        self._editor.SetEditable(editable)
        self._editor.SetName(member)
        self._file_label.SetLabel(_("&Text of {file}", file=member))
        self._notice_label.SetLabel(notice)
        self._place_caret(1, 1)
        self._update_title()
        self._update_buttons()
        self._editor.GetParent().Layout()
        return True

    def _read_only_message(self, doc: TextDocument) -> str:
        if doc.read_only_reason == READ_ONLY_PACKAGED:
            return _("Read-only: this file is inside a packaged publication.")
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

    def _update_position(self) -> None:
        if self._member is None:
            self._position_label.SetLabel("")
            return
        found = self._editor.PositionToXY(self._editor.GetInsertionPoint())
        if len(found) == 3:
            ok, x, y = found
        else:
            ok, (x, y) = True, found
        if ok:
            self._position_label.SetLabel(
                _("Line {line}, column {column}", line=y + 1, column=x + 1)
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
        self._place_caret(line, column)
        self._editor.SetFocus()

    # --- Save / revert / external ---

    def _current_mtime(self) -> int | None:
        if self._member is None:
            return None
        path = member_disk_path(self._source, self._member)
        if path is None:
            return None
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return None

    def save(self) -> bool:
        """Save the open file. Returns False if the save failed or was cancelled."""
        if not self._dirty or self._member is None or self._doc is None:
            return True
        disk = self._current_mtime()
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
        except OSError as exc:
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
        self._disk_mtime = self._current_mtime()
        self._update_title()
        self._update_buttons()
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
        except OSError as exc:
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
        self._load(member)
        self._set_status(_("Reverted {file}.", file=member))

    def open_external(self) -> None:
        if self._member is None:
            return
        path = member_disk_path(self._source, self._member)
        if path is None:
            return
        if self._dirty and not self.confirm_unsaved():
            return
        if not wx.LaunchDefaultApplication(str(path)):
            wx.MessageBox(
                _("Could not open {file} in its default application.", file=self._member),
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
        parts = [f"{mark}{self._member}" if self._member else None, _("Edit files"), self._source.target.name]
        self.SetTitle(" — ".join(p for p in parts if p))

    def _update_buttons(self) -> None:
        on_disk = self._member is not None and not self._source.packaged
        editable = self._doc is not None and self._doc.editable
        self._save_btn.Enable(editable and self._dirty)
        self._recheck_btn.Enable(not self._checking)
        self._revert_btn.Enable(on_disk and self._member in self._session_backups)
        self._external_btn.Enable(on_disk)

    def _toggle_pane(self) -> None:
        if self._editor.HasFocus():
            self._tree.ctrl.SetFocus()
        else:
            self._editor.SetFocus()

    # --- Events ---

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
        disk = self._current_mtime()
        if disk is not None and self._disk_mtime is not None and disk != self._disk_mtime:
            member = self._member
            line = self._editor.PositionToXY(self._editor.GetInsertionPoint())
            self._member = None
            if self._load(member) and len(line) == 3 and line[0]:
                self._place_caret(line[2] + 1, line[1] + 1)
            self._set_status(_("Reloaded {file} because it changed on disk.", file=member))

    def _on_close(self, event: wx.CloseEvent) -> None:
        if event.CanVeto() and not self.confirm_unsaved():
            event.Veto()
            return
        try:
            self._main.on_workspace_closed(self)
        except Exception:
            logger.debug("Main window did not accept workspace close", exc_info=True)
        self.Destroy()
