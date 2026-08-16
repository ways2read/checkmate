"""Stratified sampling for alt-text vision assessment."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..i18n import _
from .alt_export import AltExport, AltExportImage
from .alt_heuristics import (
    FLAG_CLASS_DECORATIVE_MISMATCH,
    FLAG_JOINED_IMAGES,
    HARD_FLAGS,
    HeuristicReport,
)

# Small exports: always assess everything.
AUTO_ALL_MAX = 20
# Percentage choices when total > AUTO_ALL_MAX.
SAMPLE_PERCENTS: tuple[int, ...] = (10, 25, 50)
DEFAULT_SAMPLE_PERCENT = 25


@dataclass(frozen=True)
class SamplePlan:
    """Which images to send to vision, and why."""

    indices: list[int]
    mode: str  # "all" | "percent"
    total_images: int
    reasons: dict[int, str]  # index -> short reason tag
    percent: int | None = None  # set when mode == "percent"
    excluded: frozenset[int] = field(default_factory=frozenset)

    @property
    def size(self) -> int:
        return len(self.indices)

    @property
    def estimated_vision_calls(self) -> int:
        return self.size


def _sorted_by_index(images: list[AltExportImage]) -> list[AltExportImage]:
    return sorted(images, key=lambda im: im.index)


def _stratified_picks(images: list[AltExportImage], n: int) -> list[AltExportImage]:
    """Pick up to *n* images spread evenly through the publication by index."""
    if n <= 0 or not images:
        return []
    ordered = _sorted_by_index(images)
    if len(ordered) <= n:
        return list(ordered)

    picks: list[AltExportImage] = []
    seen: set[int] = set()

    def add(im: AltExportImage) -> None:
        if im.index not in seen and len(picks) < n:
            picks.append(im)
            seen.add(im.index)

    # Evenly spaced positions across the full ordered list (through the book).
    last = len(ordered) - 1
    for i in range(n):
        if n == 1:
            pos = 0
        else:
            pos = round(i * last / (n - 1))
        add(ordered[min(max(pos, 0), last)])
    for im in ordered:
        if len(picks) >= n:
            break
        add(im)
    return picks


def _content_like(im: AltExportImage) -> bool:
    c = (im.classification or "").strip().lower()
    return any(
        c.startswith(p)
        for p in (
            "photograph",
            "composite",
            "logo",
            "chart",
            "diagram",
            "map",
            "screenshot",
            "illustration",
        )
    )


def count_for_percent(total: int, percent: int) -> int:
    """How many images a percentage sample should include (at least 1 if total>0)."""
    if total <= 0:
        return 0
    pct = max(1, min(100, int(percent)))
    return max(1, min(total, int(math.ceil(total * pct / 100.0))))


def should_assess_all(total: int) -> bool:
    return total <= AUTO_ALL_MAX


def build_sample_plan(
    export: AltExport,
    heuristics: HeuristicReport,
    *,
    mode: str = "percent",
    percent: int = DEFAULT_SAMPLE_PERCENT,
    sample_size: int | None = None,
    exclude_indices: set[int] | frozenset[int] | None = None,
) -> SamplePlan:
    """Build a stratified sample (or full-assess) plan.

    Sampling spreads through the publication by index — never “first N only”.
    Hard Pass A flags are always included first (when not excluded).
    """
    exclude = set(exclude_indices or ())
    all_images = [im for im in export.images if im.index not in exclude]
    total_all = len(export.images)
    available = len(all_images)

    mode_norm = (mode or "percent").strip().lower()
    if mode_norm in {"sample", "pct"}:
        mode_norm = "percent"
    if mode_norm not in {"percent", "all"}:
        mode_norm = "percent"

    # Auto-all for small books (only on a fresh run with nothing excluded).
    if not exclude and should_assess_all(total_all):
        mode_norm = "all"

    if mode_norm == "all" or (
        sample_size is not None and sample_size >= available
    ):
        indices = [im.index for im in _sorted_by_index(all_images)]
        return SamplePlan(
            indices=indices,
            mode="all",
            total_images=total_all,
            reasons={i: "all" for i in indices},
            percent=100 if indices else None,
            excluded=frozenset(exclude),
        )

    if sample_size is None:
        target = count_for_percent(total_all, percent)
        # When continuing, target is absolute reviewed count — caller passes
        # sample_size for the *new* batch instead.
        sample_size = max(0, target - len(exclude))
        sample_size = min(sample_size, available)
    else:
        sample_size = max(0, min(int(sample_size), available))

    if sample_size <= 0:
        return SamplePlan(
            indices=[],
            mode="percent",
            total_images=total_all,
            reasons={},
            percent=int(percent),
            excluded=frozenset(exclude),
        )

    by_index = {im.index: im for im in all_images}
    selected: list[int] = []
    reasons: dict[int, str] = {}

    def take(index: int, reason: str) -> None:
        if index in reasons or index not in by_index:
            return
        if len(selected) >= sample_size:
            return
        selected.append(index)
        reasons[index] = reason

    # 1) Hard Pass A flags (stratified if they somehow exceed budget)
    hard_imgs = [
        by_index[f.index]
        for f in heuristics.findings
        if f.index in by_index and any(flag in HARD_FLAGS for flag in f.flags)
    ]
    for im in _stratified_picks(hard_imgs, sample_size):
        take(im.index, "hard_heuristic")

    remaining_budget = sample_size - len(selected)
    if remaining_budget <= 0:
        selected_sorted = sorted(selected)
        return SamplePlan(
            indices=selected_sorted,
            mode="percent",
            total_images=total_all,
            reasons={i: reasons[i] for i in selected_sorted},
            percent=int(percent),
            excluded=frozenset(exclude),
        )

    # 1.5) Classified joined / multi-panel figures (review even when alt looks fine)
    joined_imgs = [
        by_index[f.index]
        for f in heuristics.findings
        if f.index in by_index and FLAG_JOINED_IMAGES in f.flags
    ]
    for im in _stratified_picks(joined_imgs, remaining_budget):
        take(im.index, "joined_images")

    remaining_budget = sample_size - len(selected)
    if remaining_budget <= 0:
        selected_sorted = sorted(selected)
        return SamplePlan(
            indices=selected_sorted,
            mode="percent",
            total_images=total_all,
            reasons={i: reasons[i] for i in selected_sorted},
            percent=int(percent),
            excluded=frozenset(exclude),
        )

    has_alt_pool = [
        im
        for im in all_images
        if im.has_alt_status and im.index not in reasons
    ]
    dec_pool = [
        im
        for im in all_images
        if im.is_decorative and _content_like(im) and im.index not in reasons
    ]
    # Prefer class-mismatch decorative images inside the decorative pool.
    mismatch_ids = {
        f.index
        for f in heuristics.findings
        if FLAG_CLASS_DECORATIVE_MISMATCH in f.flags
    }
    dec_pool = _sorted_by_index(
        [im for im in dec_pool if im.index in mismatch_ids]
    ) + _sorted_by_index(
        [im for im in dec_pool if im.index not in mismatch_ids]
    )
    # Dedupe decorative pool
    seen_dec: set[int] = set()
    dec_deduped: list[AltExportImage] = []
    for im in dec_pool:
        if im.index not in seen_dec:
            dec_deduped.append(im)
            seen_dec.add(im.index)
    dec_pool = dec_deduped

    # Split remaining budget by pool sizes (through the book), ~half each when both exist.
    if has_alt_pool and dec_pool:
        has_slots = max(1, remaining_budget // 2)
        dec_slots = remaining_budget - has_slots
    elif has_alt_pool:
        has_slots, dec_slots = remaining_budget, 0
    else:
        has_slots, dec_slots = 0, remaining_budget

    for im in _stratified_picks(has_alt_pool, has_slots):
        take(im.index, "has_alt_sample")
    for im in _stratified_picks(dec_pool, dec_slots):
        take(im.index, "decorative_sample")

    # 3) Stratified fill through the rest of the publication
    if len(selected) < sample_size:
        rest = [im for im in all_images if im.index not in reasons]
        for im in _stratified_picks(rest, sample_size - len(selected)):
            take(im.index, "fill")

    selected_sorted = sorted(selected)
    return SamplePlan(
        indices=selected_sorted,
        mode="percent",
        total_images=total_all,
        reasons={i: reasons[i] for i in selected_sorted},
        percent=int(percent),
        excluded=frozenset(exclude),
    )


def merge_sample_plans(prior: SamplePlan | None, new: SamplePlan) -> SamplePlan:
    """Combine prior + new batch into one cumulative plan for the report."""
    if prior is None or not prior.indices:
        return new
    reasons = dict(prior.reasons)
    reasons.update(new.reasons)
    indices = sorted(set(prior.indices) | set(new.indices))
    mode = "all" if len(indices) >= new.total_images else "percent"
    pct = None
    if mode == "all":
        pct = 100
    elif new.percent is not None:
        pct = new.percent
    elif prior.percent is not None:
        pct = prior.percent
    return SamplePlan(
        indices=indices,
        mode=mode,
        total_images=new.total_images,
        reasons={i: reasons.get(i, "prior") for i in indices},
        percent=pct,
        excluded=frozenset(),
    )


def describe_sample_plan(plan: SamplePlan, model_name: str = "") -> str:
    """User-facing preflight summary."""
    model_bit = f" Model: {model_name}." if model_name else ""
    if plan.mode == "all":
        return (
            f"Assess all {plan.total_images} images "
            f"(~{plan.estimated_vision_calls} vision calls + 1 summary).{model_bit}"
        )
    pct = f"{plan.percent}% " if plan.percent else ""
    return (
        f"Assess {pct}{plan.size} of {plan.total_images} "
        f"(stratified through the publication; "
        f"~{plan.estimated_vision_calls} vision calls + 1 summary).{model_bit}"
    )


def sample_choice_labels(total: int) -> list[tuple[str, str, int | None]]:
    """Return ``(label, mode, percent)`` rows for the preflight dialog.

    ``percent`` is None when mode is ``all``.
    """
    if should_assess_all(total):
        return [
            (
                _("Assess all {total} images").format(total=total),
                "all",
                None,
            )
        ]
    rows: list[tuple[str, str, int | None]] = []
    for pct in SAMPLE_PERCENTS:
        n = count_for_percent(total, pct)
        rows.append(
            (
                _("Assess {pct}% ({n} of {total}, stratified through the publication)").format(
                    pct=pct, n=n, total=total
                ),
                "percent",
                pct,
            )
        )
    rows.append(
        (
            _("Assess all {total} images (slower, higher cost)").format(total=total),
            "all",
            None,
        )
    )
    return rows


def assess_more_choice_labels(
    total: int, already: int
) -> list[tuple[str, str, int | None]]:
    """Choices for expanding an existing assessment without redoing prior images."""
    remaining = max(0, total - already)
    if remaining <= 0:
        return []
    rows: list[tuple[str, str, int | None]] = []
    for pct in SAMPLE_PERCENTS:
        target = count_for_percent(total, pct)
        extra = target - already
        if extra <= 0:
            continue
        extra = min(extra, remaining)
        rows.append(
            (
                _(
                    "Bring coverage to {pct}% (assess {extra} more, {target} of {total} total)"
                ).format(pct=pct, extra=extra, target=already + extra, total=total),
                "percent",
                pct,
            )
        )
    rows.append(
        (
            _("Assess all remaining ({remaining} images)").format(remaining=remaining),
            "all",
            None,
        )
    )
    return rows


# Late import removed — _ is imported at top.