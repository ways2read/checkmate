"""Extract and rebuild EPUB / eBraille ZIP packages.

Mirrors FIDO's ``extract_epub`` / ``create_epub`` (Off the Leash and EPUB-on-disc
flows): mimetype is written first and stored uncompressed so the result stays
EPUB-valid. CheckMate does not import the FIDO package.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_SUFFIXES = {".epub", ".ebrl", ".zip"}


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


def _replace_once(text: str, original: str, replacement: str) -> tuple[str | None, str | None]:
    """Return (new_text, error_key). error_key set when original cannot be applied safely."""
    if not original:
        return None, "empty_original"
    count = text.count(original)
    if count == 0:
        return None, "no_match"
    if count > 1:
        return None, "ambiguous_match"
    return text.replace(original, replacement, 1), None


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
    target = Path(target).expanduser().resolve()
    if not target.exists():
        return ApplyResult(ok=False, error_key="no_target")

    resolved, text = read_member_text(target, member)
    if text is None or resolved is None:
        return ApplyResult(ok=False, error_key="no_member", member=member)

    new_text, err = _replace_once(text, original, replacement)
    if err or new_text is None:
        return ApplyResult(ok=False, error_key=err or "no_match", member=resolved)

    backup_path = ""
    try:
        if target.is_dir():
            path = resolve_member_path(target, resolved) or (target / resolved)
            if backup and path.is_file():
                bak = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, bak)
                backup_path = str(bak)
            path.write_text(new_text, encoding="utf-8", newline="")
            return ApplyResult(ok=True, backup_path=backup_path, member=resolved)

        if is_packaged_publication(target):
            if backup:
                bak = target.with_suffix(target.suffix + ".bak")
                # Avoid clobbering an older backup silently: numbered if needed.
                if bak.exists():
                    n = 1
                    while True:
                        cand = target.with_suffix(f"{target.suffix}.bak{n}")
                        if not cand.exists():
                            bak = cand
                            break
                        n += 1
                shutil.copy2(target, bak)
                backup_path = str(bak)

            with tempfile.TemporaryDirectory(prefix="checkmate-fix-") as tmp:
                work = Path(tmp) / "work"
                work.mkdir()
                extract_epub(target, work)
                member_path = resolve_member_path(work, resolved)
                if member_path is None:
                    return ApplyResult(
                        ok=False, error_key="no_member", member=resolved
                    )
                member_path.write_text(new_text, encoding="utf-8", newline="")
                out_tmp = Path(tmp) / f"out{target.suffix.lower()}"
                create_epub(work, out_tmp)
                shutil.move(str(out_tmp), str(target))
            return ApplyResult(ok=True, backup_path=backup_path, member=resolved)

        return ApplyResult(ok=False, error_key="unsupported_target")
    except OSError as exc:
        return ApplyResult(
            ok=False,
            error_key="write_failed",
            detail=str(exc),
            member=resolved,
            backup_path=backup_path,
        )
    except zipfile.BadZipFile as exc:
        return ApplyResult(
            ok=False,
            error_key="bad_zip",
            detail=str(exc),
            member=resolved,
            backup_path=backup_path,
        )
