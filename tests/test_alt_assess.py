"""Tests for Fido alt-text export ingest, heuristics, sampling, and JSON parse."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from checkmate.ai.alt_assess import parse_vision_assessment, vision_image_limits
from checkmate.ai.alt_export import AltExportImage, load_alt_export
from checkmate.ai.alt_heuristics import (
    FLAG_CLASS_DECORATIVE_MISMATCH,
    FLAG_FILENAME_AS_ALT,
    FLAG_MISSING_ALT,
    FLAG_PLACEHOLDER_ALT,
    FLAG_VERY_SHORT_ALT,
    analyze_image,
    run_heuristics,
)
from checkmate.ai.alt_sample import (
    AUTO_ALL_MAX,
    build_sample_plan,
    count_for_percent,
    merge_sample_plans,
    sample_choice_labels,
    should_assess_all,
)


def _write_export(tmp: Path, rows: list[dict[str, str]], *, with_files: bool = True) -> Path:
    images = tmp / "images"
    images.mkdir(parents=True, exist_ok=True)
    csv_path = tmp / "alt_text_export.csv"
    fieldnames = [
        "Index",
        "Filename",
        "Classification",
        "Alt Text",
        "Status",
        "Dimensions",
        "File Size",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            if with_files:
                name = row["Filename"]
                (images / name).write_bytes(b"\xff\xd8\xff\xd9")
    return tmp


def _rows(n: int, *, bad_first: bool = False) -> list[dict[str, str]]:
    rows = []
    for i in range(1, n + 1):
        if i <= max(1, n // 3):
            status = "Has Alt Text"
            alt = f"A descriptive alt text for image number {i} with enough length."
            klass = "Unclassified"
        else:
            status = "Decorative"
            alt = ""
            klass = "Photograph / Food photograph"
        if bad_first and i == 1:
            alt = "image"
        rows.append(
            {
                "Index": str(i),
                "Filename": f"image_{i:04d}.jpg",
                "Classification": klass,
                "Alt Text": alt,
                "Status": status,
                "Dimensions": "10x10",
                "File Size": "1 KB",
            }
        )
    return rows


def test_load_alt_export_reads_alt_text_column(tmp_path: Path) -> None:
    folder = _write_export(
        tmp_path,
        [
            {
                "Index": "1",
                "Filename": "image_0001.jpg",
                "Classification": "Unclassified",
                "Alt Text": 'A smiling woman holds fried chicken. Text reads ""Hello"".',
                "Status": "Has Alt Text",
                "Dimensions": "100x100",
                "File Size": "1 KB",
            },
            {
                "Index": "2",
                "Filename": "image_0002.jpg",
                "Classification": "Photograph / Food photograph",
                "Alt Text": "",
                "Status": "Decorative",
                "Dimensions": "50x50",
                "File Size": "1 KB",
            },
        ],
    )
    export = load_alt_export(folder)
    assert export.total == 2
    assert export.images[0].alt_stripped.startswith("A smiling woman")
    assert "Hello" in export.images[0].alt_text
    assert export.images[0].has_alt_status
    assert export.images[1].is_decorative
    assert export.images[0].image_path is not None
    counts = export.counts()
    assert counts["with_alt"] == 1
    assert counts["decorative"] == 1
    assert counts["missing"] == 0


def test_heuristics_placeholder_and_mismatch() -> None:
    bad = AltExportImage(
        index=1,
        filename="image_0001.jpg",
        classification="Unclassified",
        alt_text="image",
        status="Has Alt Text",
    )
    flags = analyze_image(bad)
    assert FLAG_PLACEHOLDER_ALT in flags

    fn = AltExportImage(
        index=2,
        filename="photo.jpg",
        classification="Unclassified",
        alt_text="photo.jpg",
        status="Has Alt Text",
    )
    assert FLAG_FILENAME_AS_ALT in analyze_image(fn)

    short = AltExportImage(
        index=3,
        filename="a.jpg",
        classification="Unclassified",
        alt_text="chili",
        status="Has Alt Text",
    )
    assert FLAG_VERY_SHORT_ALT in analyze_image(short)

    missing = AltExportImage(
        index=4,
        filename="b.jpg",
        classification="Unclassified",
        alt_text="",
        status="Unclassified",
    )
    assert FLAG_MISSING_ALT in analyze_image(missing)

    dec = AltExportImage(
        index=5,
        filename="c.jpg",
        classification="Photograph / Food photograph",
        alt_text="",
        status="Decorative",
    )
    assert FLAG_CLASS_DECORATIVE_MISMATCH in analyze_image(dec)


def test_small_export_assesses_all(tmp_path: Path) -> None:
    n = AUTO_ALL_MAX
    folder = _write_export(tmp_path, _rows(n))
    export = load_alt_export(folder)
    report = run_heuristics(export)
    plan = build_sample_plan(export, report, mode="percent", percent=10)
    assert should_assess_all(n)
    assert plan.mode == "all"
    assert plan.size == n
    labels = sample_choice_labels(n)
    assert len(labels) == 1
    assert labels[0][1] == "all"


def test_percent_sample_is_stratified_not_prefix(tmp_path: Path) -> None:
    folder = _write_export(tmp_path, _rows(100, bad_first=True))
    export = load_alt_export(folder)
    report = run_heuristics(export)
    plan = build_sample_plan(export, report, mode="percent", percent=10)
    assert plan.mode == "percent"
    assert plan.size == count_for_percent(100, 10)
    assert 1 in plan.indices  # hard heuristic
    # Spread through the book: include something from the last half
    assert any(i > 50 for i in plan.indices)
    # Not merely 1..N
    assert plan.indices != list(range(1, plan.size + 1))


def test_assess_more_excludes_prior(tmp_path: Path) -> None:
    folder = _write_export(tmp_path, _rows(80))
    export = load_alt_export(folder)
    report = run_heuristics(export)
    first = build_sample_plan(export, report, mode="percent", percent=10)
    second = build_sample_plan(
        export,
        report,
        mode="percent",
        percent=25,
        exclude_indices=set(first.indices),
    )
    assert not set(second.indices) & set(first.indices)
    merged = merge_sample_plans(first, second)
    assert merged.size == len(set(first.indices) | set(second.indices))
    assert merged.size == count_for_percent(80, 25)


def test_parse_vision_assessment_json() -> None:
    image = AltExportImage(
        index=58,
        filename="image_0058.jpg",
        classification="Photograph / Food photograph",
        alt_text="",
        status="Decorative",
    )
    raw = """
    Here you go:
    ```json
    {
      "verdict": "needs_attention",
      "confidence": "medium",
      "status_ok": false,
      "recommended_status": "has_alt",
      "descriptiveness": "n_a",
      "accuracy": "n_a",
      "usefulness": "n_a",
      "issues": ["likely_content_marked_decorative", "bogus_ignored"],
      "reason": "Food photo marked decorative.",
      "teaching_note": "Product photos usually need alt.",
      "suggested_alt": "should be ignored"
    }
    ```
    """
    parsed = parse_vision_assessment(raw, image=image)
    assert parsed is not None
    assert parsed.verdict == "needs_attention"
    assert parsed.status_ok is False
    assert parsed.issues == ["likely_content_marked_decorative"]
    assert parsed.suggested_alt is None
    assert parsed.filename == "image_0058.jpg"


def test_parse_vision_assessment_rejects_garbage() -> None:
    image = AltExportImage(
        index=1,
        filename="a.jpg",
        classification="",
        alt_text="x",
        status="Has Alt Text",
    )
    assert parse_vision_assessment("not json at all", image=image) is None


def test_load_alt_export_missing_csv(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_alt_export(tmp_path)


def test_vision_image_limits_defaults() -> None:
    edge, max_bytes, quality = vision_image_limits()
    assert edge >= 64
    assert max_bytes >= 100_000
    assert 40 <= quality <= 95


def test_ensure_alt_report_html_regenerates(tmp_path: Path) -> None:
    from checkmate.ai.alt_export import ensure_alt_report_html

    folder = _write_export(tmp_path, _rows(3))
    html = folder / "alt_text_report.html"
    assert not html.exists()
    path = ensure_alt_report_html(folder)
    assert path == html
    assert html.is_file()
    text = html.read_text(encoding="utf-8")
    assert "Alt Text Report" in text
    assert "image_0001.jpg" in text
    # Second call leaves existing file
    mtime = html.stat().st_mtime
    path2 = ensure_alt_report_html(folder)
    assert path2 == html
    assert html.stat().st_mtime == mtime


def test_alt_export_cache_reuses_folder(tmp_path: Path) -> None:
    from checkmate.ai.alt_build_export import (
        clear_alt_export_cache,
        get_cached_alt_export,
        remember_alt_export,
    )

    clear_alt_export_cache()
    pub = tmp_path / "book.epub"
    pub.write_bytes(b"PK fake")
    export = _write_export(tmp_path / "export", _rows(2))
    assert get_cached_alt_export(pub) is None
    remember_alt_export(pub, export)
    assert get_cached_alt_export(pub) == export.resolve()
    # Fingerprint change invalidates
    pub.write_bytes(b"PK fake2")
    assert get_cached_alt_export(pub) is None
    clear_alt_export_cache()
