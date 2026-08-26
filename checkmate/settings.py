"""Persisted UI preferences helpers (language is written via i18n)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_data_dir

# veraPDF --flavour values CheckMate exposes in Settings.
# PDF/UA first (accessibility default), then PDF/A (archival), then WTPDF.
VERAPDF_FLAVOURS: tuple[str, ...] = (
    "ua2",
    "ua1",
    "1a",
    "1b",
    "2a",
    "2b",
    "2u",
    "3a",
    "3b",
    "3u",
    "4",
    "4e",
    "4f",
    "wt1a",
    "wt1r",
)
VERAPDF_FLAVOUR_LABELS: dict[str, str] = {
    "ua2": "PDF/UA-2",
    "ua1": "PDF/UA-1",
    "1a": "PDF/A-1a",
    "1b": "PDF/A-1b",
    "2a": "PDF/A-2a",
    "2b": "PDF/A-2b",
    "2u": "PDF/A-2u",
    "3a": "PDF/A-3a",
    "3b": "PDF/A-3b",
    "3u": "PDF/A-3u",
    "4": "PDF/A-4",
    "4e": "PDF/A-4e",
    "4f": "PDF/A-4f",
    "wt1a": "WTPDF 1.0 Accessibility",
    "wt1r": "WTPDF 1.0 Reuse",
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

# MathML quality pass (Nordic guidelines). Off: Nu schema only.
DEFAULT_MATHML_NORDIC_GUIDELINES = False

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


def chat_pane_shown() -> bool:
    """Whether the native conversation pane starts split (Hide/Show)."""
    return bool(read_settings().get("chat_pane_shown", False))


DEFAULT_CHAT_PANE_WIDTH = 340
MIN_CHAT_PANE_WIDTH = 200
MAX_CHAT_PANE_WIDTH = 2400

# AI overview / image report: first-open size as a fraction of the work area.
# Ultrawide (≈21:9 and wider) uses a smaller width fraction so the dialog
# does not span the whole desk.
MIN_WEBVIEW_CHAT_DIALOG_WIDTH = 720
MIN_WEBVIEW_CHAT_DIALOG_HEIGHT = 520
ULTRAWIDE_ASPECT_RATIO = 2.0
WEBVIEW_CHAT_DIALOG_WIDTH_FRACTION = 0.75
WEBVIEW_CHAT_DIALOG_ULTRAWIDE_WIDTH_FRACTION = 0.50
WEBVIEW_CHAT_DIALOG_HEIGHT_FRACTION = 0.75


def chat_pane_width() -> int:
    """Width in pixels of the native conversation pane (right splitter)."""
    try:
        raw = int(read_settings().get("chat_pane_width", DEFAULT_CHAT_PANE_WIDTH) or DEFAULT_CHAT_PANE_WIDTH)
    except (TypeError, ValueError):
        raw = DEFAULT_CHAT_PANE_WIDTH
    return max(MIN_CHAT_PANE_WIDTH, min(raw, MAX_CHAT_PANE_WIDTH))


def set_chat_pane_width(px: int) -> None:
    update_settings(
        chat_pane_width=max(MIN_CHAT_PANE_WIDTH, min(int(px), MAX_CHAT_PANE_WIDTH))
    )


def default_webview_chat_dialog_size(work_w: int, work_h: int) -> tuple[int, int]:
    """First-open size: ¾ of the work area, or ½ width on ultrawide displays."""
    width = max(int(work_w), 1)
    height = max(int(work_h), 1)
    aspect = width / height
    frac = (
        WEBVIEW_CHAT_DIALOG_ULTRAWIDE_WIDTH_FRACTION
        if aspect >= ULTRAWIDE_ASPECT_RATIO
        else WEBVIEW_CHAT_DIALOG_WIDTH_FRACTION
    )
    w = min(width, max(MIN_WEBVIEW_CHAT_DIALOG_WIDTH, int(width * frac)))
    h = min(
        height,
        max(MIN_WEBVIEW_CHAT_DIALOG_HEIGHT, int(height * WEBVIEW_CHAT_DIALOG_HEIGHT_FRACTION)),
    )
    return w, h


def webview_chat_dialog_size(kind: str) -> tuple[int, int] | None:
    """Saved AI overview / image report size, or None to use the display default."""
    key = str(kind or "").strip() or "overview"
    data = read_settings()
    try:
        width = int(data.get(f"{key}_dialog_width") or 0)
        height = int(data.get(f"{key}_dialog_height") or 0)
    except (TypeError, ValueError):
        return None
    if width < 200 or height < 200:
        return None
    return width, height


def set_webview_chat_dialog_size(kind: str, width: int, height: int) -> None:
    key = str(kind or "").strip() or "overview"
    update_settings(
        **{
            f"{key}_dialog_width": max(200, int(width)),
            f"{key}_dialog_height": max(200, int(height)),
        }
    )


def set_chat_pane_shown(shown: bool) -> None:
    update_settings(chat_pane_shown=bool(shown))


def include_chat_in_html_report() -> bool:
    """Whether Open in browser / Save as HTML include the conversation."""
    return bool(read_settings().get("include_chat_in_html_report", True))


def set_include_chat_in_html_report(include: bool) -> None:
    update_settings(include_chat_in_html_report=bool(include))


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
    """Selected veraPDF validation flavour (PDF/UA, PDF/A, or WTPDF; default UA-2)."""
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


def mathml_nordic_guidelines() -> bool:
    """True when MathML/HTML checks run the Nordic MathML quality pass."""
    return bool(
        read_settings().get(
            "mathml_nordic_guidelines", DEFAULT_MATHML_NORDIC_GUIDELINES
        )
    )
