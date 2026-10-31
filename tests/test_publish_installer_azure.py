"""version.json merge for Azure installer publish."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_installer_azure as pub  # noqa: E402


def test_merge_windows_preserves_macos_version(tmp_path: Path) -> None:
    existing = {
        "windows_latest_version": "0.7.40",
        "macos_latest_version": "0.7.41",
        "windows_download_url": "https://example.invalid/old.exe",
        "macos_download_url": "https://example.invalid/old.dmg",
    }
    merged = pub.merge_version_manifest(existing, platform="windows", version="0.7.42")
    assert merged["windows_latest_version"] == "0.7.42"
    assert merged["macos_latest_version"] == "0.7.41"
    assert merged["windows_download_url"] == pub.PUBLIC_SETUP_WINDOWS_URL
    assert merged["macos_download_url"] == "https://example.invalid/old.dmg"


def test_merge_macos_preserves_windows_version() -> None:
    existing = {
        "windows_latest_version": "0.7.42",
        "macos_latest_version": "0.7.40",
    }
    merged = pub.merge_version_manifest(existing, platform="macos", version="0.7.43")
    assert merged["windows_latest_version"] == "0.7.42"
    assert merged["macos_latest_version"] == "0.7.43"
    assert merged["macos_download_url"] == pub.PUBLIC_SETUP_MACOS_URL


def test_merge_empty_existing_only_fills_one_platform() -> None:
    merged = pub.merge_version_manifest(None, platform="windows", version="0.7.42")
    assert merged["windows_latest_version"] == "0.7.42"
    assert merged["macos_latest_version"] == ""
    assert merged["windows_download_url"] == pub.PUBLIC_SETUP_WINDOWS_URL
    assert merged["macos_download_url"] == pub.PUBLIC_SETUP_MACOS_URL


def test_write_version_json_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "version.json"
    manifest = pub.merge_version_manifest(None, platform="windows", version="0.7.42")
    pub.write_version_json(path, manifest)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["windows_latest_version"] == "0.7.42"
    assert "macos_latest_version" in loaded


def test_setup_blob_macos_is_dmg() -> None:
    blob, content_type, url = pub.setup_blob_for_platform("macos")
    assert blob.endswith("CheckMate-setup.dmg")
    assert content_type == pub.DMG_CONTENT_TYPE
    assert url == pub.PUBLIC_SETUP_MACOS_URL


def test_macos_skip_upload_accepts_setup_dmg(tmp_path: Path, monkeypatch) -> None:
    dmg = tmp_path / "CheckMate-macos.dmg"
    dmg.write_bytes(b"x")
    out = tmp_path / "version.json"
    monkeypatch.setattr(pub, "fetch_public_version_manifest", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "publish_installer_azure.py",
            "--platform",
            "macos",
            "--setup-dmg",
            str(dmg),
            "--version",
            "0.7.43",
            "--write-version-json",
            str(out),
            "--skip-upload",
        ],
    )
    assert pub.main() == 0
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["macos_latest_version"] == "0.7.43"
    assert loaded["macos_download_url"] == pub.PUBLIC_SETUP_MACOS_URL
    assert loaded["windows_latest_version"] == ""


def test_bundle_build_encodes_xyz() -> None:
    import package as pkg

    assert pkg._bundle_build_from_version("0.7.43") == 743
    assert pkg._bundle_build_from_version("1.2.3") == 10203
    assert pkg._bundle_build_from_version("dev") == 0
