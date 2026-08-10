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

# Which checkers run for packaged/exploded EPUB (not eBraille).
EPUB_CHECKERS: tuple[str, ...] = ("both", "epubcheck", "ace")
EPUB_CHECKERS_LABELS: dict[str, str] = {
    "both": "EPUBCheck + Ace",
    "epubcheck": "EPUBCheck only",
    "ace": "Ace only",
}
DEFAULT_EPUB_CHECKERS = "both"


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
    data.pop("epubcheck_profile", None)
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


def single_instance_enabled() -> bool:
    """True when a second CheckMate launch should focus the existing window.

    Default on: avoids conflicting Fix/apply edits and settings races when
    two copies would otherwise run at once.
    """
    return bool(read_settings().get("single_instance", True))


def verapdf_flavour() -> str:
    """Selected veraPDF validation flavour (``ua1`` or ``ua2``; default UA-2)."""
    value = str(read_settings().get("verapdf_flavour", DEFAULT_VERAPDF_FLAVOUR)).strip()
    return value if value in VERAPDF_FLAVOURS else DEFAULT_VERAPDF_FLAVOUR


def verapdf_flavour_label(flavour: str | None = None) -> str:
    """Human label for a veraPDF flavour (e.g. ``PDF/UA-2``)."""
    key = flavour if flavour is not None else verapdf_flavour()
    return VERAPDF_FLAVOUR_LABELS.get(key, key)


def epub_checkers() -> str:
    """Selected EPUB checker mode (``both``, ``epubcheck``, or ``ace``)."""
    value = str(read_settings().get("epub_checkers", DEFAULT_EPUB_CHECKERS)).strip()
    return value if value in EPUB_CHECKERS else DEFAULT_EPUB_CHECKERS


def epub_checkers_label(mode: str | None = None) -> str:
    """Human label for an EPUB checker mode (e.g. ``EPUBCheck + Ace``)."""
    key = mode if mode is not None else epub_checkers()
    return EPUB_CHECKERS_LABELS.get(key, key)
