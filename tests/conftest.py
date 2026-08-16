"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_alt_export_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep export-cache index writes out of the user's AppData during tests."""
    cache = tmp_path / "_alt_export_cache"
    cache.mkdir()
    monkeypatch.setattr(
        "checkmate.ai.alt_build_export.alt_export_cache_dir",
        lambda: cache,
    )
    from checkmate.ai.alt_build_export import reset_alt_export_cache_state

    reset_alt_export_cache_state()
