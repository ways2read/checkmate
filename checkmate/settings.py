"""Persisted UI preferences helpers (language is written via i18n)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_data_dir


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def read_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def update_settings(**kwargs: Any) -> None:
    path = settings_path()
    data = read_settings()
    data.update(kwargs)
    # Drop obsolete keys from earlier builds.
    data.pop("select_result_on_focus", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def ai_features_enabled() -> bool:
    """True when FIDO AI is available and the user has not turned it off.

    The preference defaults to on when AI is available. Used for training so
    the UI can match a no-AI install without removing FIDO settings.
    """
    from .fido_settings import fido_settings_present

    if not fido_settings_present():
        return False
    return bool(read_settings().get("ai_features_enabled", True))


def show_issues_always() -> bool:
    """True when the Issues list should open automatically after a check."""
    return bool(read_settings().get("show_issues_always", False))
