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
    FLAG_JOINED_IMAGES,
    FLAG_LOW_RESOLUTION,
    FLAG_MISSING_ALT,
    FLAG_PLACEHOLDER_ALT,
    FLAG_VERY_SHORT_ALT,
    analyze_image,
    looks_joined_panel,
    looks_low_resolution,
    parse_dimensions,
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
        "Context",
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
                "Context": f"Nearby paragraph mentioning image {i}.",
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
                "Context": "Chapter about Korean fried chicken recipes.",
            },
            {
                "Index": "2",
                "Filename": "image_0002.jpg",
                "Classification": "Photograph / Food photograph",
                "Alt Text": "",
                "Status": "Decorative",
                "Dimensions": "50x50",
                "File Size": "1 KB",
                "Context": "",
            },
        ],
    )
    export = load_alt_export(folder)
    assert export.total == 2
    assert export.images[0].alt_stripped.startswith("A smiling woman")
    assert "Hello" in export.images[0].alt_text
    assert "fried chicken" in export.images[0].context_stripped
    assert export.images[0].has_alt_status
    assert export.images[1].is_decorative
    assert export.images[1].context_stripped == ""
    assert export.images[0].image_path is not None
    counts = export.counts()
    assert counts["with_alt"] == 1
    assert counts["decorative"] == 1
    assert counts["missing"] == 0


def test_vision_user_text_includes_context() -> None:
    from checkmate.ai.alt_assess import build_vision_user_text

    image = AltExportImage(
        index=3,
        filename="image_0003.jpg",
        classification="Photograph",
        alt_text="A wok on a stove.",
        status="Has Alt Text",
        context="Next steps: heat oil and add garlic.",
    )
    text = build_vision_user_text(image, heuristic_flags=[])
    assert "Surrounding text:" in text
    assert "heat oil and add garlic" in text
    assert "A wok on a stove." in text
    assert "Publication format:" in text


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


def test_low_resolution_heuristic() -> None:
    assert parse_dimensions("320×240") == (320, 240)
    assert parse_dimensions("1000x800") == (1000, 800)
    assert parse_dimensions("") is None

    low = AltExportImage(
        index=1,
        filename="photo.jpg",
        classification="Photograph",
        alt_text="A landscape with mountains in the distance.",
        status="Has Alt Text",
        dimensions="320x240",
    )
    assert looks_low_resolution(low)
    assert FLAG_LOW_RESOLUTION in analyze_image(low)

    ok = AltExportImage(
        index=2,
        filename="photo.jpg",
        classification="Photograph",
        alt_text="A landscape with mountains in the distance.",
        status="Has Alt Text",
        dimensions="1200x800",
    )
    assert not looks_low_resolution(ok)
    assert FLAG_LOW_RESOLUTION not in analyze_image(ok)

    svg = AltExportImage(
        index=3,
        filename="icon.svg",
        classification="Unclassified",
        alt_text="Publisher logo.",
        status="Has Alt Text",
        dimensions="64x64",
    )
    assert not looks_low_resolution(svg)

    spacer = AltExportImage(
        index=4,
        filename="dot.png",
        classification="Unclassified",
        alt_text="",
        status="Decorative",
        dimensions="10x10",
    )
    assert FLAG_LOW_RESOLUTION not in analyze_image(spacer)


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


def test_percent_sample_prioritizes_joined_panels(tmp_path: Path) -> None:
    rows = _rows(80)
    rows[39]["Classification"] = "Composite image / Multi-panel figure"
    rows[39]["Status"] = "Has Alt Text"
    rows[39]["Alt Text"] = "A long enough description of the apparatus from several angles."
    folder = _write_export(tmp_path, rows)
    export = load_alt_export(folder)
    report = run_heuristics(export)
    plan = build_sample_plan(export, report, mode="percent", percent=10)
    assert 40 in plan.indices
    assert plan.reasons[40] == "joined_images"


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


def test_parse_vision_format_and_orientation_issues() -> None:
    image = AltExportImage(
        index=12,
        filename="eq_12.png",
        classification="Unclassified",
        alt_text="Equation for the area of a circle.",
        status="Has Alt Text",
    )
    raw = """
    {
      "verdict": "needs_attention",
      "confidence": "high",
      "status_ok": true,
      "recommended_status": "has_alt",
      "descriptiveness": "good",
      "accuracy": "good",
      "usefulness": "weak",
      "issues": [
        "image_of_math",
        "image_of_table",
        "likely_wrong_orientation",
        "low_resolution",
        "joined_images",
        "not_a_real_issue"
      ],
      "reason": "Equation image, also looks rotated; nearby table is a screenshot.",
      "teaching_note": "Encode as digital math or tag the PDF image with MathML.",
      "suggested_alt": null
    }
    """
    parsed = parse_vision_assessment(raw, image=image)
    assert parsed is not None
    assert parsed.issues == [
        "image_of_math",
        "image_of_table",
        "likely_wrong_orientation",
        "low_resolution",
        "joined_images",
    ]


def test_vision_prompt_math_remediation_guidance() -> None:
    from checkmate.ai.alt_assess import build_vision_system_prompt

    prompt = build_vision_system_prompt()
    assert "image_of_math" in prompt
    assert "image_of_table" in prompt
    assert "likely_wrong_orientation" in prompt
    assert "MathML" in prompt
    assert "OMML" in prompt
    assert "tagged/associated with MathML" in prompt
    assert "do NOT recommend MathJax" in prompt
    assert "Do NOT recommend MathML alttext" in prompt
    assert "machine-readable" in prompt
    assert "low_resolution" in prompt
    assert "magnify" in prompt
    assert "joined_images" in prompt
    assert "repeats_surrounding_text" in prompt
    assert "wrong_language" in prompt
    assert "spelling_or_grammar" in prompt
    assert "screen-reader" in prompt
    assert "extended descriptions" in prompt.lower()
    assert "PDF and PowerPoint do not have that feature" in prompt


def test_vision_prompt_pdf_does_not_recommend_extended_descriptions() -> None:
    from checkmate.ai.alt_assess import build_vision_system_prompt

    prompt = build_vision_system_prompt(publication_format="pdf")
    assert "Publication format: PDF" in prompt
    assert "no extended-description feature" in prompt
    assert "aria-details" in prompt
    assert "tag/associate it with MathML" in prompt
    assert "Do not recommend EPUB MathML" in prompt


def test_vision_prompt_epub_recommends_extended_descriptions() -> None:
    from checkmate.ai.alt_assess import build_vision_system_prompt

    prompt = build_vision_system_prompt(publication_format="epub")
    assert "Publication format: EPUB" in prompt
    assert "extended description" in prompt.lower()
    assert "aria-details" in prompt
    assert "MathML in the EPUB" in prompt


def test_vision_prompt_ebraille_uses_epub_style_techniques() -> None:
    from checkmate.ai.alt_assess import build_vision_system_prompt

    prompt = build_vision_system_prompt(publication_format="ebrl")
    assert "Publication format: eBraille" in prompt
    assert "extended description" in prompt.lower()
    assert "MathML in the eBraille" in prompt


def test_vision_user_text_includes_publication_format() -> None:
    from checkmate.ai.alt_assess import build_vision_user_text

    image = AltExportImage(
        index=1,
        filename="fig.png",
        classification="Photograph",
        alt_text="A lake.",
        status="Has Alt Text",
    )
    text = build_vision_user_text(
        image, heuristic_flags=[], publication_format="epub"
    )
    assert "Publication format: EPUB" in text


def test_infer_publication_format_from_name() -> None:
    from checkmate.ai.alt_export import infer_publication_format

    assert infer_publication_format(document_name="Tide.pdf") == "pdf"
    assert infer_publication_format(document_name="book.epub") == "epub"
    assert infer_publication_format(document_name="book.ebrl") == "ebrl"
    assert infer_publication_format(explicit="docx") == "docx"
    assert infer_publication_format(document_name="notes") == "unknown"


def test_parse_vision_document_fit_issues() -> None:
    image = AltExportImage(
        index=8,
        filename="headshot.png",
        classification="Photograph",
        alt_text="Alanna spoke at the community meeting on Tuesday.",
        status="Has Alt Text",
        context="Alanna spoke at the community meeting on Tuesday.",
    )
    raw = """
    {
      "verdict": "needs_attention",
      "confidence": "high",
      "status_ok": true,
      "recommended_status": "has_alt",
      "descriptiveness": "good",
      "accuracy": "good",
      "usefulness": "weak",
      "issues": [
        "repeats_surrounding_text",
        "wrong_language",
        "spelling_or_grammar",
        "not_a_real_issue"
      ],
      "reason": "The alt copies the nearby sentence and is in the wrong language.",
      "teaching_note": "Describe what the photo shows; write it in the publication language.",
      "suggested_alt": null
    }
    """
    parsed = parse_vision_assessment(raw, image=image)
    assert parsed is not None
    assert parsed.issues == [
        "repeats_surrounding_text",
        "wrong_language",
        "spelling_or_grammar",
    ]


def test_document_fit_issues_are_needs_not_format() -> None:
    from checkmate.ai.alt_assess import AltImageAssessment
    from checkmate.ai.alt_report import _filter_bucket

    a = AltImageAssessment(
        index=1,
        filename="p.png",
        verdict="needs_attention",
        issues=["repeats_surrounding_text"],
    )
    assert _filter_bucket(a) == "needs"
    b = AltImageAssessment(
        index=2,
        filename="p.png",
        verdict="needs_attention",
        issues=["wrong_language"],
    )
    assert _filter_bucket(b) == "needs"
    c = AltImageAssessment(
        index=3,
        filename="p.png",
        verdict="needs_attention",
        issues=["spelling_or_grammar"],
    )
    assert _filter_bucket(c) == "needs"


def test_synthesis_prompt_mentions_document_fit_issues() -> None:
    from checkmate.ai.alt_assess import build_synthesis_system_prompt

    prompt = build_synthesis_system_prompt()
    assert "repeats_surrounding_text" in prompt
    assert "wrong_language" in prompt
    assert "spelling_or_grammar" in prompt
    assert "same language as the publication" in prompt
    assert "quotes text" in prompt


def test_synthesis_prompt_pdf_vs_epub_coaching() -> None:
    from checkmate.ai.alt_assess import build_synthesis_system_prompt

    pdf = build_synthesis_system_prompt(publication_format="pdf")
    epub = build_synthesis_system_prompt(publication_format="epub")
    assert "no extended-description feature" in pdf
    assert "extended description in the EPUB" in epub
    assert "Do not recommend EPUB extended-description" in pdf
    assert "Do not recommend EPUB extended-description" in epub


def test_format_issues_filter_bucket() -> None:
    from checkmate.ai.alt_assess import AltImageAssessment
    from checkmate.ai.alt_report import _filter_bucket

    a = AltImageAssessment(
        index=1,
        filename="t.png",
        verdict="needs_attention",
        issues=["image_of_table"],
    )
    assert _filter_bucket(a) == "format"
    b = AltImageAssessment(
        index=2,
        filename="o.png",
        verdict="needs_attention",
        issues=["likely_wrong_orientation"],
    )
    assert _filter_bucket(b) == "format"
    r = AltImageAssessment(
        index=4,
        filename="small.png",
        verdict="needs_attention",
        issues=["low_resolution"],
    )
    assert _filter_bucket(r) == "format"
    j = AltImageAssessment(
        index=5,
        filename="grid.png",
        verdict="needs_attention",
        issues=["joined_images"],
    )
    assert _filter_bucket(j) == "format"
    c = AltImageAssessment(
        index=3,
        filename="d.png",
        verdict="needs_attention",
        issues=["likely_content_marked_decorative"],
    )
    assert _filter_bucket(c) == "decorative"


def _joined_image(classification: str, **kwargs) -> AltExportImage:
    defaults = dict(
        index=1,
        filename="panel.png",
        classification=classification,
        alt_text="A figure combining several photographs of the same apparatus.",
        status="Has Alt Text",
        dimensions="1200x800",
    )
    defaults.update(kwargs)
    return AltExportImage(**defaults)


def test_joined_images_heuristic_matches_composite_panels() -> None:
    multi = _joined_image("Composite image / Multi-panel figure")
    assert looks_joined_panel(multi.classification)
    assert FLAG_JOINED_IMAGES in analyze_image(multi)

    grid = _joined_image("Photo grid or collage")
    assert looks_joined_panel(grid.classification)
    assert FLAG_JOINED_IMAGES in analyze_image(grid)

    parent = _joined_image("Composite image")
    assert FLAG_JOINED_IMAGES in analyze_image(parent)


def test_joined_images_heuristic_skips_artistic_and_insets() -> None:
    photo = _joined_image("Composite photograph")
    assert not looks_joined_panel(photo.classification)
    assert FLAG_JOINED_IMAGES not in analyze_image(photo)

    inset = _joined_image("Composite image / Figure with inset")
    assert not looks_joined_panel(inset.classification)
    assert FLAG_JOINED_IMAGES not in analyze_image(inset)


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
        reset_alt_export_cache_state,
    )

    clear_alt_export_cache()
    pub = tmp_path / "book.epub"
    pub.write_bytes(b"PK fake")
    export = _write_export(tmp_path / "export", _rows(2))
    assert get_cached_alt_export(pub) is None
    remember_alt_export(pub, export)
    assert get_cached_alt_export(pub) == export.resolve()
    # Survives process-style memory drop (index is on disk).
    reset_alt_export_cache_state()
    assert get_cached_alt_export(pub) == export.resolve()
    # Fingerprint change invalidates
    pub.write_bytes(b"PK fake2")
    assert get_cached_alt_export(pub) is None
    clear_alt_export_cache()


def test_alt_export_cache_rejects_old_format(tmp_path: Path) -> None:
    import json

    from checkmate.ai.alt_build_export import (
        _MANIFEST_NAME,
        get_cached_alt_export,
        remember_alt_export,
        reset_alt_export_cache_state,
    )

    pub = tmp_path / "book.epub"
    pub.write_bytes(b"PK fake")
    export = _write_export(tmp_path / "export", _rows(2))
    remember_alt_export(pub, export)
    manifest = export / _MANIFEST_NAME
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["format"] = 1  # older than CACHE_FORMAT
    manifest.write_text(json.dumps(data), encoding="utf-8")
    reset_alt_export_cache_state()
    assert get_cached_alt_export(pub) is None


def test_inventory_html_lightbox_uses_full_image(tmp_path: Path) -> None:
    from checkmate.doc_images.export import write_alt_text_html_report

    html_path = tmp_path / "alt_text_report.html"
    write_alt_text_html_report(
        html_path,
        doc_name="Demo.pdf",
        export_data=[
            {
                "index": 1,
                "filename": "image_0001.png",
                "thumb_filename": "image_0001.jpg",
                "alt_text": "A cat.",
                "status": "Has Alt Text",
                "is_decorative": False,
                "dimensions": "800x600",
                "file_size": "12 KB",
                "image_classification": "Photograph",
                "context": "",
            }
        ],
        stats={"total": 1, "with_alt_text": 1, "decorative": 0, "no_alt_text": 0},
        timestamp="20260815_120000",
    )
    text = html_path.read_text(encoding="utf-8")
    assert 'src="thumbs/image_0001.jpg"' in text
    assert 'data-full-src="images/image_0001.png"' in text
    assert "data-full-src" in text
    assert "img.getAttribute('data-full-src')" in text
    assert 'data-preview="' not in text


def test_inventory_webview_html_uses_host_preview(tmp_path: Path) -> None:
    from checkmate.ai.alt_export import inventory_webview_html

    folder = _write_export(tmp_path, _rows(2))
    html = inventory_webview_html(folder)
    assert 'href="https://checkmate.invalid/preview/1"' in html
    assert 'class="thumb-link"' in html
    assert "checkmate://preview/" not in html
    # Full-size 800px embeds used to bloat SetPage and blank the WebView.
    assert len(html) < 200_000


def test_inspector_html_has_filter_search_sort_and_model(tmp_path: Path) -> None:
    from checkmate.ai.alt_assess import AltAssessResult, AltImageAssessment
    from checkmate.ai.alt_export import AltExport, AltExportImage
    from checkmate.ai.alt_report import assessment_markdown_export, build_assessment_html

    img1 = tmp_path / "a.png"
    img2 = tmp_path / "zebra.png"
    img1.write_bytes(b"not-a-real-png")
    img2.write_bytes(b"not-a-real-png")
    export = AltExport(
        folder=tmp_path,
        document_name="Demo.pdf",
        publication_format="pdf",
        images=[
            AltExportImage(
                index=1,
                filename="a.png",
                classification="",
                alt_text="A cat.",
                status="Has Alt Text",
                image_path=img1,
            ),
            AltExportImage(
                index=2,
                filename="zebra.png",
                classification="",
                alt_text="",
                status="No Alt Text",
                image_path=img2,
            ),
        ],
    )
    result = AltAssessResult(
        ok=True,
        text="The document needs attention overall.",
        export=export,
        model="openai/gpt-4o",
        assessments=[
            AltImageAssessment(
                index=1,
                filename="a.png",
                verdict="ok",
                reason="Describes the photo.",
                issues=[],
            ),
            AltImageAssessment(
                index=2,
                filename="zebra.png",
                verdict="needs_attention",
                reason="Missing alt text.",
                issues=["missing_alt"],
            ),
        ],
    )
    html = build_assessment_html(result)
    assert 'data-full-src="a.png"' in html
    assert "AI Image Sniff Test" in html
    assert 'class="ai-note"' in html
    assert "This report was generated by CheckMate using AI (openai/gpt-4o)" in html
    assert "may contain mistakes!" in html
    assert 'id="search-box"' in html
    assert 'id="sort-box"' in html
    assert 'id="finding-cards"' in html
    assert "data-search=" in html
    assert "function sortCards" in html
    assert "Search findings" in html
    assert "Image number" in html
    assert "scrollLatestFollowup" not in html
    md = assessment_markdown_export(result)
    assert md.startswith("# AI Image Sniff Test")
    assert "## Note" in md
    assert "This report was generated by CheckMate using AI (openai/gpt-4o)" in md


def test_inspector_html_scrolls_followup_only_when_requested(tmp_path: Path) -> None:
    from checkmate.ai.alt_assess import AltAssessResult, AltImageAssessment
    from checkmate.ai.alt_export import AltExport, AltExportImage
    from checkmate.ai.markdown_html import append_followup_markdown
    from checkmate.ai.alt_report import build_assessment_html

    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    export = AltExport(
        folder=tmp_path,
        document_name="Demo.pdf",
        publication_format="pdf",
        images=[
            AltExportImage(
                index=1,
                filename="a.png",
                classification="",
                alt_text="A cat.",
                status="Has Alt Text",
                image_path=img,
            ),
        ],
    )
    text = append_followup_markdown(
        "Overall the sample looks fine.",
        heading="Follow-up",
        question="What about the logo?",
        answer="It is decorative.",
    )
    result = AltAssessResult(
        ok=True,
        text=text,
        export=export,
        assessments=[
            AltImageAssessment(
                index=1,
                filename="a.png",
                verdict="ok",
                reason="Describes the photo.",
                issues=[],
            ),
        ],
    )
    idle = build_assessment_html(result, for_dialog=True, scroll_followup=False)
    asked = build_assessment_html(result, for_dialog=True, scroll_followup=True)
    assert 'id="cm-latest-followup"' in idle
    assert "What about the logo?" in idle
    assert "scrollLatestFollowup" not in idle
    assert "scrollLatestFollowup" in asked
    assert "el.focus(" not in asked
    browser = build_assessment_html(result, for_dialog=False, scroll_followup=True)
    assert "scrollLatestFollowup" not in browser


def test_pass_a_low_resolution_is_merged_into_comment() -> None:
    from checkmate.ai.alt_assess import (
        AltImageAssessment,
        apply_pass_a_flags_to_assessment,
    )

    assessment = AltImageAssessment(
        index=6,
        filename="docx_image_6.png",
        verdict="needs_attention",
        issues=["inaccurate_alt", "image_of_math"],
        reason="The alt misstates the formula.",
        teaching_note="The alt text misstates the formula.",
    )
    merged = apply_pass_a_flags_to_assessment(assessment, ["low_resolution"])
    assert "low_resolution" in merged.issues
    assert "image_of_math" in merged.issues
    assert "magnification" in merged.teaching_note.lower()


def test_vision_parallel_workers_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from checkmate.ai.alt_assess import vision_parallel_workers

    monkeypatch.setattr("checkmate.settings.read_settings", lambda: {})
    assert vision_parallel_workers("openai/gpt-4o") == 8
    assert vision_parallel_workers("gemini/gemini-2.5-flash") == 8
    assert vision_parallel_workers("") == 8
    assert vision_parallel_workers("openai/gpt-4o", requested=9) == 9
    assert vision_parallel_workers("openai/gpt-4o", requested=99) == 16
    assert vision_parallel_workers("openai/gpt-4o", requested=0) == 1


def test_vision_parallel_workers_settings_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from checkmate.ai.alt_assess import vision_parallel_workers

    monkeypatch.setattr(
        "checkmate.settings.read_settings",
        lambda: {"ai_alt_assess_workers": 12},
    )
    assert vision_parallel_workers("gemini/gemini-2.5-flash") == 12
    monkeypatch.setattr(
        "checkmate.settings.read_settings",
        lambda: {"ai_alt_assess_workers": "nope"},
    )
    assert vision_parallel_workers("openai/gpt-4o") == 8


def test_format_vision_progress_dialog_reserves_lines() -> None:
    import sys

    from checkmate.ai.alt_assess import (
        _VISION_PROGRESS_LINE_COUNT,
        format_vision_progress_dialog,
    )

    body = format_vision_progress_dialog("Checking AI credentials…")
    if sys.platform != "win32":
        assert not body.startswith("\n")
    assert len(body.lstrip("\n").split("\n")) == _VISION_PROGRESS_LINE_COUNT
    assert format_vision_progress_dialog(body) == body


def test_format_vision_progress_dialog_no_lead_nl_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import checkmate.ai.alt_assess as alt_assess

    monkeypatch.setattr(alt_assess.sys, "platform", "darwin")
    body = alt_assess.format_vision_progress_dialog("Checking AI credentials…")
    assert not body.startswith("\n")
    assert body.startswith("Checking AI credentials…")


def test_is_rate_limit_error() -> None:
    from checkmate.ai.alt_assess import _is_rate_limit_error

    assert _is_rate_limit_error("provider_error", "Error 429 Too Many Requests")
    assert _is_rate_limit_error("provider_error", "RESOURCE_EXHAUSTED")
    assert not _is_rate_limit_error("timeout", "the request timed out")


def test_vision_progress_message_includes_verdicts_inflight_and_eta() -> None:
    from checkmate.ai.alt_assess import vision_progress_message

    start = vision_progress_message(
        done=0, total=20, inflight=["a.jpg", "b.jpg"], elapsed_s=0.1
    )
    assert "20" in start
    assert "Likely OK: 0" in start
    assert "Needs attention: 0" in start
    assert "OK with caveat: 0" in start
    assert "Uncertain: 0" in start
    assert "a.jpg" in start
    assert "Parallel workers" not in start
    assert start.count("\n") >= 5
    mid = vision_progress_message(
        done=5,
        total=20,
        inflight=["c.jpg"],
        elapsed_s=10.0,
        verdicts={"ok": 2, "needs_attention": 3},
    )
    assert "Finished 5 of 20 images." in mid
    assert "Likely OK: 2" in mid
    assert "Needs attention: 3" in mid
    assert "Est. time remaining:" in mid
    assert "c.jpg" in mid


def test_vision_batch_runs_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    from checkmate.ai.alt_assess import (
        AltExportImage,
        AltImageAssessment,
        _run_vision_batch,
    )
    from checkmate.ai.session import ExplainSession

    current = 0
    max_seen = 0
    lock = threading.Lock()

    def fake_assess(image, **_kwargs):
        nonlocal current, max_seen
        with lock:
            current += 1
            max_seen = max(max_seen, current)
        time.sleep(0.2)
        with lock:
            current -= 1
        return (
            AltImageAssessment(
                index=image.index, filename=image.filename, verdict="ok"
            ),
            None,
            False,
        )

    monkeypatch.setattr("checkmate.ai.alt_assess._assess_one_image", fake_assess)
    images = [
        AltExportImage(
            index=i,
            filename=f"image_{i:04d}.jpg",
            classification="",
            alt_text="A photo.",
            status="Has Alt Text",
        )
        for i in range(1, 9)
    ]
    by_index = {im.index: im for im in images}
    session = ExplainSession(model="openai/gpt-4o", api_key="k")
    started = time.perf_counter()
    assessments, fatal = _run_vision_batch(
        [im.index for im in images],
        by_index=by_index,
        findings_by_index={},
        pub_fmt="pdf",
        system="sys",
        session=session,
        cancel_event=None,
        status_callback=None,
        max_workers=4,
    )
    elapsed = time.perf_counter() - started
    assert fatal is None
    assert sorted(a.index for a in assessments) == list(range(1, 9))
    assert max_seen >= 3
    # Sequential 8×0.2s would be ~1.6s; four workers should finish much sooner.
    assert elapsed < 1.2


def test_vision_batch_cancel_skips_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    from checkmate.ai.alt_assess import (
        AltExportImage,
        AltImageAssessment,
        _run_vision_batch,
    )
    from checkmate.ai.session import ExplainSession

    started = 0
    lock = threading.Lock()
    cancel = threading.Event()

    def fake_assess(image, *, cancel_event, **_kwargs):
        nonlocal started
        with lock:
            started += 1
        for _ in range(40):
            if cancel_event is not None and cancel_event.is_set():
                return None, None, False
            time.sleep(0.02)
        return (
            AltImageAssessment(
                index=image.index, filename=image.filename, verdict="ok"
            ),
            None,
            False,
        )

    monkeypatch.setattr("checkmate.ai.alt_assess._assess_one_image", fake_assess)
    images = [
        AltExportImage(
            index=i,
            filename=f"image_{i:04d}.jpg",
            classification="",
            alt_text="A photo.",
            status="Has Alt Text",
        )
        for i in range(1, 7)
    ]
    by_index = {im.index: im for im in images}
    session = ExplainSession(model="openai/gpt-4o", api_key="k")

    def cancel_soon() -> None:
        time.sleep(0.05)
        cancel.set()

    threading.Thread(target=cancel_soon, daemon=True).start()
    t0 = time.perf_counter()
    assessments, fatal = _run_vision_batch(
        [im.index for im in images],
        by_index=by_index,
        findings_by_index={},
        pub_fmt="pdf",
        system="sys",
        session=session,
        cancel_event=cancel,
        status_callback=None,
        max_workers=2,
    )
    elapsed = time.perf_counter() - t0
    assert fatal is None
    assert started <= 3
    assert len(assessments) < 6
    assert elapsed < 1.0


def test_vision_batch_fatal_stops_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from checkmate.ai.alt_assess import (
        AltExportImage,
        AltImageAssessment,
        _error_assessment,
        _run_vision_batch,
    )
    from checkmate.ai.session import ExplainSession, ProviderError

    def fake_assess(image, **_kwargs):
        time.sleep(0.05)
        err = ProviderError("timeout", "provider timed out")
        return (
            _error_assessment(image, reason=err.detail, error=err.error_key),
            err,
            False,
        )

    monkeypatch.setattr("checkmate.ai.alt_assess._assess_one_image", fake_assess)
    images = [
        AltExportImage(
            index=i,
            filename=f"image_{i:04d}.jpg",
            classification="",
            alt_text="A photo.",
            status="Has Alt Text",
        )
        for i in range(1, 9)
    ]
    by_index = {im.index: im for im in images}
    session = ExplainSession(model="openai/gpt-4o", api_key="k")
    assessments, fatal = _run_vision_batch(
        [im.index for im in images],
        by_index=by_index,
        findings_by_index={},
        pub_fmt="pdf",
        system="sys",
        session=session,
        cancel_event=None,
        status_callback=None,
        max_workers=2,
    )
    assert fatal is not None
    assert fatal.error_key == "timeout"
    assert 1 <= len(assessments) <= 4
    assert all(isinstance(a, AltImageAssessment) for a in assessments)


def test_vision_batch_rate_limit_retries_and_reduces_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from checkmate.ai.alt_assess import (
        AltExportImage,
        AltImageAssessment,
        _error_assessment,
        _run_vision_batch,
    )
    from checkmate.ai.session import ExplainSession

    monkeypatch.setattr("checkmate.ai.alt_assess._RATE_LIMIT_BACKOFF_INITIAL_S", 0)
    monkeypatch.setattr("checkmate.ai.alt_assess._RATE_LIMIT_BACKOFF_MAX_S", 0)

    attempts: dict[int, int] = {}
    worker_notes: list[int] = []

    def fake_assess(image, **_kwargs):
        n = attempts.get(image.index, 0) + 1
        attempts[image.index] = n
        if n == 1:
            return (
                _error_assessment(
                    image, reason="429 Too Many Requests", error="provider_error"
                ),
                None,
                True,
            )
        return (
            AltImageAssessment(
                index=image.index, filename=image.filename, verdict="ok"
            ),
            None,
            False,
        )

    orig_set_workers = None

    def capture_workers(self, workers: int) -> None:
        worker_notes.append(workers)
        return orig_set_workers(self, workers)

    from checkmate.ai.alt_assess import _VisionProgress

    orig_set_workers = _VisionProgress.set_workers
    monkeypatch.setattr(_VisionProgress, "set_workers", capture_workers)
    monkeypatch.setattr("checkmate.ai.alt_assess._assess_one_image", fake_assess)
    images = [
        AltExportImage(
            index=i,
            filename=f"image_{i:04d}.jpg",
            classification="",
            alt_text="A photo.",
            status="Has Alt Text",
        )
        for i in range(1, 5)
    ]
    by_index = {im.index: im for im in images}
    session = ExplainSession(model="openai/gpt-4o", api_key="k")
    assessments, fatal = _run_vision_batch(
        [im.index for im in images],
        by_index=by_index,
        findings_by_index={},
        pub_fmt="pdf",
        system="sys",
        session=session,
        cancel_event=None,
        status_callback=None,
        max_workers=4,
    )
    assert fatal is None
    assert sorted(a.index for a in assessments) == [1, 2, 3, 4]
    assert all(a.verdict == "ok" for a in assessments)
    assert all(attempts[i] == 2 for i in range(1, 5))
    assert worker_notes and worker_notes[0] == 2
