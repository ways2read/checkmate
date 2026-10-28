"""List a publication's files and load/save their text losslessly for the workspace."""

from __future__ import annotations

import codecs
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .epub_package import (
    is_checkmate_artifact,
    is_packaged_publication,
    next_backup_path,
    normalize_member_ref,
    restore_from_backup,
)

TEXT_SUFFIXES = frozenset(
    {
        ".xhtml",
        ".html",
        ".htm",
        ".css",
        ".opf",
        ".ncx",
        ".xml",
        ".svg",
        ".smil",
        ".mml",
        ".js",
        ".json",
        ".txt",
        ".pls",
    }
)
# Shown on the Preview tab; other text files show only on the Code tab.
RENDER_SUFFIXES = frozenset(
    {
        ".xhtml",
        ".html",
        ".htm",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".avif",
    }
)
MAX_EDIT_BYTES = 8 * 1024 * 1024
# session_backups key for the one whole-package backup of a packaged source.
PACKAGE_BACKUP_KEY = ""

# Read-only reasons (the UI maps these to translated messages).
READ_ONLY_UNDECODABLE = "undecodable"
READ_ONLY_TOO_LARGE = "too_large"

_XML_DECL_ENCODING_RE = re.compile(
    rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']"
)


@dataclass(frozen=True)
class WorkspaceSource:
    """What the workspace shows: a folder, a single file, or a packaged EPUB."""

    target: Path
    # Folder holding the files on disk; None when files live inside a package.
    root: Path | None
    members: tuple[str, ...]

    @property
    def packaged(self) -> bool:
        return self.root is None


@dataclass
class TextDocument:
    text: str
    encoding: str
    bom: bytes
    newline: str
    read_only_reason: str = ""

    @property
    def editable(self) -> bool:
        return not self.read_only_reason


@dataclass(frozen=True)
class SaveOutcome:
    path: Path
    backup_path: str
    backup_created: bool


def _member_sort_key(member: str) -> list[str]:
    return [part.lower() for part in member.split("/")]


def _folder_members(root: Path) -> list[str]:
    members: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or is_checkmate_artifact(path):
            continue
        members.append(path.relative_to(root).as_posix())
    return sorted(members, key=_member_sort_key)


def _package_members(package: Path) -> list[str]:
    with zipfile.ZipFile(package, "r") as zf:
        names = [
            n.replace("\\", "/")
            for n in zf.namelist()
            if not n.endswith("/") and not is_checkmate_artifact(Path(n))
        ]
    return sorted(names, key=_member_sort_key)


def open_source(target: str | Path) -> WorkspaceSource:
    """Describe *target* for browsing. Raises OSError / BadZipFile when unreadable."""
    target = Path(target).expanduser().resolve()
    if target.is_dir():
        return WorkspaceSource(target, target, tuple(_folder_members(target)))
    if is_packaged_publication(target):
        return WorkspaceSource(target, None, tuple(_package_members(target)))
    if target.is_file():
        return WorkspaceSource(target, target.parent, (target.name,))
    raise FileNotFoundError(str(target))


def resolve_member(source: WorkspaceSource, member: str) -> str | None:
    """Map a checker-reported path onto one of the source's members."""
    ref = normalize_member_ref(member or "").replace("\\", "/").lstrip("/")
    if not ref:
        return None
    if source.root is not None:
        try:
            abs_ref = Path(ref)
            if abs_ref.is_absolute():
                ref = abs_ref.resolve().relative_to(source.root).as_posix()
        except (OSError, ValueError):
            pass
    if ref in source.members:
        return ref
    lower = ref.lower()
    for candidate in source.members:
        if candidate.lower() == lower:
            return candidate
    # Ace / OPF-relative paths: "nav.xhtml" for "EPUB/nav.xhtml".
    suffix = [c for c in source.members if c.lower().endswith("/" + lower)]
    if len(suffix) == 1:
        return suffix[0]
    name = Path(ref).name.lower()
    by_name = [c for c in source.members if Path(c).name.lower() == name]
    if len(by_name) == 1:
        return by_name[0]
    return None


def member_disk_path(source: WorkspaceSource, member: str) -> Path | None:
    """Disk path for *member*, or None for packaged files / paths outside the root."""
    if source.root is None:
        return None
    path = (source.root / member).resolve()
    try:
        path.relative_to(source.root)
    except ValueError:
        return None
    return path


def read_member_bytes(source: WorkspaceSource, member: str) -> bytes:
    if source.root is None:
        with zipfile.ZipFile(source.target, "r") as zf:
            return zf.read(member)
    path = member_disk_path(source, member)
    if path is None:
        raise FileNotFoundError(member)
    return path.read_bytes()


def member_size(source: WorkspaceSource, member: str) -> int:
    if source.root is None:
        with zipfile.ZipFile(source.target, "r") as zf:
            return zf.getinfo(member).file_size
    path = member_disk_path(source, member)
    if path is None:
        raise FileNotFoundError(member)
    return path.stat().st_size


def is_text_member(member: str) -> bool:
    name = Path(member).name.lower()
    return name == "mimetype" or Path(name).suffix in TEXT_SUFFIXES


def _declared_xml_encoding(raw: bytes) -> str | None:
    match = _XML_DECL_ENCODING_RE.match(raw[:256])
    if not match:
        return None
    try:
        name = codecs.lookup(match.group(1).decode("ascii")).name
    except LookupError:
        return None
    # A UTF-16/32 declaration without a BOM on an ASCII-readable prolog is wrong.
    if name.startswith(("utf-16", "utf-32")):
        return None
    return name


def _dominant_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf and crlf >= lf and crlf >= cr:
        return "\r\n"
    if cr > lf:
        return "\r"
    return "\n"


def decode_text(raw: bytes) -> TextDocument:
    """Decode file bytes for editing; ``text`` always uses ``\\n`` newlines."""
    bom = b""
    encoding = "utf-8"
    body = raw
    if raw.startswith(codecs.BOM_UTF8):
        bom, body = codecs.BOM_UTF8, raw[3:]
    elif raw.startswith(codecs.BOM_UTF16_LE):
        bom, body, encoding = codecs.BOM_UTF16_LE, raw[2:], "utf-16-le"
    elif raw.startswith(codecs.BOM_UTF16_BE):
        bom, body, encoding = codecs.BOM_UTF16_BE, raw[2:], "utf-16-be"
    else:
        encoding = _declared_xml_encoding(raw) or "utf-8"
    reason = ""
    try:
        text = body.decode(encoding)
    except UnicodeDecodeError:
        text = body.decode(encoding, errors="replace")
        reason = READ_ONLY_UNDECODABLE
    newline = _dominant_newline(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return TextDocument(text, encoding, bom, newline, reason)


def encode_text(doc: TextDocument, text: str) -> bytes:
    """Encode editor text back in the file's own encoding, BOM, and newline style.

    Raises UnicodeEncodeError when *text* holds characters the encoding cannot store.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if doc.newline != "\n":
        normalized = normalized.replace("\n", doc.newline)
    return doc.bom + normalized.encode(doc.encoding)


def is_rendered_member(member: str) -> bool:
    return Path(member).suffix.lower() in RENDER_SUFFIXES


def source_mtime_ns(source: WorkspaceSource, member: str) -> int | None:
    """Modification time of the file a save would overwrite (member or package)."""
    path = source.target if source.packaged else member_disk_path(source, member)
    if path is None:
        return None
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def load_text_document(source: WorkspaceSource, member: str) -> TextDocument:
    """Load *member* as text, flagging why it must stay read-only (if it must)."""
    if member_size(source, member) > MAX_EDIT_BYTES:
        return TextDocument("", "utf-8", b"", "\n", READ_ONLY_TOO_LARGE)
    return decode_text(read_member_bytes(source, member))


def rewrite_package_members(package: Path, replacements: dict[str, bytes]) -> None:
    """
    Replace members of a zip package in place, keeping everything else as it was.

    Entry order, compression method, and timestamps of untouched members are
    preserved, so a valid EPUB (stored ``mimetype`` first) stays valid. The new
    package is written beside the original and swapped in only when complete.
    """
    package = Path(package)
    tmp = package.with_name(f".{package.name}.checkmate-tmp")
    now = time.localtime()[:6]
    found: set[str] = set()
    try:
        with zipfile.ZipFile(package, "r") as zin, zipfile.ZipFile(tmp, "w") as zout:
            zout.comment = zin.comment
            for info in zin.infolist():
                name = info.filename.replace("\\", "/")
                replaced = name in replacements
                out = zipfile.ZipInfo(info.filename, date_time=now if replaced else info.date_time)
                out.compress_type = info.compress_type
                out.external_attr = info.external_attr
                out.create_system = info.create_system
                out.comment = info.comment
                if replaced:
                    found.add(name)
                    zout.writestr(out, replacements[name])
                    continue
                # Stream untouched members (audio/video can be large).
                out.file_size = info.file_size
                with zin.open(info) as src, zout.open(out, "w") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        missing = set(replacements) - found
        if missing:
            raise KeyError(", ".join(sorted(missing)))
        os.replace(tmp, package)
    finally:
        tmp.unlink(missing_ok=True)


def _ensure_backup(
    source: WorkspaceSource, member: str, session_backups: dict[str, str]
) -> tuple[str, bool]:
    """Back up the member (folder) or whole package once per editing session."""
    key = PACKAGE_BACKUP_KEY if source.packaged else member
    backup = session_backups.get(key, "")
    created = False
    if not backup or not Path(backup).is_file():
        original = source.target if source.packaged else member_disk_path(source, member)
        if original is None:
            raise PermissionError(member)
        bak = next_backup_path(original)
        shutil.copy2(original, bak)
        backup = str(bak)
        session_backups[key] = backup
        created = True
    session_backups[member] = backup
    return backup, created


def save_member_text(
    source: WorkspaceSource,
    member: str,
    doc: TextDocument,
    text: str,
    *,
    session_backups: dict[str, str],
) -> SaveOutcome:
    """Write *text* to *member*, backing the original up once per editing session.

    For packaged publications the member is replaced inside the package and the
    backup is a copy of the whole package (``book.epub.bak``).
    """
    if not doc.editable:
        raise PermissionError(member)
    path = source.target if source.packaged else member_disk_path(source, member)
    if path is None:
        raise PermissionError(member)
    data = encode_text(doc, text)
    backup, created = _ensure_backup(source, member, session_backups)
    if source.packaged:
        rewrite_package_members(path, {member: data})
    else:
        path.write_bytes(data)
    return SaveOutcome(path, backup, created)


def revert_member(
    source: WorkspaceSource, member: str, session_backups: dict[str, str]
) -> Path:
    """Restore *member* from this session's backup. Raises FileNotFoundError if none."""
    backup = session_backups.get(member, "")
    if not backup:
        raise FileNotFoundError(member)
    if source.packaged:
        with zipfile.ZipFile(backup, "r") as zf:
            original = zf.read(member)
        rewrite_package_members(source.target, {member: original})
        return source.target
    path = member_disk_path(source, member)
    if path is None:
        raise FileNotFoundError(member)
    restore_from_backup(backup, path)
    return path
