"""CheckMate app update feed (version.json)."""

from __future__ import annotations

from unittest.mock import patch

from checkmate.app_update import (
    SETUP_BASE_URL,
    SETUP_DMG,
    SETUP_EXE,
    check_for_app_update,
    platform_latest_from_payload,
    running_app_version,
    version_from_info_plist,
)
from checkmate.updater import is_update_available


PAYLOAD = {
    "windows_latest_version": "0.7.43",
    "macos_latest_version": "0.7.42.15",
    "windows_download_url": SETUP_BASE_URL + SETUP_EXE,
    "macos_download_url": SETUP_BASE_URL + SETUP_DMG,
}


def test_platform_fields_are_separate() -> None:
    with patch("checkmate.app_update.sys.platform", "win32"):
        latest, url = platform_latest_from_payload(PAYLOAD)
        assert latest == "0.7.43"
        assert url and url.endswith(SETUP_EXE)
    with patch("checkmate.app_update.sys.platform", "darwin"):
        latest, url = platform_latest_from_payload(PAYLOAD)
        assert latest == "0.7.42.15"
        assert url and url.endswith(SETUP_DMG)
    with patch("checkmate.app_update.sys.platform", "linux"):
        latest, url = platform_latest_from_payload(PAYLOAD)
        assert latest is None
        assert url is None


def test_empty_platform_version_is_not_an_update() -> None:
    payload = {
        "windows_latest_version": "0.7.42",
        "macos_latest_version": "",
        "macos_download_url": SETUP_BASE_URL + SETUP_DMG,
    }
    with patch("checkmate.app_update.sys.platform", "darwin"):
        latest, url = platform_latest_from_payload(payload)
        assert latest is None
        assert url is None


def test_fallback_download_url_when_json_omits_it() -> None:
    payload = {"windows_latest_version": "0.7.43"}
    with patch("checkmate.app_update.sys.platform", "win32"):
        latest, url = platform_latest_from_payload(payload)
        assert latest == "0.7.43"
        assert url == SETUP_BASE_URL + SETUP_EXE


def test_newer_marketing_version_is_an_update() -> None:
    assert is_update_available("0.7.43", "0.7.42")
    assert not is_update_available("0.7.42", "0.7.42")


def test_macos_build_number_newer_than_marketing_version() -> None:
    assert is_update_available("0.7.42.15", "0.7.42")
    assert not is_update_available("0.7.42.15", "0.7.42.15")


def test_info_plist_combines_marketing_and_build(tmp_path: Path) -> None:
    plist = tmp_path / "Info.plist"
    plist.write_bytes(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleShortVersionString</key>
  <string>0.7.42</string>
  <key>CFBundleVersion</key>
  <string>15</string>
</dict>
</plist>
"""
    )
    assert version_from_info_plist(plist) == "0.7.42.15"


def test_running_version_defaults_to_package_version() -> None:
    from checkmate import __version__

    with patch("checkmate.app_update.is_frozen", return_value=False):
        assert running_app_version() == __version__


def test_check_for_app_update_available(monkeypatch) -> None:
    monkeypatch.setattr("checkmate.app_update.sys.platform", "win32")
    monkeypatch.setattr("checkmate.app_update.running_app_version", lambda: "0.7.42")
    monkeypatch.setattr(
        "checkmate.app_update.fetch_app_release_payload",
        lambda timeout=15.0: PAYLOAD,
    )
    info = check_for_app_update()
    assert info.available
    assert info.latest == "0.7.43"
    assert info.current == "0.7.42"
    assert info.download_url and info.download_url.endswith(SETUP_EXE)


def test_check_for_app_update_up_to_date(monkeypatch) -> None:
    monkeypatch.setattr("checkmate.app_update.sys.platform", "win32")
    monkeypatch.setattr("checkmate.app_update.running_app_version", lambda: "0.7.43")
    monkeypatch.setattr(
        "checkmate.app_update.fetch_app_release_payload",
        lambda timeout=15.0: PAYLOAD,
    )
    info = check_for_app_update()
    assert not info.available
    assert info.latest == "0.7.43"


def test_linux_has_no_app_installer(monkeypatch) -> None:
    monkeypatch.setattr("checkmate.app_update.sys.platform", "linux")
    info = check_for_app_update()
    assert not info.available
    assert info.latest is None
    assert info.error is None
