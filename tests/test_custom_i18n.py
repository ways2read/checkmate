"""Tests for UI language catalogs (packaged + AppData overlay + hide)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checkmate import i18n
from checkmate import settings as settings_mod


@pytest.fixture()
def tmp_i18n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(i18n, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings_mod, "app_data_dir", lambda: tmp_path)
    i18n._catalogs_loaded = False
    i18n.load_all_catalogs()
    i18n._current_language = i18n.DEFAULT_LANGUAGE
    update_settings = settings_mod.update_settings
    update_settings(hidden_languages=[], custom_languages=[], language="en")
    yield tmp_path
    i18n._catalogs_loaded = False
    i18n.load_all_catalogs()
    i18n._current_language = i18n.DEFAULT_LANGUAGE


def _sample_catalog(
    code: str = "it",
    *,
    direction: str = "ltr",
    close: str = "Chiudi",
) -> dict:
    return {
        "format": i18n.CUSTOM_I18N_FORMAT,
        "version": i18n.CUSTOM_I18N_VERSION,
        "code": code,
        "native_name": "Italiano" if code == "it" else code,
        "display_name": "Italian" if code == "it" else code,
        "direction": direction,
        "source_msgid_hash": "abc",
        "strings": {
            "Close": close,
            "&Language": "&Lingua",
        },
    }


def test_packaged_locales_load(tmp_i18n: Path) -> None:
    for code in ("fr", "es", "ar", "ru", "ja"):
        assert i18n.is_shipped_language(code)
        assert code in i18n.effective_languages()
    assert i18n.get_text_direction("fr") == "ltr"
    assert i18n.get_text_direction("ja") == "ltr"
    assert i18n.get_text_direction("ar") == "rtl"
    catalog = i18n.read_catalog("fr")
    assert catalog is not None
    assert catalog["direction"] == "ltr"
    assert "Close" in catalog["strings"] or "Fermer" in catalog["strings"].values()


def test_install_and_effective_languages(tmp_i18n: Path) -> None:
    code = i18n.install_custom_catalog(_sample_catalog())
    assert code == "it"
    assert i18n.is_custom_language("it")
    assert "it" in i18n.effective_languages()
    assert i18n.effective_languages()["it"] == "Italiano (Italian)"
    assert i18n.language_display_name("it") == "Italian"
    assert i18n.language_menu_label("it") == "Italiano (Italian)"
    assert i18n.language_menu_label("en") == "English"
    assert i18n._TRANSLATIONS["it"]["Close"] == "Chiudi"
    assert i18n.get_text_direction("it") == "ltr"


def test_overlay_shipped_language(tmp_i18n: Path) -> None:
    packaged = i18n.read_packaged_catalog("fr")
    assert packaged is not None
    overlay = dict(packaged)
    overlay["strings"] = dict(packaged["strings"])
    overlay["strings"]["Close"] = "FERMER-TEST"
    i18n.install_custom_catalog(overlay, overwrite=True)
    assert i18n.has_overlay("fr")
    assert i18n.read_catalog("fr")["strings"]["Close"] == "FERMER-TEST"
    i18n.set_language("fr")
    assert i18n._("Close") == "FERMER-TEST"
    i18n.set_language("en")


def test_direction_rtl(tmp_i18n: Path) -> None:
    cat = _sample_catalog("ar", direction="rtl")
    cat["native_name"] = "العربية"
    cat["display_name"] = "Arabic"
    i18n.install_custom_catalog(cat)
    assert i18n.get_text_direction("ar") == "rtl"
    exported = tmp_i18n / "ar-out.json"
    i18n.export_language("ar", exported)
    raw = json.loads(exported.read_text(encoding="utf-8"))
    assert raw["direction"] == "rtl"
    assert raw["version"] == i18n.CUSTOM_I18N_VERSION


def test_hide_and_unhide(tmp_i18n: Path) -> None:
    assert "fr" in i18n.effective_languages()
    i18n.hide_language("fr")
    assert "fr" not in i18n.effective_languages()
    assert i18n.is_language_hidden("fr")
    assert i18n.read_catalog("fr") is not None
    i18n.unhide_language("fr")
    assert "fr" in i18n.effective_languages()


def test_hide_active_language_switches_to_english(tmp_i18n: Path) -> None:
    i18n.set_language("fr")
    assert i18n.get_language() == "fr"
    i18n.hide_language("fr")
    assert i18n.get_language() == "en"


def test_export_import_roundtrip(tmp_i18n: Path) -> None:
    i18n.install_custom_catalog(_sample_catalog())
    export_path = tmp_i18n / "out" / "checkmate-ui-it.json"
    export_path.parent.mkdir(parents=True)
    i18n.export_language("it", export_path)
    i18n.hide_language("it")
    assert "it" not in i18n.effective_languages()

    installed = i18n.import_custom_language(export_path, overwrite=True)
    assert installed == "it"
    assert "it" in i18n.effective_languages()
    raw = json.loads(export_path.read_text(encoding="utf-8"))
    assert raw["format"] == i18n.CUSTOM_I18N_FORMAT
    assert raw["strings"]["Close"] == "Chiudi"
    assert raw["direction"] == "ltr"


def test_import_overwrite_requires_flag(tmp_i18n: Path) -> None:
    i18n.install_custom_catalog(_sample_catalog())
    path = tmp_i18n / "dup.json"
    i18n.export_language("it", path)
    with pytest.raises(ValueError, match="exists"):
        i18n.import_custom_language(path, overwrite=False)
    i18n.import_custom_language(path, overwrite=True)


def test_load_overlays_from_disk(tmp_i18n: Path) -> None:
    path = i18n.overlay_catalog_path("it")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_sample_catalog()), encoding="utf-8")
    i18n._catalogs_loaded = False
    i18n.load_all_catalogs()
    assert i18n.is_custom_language("it")
    i18n.set_language("it")
    assert i18n._("Close") == "Chiudi"
    i18n.set_language("en")


def test_v1_catalog_defaults_direction(tmp_i18n: Path) -> None:
    data = _sample_catalog()
    data["version"] = 1
    del data["direction"]
    code = i18n.install_custom_catalog(data)
    assert code == "it"
    assert i18n.get_text_direction("it") == "ltr"


def test_bootstrap_msgids_nonempty(tmp_i18n: Path) -> None:
    keys = i18n.bootstrap_msgids()
    assert "Close" in keys or len(keys) > 100
    assert "{n} errors" in keys


def test_export_shipped_language(tmp_i18n: Path) -> None:
    path = tmp_i18n / "fr-export.json"
    i18n.export_language("fr", path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["code"] == "fr"
    assert raw["direction"] == "ltr"
    assert isinstance(raw["strings"], dict)
    assert raw["strings"]
