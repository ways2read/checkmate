"""Check for a newer CheckMate app using the published version.json feed."""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from . import __version__
from .paths import application_dir, is_frozen
from .updater import is_update_available

logger = logging.getLogger(__name__)

VERSION_JSON_URL = "https://dl.daisy.org/tools/Fido/checkmate/version.json"
SETUP_BASE_URL = "https://dl.daisy.org/tools/Fido/checkmate/"
SETUP_EXE = "CheckMate-setup.exe"
SETUP_DMG = "CheckMate-setup.dmg"
WINDOWS_VERSION_KEY = "windows_latest_version"
MACOS_VERSION_KEY = "macos_latest_version"
WINDOWS_URL_KEY = "windows_download_url"
MACOS_URL_KEY = "macos_download_url"

ProgressCallback = Callable[[int, Optional[int]], None]


@dataclass
class AppUpdateInfo:
    current: str
    latest: str | None
    download_url: str | None
    available: bool
    error: str | None = None


def app_installers_supported() -> bool:
    return sys.platform in ("win32", "darwin")


def version_from_info_plist(plist_path: Path) -> str | None:
    """Return marketing.build from a macOS Info.plist, or the marketing version."""
    if not plist_path.is_file():
        return None
    try:
        with plist_path.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    short = str(data.get("CFBundleShortVersionString") or "").strip()
    build = str(data.get("CFBundleVersion") or "").strip()
    if short and build.isdigit() and not short.endswith("." + build):
        return f"{short}.{build}"
    return short or None


def running_app_version() -> str:
    """Version of this running CheckMate (macOS packaged builds include the build number)."""
    if sys.platform == "darwin" and is_frozen():
        bundled = version_from_info_plist(application_dir() / "Info.plist")
        if bundled:
            return bundled
    return str(__version__)


def platform_latest_from_payload(payload: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return (latest_version, download_url) for this OS from version.json."""
    if not isinstance(payload, dict):
        return None, None
    if sys.platform == "win32":
        version_key, url_key, fallback_name = (
            WINDOWS_VERSION_KEY,
            WINDOWS_URL_KEY,
            SETUP_EXE,
        )
    elif sys.platform == "darwin":
        version_key, url_key, fallback_name = (
            MACOS_VERSION_KEY,
            MACOS_URL_KEY,
            SETUP_DMG,
        )
    else:
        return None, None
    latest = str(payload.get(version_key) or "").strip() or None
    url = str(payload.get(url_key) or "").strip()
    if not url.startswith("https://"):
        url = (SETUP_BASE_URL + fallback_name) if latest else ""
    elif not latest:
        url = ""
    return latest, (url or None)


def fetch_app_release_payload(timeout: float = 15.0) -> dict[str, Any]:
    headers = {
        "User-Agent": f"CheckMate/{running_app_version()}",
        "Accept": "application/json",
    }
    response = requests.get(VERSION_JSON_URL, headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("CheckMate version.json was not an object")
    return data


def check_for_app_update(timeout: float = 15.0) -> AppUpdateInfo:
    """Compare this running CheckMate with the published platform version."""
    current = running_app_version()
    if not app_installers_supported():
        return AppUpdateInfo(
            current=current,
            latest=None,
            download_url=None,
            available=False,
        )
    try:
        payload = fetch_app_release_payload(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — report, do not fail checker updates
        logger.info("CheckMate app update check failed: %s", exc)
        return AppUpdateInfo(
            current=current,
            latest=None,
            download_url=None,
            available=False,
            error=str(exc),
        )
    latest, url = platform_latest_from_payload(payload)
    if not latest:
        return AppUpdateInfo(
            current=current,
            latest=None,
            download_url=url,
            available=False,
        )
    return AppUpdateInfo(
        current=current,
        latest=latest,
        download_url=url,
        available=is_update_available(latest, current),
    )


def download_app_installer(
    url: str,
    *,
    dest_dir: Path | None = None,
    cancel_event: threading.Event | None = None,
    progress_cb: ProgressCallback | None = None,
    timeout: tuple[float, float] = (15.0, 600.0),
) -> tuple[Path | None, str | None]:
    """Download the platform installer. Returns (path, error_message)."""
    parsed = urlparse(url)
    name = os.path.basename(parsed.path) or (
        SETUP_EXE if sys.platform == "win32" else SETUP_DMG
    )
    folder = dest_dir or Path(tempfile.gettempdir())
    dest = folder / name
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        if part.is_file():
            part.unlink()
        if dest.is_file():
            dest.unlink()
        headers = {"User-Agent": f"CheckMate/{running_app_version()}"}
        with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            if resp.status_code != 200:
                return None, f"HTTP {resp.status_code}"
            total = int(resp.headers.get("Content-Length") or 0) or None
            received = 0
            with part.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if cancel_event is not None and cancel_event.is_set():
                        return None, "cancelled"
                    if not chunk:
                        continue
                    fh.write(chunk)
                    received += len(chunk)
                    if progress_cb:
                        progress_cb(received, total)
            if total is not None and received != total:
                return None, f"incomplete ({received} of {total} bytes)"
        part.replace(dest)
        return dest, None
    except requests.RequestException as exc:
        return None, str(exc)
    except OSError as exc:
        return None, str(exc)
    finally:
        try:
            if part.is_file():
                part.unlink()
        except OSError:
            pass


def start_app_installer(path: Path) -> None:
    """Open the downloaded CheckMate installer (Windows exe or macOS dmg)."""
    exe = path.expanduser().resolve()
    if not exe.is_file():
        raise FileNotFoundError(exe)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(exe)], start_new_session=True)
    elif sys.platform == "win32":
        os.startfile(str(exe))  # type: ignore[attr-defined]
    else:
        raise OSError("CheckMate installers are available for Windows and macOS")
    logger.info("CheckMate installer started: %s", exe)
