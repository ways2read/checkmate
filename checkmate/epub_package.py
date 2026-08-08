"""Extract and rebuild EPUB / eBraille ZIP packages.

Mirrors FIDO's ``extract_epub`` / ``create_epub`` (Off the Leash and EPUB-on-disc
flows): mimetype is written first and stored uncompressed so the result stays
EPUB-valid. CheckMate does not import the FIDO package.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_SUFFIXES = {".epub", ".ebrl", ".zip"}
_BACKUP_NAME_RE = re.compile(r"^(?P<stem>.+)(?P<bak>\.bak\d*)$", re.IGNORECASE)


def is_packaged_publication(path: Path) -> bool:
    """True when *path* is a packaged .epub / .ebrl / legacy .zip file."""
    path = Path(path)
    return path.is_file() and path.suffix.lower() in _PACKAGE_SUFFIXES


def extract_epub(epub_path: str | Path, extract_to: str | Path) -> None:
    """Extract an EPUB/eBraille package (zip) to a directory."""
    with zipfile.ZipFile(epub_path, "r") as zf:
        zf.extractall(extract_to)


def create_epub(source_dir: str | Path, output_path: str | Path) -> None:
    """Repackage a directory as a valid EPUB (mimetype first and uncompressed)."""
    source_dir = Path(source_dir)
    output_path = Path(output_path)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as epub:
        mimetype_path = source_dir / "mimetype"
        if mimetype_path.is_file():
            epub.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)
        for file_path in source_dir.rglob("*"):
            if file_path.is_file() and file_path.name != "mimetype":
                arcname = str(file_path.relative_to(source_dir)).replace("\\", "/")
                epub.write(file_path, arcname)


def resolve_member_path(root: Path, member: str) -> Path | None:
    """Resolve a package-relative member path under an exploded directory."""
    member = member.lstrip("/").replace("\\", "/")
    path = root / member
    if path.is_file():
        return path
    candidates = list(root.rglob(Path(member).name))
    if len(candidates) == 1 and candidates[0].is_file():
        return candidates[0]
    return None


def read_member_text(target: Path, member: str) -> tuple[str | None, str | None]:
    """
    Read a text member from an exploded folder or packaged ZIP.

    Returns ``(resolved_member_name, text)`` or ``(None, None)``.
    """
    member = member.lstrip("/").replace("\\", "/")
    target = Path(target)
    if target.is_dir():
        path = resolve_member_path(target, member)
        if path is None:
            return None, None
        try:
            rel = path.relative_to(target).as_posix()
        except ValueError:
            rel = member
        try:
            return rel, path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None, None

    if is_packaged_publication(target):
        try:
            with zipfile.ZipFile(target, "r") as zf:
                names = zf.namelist()
                if member in names:
                    name = member
                else:
                    matches = [n for n in names if n.replace("\\", "/").endswith(member)]
                    if len(matches) != 1:
                        base = Path(member).name
                        matches = [n for n in names if Path(n).name == base]
                        if len(matches) != 1:
                            return None, None
                    name = matches[0]
                raw = zf.read(name)
            return name.replace("\\", "/"), raw.decode("utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile, KeyError):
            return None, None
    return None, None


@dataclass
class ApplyResult:
    ok: bool
    error_key: str | None = None
    detail: str = ""
    backup_path: str = ""
    member: str = ""


def next_backup_path(path: Path) -> Path:
    """
    Choose a backup path that does not overwrite an existing backup.

    First backup is ``file.ext.bak``; further ones are ``file.ext.bak1``,
    ``file.ext.bak2``, …
    """
    path = Path(path)
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        return bak
    n = 1
    while True:
        cand = path.with_suffix(f"{path.suffix}.bak{n}")
        if not cand.exists():
            return cand
        n += 1


def original_path_from_backup(backup_path: str | Path) -> Path | None:
    """Map ``file.ext.bak`` / ``file.ext.bakN`` back to ``file.ext``."""
    bak = Path(backup_path)
    match = _BACKUP_NAME_RE.match(bak.name)
    if not match:
        return None
    return bak.with_name(match.group("stem"))


def _newline_style(text: str) -> str:
    """Dominant newline convention in *text* (``\\n``, ``\\r\\n``, or ``\\r``)."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf > 0:
        return "\r\n"
    if cr > lf and cr > 0:
        return "\r"
    return "\n"


def normalize_newlines(text: str) -> str:
    """Normalize all newlines to ``\\n`` for matching."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def count_occurrences(text: str, needle: str) -> int:
    """Count *needle* in *text*, trying exact then newline-normalized match."""
    if not needle:
        return 0
    count = text.count(needle)
    if count:
        return count
    norm_text = normalize_newlines(text)
    norm_needle = normalize_newlines(needle)
    if norm_needle == needle and norm_text == text:
        return 0
    return norm_text.count(norm_needle)


def _replace_once(text: str, original: str, replacement: str) -> tuple[str | None, str | None]:
    """Return (new_text, error_key). error_key set when original cannot be applied safely."""
    if not original:
        return None, "empty_original"
    count = text.count(original)
    if count == 1:
        return text.replace(original, replacement, 1), None
    if count > 1:
        return None, "ambiguous_match"

    # AI excerpts join with ``\\n`` after splitlines(); packaged CSS often uses CRLF.
    style = _newline_style(text)
    norm_text = normalize_newlines(text)
    norm_orig = normalize_newlines(original)
    norm_repl = normalize_newlines(replacement)
    if not norm_orig:
        return None, "empty_original"
    count = norm_text.count(norm_orig)
    if count == 0:
        return None, "no_match"
    if count > 1:
        return None, "ambiguous_match"
    new_norm = norm_text.replace(norm_orig, norm_repl, 1)
    if style != "\n":
        return new_norm.replace("\n", style), None
    return new_norm, None


def apply_text_replacement(
    target: Path,
    member: str,
    original: str,
    replacement: str,
    *,
    backup: bool = True,
) -> ApplyResult:
    """
    Apply a single exact text replacement to a publication member.

    For exploded folders, writes the member in place.
    For packaged files, extracts → edits → rebuilds via ``create_epub``,
    replacing the original package (optional ``.bak`` backup first).
    """
    return apply_text_replacements(
        target,
        [(member, original, replacement)],
        backup=backup,
    )


def apply_text_replacements(
    target: Path,
    patches: list[tuple[str, str, str]],
    *,
    backup: bool = True,
) -> ApplyResult:
    """
    Apply one or more exact text replacements in a single backup/rebuild cycle.

    Each patch is ``(member, original, replacement)``. Patches for the same
    member are applied sequentially (each ``original`` must occur exactly once
    in the member text as it stands before that patch).
    """
    target = Path(target).expanduser().resolve()
    if not target.exists():
        return ApplyResult(ok=False, error_key="no_target")
    if not patches:
        return ApplyResult(ok=False, error_key="no_match")

    # Plan final text per resolved member.
    planned: dict[str, str] = {}
    resolved_names: dict[str, str] = {}
    for member, original, replacement in patches:
        if not original:
            return ApplyResult(ok=False, error_key="empty_original", member=member)
        resolved, text = read_member_text(target, member)
        if text is None or resolved is None:
            return ApplyResult(ok=False, error_key="no_member", member=member)
        key = resolved.replace("\\", "/").lstrip("/")
        resolved_names[key] = resolved
        current = planned.get(key, text)
        new_text, err = _replace_once(current, original, replacement)
        if err or new_text is None:
            return ApplyResult(
                ok=False, error_key=err or "no_match", member=resolved
            )
        planned[key] = new_text

    backup_path = ""
    extra_backups: list[tuple[str, str]] = []
    first_member = next(iter(resolved_names.values()))
    try:
        if target.is_dir():
            for key, new_text in planned.items():
                resolved = resolved_names[key]
                path = resolve_member_path(target, resolved) or (target / resolved)
                if backup and path.is_file():
                    bak = next_backup_path(path)
                    shutil.copy2(path, bak)
                    if not backup_path:
                        backup_path = str(bak)
                    else:
                        extra_backups.append((str(bak), str(path)))
                path.write_text(new_text, encoding="utf-8", newline="")
            detail = ""
            if extra_backups:
                detail = "extra_backups=" + ";".join(
                    f"{b}|{r}" for b, r in extra_backups
                )
            return ApplyResult(
                ok=True,
                backup_path=backup_path,
                member=first_member,
                detail=detail,
            )

        if is_packaged_publication(target):
            if backup:
                bak = next_backup_path(target)
                shutil.copy2(target, bak)
                backup_path = str(bak)

            with tempfile.TemporaryDirectory(prefix="checkmate-fix-") as tmp:
                work = Path(tmp) / "work"
                work.mkdir()
                extract_epub(target, work)
                for key, new_text in planned.items():
                    resolved = resolved_names[key]
                    member_path = resolve_member_path(work, resolved)
                    if member_path is None:
                        return ApplyResult(
                            ok=False,
                            error_key="no_member",
                            member=resolved,
                            backup_path=backup_path,
                        )
                    member_path.write_text(new_text, encoding="utf-8", newline="")
                out_tmp = Path(tmp) / f"out{target.suffix.lower()}"
                create_epub(work, out_tmp)
                shutil.move(str(out_tmp), str(target))
            return ApplyResult(
                ok=True, backup_path=backup_path, member=first_member
            )

        return ApplyResult(ok=False, error_key="unsupported_target")
    except OSError as exc:
        return ApplyResult(
            ok=False,
            error_key="write_failed",
            detail=str(exc),
            member=first_member,
            backup_path=backup_path,
        )
    except zipfile.BadZipFile as exc:
        return ApplyResult(
            ok=False,
            error_key="bad_zip",
            detail=str(exc),
            member=first_member,
            backup_path=backup_path,
        )


def restore_from_backup(backup_path: str | Path, restore_to: str | Path) -> None:
    """Copy a ``.bak`` / ``.bakN`` file back over the publication or member it came from."""
    bak = Path(backup_path)
    dest = Path(restore_to)
    if not bak.is_file():
        raise FileNotFoundError(str(bak))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bak, dest)


def restore_path_for_apply(
    target: str | Path,
    apply_result: ApplyResult,
) -> str:
    """
    Path that ``restore_from_backup`` should overwrite after a successful apply.

    Packaged publications restore onto the ``.epub``/``.ebrl``; exploded folders
    restore onto the edited member file beside the ``.bak`` / ``.bakN``.
    """
    target = Path(target)
    bak = (apply_result.backup_path or "").strip()
    if is_packaged_publication(target):
        return str(target)
    if bak:
        original = original_path_from_backup(bak)
        if original is not None:
            return str(original)
    if apply_result.member:
        member_path = resolve_member_path(target, apply_result.member)
        if member_path is not None:
            return str(member_path)
    return str(target)
