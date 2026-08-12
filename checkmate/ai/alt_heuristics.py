"""Pass A: local alt-text heuristics (no AI)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .alt_export import AltExport, AltExportImage

# Closed vocabulary (shared with vision issues where overlap exists).
FLAG_MISSING_ALT = "missing_alt"
FLAG_PLACEHOLDER_ALT = "placeholder_alt"
FLAG_FILENAME_AS_ALT = "filename_as_alt"
FLAG_EMPTY_HAS_ALT = "empty_has_alt"
FLAG_DECORATIVE_WITH_ALT = "decorative_with_alt"
FLAG_DUPLICATE_ALT = "duplicate_alt"
FLAG_VERY_SHORT_ALT = "very_short_alt"
FLAG_CLASS_DECORATIVE_MISMATCH = "class_decorative_mismatch"
FLAG_LOW_RESOLUTION = "low_resolution"

HARD_FLAGS = frozenset(
    {
        FLAG_MISSING_ALT,
        FLAG_PLACEHOLDER_ALT,
        FLAG_FILENAME_AS_ALT,
        FLAG_EMPTY_HAS_ALT,
        FLAG_VERY_SHORT_ALT,
    }
)

_CONTENT_CLASS_PREFIXES = (
    "photograph",
    "composite",
    "logo",
    "chart",
    "diagram",
    "map",
    "screenshot",
    "illustration",
)

_PLACEHOLDER_RE = re.compile(
    r"^(?:"
    r"image|photo|picture|img|figure|graphic|logo|icon|illustration"
    r"|untitled|n/?a|none|null|alt\s*text"
    r")[\s\d._-]*$",
    re.IGNORECASE,
)
_FILENAME_RE = re.compile(
    r"^(?:[\w.-]+\.(?:jpe?g|png|gif|svg|webp|bmp|tiff?))|"
    r"(?:image|img|photo|picture|dsc|img_)[\s_-]*\d+$",
    re.IGNORECASE,
)
_DIM_RE = re.compile(
    r"(\d+)\s*[x×]\s*(\d+)",
    re.IGNORECASE,
)

_SHORT_ALT_LEN = 12
# Longest edge below this (px) is risky for magnification of content images.
_LOW_RES_LONG_EDGE = 400
# Ignore sub-icon spacers / bullets even when marked as content.
_LOW_RES_MIN_EDGE_TO_CONSIDER = 48


@dataclass
class HeuristicFinding:
    index: int
    filename: str
    flags: list[str] = field(default_factory=list)

    @property
    def has_hard(self) -> bool:
        return any(f in HARD_FLAGS for f in self.flags)


@dataclass
class HeuristicReport:
    findings: list[HeuristicFinding] = field(default_factory=list)
    flag_counts: dict[str, int] = field(default_factory=dict)

    def by_index(self) -> dict[int, HeuristicFinding]:
        return {f.index: f for f in self.findings}

    def hard_indices(self) -> set[int]:
        return {f.index for f in self.findings if f.has_hard}


def parse_dimensions(dimensions: str) -> tuple[int, int] | None:
    """Parse ``WIDTHxHEIGHT`` (or ×) from an export dimensions string."""
    text = (dimensions or "").strip()
    if not text:
        return None
    m = _DIM_RE.search(text)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        return None
    return w, h


def _is_vector_filename(filename: str) -> bool:
    return (filename or "").strip().lower().endswith(".svg")


def looks_low_resolution(image: AltExportImage) -> bool:
    """True when raster pixel size is likely too small for magnification."""
    if _is_vector_filename(image.filename):
        return False
    dims = parse_dimensions(image.dimensions)
    if dims is None:
        return False
    w, h = dims
    long_edge = max(w, h)
    short_edge = min(w, h)
    if (
        long_edge < _LOW_RES_MIN_EDGE_TO_CONSIDER
        and short_edge < _LOW_RES_MIN_EDGE_TO_CONSIDER
    ):
        return False
    return long_edge < _LOW_RES_LONG_EDGE


def _should_check_resolution(image: AltExportImage) -> bool:
    if image.is_decorative:
        # Tiny decorative chrome is fine; content-like “decorative” photos are not.
        return _content_like_classification(image.classification)
    return True


def _content_like_classification(classification: str) -> bool:
    c = (classification or "").strip().lower()
    if not c or c == "unclassified":
        return False
    return any(c.startswith(p) for p in _CONTENT_CLASS_PREFIXES)


def _placeholder_or_filename(alt: str, filename: str) -> list[str]:
    flags: list[str] = []
    text = alt.strip()
    if not text:
        return flags
    if _PLACEHOLDER_RE.match(text):
        flags.append(FLAG_PLACEHOLDER_ALT)
    if _FILENAME_RE.match(text):
        flags.append(FLAG_FILENAME_AS_ALT)
    # Exact match to the export filename (common bad pattern)
    fn = (filename or "").strip()
    if fn and text.lower() == fn.lower():
        if FLAG_FILENAME_AS_ALT not in flags:
            flags.append(FLAG_FILENAME_AS_ALT)
    return flags


def analyze_image(image: AltExportImage, *, duplicate: bool = False) -> list[str]:
    """Return heuristic flags for a single image."""
    flags: list[str] = []
    alt = image.alt_stripped

    if image.is_decorative:
        if alt:
            flags.append(FLAG_DECORATIVE_WITH_ALT)
        if _content_like_classification(image.classification):
            flags.append(FLAG_CLASS_DECORATIVE_MISMATCH)
        if _should_check_resolution(image) and looks_low_resolution(image):
            flags.append(FLAG_LOW_RESOLUTION)
        return flags

    # Content (or unclassified / other non-decorative)
    if not alt:
        if image.has_alt_status:
            flags.append(FLAG_EMPTY_HAS_ALT)
        else:
            flags.append(FLAG_MISSING_ALT)
        if _should_check_resolution(image) and looks_low_resolution(image):
            flags.append(FLAG_LOW_RESOLUTION)
        return flags

    flags.extend(_placeholder_or_filename(alt, image.filename))
    if len(alt) < _SHORT_ALT_LEN:
        flags.append(FLAG_VERY_SHORT_ALT)
    if duplicate:
        flags.append(FLAG_DUPLICATE_ALT)
    if _should_check_resolution(image) and looks_low_resolution(image):
        flags.append(FLAG_LOW_RESOLUTION)
    return flags


def run_heuristics(export: AltExport) -> HeuristicReport:
    """Run Pass A over every image in the export."""
    # Duplicate alts among non-decorative images with non-empty alt
    groups: dict[str, list[AltExportImage]] = defaultdict(list)
    for im in export.images:
        if im.is_decorative:
            continue
        text = im.alt_stripped
        if text:
            groups[text].append(im)
    dup_indices = {
        im.index
        for members in groups.values()
        if len(members) > 1
        for im in members
    }

    findings: list[HeuristicFinding] = []
    counts: dict[str, int] = defaultdict(int)
    for im in export.images:
        flags = analyze_image(im, duplicate=im.index in dup_indices)
        if not flags:
            continue
        findings.append(
            HeuristicFinding(index=im.index, filename=im.filename, flags=flags)
        )
        for flag in flags:
            counts[flag] += 1

    return HeuristicReport(findings=findings, flag_counts=dict(counts))


def summarize_heuristics(report: HeuristicReport) -> str:
    """Plain-text summary for prompts / logs."""
    if not report.findings:
        return "Pass A heuristics found no issues."
    lines = ["Pass A heuristic flag counts:"]
    for flag, n in sorted(report.flag_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {flag}: {n}")
    lines.append(f"Images with any flag: {len(report.findings)}")
    return "\n".join(lines)


def images_with_flags(
    export: AltExport, report: HeuristicReport
) -> Iterable[tuple[AltExportImage, HeuristicFinding]]:
    by_idx = {im.index: im for im in export.images}
    for finding in report.findings:
        im = by_idx.get(finding.index)
        if im is not None:
            yield im, finding
