"""Working copies of files inside packaged EPUB/eBraille for external editors.

An external editor needs a real file. The first time a member of a package
is opened externally it is extracted to a temporary folder named after the
package (``…/checkmate-edit-xxxx/book.epub/EPUB/ch1.xhtml``). Edits saved
there are written back into the package when CheckMate asks: on returning
to CheckMate, before a check, and at exit. Leaving CheckMate refreshes any
working file the editor has not changed, so changes CheckMate made in the
meantime (Edit files, Apply fix) reach the editor instead of being
overwritten by an older copy.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .workspace_files import (
    SaveOutcome,
    WorkspaceSource,
    read_member_bytes,
    save_members_bytes,
)


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


@dataclass
class WriteBack:
    """Members written into *package* from the working copy, and the backup used."""

    package: Path
    members: list[str]
    outcome: SaveOutcome


class PackageWorkingCopy:
    def __init__(self, source: WorkspaceSource, folder: Path) -> None:
        self.source = source
        self.folder = folder
        # member -> (mtime_ns, size) of the working file when it last matched the package.
        self._stamps: dict[str, tuple[int, int] | None] = {}

    @property
    def package(self) -> Path:
        return self.source.target

    def file_for(self, member: str) -> Path:
        """The working file for *member*, extracted on first use."""
        path = (self.folder / member).resolve()
        path.relative_to(self.folder.resolve())  # never outside the working folder
        if member not in self._stamps or not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(read_member_bytes(self.source, member))
            self._stamps[member] = _stamp(path)
        return path

    def changed(self) -> list[str]:
        """Members whose working file was saved since it last matched the package."""
        out = []
        for member, stamp in self._stamps.items():
            now = _stamp(self.folder / member)
            if now is not None and now != stamp:
                out.append(member)
        return out

    def write_back(self, session_backups: dict[str, str]) -> WriteBack | None:
        """Write edited working files into the package (backing it up once)."""
        replacements: dict[str, bytes] = {}
        for member in self.changed():
            path = self.folder / member
            data = path.read_bytes()
            if data != read_member_bytes(self.source, member):
                replacements[member] = data
            else:
                self._stamps[member] = _stamp(path)
        if not replacements:
            return None
        outcome = save_members_bytes(self.source, replacements, session_backups=session_backups)
        for member in replacements:
            self._stamps[member] = _stamp(self.folder / member)
        return WriteBack(self.package, list(replacements), outcome)

    def refresh_unchanged(self) -> None:
        """Bring working files the editor has not touched up to date with the package."""
        for member, stamp in list(self._stamps.items()):
            path = self.folder / member
            if _stamp(path) != stamp:
                continue  # edited externally: written back later, never overwritten
            try:
                data = read_member_bytes(self.source, member)
                if path.read_bytes() != data:
                    path.write_bytes(data)
                    self._stamps[member] = _stamp(path)
            except (OSError, KeyError):
                continue


class WorkingCopies:
    """All working copies for this CheckMate session, one per package."""

    def __init__(self) -> None:
        self._copies: dict[Path, PackageWorkingCopy] = {}

    def __iter__(self):
        return iter(list(self._copies.values()))

    def for_source(self, source: WorkspaceSource) -> PackageWorkingCopy:
        copy = self._copies.get(source.target)
        if copy is None:
            folder = Path(tempfile.mkdtemp(prefix="checkmate-edit-")) / source.target.name
            folder.mkdir(parents=True, exist_ok=True)
            copy = PackageWorkingCopy(source, folder)
            self._copies[source.target] = copy
        return copy

    def refresh_unchanged(self) -> None:
        for copy in self:
            copy.refresh_unchanged()

    def has_pending(self) -> bool:
        return any(copy.changed() for copy in self)

    def remove_all(self) -> None:
        for copy in self:
            shutil.rmtree(copy.folder.parent, ignore_errors=True)
        self._copies.clear()
