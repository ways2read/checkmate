"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_image_report_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Fido image-report cache writes out of the user's AppData during tests."""
    cache = tmp_path / "_image_report_cache"
    cache.mkdir()
    monkeypatch.setattr(
        "checkmate.ai.fido_image_report.image_report_cache_dir",
        lambda: cache,
    )
