"""Load Fido image_report.json and run the CLI wrapper (mocked subprocess)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from checkmate.ai.fido_image_report import (
    HTML_NAME,
    IMAGE_REPORT_JSON,
    format_verdict_tally_spoken,
    image_report_ai_sample_choices,
    image_report_mode_choices,
    load_image_report,
    run_fido_image_report,
    sample_percent_choices,
    save_image_report_qa,
    supports_image_report_path,
)


def _write_report(folder: Path, *, with_ai: bool = False) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / HTML_NAME).write_text("<html><body>ok</body></html>", encoding="utf-8")
    images = [
        {
            "index": 1,
            "filename": "cover.png",
            "alt_text": "Cover",
            "status": "has alt",
            "heuristics": {"flags": ["filename_as_alt"]},
        },
        {
            "index": 2,
            "filename": "deco.png",
            "alt_text": "",
            "status": "decorative",
        },
    ]
    if with_ai:
        images[0]["assessment"] = {
            "verdict": "ok",
            "reason": "Describes the cover.",
            "issues": [],
        }
        images[1]["assessment"] = {
            "verdict": "needs_attention",
            "reason": "May not be decorative.",
            "issues": ["decorative_mismatch"],
        }
    payload = {
        "schema_version": 1,
        "document": "book.epub",
        "publication_format": "epub",
        "images": images,
        "counts": {"total": 2, "with_alt": 1, "decorative": 1, "missing": 0},
        "synthesis_markdown": "Looks mostly fine." if with_ai else "",
        "sample": {"percent": 25} if with_ai else None,
    }
    (folder / IMAGE_REPORT_JSON).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_supports_image_report_path(tmp_path: Path) -> None:
    epub = tmp_path / "a.epub"
    epub.write_bytes(b"PK")
    html = tmp_path / "a.html"
    html.write_text("<html></html>", encoding="utf-8")
    assert supports_image_report_path(epub) is True
    assert supports_image_report_path(html) is False
    assert supports_image_report_path(tmp_path) is False


def test_load_image_report_tally(tmp_path: Path) -> None:
    _write_report(tmp_path, with_ai=True)
    report = load_image_report(tmp_path)
    assert report.total == 2
    assert report.document_name == "book.epub"
    assert report.publication_format == "epub"
    counts = report.counts()
    assert counts["with_alt"] == 1
    assert counts["decorative"] == 1
    tally = report.verdict_tally()
    assert tally["ok"] == 1
    assert tally["needs_attention"] == 1
    spoken = format_verdict_tally_spoken(tally)
    assert "Likely OK" in spoken or "1" in spoken
    brief = report.qa_context_brief()
    assert "cover.png" in brief
    assert report.sample_is_partial() is True


def test_save_image_report_qa_preserves_other_fields(tmp_path: Path) -> None:
    _write_report(tmp_path, with_ai=True)
    assert save_image_report_qa(tmp_path, "Why is this decorative?\n\nBecause.")
    loaded = load_image_report(tmp_path)
    assert loaded.qa_markdown.startswith("Why is this decorative?")
    assert loaded.synthesis_markdown == "Looks mostly fine."
    assert loaded.document_name == "book.epub"
    raw = json.loads((tmp_path / IMAGE_REPORT_JSON).read_text(encoding="utf-8"))
    assert raw["counts"]["total"] == 2


def test_sample_percent_choices_small() -> None:
    rows = sample_percent_choices(10)
    assert len(rows) == 1
    assert rows[0][1] is None


def test_image_report_mode_choices() -> None:
    rows = image_report_mode_choices()
    assert len(rows) == 2
    assert rows[0] == (rows[0][0], False)
    assert rows[1][1] is True
    samples = image_report_ai_sample_choices()
    assert any(pct == 25 for _label, pct in samples)
    assert any(pct is None for _label, pct in samples)


def _stub_fido_cli(monkeypatch, tmp_path: Path, *, supported: bool = True) -> None:
    exe = str(tmp_path / "Fido.exe")
    monkeypatch.setattr("checkmate.ai.fido_image_report.find_fido_app", lambda: exe)
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report.fido_cli_command", lambda: [exe]
    )
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report.fido_supports_image_report_cli",
        lambda *a, **k: supported,
    )


def test_run_fido_uses_cache(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "book.epub"
    src.write_bytes(b"PK")
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report.image_report_cache_dir", lambda: cache
    )
    _stub_fido_cli(monkeypatch, tmp_path)

    calls = {"n": 0}

    def fake_run(argv, *, cancel_event, progress):
        calls["n"] += 1
        dest = Path(argv[argv.index("--output") + 1])
        dest.mkdir(parents=True, exist_ok=True)
        _write_report(dest)
        return 0, "ok"

    monkeypatch.setattr("checkmate.ai.fido_image_report._run_fido_process", fake_run)
    first = run_fido_image_report(src)
    second = run_fido_image_report(src)
    assert first.from_cache is False
    assert second.from_cache is True
    assert calls["n"] == 1
    assert first.html_path.is_file()


def test_run_fido_maps_exit_code(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "book.pdf"
    src.write_bytes(b"%PDF")
    _stub_fido_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report._run_fido_process",
        lambda *a, **k: (2, "bad input"),
    )
    from checkmate.ai.fido_image_report import FidoImageReportError

    try:
        run_fido_image_report(src, use_cache=False)
        raise AssertionError("expected FidoImageReportError")
    except FidoImageReportError as exc:
        assert exc.exit_code == 2
        assert "EPUB" in str(exc) or "PDF" in str(exc)


def test_run_fido_skips_old_install_without_cli(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "book.epub"
    src.write_bytes(b"PK")
    _stub_fido_cli(monkeypatch, tmp_path, supported=False)
    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        return 0, "ok"

    monkeypatch.setattr("checkmate.ai.fido_image_report._run_fido_process", fake_run)
    from checkmate.ai.fido_image_report import FidoImageReportError

    try:
        run_fido_image_report(src, use_cache=False)
        raise AssertionError("expected FidoImageReportError")
    except FidoImageReportError as exc:
        assert "Update Fido" in str(exc)
    assert called["n"] == 0


def test_run_fido_maps_already_running_to_unsupported(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "book.pdf"
    src.write_bytes(b"%PDF")
    _stub_fido_cli(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report._run_fido_process",
        lambda *a, **k: (1, "The FidoApp is already running."),
    )
    from checkmate.ai.fido_image_report import FidoImageReportError

    try:
        run_fido_image_report(src, use_cache=False)
        raise AssertionError("expected FidoImageReportError")
    except FidoImageReportError as exc:
        assert "Update Fido" in str(exc)

def test_load_image_report_accepts_alt_alias(tmp_path: Path) -> None:
    folder = tmp_path / "rep"
    folder.mkdir()
    (folder / HTML_NAME).write_text("<html></html>", encoding="utf-8")
    payload = {
        "document": "book.epub",
        "publication_format": "epub",
        "images": [
            {"index": 1, "filename": "cover.png", "alt": "Cover photo", "status": "has alt"}
        ],
    }
    (folder / IMAGE_REPORT_JSON).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    report = load_image_report(folder)
    assert report.images[0].alt_text == "Cover photo"
    assert report.counts()["with_alt"] == 1


def test_sanitize_cli_progress_keeps_newlines() -> None:
    from checkmate.ai.fido_image_report import (
        PROGRESS_LINE_COUNT,
        PROGRESS_LINE_MAX_CHARS,
        pad_progress_message,
        progress_speech_text,
        sanitize_cli_progress,
    )

    assert sanitize_cli_progress("a\n\nb") == "a\n\nb"
    assert sanitize_cli_progress("line1\u2028line2") == "line1\nline2"
    assert sanitize_cli_progress("ok\ufffd!") == "ok!"
    assert sanitize_cli_progress("Assessing\u2026") == "Assessing..."
    padded = pad_progress_message("hello\nworld")
    assert padded.startswith("hello\nworld")
    assert padded.count("\n") == PROGRESS_LINE_COUNT - 1
    assert progress_speech_text("hello\nworld") == "hello. world"
    spoken = (
        "Finished 3 of 23 images .. Likely OK: 3 Needs attention: 0. "
        "OK with caveat: 0 Uncertain: 0. Est. time remaining: about a minute"
    )
    lines = sanitize_cli_progress(spoken).split("\n")
    assert lines[0].startswith("Finished 3 of 23 images")
    assert lines[1].startswith("Likely OK:")
    assert "Needs attention:" in lines[1]
    assert lines[2].startswith("OK with caveat:")
    assert lines[3].startswith("Est. time remaining:")
    long = "Starting AI image analysis " + ("workers-and-paths " * 20)
    padded_long = pad_progress_message(long)
    for line in padded_long.split("\n"):
        assert len(line) <= PROGRESS_LINE_MAX_CHARS


def test_peek_cached_ignores_other_session(tmp_path: Path, monkeypatch) -> None:
    from checkmate.ai.fido_image_report import peek_cached_image_report

    src = tmp_path / "book.epub"
    src.write_bytes(b"PK")
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report.image_report_cache_dir", lambda: cache
    )
    _stub_fido_cli(monkeypatch, tmp_path)

    def fake_run(argv, *, cancel_event, progress):
        dest = Path(argv[argv.index("--output") + 1])
        dest.mkdir(parents=True, exist_ok=True)
        _write_report(dest)
        return 0, "ok"

    monkeypatch.setattr("checkmate.ai.fido_image_report._run_fido_process", fake_run)
    first = run_fido_image_report(src)
    assert first.from_cache is False
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report._PROCESS_SESSION", "other-process"
    )
    assert peek_cached_image_report(src) is None
    second = run_fido_image_report(src)
    assert second.from_cache is False


def test_run_fido_rebuilds_when_cache_disabled(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "book.epub"
    src.write_bytes(b"PK")
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report.image_report_cache_dir", lambda: cache
    )
    _stub_fido_cli(monkeypatch, tmp_path)
    calls = {"n": 0, "outputs": []}

    def fake_run(argv, *, cancel_event, progress):
        calls["n"] += 1
        dest = Path(argv[argv.index("--output") + 1])
        calls["outputs"].append(dest)
        dest.mkdir(parents=True, exist_ok=True)
        _write_report(dest)
        return 0, "ok"

    monkeypatch.setattr("checkmate.ai.fido_image_report._run_fido_process", fake_run)
    first = run_fido_image_report(src)
    again = run_fido_image_report(src, use_cache=False)
    assert again.from_cache is False
    assert calls["n"] == 2
    assert calls["outputs"][0] != calls["outputs"][1]
    assert first.folder != again.folder


def test_run_fido_uses_fresh_output_per_document(tmp_path: Path, monkeypatch) -> None:
    first_src = tmp_path / "one.epub"
    second_src = tmp_path / "two.epub"
    first_src.write_bytes(b"PK")
    second_src.write_bytes(b"PK")
    cache = tmp_path / "cache"
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report.image_report_cache_dir", lambda: cache
    )
    _stub_fido_cli(monkeypatch, tmp_path)
    outputs: list[Path] = []

    def fake_run(argv, *, cancel_event, progress):
        dest = Path(argv[argv.index("--output") + 1])
        outputs.append(dest)
        dest.mkdir(parents=True, exist_ok=True)
        _write_report(dest)
        return 0, "ok"

    monkeypatch.setattr("checkmate.ai.fido_image_report._run_fido_process", fake_run)
    run_fido_image_report(first_src)
    run_fido_image_report(second_src)
    assert len(outputs) == 2
    assert outputs[0] != outputs[1]
    assert outputs[0].parent == cache
    assert outputs[1].parent == cache
