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

# Which checkers run for HTML files / folders / URLs.
HTML_CHECKERS: tuple[str, ...] = ("both", "vnu", "axe")
HTML_CHECKERS_LABELS: dict[str, str] = {
    "both": "Nu HTML Checker + axe",
    "vnu": "Nu HTML Checker only",
    "axe": "axe only",
}
DEFAULT_HTML_CHECKERS = "both"

# Follow same-site links when checking HTML (capped). Off: start page only.
DEFAULT_HTML_FOLLOW_LINKS = False

# Check completion chimes (files: check-started/passed/failed-{n}.wav).
SOUND_SCHEMES: tuple[str, ...] = ("1", "2", "off")
SOUND_SCHEME_LABELS: dict[str, str] = {
    "1": "Sound scheme 1",
    "2": "Sound scheme 2",
    "off": "Sounds off",
}
DEFAULT_SOUND_SCHEME = "1"

# Light / dark / follow the OS (Tools → Settings…).
COLOR_THEMES: tuple[str, ...] = ("system", "light", "dark")
COLOR_THEME_LABELS: dict[str, str] = {
    "system": "System",
    "light": "Light",
    "dark": "Dark",
}
DEFAULT_COLOR_THEME = "system"


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
    # Persist legacy sounds_enabled as sound_scheme before dropping the old key.
    if (
        "sound_scheme" not in kwargs
        and "sound_scheme" not in data
        and "sounds_enabled" in data
    ):
        kwargs = {
            **kwargs,
            "sound_scheme": (
                DEFAULT_SOUND_SCHEME if data.get("sounds_enabled", True) else "off"
            ),
        }
    data.update(kwargs)
    # Drop obsolete keys from earlier builds.
    data.pop("select_result_on_focus", None)
    data.pop("epubcheck_profile", None)
    data.pop("sounds_enabled", None)
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


def color_theme() -> str:
    """Selected UI color theme (``system``, ``light``, or ``dark``)."""
    from .ui_appearance import normalize_color_theme

    return normalize_color_theme(
        read_settings().get("ui_color_theme", DEFAULT_COLOR_THEME)
    )


def color_theme_label(theme: str | None = None) -> str:
    """Human label for a color theme (e.g. ``System``)."""
    key = theme if theme is not None else color_theme()
    return COLOR_THEME_LABELS.get(key, key)


def sound_scheme() -> str:
    """Selected UI sound scheme (``1``, ``2``, or ``off``).

    Defaults to scheme 1. Migrates the legacy ``sounds_enabled`` boolean:
    False → ``off``, True/missing → ``1``.
    """
    data = read_settings()
    value = str(data.get("sound_scheme", "")).strip()
    if value in SOUND_SCHEMES:
        return value
    if "sounds_enabled" in data:
        return DEFAULT_SOUND_SCHEME if data.get("sounds_enabled", True) else "off"
    return DEFAULT_SOUND_SCHEME


def sound_scheme_label(scheme: str | None = None) -> str:
    """Human label for a sound scheme (e.g. ``Sound scheme 1``)."""
    key = scheme if scheme is not None else sound_scheme()
    return SOUND_SCHEME_LABELS.get(key, key)


def sounds_enabled() -> bool:
    """True when a sound scheme other than ``off`` is selected."""
    return sound_scheme() != "off"


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


def html_checkers() -> str:
    """Selected HTML checker mode (``both``, ``vnu``, or ``axe``)."""
    value = str(read_settings().get("html_checkers", DEFAULT_HTML_CHECKERS)).strip()
    return value if value in HTML_CHECKERS else DEFAULT_HTML_CHECKERS


def html_checkers_label(mode: str | None = None) -> str:
    """Human label for an HTML checker mode (e.g. ``Nu HTML Checker + axe``)."""
    key = mode if mode is not None else html_checkers()
    return HTML_CHECKERS_LABELS.get(key, key)


def html_follow_links() -> bool:
    """True when HTML checks should follow same-site links (up to the crawl cap)."""
    return bool(read_settings().get("html_follow_links", DEFAULT_HTML_FOLLOW_LINKS))
