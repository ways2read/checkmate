"""Gather publication excerpts for AI explain context."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from ..models import CheckResult, Issue
from ..publication import PublicationKind, classify_publication
from ..settings import read_settings

_MAX_EXCERPT_CHARS = 6000
_CONTEXT_LINES = 20


def _parse_ace_file(location: str) -> str | None:
    """Ace locations look like ``file · CSS · snippet``."""
    loc = (location or "").strip()
    if not loc:
        return None
    part = loc.split("·")[0].strip() if "·" in loc else loc.split("\u00b7")[0].strip()
    if part and not part.startswith("<"):
        return part.replace("\\", "/")
    return None


def _ace_location_parts(location: str) -> list[str]:
    loc = (location or "").strip()
    if not loc:
        return []
    return [p.strip() for p in re.split(r"\s*[·\u00b7]\s*", loc) if p.strip()]


def _find_line_for_hints(text: str, hints: list[str]) -> int | None:
    """
    Best-effort 1-based line for Ace (no line/column): match CSS selectors or
    HTML/CSS snippets from the location string inside the file text.
    """
    if not text or not hints:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    # Prefer longer, more specific hints first.
    ordered = sorted(
        (h for h in hints if h and len(h.strip()) >= 2),
        key=len,
        reverse=True,
    )
    for hint in ordered:
        needle = hint.strip()
        # Strip Ace's compacting ellipsis for matching.
        if needle.endswith("…"):
            needle = needle[:-1].rstrip()
        if len(needle) < 2:
            continue
        # Exact substring on a single line.
        for i, line in enumerate(lines):
            if needle in line:
                return i + 1
        # Compacted HTML snippets: allow whitespace flexibility per line.
        compact_needle = re.sub(r"\s+", "", needle)
        if len(compact_needle) >= 4:
            for i, line in enumerate(lines):
                if compact_needle in re.sub(r"\s+", "", line):
                    return i + 1
        # Multi-line: search normalized file for a short unique fragment.
        if len(needle) >= 8:
            norm = text.replace("\r\n", "\n").replace("\r", "\n")
            pos = norm.find(needle)
            if pos < 0 and compact_needle:
                compact_file = re.sub(r"\s+", "", norm)
                cpos = compact_file.find(compact_needle)
                if cpos >= 0:
                    # Map compacted index back roughly via line scan already failed;
                    # fall through to next hint.
                    pass
            elif pos >= 0:
                return norm.count("\n", 0, pos) + 1
    return None


def _parse_epubcheck_location(location: str) -> tuple[str | None, int | None]:
    """Parse ``path (line,column)`` → (path, line)."""
    loc = (location or "").strip()
    if not loc:
        return None, None
    m = re.match(r"^(.+?)\s+\((\d+)\s*,\s*\d+\)\s*$", loc)
    if m:
        return m.group(1).strip().replace("\\", "/"), int(m.group(2))
    # Bare path
    if "/" in loc or "\\" in loc or loc.endswith((".xhtml", ".html", ".opf", ".css", ".xml")):
        return loc.replace("\\", "/"), None
    return None, None


def parse_issue_location(location: str) -> tuple[str | None, int | None]:
    """Parse checker location → (member path, line number or None)."""
    loc = location or ""
    # Ace uses a middle-dot separator; do not treat the whole string as a path.
    if "·" in loc or "\u00b7" in loc:
        return _parse_ace_file(loc), None
    return _parse_epubcheck_location(loc)


def _read_member_text(target: Path, member: str) -> str | None:
    member = member.lstrip("/")
    if target.is_dir():
        path = target / member
        if not path.is_file():
            # Try basename match
            candidates = list(target.rglob(Path(member).name))
            path = candidates[0] if len(candidates) == 1 else None
        if path is None or not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    if target.is_file() and target.suffix.lower() in {".epub", ".ebrl", ".zip"}:
        try:
            with zipfile.ZipFile(target, "r") as zf:
                names = zf.namelist()
                if member in names:
                    name = member
                else:
                    matches = [n for n in names if n.replace("\\", "/").endswith(member)]
                    if len(matches) == 1:
                        name = matches[0]
                    else:
                        base = Path(member).name
                        matches = [n for n in names if Path(n).name == base]
                        if len(matches) != 1:
                            return None
                        name = matches[0]
                raw = zf.read(name)
            return raw.decode("utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile, KeyError):
            return None
    return None


def _slice_around_line(text: str, line: int | None) -> tuple[list[str], int | None]:
    """Return (line_list_slice, index_of_hit_within_slice)."""
    lines = text.splitlines()
    if not lines:
        return [], None
    if line is None or line < 1:
        return lines[:80], None
    idx = line - 1
    start = max(0, idx - _CONTEXT_LINES)
    end = min(len(lines), idx + _CONTEXT_LINES + 1)
    return lines[start:end], idx - start


def _window_around_line(text: str, line: int | None) -> str:
    chunk_lines, hit = _slice_around_line(text, line)
    if not chunk_lines:
        return ""
    if line is None or line < 1 or hit is None:
        chunk = "\n".join(chunk_lines)
        if len(chunk) > _MAX_EXCERPT_CHARS:
            return chunk[:_MAX_EXCERPT_CHARS] + "\n…"
        return chunk
    start_line = max(1, line - _CONTEXT_LINES)
    numbered: list[str] = []
    for i, content in enumerate(chunk_lines):
        abs_n = start_line + i
        mark = ">>" if i == hit else "  "
        numbered.append(f"{mark} {abs_n}: {content}")
    chunk = "\n".join(numbered)
    if len(chunk) > _MAX_EXCERPT_CHARS:
        return chunk[:_MAX_EXCERPT_CHARS] + "\n…"
    return chunk


def _raw_window_around_line(text: str, line: int | None) -> str:
    """Exact file lines (no line-number prefixes) for Fix with AI matching."""
    chunk_lines, _hit = _slice_around_line(text, line)
    if not chunk_lines:
        return ""
    chunk = "\n".join(chunk_lines)
    if len(chunk) > _MAX_EXCERPT_CHARS:
        return chunk[:_MAX_EXCERPT_CHARS] + "\n…"
    return chunk


def send_file_context_enabled() -> bool:
    val = read_settings().get("ai_send_file_context", True)
    return bool(val)


def gather_issue_context(
    issue: Issue,
    result: CheckResult | None,
    *,
    target_path: str | Path | None = None,
) -> dict[str, str]:
    """Build a dict of context strings for the explain prompt."""
    ctx: dict[str, str] = {
        "severity": issue.severity.label,
        "code": issue.code or "",
        "message": issue.message or "",
        "location": issue.location or "",
        "source": issue.source or "",
    }
    path: Path | None = None
    if target_path:
        path = Path(target_path)
    elif result and result.target_path:
        path = Path(result.target_path)

    if result and result.tool_name:
        ctx["tool"] = f"{result.tool_name} {result.tool_version or ''}".strip()

    if path is not None and path.exists():
        ctx["target_path"] = str(path)
        kind_val = ""
        try:
            kind_val = classify_publication(path).value
            ctx["publication_kind"] = kind_val
        except Exception:
            kind_val = ctx.get("publication_kind", "")

        if send_file_context_enabled() and kind_allows_excerpt(kind_val):
            member, line = parse_issue_location(issue.location)
            if member:
                text = _read_member_text(path, member)
                if text:
                    # Ace has no line/column — locate via CSS selector / HTML snippet.
                    if line is None and (
                        "·" in (issue.location or "")
                        or "\u00b7" in (issue.location or "")
                    ):
                        hints = _ace_location_parts(issue.location)[1:]
                        line = _find_line_for_hints(text, hints)
                    ctx["file_member"] = member
                    ctx["file_excerpt"] = _window_around_line(text, line)
                    ctx["file_excerpt_raw"] = _raw_window_around_line(text, line)

    return ctx


def fix_allowed_for_result(result: CheckResult | None) -> bool:
    """True when Fix with AI may run (EPUB / eBraille only)."""
    if result is None or not result.target_path:
        return False
    path = Path(result.target_path)
    if not path.exists():
        return False
    try:
        kind = classify_publication(path).value
    except Exception:
        return False
    return kind_allows_excerpt(kind)


def kind_allows_excerpt(kind: str) -> bool:
    k = (kind or "").lower()
    return k in {
        PublicationKind.EPUB.value,
        PublicationKind.EBRAILLE.value,
        "epub",
        "ebraille",
    }
