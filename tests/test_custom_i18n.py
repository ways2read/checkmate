"""Tests for custom UI language catalogs (AppData overlay + import/export)."""

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
    # Drop prior custom overlay state for test codes.
    for code in ("it", "xx-test"):
        i18n._TRANSLATIONS.pop(code, None)
    i18n._custom_languages.clear()
    i18n._custom_display_names.clear()
    yield tmp_path
    for code in ("it", "xx-test"):
        i18n._TRANSLATIONS.pop(code, None)
    i18n._custom_languages.clear()
    i18n._custom_display_names.clear()
    i18n._current_language = i18n.DEFAULT_LANGUAGE


def _sample_catalog(code: str = "it") -> dict:
    return {
        "format": i18n.CUSTOM_I18N_FORMAT,
        "version": i18n.CUSTOM_I18N_VERSION,
        "code": code,
        "native_name": "Italiano",
        "display_name": "Italian",
        "source_msgid_hash": "abc",
        "strings": {
            "Close": "Chiudi",
            "&Language": "&Lingua",
        },
    }


def test_install_and_effective_languages(tmp_i18n: Path) -> None:
    code = i18n.install_custom_catalog(_sample_catalog())
    assert code == "it"
    assert i18n.is_custom_language("it")
    assert "it" in i18n.effective_languages()
    assert i18n.effective_languages()["it"] == "Italiano"
    assert i18n.language_display_name("it") == "Italian"
    assert i18n._TRANSLATIONS["it"]["Close"] == "Chiudi"


def test_reject_builtin_code(tmp_i18n: Path) -> None:
    data = _sample_catalog("fr")
    with pytest.raises(ValueError, match="builtin_code"):
        i18n.install_custom_catalog(data)


def test_export_import_roundtrip(tmp_i18n: Path) -> None:
    i18n.install_custom_catalog(_sample_catalog())
    export_path = tmp_i18n / "out" / "checkmate-ui-it.json"
    export_path.parent.mkdir(parents=True)
    i18n.export_custom_language("it", export_path)
    i18n.remove_custom_language("it")
    assert not i18n.is_custom_language("it")

    installed = i18n.import_custom_language(export_path)
    assert installed == "it"
    assert i18n.is_custom_language("it")
    raw = json.loads(export_path.read_text(encoding="utf-8"))
    assert raw["format"] == i18n.CUSTOM_I18N_FORMAT
    assert raw["strings"]["Close"] == "Chiudi"


def test_import_overwrite_requires_flag(tmp_i18n: Path) -> None:
    i18n.install_custom_catalog(_sample_catalog())
    path = tmp_i18n / "dup.json"
    i18n.export_custom_language("it", path)
    with pytest.raises(ValueError, match="exists"):
        i18n.import_custom_language(path, overwrite=False)
    i18n.import_custom_language(path, overwrite=True)


def test_load_custom_languages_from_disk(tmp_i18n: Path) -> None:
    path = i18n.custom_catalog_path("it")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_sample_catalog()), encoding="utf-8")
    i18n._custom_languages.clear()
    i18n._custom_display_names.clear()
    i18n._TRANSLATIONS.pop("it", None)
    i18n.load_custom_languages()
    assert i18n.is_custom_language("it")
    i18n.set_language("it")
    assert i18n._("Close") == "Chiudi"
    i18n.set_language("en")


def test_bootstrap_msgids_include_plurals() -> None:
    keys = i18n.bootstrap_msgids()
    assert "{n} errors" in keys
    assert len(keys) > 100
