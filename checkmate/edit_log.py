"""Append-only edit changelog for CheckMate publication changes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import Issue

_MAX_SNIPPET = 400
_HEADER = """# CheckMate edit changelog

This file is an audit trail of edits CheckMate made to this publication
(for example **Fix with AI**). Each entry names the **backup file** created
before the change so you can restore manually if needed.

"""


def changelog_path_for(target: str | Path) -> Path:
    """
    Changelog path next to the publication.

    - Packaged ``book.epub`` → ``book.epub.checkmate-changelog.md`` beside it
    - Exploded folder → ``checkmate-changelog.md`` inside the folder
    """
    path = Path(target).expanduser().resolve()
    if path.is_dir():
        return path / "checkmate-changelog.md"
    return path.parent / f"{path.name}.checkmate-changelog.md"


def find_changelog(target: str | Path | None) -> Path | None:
    """Return the changelog path when a non-empty file exists for *target*."""
    if not target:
        return None
    path = changelog_path_for(target)
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path
    except OSError:
        return None
    return None


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _clip(text: str, limit: int = _MAX_SNIPPET) -> str:
    t = (text or "").replace("\r\n", "\n").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


def _fence(text: str) -> str:
    body = _clip(text)
    # Avoid breaking the outer fence if the snippet itself contains ```.
    body = body.replace("```", "'''")
    return f"```\n{body}\n```" if body else "_(empty)_"


def _issue_lines(issue: Issue) -> list[str]:
    lines = [
        f"- **Source:** {issue.source or '—'}",
        f"- **Code:** `{issue.code or '—'}`",
        f"- **Severity:** {issue.severity.label}",
    ]
    if issue.location:
        lines.append(f"- **Location:** {issue.location}")
    if issue.message:
        lines.append(f"- **Message:** {_clip(issue.message, 300)}")
    return lines


def _ensure_header(path: Path) -> None:
    if path.is_file() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HEADER, encoding="utf-8")


def _append(path: Path, body: str) -> Path:
    _ensure_header(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(body)
        if not body.endswith("\n"):
            fh.write("\n")
    return path


def log_fix_applied(
    *,
    target_path: str | Path,
    issue: Issue,
    member: str,
    backup_path: str,
    rationale: str = "",
    original: str = "",
    replacement: str = "",
) -> Path:
    """Record that an AI fix was written (before / during re-check)."""
    target = Path(target_path)
    log_path = changelog_path_for(target)
    bak_name = Path(backup_path).name if backup_path else "—"
    bak_full = backup_path or "—"
    lines = [
        f"## {_utc_stamp()} — AI fix applied",
        "",
        f"- **Publication:** `{target.name}`",
        f"- **Member edited:** `{member or '—'}`",
        f"- **Backup file:** `{bak_name}`",
        f"- **Backup path:** `{bak_full}`",
        "",
        "### Issue",
        *_issue_lines(issue),
        "",
    ]
    if rationale.strip():
        lines.extend(["### AI rationale", "", _clip(rationale, 800), ""])
    if original or replacement:
        lines.extend(
            [
                "### Change (excerpt)",
                "",
                "**Before:**",
                _fence(original),
                "",
                "**After:**",
                _fence(replacement),
                "",
            ]
        )
    lines.extend(
        [
            "### Revert",
            "",
            f"To undo this change, replace the publication with the backup "
            f"`{bak_name}` (same folder as the publication, or the member "
            f"`.bak` file for exploded folders). CheckMate may also offer "
            f"revert after validation if the fix looks wrong.",
            "",
            "---",
            "",
        ]
    )
    return _append(log_path, "\n".join(lines))


def log_batch_fix_applied(
    *,
    target_path: str | Path,
    issue: Issue,
    backup_path: str,
    patches: list[tuple[str, str, str]],
    rationale: str = "",
    matched_issue_count: int = 0,
    extra_backups: list[tuple[str, str]] | None = None,
) -> Path:
    """
    Record a multi-patch AI fix (Fix all like this).

    *patches* are ``(member, original, replacement)`` tuples.
    """
    target = Path(target_path)
    log_path = changelog_path_for(target)
    bak_name = Path(backup_path).name if backup_path else "—"
    bak_full = backup_path or "—"
    members = sorted({(m or "—") for m, _o, _r in patches})
    lines = [
        f"## {_utc_stamp()} — AI batch fix applied",
        "",
        f"- **Publication:** `{target.name}`",
        f"- **Patches:** {len(patches)}",
        f"- **Members edited:** {', '.join(f'`{m}`' for m in members) or '—'}",
        f"- **Matching issues (before):** {matched_issue_count or '—'}",
        f"- **Backup file:** `{bak_name}`",
        f"- **Backup path:** `{bak_full}`",
        "",
        "### Seed issue",
        *_issue_lines(issue),
        "",
    ]
    if extra_backups:
        lines.append("### Additional member backups")
        lines.append("")
        for bak, restore in extra_backups:
            lines.append(f"- `{Path(bak).name}` → `{restore}`")
        lines.append("")
    if rationale.strip():
        lines.extend(["### AI rationale", "", _clip(rationale, 800), ""])
    if patches:
        lines.extend(["### Changes (excerpts)", ""])
        for i, (member, original, replacement) in enumerate(patches, start=1):
            lines.extend(
                [
                    f"#### Patch {i} — `{member or '—'}`",
                    "",
                    "**Before:**",
                    _fence(original),
                    "",
                    "**After:**",
                    _fence(replacement),
                    "",
                ]
            )
    lines.extend(
        [
            "### Revert",
            "",
            f"To undo this change, restore from `{bak_name}`"
            + (
                " (and any additional member `.bak` files listed above)"
                if extra_backups
                else ""
            )
            + ". CheckMate may also offer revert after validation.",
            "",
            "---",
            "",
        ]
    )
    return _append(log_path, "\n".join(lines))


def log_fix_validation(
    *,
    target_path: str | Path,
    issue: Issue,
    backup_path: str,
    outcome: str,
    detail: str = "",
) -> Path:
    """
    Record validation after re-check.

    *outcome* examples: ``confirmed``, ``concerns_kept``, ``recheck_failed_kept``.
    """
    target = Path(target_path)
    log_path = changelog_path_for(target)
    bak_name = Path(backup_path).name if backup_path else "—"
    lines = [
        f"## {_utc_stamp()} — AI fix validation",
        "",
        f"- **Publication:** `{target.name}`",
        f"- **Code:** `{issue.code or '—'}`",
        f"- **Outcome:** {outcome}",
        f"- **Backup file:** `{bak_name}`",
    ]
    if detail.strip():
        lines.extend(["", _clip(detail, 1200), ""])
    else:
        lines.append("")
    lines.extend(["---", ""])
    return _append(log_path, "\n".join(lines))


def log_fix_reverted(
    *,
    target_path: str | Path,
    issue: Issue,
    backup_path: str,
    restore_to: str,
) -> Path:
    """Record that the user reverted using the named backup."""
    target = Path(target_path)
    log_path = changelog_path_for(target)
    bak_name = Path(backup_path).name if backup_path else "—"
    lines = [
        f"## {_utc_stamp()} — AI fix reverted",
        "",
        f"- **Publication:** `{target.name}`",
        f"- **Code:** `{issue.code or '—'}`",
        f"- **Restored from backup:** `{bak_name}`",
        f"- **Restored to:** `{restore_to}`",
        "",
        "The publication was restored from the backup created before the fix.",
        "",
        "---",
        "",
    ]
    return _append(log_path, "\n".join(lines))
