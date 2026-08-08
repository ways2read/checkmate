"""Gather publication excerpts for AI explain context."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ..models import CheckResult, Issue
from ..publication import PublicationKind, classify_publication, find_package_document
from ..settings import read_settings

_MAX_EXCERPT_CHARS = 6000
# Package documents are usually small; Fix works better with most/all of the OPF.
_MAX_OPF_CHARS = 48_000
_CONTEXT_LINES = 20
_FALLBACK_HEAD_LINES = 80

# OPF region local-names → keyword groups used when no line number is available.
_OPF_REGION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "metadata",
        (
            "metadata",
            "accessibility",
            "dc:",
            "schema:",
            "dcterms:",
        ),
    ),
    (
        "manifest",
        (
            "manifest",
            "media-type",
            "media_type",
            "mediatype",
        ),
    ),
    (
        "spine",
        (
            "spine",
            "itemref",
        ),
    ),
)

_META_WORD_RE = re.compile(r"\bmeta\b", re.IGNORECASE)
_ITEM_WORD_RE = re.compile(r"\bitem\b", re.IGNORECASE)


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


def _issue_hint_tokens(issue: Issue) -> list[str]:
    """Generic tokens from message/location for XHTML/CSS hint search."""
    hints: list[str] = []
    blob = f"{issue.message or ''}\n{issue.location or ''}"
    hints.extend(re.findall(r"['\"]([^'\"]{2,80})['\"]", blob))
    hints.extend(re.findall(r"`([^`]{2,60})`", blob))
    hints.extend(re.findall(r"[#.][\w-]+", blob))
    hints.extend(re.findall(r"<(/?[\w:-]{1,40})", blob))
    # Deduplicate while preserving longer-first preference via _find_line_for_hints.
    seen: set[str] = set()
    out: list[str] = []
    for h in hints:
        key = h.strip()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _parse_epubcheck_location(location: str) -> tuple[str | None, int | None]:
    """Parse ``path (line,column)`` → (path, line)."""
    loc = (location or "").strip()
    if not loc:
        return None, None
    m = re.match(r"^(.+?)\s+\((\d+)\s*,\s*\d+\)\s*$", loc)
    if m:
        return m.group(1).strip().replace("\\", "/"), int(m.group(2))
    # Bare path
    if "/" in loc or "\\" in loc or loc.endswith(
        (".xhtml", ".html", ".opf", ".css", ".xml")
    ):
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
                    matches = [
                        n for n in names if n.replace("\\", "/").endswith(member)
                    ]
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


def _member_suffix(member: str) -> str:
    return Path(member.replace("\\", "/")).suffix.lower()


def _is_package_member(member: str) -> bool:
    path = member.replace("\\", "/").lower()
    name = Path(path).name
    if path.endswith(".opf"):
        return True
    return name.startswith("package") and (name.endswith(".xml") or "." not in name)


def _is_markup_member(member: str) -> bool:
    return _member_suffix(member) in {".xhtml", ".html", ".htm"}


def _is_css_member(member: str) -> bool:
    return _member_suffix(member) == ".css"


def _xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.rsplit(":", 1)[-1]
    return tag


def _find_opf_member(target: Path) -> str | None:
    """Return the package-relative OPF path for a folder or packaged EPUB/eBraille."""
    target = target.expanduser().resolve()
    if target.is_dir():
        opf = find_package_document(target)
        if opf is None:
            return None
        try:
            return opf.resolve().relative_to(target).as_posix()
        except ValueError:
            return opf.name.replace("\\", "/")
    if target.is_file() and target.suffix.lower() in {".epub", ".ebrl", ".zip"}:
        try:
            with zipfile.ZipFile(target, "r") as zf:
                names = {n.replace("\\", "/"): n for n in zf.namelist()}
                container_key = None
                for key in ("META-INF/container.xml", "meta-inf/container.xml"):
                    if key in names:
                        container_key = names[key]
                        break
                if container_key is None:
                    lower = {k.lower(): v for k, v in names.items()}
                    container_key = lower.get("meta-inf/container.xml")
                if container_key is None:
                    # Unique .opf fallback when container.xml is missing.
                    opfs = [n for n in names if n.lower().endswith(".opf")]
                    return opfs[0] if len(opfs) == 1 else None
                root = ET.fromstring(zf.read(container_key))
                for elem in root.iter():
                    if _xml_local_name(elem.tag) != "rootfile":
                        continue
                    full = elem.attrib.get("full-path") or elem.attrib.get("fullPath")
                    if not full:
                        continue
                    full = full.replace("\\", "/")
                    if full in names:
                        return full
                    lower = {k.lower(): v for k, v in names.items()}
                    real = lower.get(full.lower())
                    if real:
                        return real.replace("\\", "/")
        except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError):
            return None
    return None


def _issue_search_blob(issue: Issue) -> str:
    return f"{issue.code or ''} {issue.message or ''} {issue.location or ''}".lower()


def _opf_preferred_region(issue: Issue) -> str | None:
    """Pick an OPF structural region from generic issue keywords."""
    blob = _issue_search_blob(issue)
    for region, keywords in _OPF_REGION_KEYWORDS:
        if any(k in blob for k in keywords):
            return region
    if _META_WORD_RE.search(blob):
        return "metadata"
    if _ITEM_WORD_RE.search(blob) and "itemref" not in blob:
        return "manifest"
    return None


def _find_element_block_lines(text: str, local_name: str) -> list[str] | None:
    """
    Lines spanning ``<ns:local_name …>…</ns:local_name>`` (any namespace prefix).

    Returns None when the element is missing or unclosed.
    """
    if not text or not local_name:
        return None
    open_re = re.compile(
        rf"<(?:[\w.-]+:)?{re.escape(local_name)}\b[^>]*>",
        re.IGNORECASE,
    )
    close_re = re.compile(
        rf"</(?:[\w.-]+:)?{re.escape(local_name)}\s*>",
        re.IGNORECASE,
    )
    m_open = open_re.search(text)
    if not m_open:
        return None
    m_close = close_re.search(text, m_open.end())
    if not m_close:
        return None
    start_line = text.count("\n", 0, m_open.start())
    end_line = text.count("\n", 0, m_close.end())
    lines = text.splitlines()
    if start_line >= len(lines):
        return None
    return lines[start_line : min(len(lines), end_line + 1)]


def _cap_chunk_lines(chunk_lines: list[str]) -> list[str]:
    chunk = "\n".join(chunk_lines)
    if len(chunk) <= _MAX_EXCERPT_CHARS:
        return chunk_lines
    # Keep the start of the region (opening tag / head of block).
    out: list[str] = []
    size = 0
    for line in chunk_lines:
        add = len(line) + (1 if out else 0)
        if size + add > _MAX_EXCERPT_CHARS:
            break
        out.append(line)
        size += add
    if out and "\n".join(chunk_lines) != "\n".join(out):
        out.append("…")
    return out or chunk_lines[:1]


def _clip_excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…"


def _opf_structural_lines(text: str, issue: Issue) -> list[str] | None:
    """
    Prefer a structural OPF region when the checker gave no line number.

    Keyword match → that region; otherwise metadata if present and not huge;
    otherwise None (caller falls back to the file head).
    """
    preferred = _opf_preferred_region(issue)
    if preferred:
        block = _find_element_block_lines(text, preferred)
        if block:
            return _cap_chunk_lines(block)
    # Default OPF unknown-line: metadata is usually the insert target.
    meta = _find_element_block_lines(text, "metadata")
    if meta:
        return _cap_chunk_lines(meta)
    return None


def _opf_member_excerpts(text: str, issue: Issue) -> tuple[str, str]:
    """
    Return (display_excerpt, raw_excerpt) for an OPF package document.

    Prefer the full document when it fits under ``_MAX_OPF_CHARS``; otherwise a
    large structural region or a clipped head of the file.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) <= _MAX_OPF_CHARS:
        return normalized, normalized

    preferred = _opf_preferred_region(issue)
    block = None
    if preferred:
        block = _find_element_block_lines(normalized, preferred)
    if block is None:
        block = _find_element_block_lines(normalized, "metadata")
    if block:
        joined = "\n".join(block)
        clipped = _clip_excerpt(joined, _MAX_OPF_CHARS)
        return clipped, clipped

    clipped = _clip_excerpt(normalized, _MAX_OPF_CHARS)
    return clipped, clipped


def _slice_around_line(text: str, line: int | None) -> tuple[list[str], int | None]:
    """Return (line_list_slice, index_of_hit_within_slice)."""
    lines = text.splitlines()
    if not lines:
        return [], None
    if line is None or line < 1:
        return lines[:_FALLBACK_HEAD_LINES], None
    idx = line - 1
    start = max(0, idx - _CONTEXT_LINES)
    end = min(len(lines), idx + _CONTEXT_LINES + 1)
    return lines[start:end], idx - start


def _format_numbered_window(
    chunk_lines: list[str],
    *,
    start_line: int,
    hit_index: int | None,
) -> str:
    if not chunk_lines:
        return ""
    if hit_index is None:
        chunk = "\n".join(chunk_lines)
        if len(chunk) > _MAX_EXCERPT_CHARS:
            return chunk[:_MAX_EXCERPT_CHARS] + "\n…"
        return chunk
    numbered: list[str] = []
    for i, content in enumerate(chunk_lines):
        abs_n = start_line + i
        mark = ">>" if i == hit_index else "  "
        numbered.append(f"{mark} {abs_n}: {content}")
    chunk = "\n".join(numbered)
    if len(chunk) > _MAX_EXCERPT_CHARS:
        return chunk[:_MAX_EXCERPT_CHARS] + "\n…"
    return chunk


def _window_around_line(text: str, line: int | None) -> str:
    chunk_lines, hit = _slice_around_line(text, line)
    if not chunk_lines:
        return ""
    if line is None or line < 1 or hit is None:
        return _format_numbered_window(chunk_lines, start_line=1, hit_index=None)
    start_line = max(1, line - _CONTEXT_LINES)
    return _format_numbered_window(chunk_lines, start_line=start_line, hit_index=hit)


def _raw_window_around_line(text: str, line: int | None) -> str:
    """Exact file lines (no line-number prefixes) for Fix with AI matching."""
    chunk_lines, _hit = _slice_around_line(text, line)
    if not chunk_lines:
        return ""
    chunk = "\n".join(chunk_lines)
    if len(chunk) > _MAX_EXCERPT_CHARS:
        return chunk[:_MAX_EXCERPT_CHARS] + "\n…"
    return chunk


def _excerpts_from_lines(
    chunk_lines: list[str],
    *,
    start_line: int = 1,
    hit_index: int | None = None,
) -> tuple[str, str]:
    """Return (numbered_excerpt, raw_excerpt) from an explicit line list."""
    capped = _cap_chunk_lines(chunk_lines)
    raw = "\n".join(capped)
    if len(raw) > _MAX_EXCERPT_CHARS:
        raw = raw[:_MAX_EXCERPT_CHARS] + "\n…"
    numbered = _format_numbered_window(
        capped, start_line=start_line, hit_index=hit_index
    )
    return numbered, raw


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
                    # Markup/CSS: also try generic tokens from the issue message.
                    if line is None and (
                        _is_markup_member(member) or _is_css_member(member)
                    ):
                        line = _find_line_for_hints(text, _issue_hint_tokens(issue))

                    ctx["file_member"] = member
                    if _is_package_member(member):
                        # OPFs are usually small — send most/all of the document
                        # so Fix does not pad around a tiny snippet.
                        numbered, raw = _opf_member_excerpts(text, issue)
                        ctx["file_excerpt"] = numbered
                        ctx["file_excerpt_raw"] = raw
                    else:
                        ctx["file_excerpt"] = _window_around_line(text, line)
                        ctx["file_excerpt_raw"] = _raw_window_around_line(text, line)

                    # Content/CSS issues often need an OPF edit (metadata, manifest,
                    # spine). Include a related package excerpt so Fix is not forced
                    # into a wrong-file workaround.
                    if not _is_package_member(member):
                        opf_member = _find_opf_member(path)
                        if opf_member and opf_member.replace("\\", "/") != member.replace(
                            "\\", "/"
                        ):
                            opf_text = _read_member_text(path, opf_member)
                            if opf_text:
                                numbered, raw = _opf_member_excerpts(opf_text, issue)
                                if raw:
                                    ctx["related_opf_member"] = opf_member
                                    ctx["related_opf_excerpt"] = numbered
                                    ctx["related_opf_excerpt_raw"] = raw

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


def issues_matching_seed(
    seed: Issue,
    result: CheckResult | None,
    *,
    max_issues: int = 40,
) -> list[Issue]:
    """
    Issues that share the seed's checker source + code (for Fix all like this).

    Message text is ignored for matching so parameterized EPUBCheck / Ace help
    suffixes still group together. Distinct locations are kept separately.
    """
    if result is None or not seed.code:
        return [seed]
    out: list[Issue] = []
    seen: set[tuple[str, str, str, str]] = set()
    for issue in result.issues:
        if issue.code != seed.code:
            continue
        if seed.source and issue.source and issue.source != seed.source:
            continue
        member, line = parse_issue_location(issue.location)
        key = (
            (issue.source or "").strip().lower(),
            (issue.code or "").strip(),
            (member or "").replace("\\", "/").lstrip("/").lower(),
            f"{line if line is not None else ''}|{(issue.location or '').strip().lower()}",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
        if len(out) >= max_issues:
            break
    if not out:
        return [seed]
    # Prefer keeping the seed first when present.
    for i, issue in enumerate(out):
        if (
            issue.code == seed.code
            and (not seed.source or issue.source == seed.source)
            and issue.location == seed.location
        ):
            if i:
                out.insert(0, out.pop(i))
            break
    else:
        out.insert(0, seed)
        # Dedupe seed if duplicated
        deduped = [out[0]]
        for issue in out[1:]:
            if issue is seed or (
                issue.location == seed.location and issue.message == seed.message
            ):
                continue
            deduped.append(issue)
        out = deduped[:max_issues]
    return out


def gather_batch_fix_context(
    seed: Issue,
    result: CheckResult | None,
    *,
    target_path: str | Path | None = None,
    max_members: int = 12,
    max_issues: int = 40,
) -> tuple[dict[str, str], list[Issue]]:
    """
    Context for Fix all like this: shared metadata + per-member excerpts.

    Returns ``(ctx, matched_issues)``. ``ctx`` includes:
    - standard seed fields from ``gather_issue_context``
    - ``batch_instance_count``, ``batch_member_count``
    - ``batch_instances`` — numbered list for the prompt
    - ``batch_files_block`` — Exact file text sections per member
    """
    matched = issues_matching_seed(seed, result, max_issues=max_issues)
    ctx = gather_issue_context(seed, result, target_path=target_path)
    path: Path | None = None
    if target_path:
        path = Path(target_path)
    elif result and result.target_path:
        path = Path(result.target_path)

    # Collect unique members (preserve order).
    members: list[str] = []
    member_issues: dict[str, list[Issue]] = {}
    for issue in matched:
        member, _line = parse_issue_location(issue.location)
        if not member:
            continue
        key = member.replace("\\", "/").lstrip("/")
        if key not in member_issues:
            if len(members) >= max_members:
                continue
            members.append(key)
            member_issues[key] = []
        member_issues[key].append(issue)

    instance_lines: list[str] = []
    for i, issue in enumerate(matched, start=1):
        member, line = parse_issue_location(issue.location)
        loc = member or issue.location or "—"
        if line:
            loc = f"{loc}:{line}"
        msg = (issue.message or "").strip()
        if len(msg) > 120:
            msg = msg[:119] + "…"
        instance_lines.append(f"{i}. [{issue.severity.label}] {loc} — {msg}")

    files_blocks: list[str] = []
    if path is not None and path.exists() and send_file_context_enabled():
        kind_val = ctx.get("publication_kind", "")
        if kind_allows_excerpt(kind_val):
            for member in members:
                text = _read_member_text(path, member)
                if not text:
                    continue
                # Use first issue in this member for line/OPF windowing.
                sample = member_issues[member][0]
                _m, line = parse_issue_location(sample.location)
                if line is None and (
                    "·" in (sample.location or "")
                    or "\u00b7" in (sample.location or "")
                ):
                    hints = _ace_location_parts(sample.location)[1:]
                    line = _find_line_for_hints(text, hints)
                if _is_package_member(member):
                    _numbered, raw = _opf_member_excerpts(text, sample)
                else:
                    raw = _raw_window_around_line(text, line)
                if not raw:
                    continue
                files_blocks.append(
                    f"### File: {member}\n"
                    f"(instances in this file: {len(member_issues[member])})\n"
                    f"```\n{raw}\n```"
                )

    ctx["batch_instance_count"] = str(len(matched))
    ctx["batch_member_count"] = str(len(members))
    ctx["batch_instances"] = "\n".join(instance_lines)
    ctx["batch_files_block"] = "\n\n".join(files_blocks)
    return ctx, matched


def kind_allows_excerpt(kind: str) -> bool:
    k = (kind or "").lower()
    return k in {
        PublicationKind.EPUB.value,
        PublicationKind.EBRAILLE.value,
        "epub",
        "ebraille",
    }
