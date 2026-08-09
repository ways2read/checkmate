"""Persisted UI preferences helpers (language is written via i18n)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_data_dir

# veraPDF --flavour values CheckMate exposes in Settings.
VERAPDF_FLAVOURS: tuple[str, ...] = ("ua2", "ua1")
VERAPDF_FLAVOUR_LABELS: dict[str, str] = {
    "ua2": "PDF/UA-2",
    "ua1": "PDF/UA-1",
}
DEFAULT_VERAPDF_FLAVOUR = "ua2"

# EPUBCheck --profile values (omit flag when "default").
EPUBCHECK_PROFILES: tuple[str, ...] = (
    "default",
    "dict",
    "edupub",
    "idx",
    "preview",
)
DEFAULT_EPUBCHECK_PROFILE = "default"


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


def ai_send_kb_article_body() -> bool:
    """True when Explain/Fix may include the offline DAISY KB article body.

    Off by default: article text increases token use. Useful for evaluating
    whether full-article context improves Ace (and related) AI output.
    """
    return bool(read_settings().get("ai_send_kb_article_body", False))


def ai_translation_warning_shown() -> bool:
    """True after the one-time non-English AI translation warning was shown."""
    return bool(read_settings().get("ai_translation_warning_shown", False))


def mark_ai_translation_warning_shown() -> None:
    """Persist that the one-time AI translation warning has been shown."""
    update_settings(ai_translation_warning_shown=True)


def show_issues_always() -> bool:
    """True when the Issues list should open automatically after a check."""
    return bool(read_settings().get("show_issues_always", False))


def sounds_enabled() -> bool:
    """True when check completion sound effects should play (default on)."""
    return bool(read_settings().get("sounds_enabled", True))


def verapdf_flavour() -> str:
    """Selected veraPDF validation flavour (``ua1`` or ``ua2``; default UA-2)."""
    value = str(read_settings().get("verapdf_flavour", DEFAULT_VERAPDF_FLAVOUR)).strip()
    return value if value in VERAPDF_FLAVOURS else DEFAULT_VERAPDF_FLAVOUR


def verapdf_flavour_label(flavour: str | None = None) -> str:
    """Human label for a veraPDF flavour (e.g. ``PDF/UA-2``)."""
    key = flavour if flavour is not None else verapdf_flavour()
    return VERAPDF_FLAVOUR_LABELS.get(key, key)


def epubcheck_profile() -> str:
    """Selected EPUBCheck ``--profile`` (default ``default``)."""
    value = str(
        read_settings().get("epubcheck_profile", DEFAULT_EPUBCHECK_PROFILE)
    ).strip()
    return value if value in EPUBCHECK_PROFILES else DEFAULT_EPUBCHECK_PROFILE
