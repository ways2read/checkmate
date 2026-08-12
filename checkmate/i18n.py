"""UI language support for CheckMate."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from .paths import app_data_dir, is_frozen
from .settings import read_settings, update_settings

# BCP 47-ish codes used in settings and menus (shipped non-English: fr, es, ar, ru, ja)
LANG_EN = "en"
LANG_FR = "fr"
LANG_ES = "es"
LANG_AR = "ar"
LANG_RU = "ru"
LANG_JA = "ja"

DEFAULT_LANGUAGE = LANG_EN

TEXT_DIRECTION_LTR = "ltr"
TEXT_DIRECTION_RTL = "rtl"
VALID_DIRECTIONS = frozenset({TEXT_DIRECTION_LTR, TEXT_DIRECTION_RTL})

CUSTOM_I18N_FORMAT = "checkmate-ui-i18n"
CUSTOM_I18N_VERSION = 2

# Populated from packaged + overlay catalogs (English always present).
LANGUAGES: dict[str, str] = {LANG_EN: "English"}
LANGUAGE_DISPLAY_NAMES: dict[str, str] = {LANG_EN: "English"}

# Snapshot aliases kept for callers that still import these names.
BUILTIN_LANGUAGES: dict[str, str] = {LANG_EN: "English"}
BUILTIN_DISPLAY_NAMES: dict[str, str] = {LANG_EN: "English"}

# English msgid → translation. Missing keys fall back to English.
_TRANSLATIONS: dict[str, dict[str, str]] = {}
_catalog_directions: dict[str, str] = {}
_shipped_codes: set[str] = set()
_overlay_codes: set[str] = set()
# Non-shipped codes currently registered from AppData overlays.
_custom_languages: dict[str, str] = {}
_custom_display_names: dict[str, str] = {}

_current_language = DEFAULT_LANGUAGE
_catalogs_loaded = False


def packaged_locales_dir() -> Path:
    """Directory with shipped ``*.json`` UI catalogs."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass is not None:
            bundled = Path(meipass) / "checkmate" / "locales"
            if bundled.is_dir():
                return bundled
            alt = Path(meipass) / "locales"
            if alt.is_dir():
                return alt
        beside = Path(sys.executable).resolve().parent / "locales"
        if beside.is_dir():
            return beside
    return Path(__file__).resolve().parent / "locales"


def custom_i18n_dir() -> Path:
    return app_data_dir() / "i18n"


def custom_catalog_path(code: str) -> Path:
    safe = _normalize_lang_code(code) or "xx"
    return custom_i18n_dir() / f"{safe}.json"


def overlay_catalog_path(code: str) -> Path:
    return custom_catalog_path(code)


def packaged_catalog_path(code: str) -> Path:
    safe = _normalize_lang_code(code) or "xx"
    return packaged_locales_dir() / f"{safe}.json"


def _normalize_lang_code(code: str) -> str:
    raw = (code or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    parts = [p for p in raw.split("-") if p]
    if not parts or not re.fullmatch(r"[a-z]{2,3}", parts[0]):
        return ""
    out = [parts[0]]
    for p in parts[1:3]:
        if re.fullmatch(r"[a-z0-9]{2,8}", p):
            out.append(p)
    return "-".join(out)


def _normalize_direction(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in VALID_DIRECTIONS:
        return raw
    return TEXT_DIRECTION_LTR


def is_shipped_language(code: str) -> bool:
    ensure_catalogs_loaded()
    return _normalize_lang_code(code) in _shipped_codes


def is_builtin_language(code: str) -> bool:
    """Compatibility alias: shipped non-English (+ English registry)."""
    c = _normalize_lang_code(code)
    if c == LANG_EN:
        return True
    return is_shipped_language(c)


def is_custom_language(code: str) -> bool:
    """True when an AppData overlay exists for a non-shipped code."""
    ensure_catalogs_loaded()
    return _normalize_lang_code(code) in _custom_languages


def has_overlay(code: str) -> bool:
    ensure_catalogs_loaded()
    return _normalize_lang_code(code) in _overlay_codes


def is_registered_language(code: str) -> bool:
    ensure_catalogs_loaded()
    c = _normalize_lang_code(code)
    return c == LANG_EN or c in _TRANSLATIONS


def hidden_language_codes() -> list[str]:
    raw = read_settings().get("hidden_languages") or []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        c = _normalize_lang_code(str(item))
        if not c or c == LANG_EN or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def is_language_hidden(code: str) -> bool:
    c = _normalize_lang_code(code)
    return bool(c) and c != LANG_EN and c in set(hidden_language_codes())


def hide_language(code: str) -> None:
    c = _normalize_lang_code(code)
    if not c or c == LANG_EN:
        raise ValueError("cannot_hide")
    if not is_registered_language(c):
        raise ValueError("not_found")
    hidden = hidden_language_codes()
    if c not in hidden:
        hidden.append(c)
        update_settings(hidden_languages=hidden)
    if get_language() == c:
        save_language(DEFAULT_LANGUAGE)


def unhide_language(code: str) -> None:
    c = _normalize_lang_code(code)
    if not c:
        return
    hidden = [x for x in hidden_language_codes() if x != c]
    update_settings(hidden_languages=hidden)


def language_menu_label(code: str) -> str:
    """Native name for menus, with Latin/English display when it differs."""
    ensure_catalogs_loaded()
    c = _normalize_lang_code(code)
    if not c or c == LANG_EN:
        return LANGUAGES.get(LANG_EN, "English")
    native = language_native_name(c)
    display = language_display_name(c)
    if display and display.casefold() != native.casefold():
        return f"{native} ({display})"
    return native


def effective_languages() -> dict[str, str]:
    """Visible languages for the Language menu (English first, then others)."""
    ensure_catalogs_loaded()
    hidden = set(hidden_language_codes())
    out: dict[str, str] = {LANG_EN: language_menu_label(LANG_EN)}
    others = [
        (code, language_menu_label(code), language_display_name(code))
        for code in LANGUAGES
        if code != LANG_EN and code not in hidden
    ]
    # Sort by Latin/English display so script-only natives stay findable.
    others.sort(key=lambda item: item[2].casefold())
    for code, label, _display in others:
        out[code] = label
    return out


def manageable_language_codes() -> list[str]:
    """Non-English languages currently visible in the Language menu."""
    return [c for c in effective_languages() if c != LANG_EN]


def custom_language_codes() -> list[str]:
    """Compatibility: non-shipped overlay codes that are visible."""
    ensure_catalogs_loaded()
    hidden = set(hidden_language_codes())
    return sorted(c for c in _custom_languages if c not in hidden)


def msgid_hash(msgids: list[str] | None = None) -> str:
    keys = msgids if msgids is not None else bootstrap_msgids()
    blob = "\n".join(keys).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def bootstrap_msgids() -> list[str]:
    """Canonical English UI msgids for AI translation / coverage checks."""
    ensure_catalogs_loaded()
    keys: set[str] = set()
    # Prefer the richest packaged catalog (FR), then other shipped tables.
    for code in (LANG_FR, *sorted(_shipped_codes)):
        catalog = _TRANSLATIONS.get(code)
        if catalog:
            keys.update(catalog.keys())
    for code, catalog in _TRANSLATIONS.items():
        if code not in _shipped_codes:
            keys.update(catalog.keys())
    keys.update(
        {
            "{n} fatal",
            "{n} fatals",
            "{n} error",
            "{n} errors",
            "{n} warning",
            "{n} warnings",
            "{n} info",
            "{n} infos",
            "{n} usage",
            "{n} usages",
        }
    )
    return sorted(keys)


def _catalog_dict_from_file(
    data: dict[str, Any],
    *,
    allow_shipped: bool = True,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("invalid_catalog")
    if data.get("format") != CUSTOM_I18N_FORMAT:
        raise ValueError("invalid_format")
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_version") from exc
    if version < 1:
        raise ValueError("invalid_version")
    code = _normalize_lang_code(str(data.get("code", "")))
    if not code or code == LANG_EN:
        raise ValueError("invalid_code")
    if not allow_shipped and is_shipped_language(code):
        raise ValueError("builtin_code")
    native = str(data.get("native_name") or "").strip()
    display = str(data.get("display_name") or "").strip()
    if not native or not display:
        raise ValueError("missing_names")
    strings = data.get("strings")
    if not isinstance(strings, dict) or not strings:
        raise ValueError("empty_strings")
    clean: dict[str, str] = {}
    for k, v in strings.items():
        if isinstance(k, str) and isinstance(v, str) and k:
            clean[k] = v
    if not clean:
        raise ValueError("empty_strings")
    return {
        "format": CUSTOM_I18N_FORMAT,
        "version": CUSTOM_I18N_VERSION,
        "code": code,
        "native_name": native,
        "display_name": display,
        "direction": _normalize_direction(data.get("direction")),
        "source_msgid_hash": str(data.get("source_msgid_hash") or ""),
        "strings": clean,
    }


def _write_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": CUSTOM_I18N_FORMAT,
        "version": CUSTOM_I18N_VERSION,
        "code": catalog["code"],
        "native_name": catalog["native_name"],
        "display_name": catalog["display_name"],
        "direction": _normalize_direction(catalog.get("direction")),
        "source_msgid_hash": catalog.get("source_msgid_hash") or "",
        "strings": catalog["strings"],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _apply_catalog_to_runtime(catalog: dict[str, Any], *, overlay: bool) -> str:
    code = catalog["code"]
    LANGUAGES[code] = catalog["native_name"]
    LANGUAGE_DISPLAY_NAMES[code] = catalog["display_name"]
    BUILTIN_LANGUAGES[code] = catalog["native_name"]
    BUILTIN_DISPLAY_NAMES[code] = catalog["display_name"]
    _TRANSLATIONS[code] = dict(catalog["strings"])
    _catalog_directions[code] = _normalize_direction(catalog.get("direction"))
    if overlay:
        _overlay_codes.add(code)
        if code not in _shipped_codes:
            _custom_languages[code] = catalog["native_name"]
            _custom_display_names[code] = catalog["display_name"]
    else:
        _shipped_codes.add(code)
    return code


def _sync_custom_languages_setting() -> None:
    update_settings(custom_languages=sorted(_custom_languages.keys()))


def _read_json_catalog(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _catalog_dict_from_file(data, allow_shipped=True)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def ensure_catalogs_loaded() -> None:
    global _catalogs_loaded
    if _catalogs_loaded:
        return
    load_all_catalogs()


def load_all_catalogs() -> None:
    """Load packaged locales, then AppData overlays (overlays win)."""
    global _catalogs_loaded, _custom_languages, _custom_display_names
    global _shipped_codes, _overlay_codes

    keep_en_label = LANGUAGES.get(LANG_EN, "English")
    keep_en_display = LANGUAGE_DISPLAY_NAMES.get(LANG_EN, "English")

    LANGUAGES.clear()
    LANGUAGE_DISPLAY_NAMES.clear()
    BUILTIN_LANGUAGES.clear()
    BUILTIN_DISPLAY_NAMES.clear()
    LANGUAGES[LANG_EN] = keep_en_label
    LANGUAGE_DISPLAY_NAMES[LANG_EN] = keep_en_display
    BUILTIN_LANGUAGES[LANG_EN] = keep_en_label
    BUILTIN_DISPLAY_NAMES[LANG_EN] = keep_en_display

    _TRANSLATIONS.clear()
    _catalog_directions.clear()
    _shipped_codes = set()
    _overlay_codes = set()
    _custom_languages = {}
    _custom_display_names = {}

    root = packaged_locales_dir()
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            catalog = _read_json_catalog(path)
            if catalog is None:
                continue
            _apply_catalog_to_runtime(catalog, overlay=False)

    overlay_root = custom_i18n_dir()
    if overlay_root.is_dir():
        for path in sorted(overlay_root.glob("*.json")):
            catalog = _read_json_catalog(path)
            if catalog is None:
                continue
            _apply_catalog_to_runtime(catalog, overlay=True)

    _sync_custom_languages_setting()
    _catalogs_loaded = True


def load_custom_languages() -> None:
    """Compatibility wrapper: reload packaged + overlay catalogs."""
    load_all_catalogs()


def install_custom_catalog(catalog: dict[str, Any], *, overwrite: bool = False) -> str:
    """Validate, write AppData overlay catalog, and register. Returns language code."""
    ensure_catalogs_loaded()
    catalog = _catalog_dict_from_file(catalog, allow_shipped=True)
    code = catalog["code"]
    path = overlay_catalog_path(code)
    if path.is_file() and not overwrite:
        raise ValueError("exists")
    _write_catalog(path, catalog)
    _apply_catalog_to_runtime(catalog, overlay=True)
    _sync_custom_languages_setting()
    unhide_language(code)
    return code


def write_overlay_catalog(catalog: dict[str, Any]) -> str:
    """Write (or overwrite) an AppData overlay and register it."""
    return install_custom_catalog(catalog, overwrite=True)


def remove_custom_language(code: str) -> None:
    """Compatibility: hide the language (files are kept)."""
    hide_language(code)


def read_packaged_catalog(code: str) -> dict[str, Any] | None:
    path = packaged_catalog_path(code)
    if not path.is_file():
        return None
    return _read_json_catalog(path)


def read_overlay_catalog(code: str) -> dict[str, Any] | None:
    path = overlay_catalog_path(code)
    if not path.is_file():
        return None
    return _read_json_catalog(path)


def read_custom_catalog(code: str) -> dict[str, Any] | None:
    """Effective catalog: AppData overlay if present, else packaged."""
    return read_catalog(code)


def read_catalog(code: str) -> dict[str, Any] | None:
    """Effective catalog for ``code`` (overlay wins over packaged)."""
    ensure_catalogs_loaded()
    c = _normalize_lang_code(code)
    if not c or c == LANG_EN:
        return None
    overlay = read_overlay_catalog(c)
    if overlay is not None:
        return overlay
    packaged = read_packaged_catalog(c)
    if packaged is not None:
        return packaged
    # Runtime-only fallback (tests / partial loads).
    strings = _TRANSLATIONS.get(c)
    if not strings:
        return None
    return {
        "format": CUSTOM_I18N_FORMAT,
        "version": CUSTOM_I18N_VERSION,
        "code": c,
        "native_name": LANGUAGES.get(c, c),
        "display_name": LANGUAGE_DISPLAY_NAMES.get(c, c),
        "direction": get_text_direction(c),
        "source_msgid_hash": "",
        "strings": dict(strings),
    }


def export_language(code: str, path: Path | str) -> None:
    catalog = read_catalog(code)
    if catalog is None:
        raise ValueError("not_found")
    _write_catalog(Path(path), catalog)


def export_custom_language(code: str, path: Path | str) -> None:
    export_language(code, path)


def import_custom_language(path: Path | str, *, overwrite: bool = False) -> str:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    return install_custom_catalog(data, overwrite=overwrite)


def custom_language_needs_update(code: str) -> bool:
    catalog = read_catalog(code)
    if catalog is None:
        return True
    current = msgid_hash()
    stored = catalog.get("source_msgid_hash") or ""
    if stored != current:
        return True
    strings = catalog.get("strings") or {}
    return any(m not in strings for m in bootstrap_msgids())


def get_text_direction(lang: str | None = None) -> str:
    ensure_catalogs_loaded()
    code = _normalize_lang_code(lang if lang is not None else _current_language)
    if code == LANG_EN or not code:
        return TEXT_DIRECTION_LTR
    return _catalog_directions.get(code, TEXT_DIRECTION_LTR)


def detect_os_language() -> str:
    """Map the OS UI / locale language to a supported app language."""
    import locale
    import os

    ensure_catalogs_loaded()
    candidates: list[str] = []

    if sys.platform == "win32":
        try:
            import ctypes

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = lang_id & 0x3FF
            win_map = {
                0x09: LANG_EN,
                0x0C: LANG_FR,
                0x0A: LANG_ES,
                0x01: LANG_AR,
                0x19: LANG_RU,
                0x11: LANG_JA,
            }
            mapped = win_map.get(primary)
            if mapped and (
                mapped == LANG_EN or is_registered_language(mapped)
            ) and not is_language_hidden(mapped):
                return mapped
        except (AttributeError, OSError, ValueError):
            pass

    if sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout:
                for token in out.stdout.replace("(", " ").replace(")", " ").replace(
                    '"', " "
                ).replace(",", " ").split():
                    candidates.append(token.strip())
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        loc = locale.getlocale()
        if loc and loc[0]:
            candidates.append(loc[0])
    except (TypeError, ValueError):
        pass

    try:
        loc = locale.getdefaultlocale()  # type: ignore[attr-defined]
        if loc and loc[0]:
            candidates.append(loc[0])
    except (AttributeError, TypeError, ValueError):
        pass

    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(key)
        if value:
            for part in value.replace(";", ":").split(":"):
                part = part.strip()
                if part:
                    candidates.append(part.split(".")[0])

    for raw in candidates:
        code = raw.replace("_", "-").lower()
        mapped = ""
        if code.startswith("fr"):
            mapped = LANG_FR
        elif code.startswith("es"):
            mapped = LANG_ES
        elif code.startswith("ar"):
            mapped = LANG_AR
        elif code.startswith("ru"):
            mapped = LANG_RU
        elif code.startswith("ja"):
            mapped = LANG_JA
        elif code.startswith("en"):
            mapped = LANG_EN
        if mapped and (
            mapped == LANG_EN or is_registered_language(mapped)
        ) and not is_language_hidden(mapped):
            return mapped

    return DEFAULT_LANGUAGE


def load_language() -> str:
    """Load saved language, or detect from the OS UI language on first run."""
    global _current_language
    load_all_catalogs()
    data = read_settings()
    lang = _normalize_lang_code(str(data.get("language", "")))
    if (
        lang
        and is_registered_language(lang)
        and not is_language_hidden(lang)
    ):
        _current_language = lang
        return lang
    detected = detect_os_language()
    _current_language = detected
    return detected


def save_language(lang: str) -> None:
    global _current_language
    code = _normalize_lang_code(lang)
    if (
        not code
        or not is_registered_language(code)
        or is_language_hidden(code)
    ):
        code = DEFAULT_LANGUAGE
    _current_language = code
    update_settings(language=code)


def get_language() -> str:
    return _current_language


def set_language(lang: str) -> None:
    save_language(lang)


def language_display_name(lang: str | None = None) -> str:
    """English language name for AI prompts (based on UI language)."""
    ensure_catalogs_loaded()
    code = _normalize_lang_code(lang if lang is not None else _current_language)
    if code in _custom_display_names:
        return _custom_display_names[code]
    return LANGUAGE_DISPLAY_NAMES.get(code, "English")


def language_native_name(lang: str | None = None) -> str:
    ensure_catalogs_loaded()
    code = _normalize_lang_code(lang if lang is not None else _current_language)
    return LANGUAGES.get(code, code or "English")


def _(message: str, **kwargs: object) -> str:
    """Translate message; optional format kwargs applied after lookup."""
    ensure_catalogs_loaded()
    catalog = _TRANSLATIONS.get(_current_language, {})
    text = catalog.get(message, message)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def ngettext(singular: str, plural: str, n: int) -> str:
    key = singular if n == 1 else plural
    return _(key, n=n)
