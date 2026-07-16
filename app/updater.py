"""Download and update the eBraille Checker distribution."""

from __future__ import annotations

import io
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
from packaging.version import InvalidVersion, Version

from .paths import (
    CHECKER_RELEASES_API,
    CHECKER_RELEASES_PAGE,
    bundled_version_file,
    checker_dir,
    find_checker_jar,
    version_file,
)

ProgressCallback = Callable[[str], None]


@dataclass
class ReleaseInfo:
    tag: str
    name: str
    zip_url: str
    zip_name: str
    html_url: str


def _normalize_version(tag: str) -> str:
    return tag.lstrip("vV").strip()


def parse_version(tag: str) -> Version | None:
    try:
        return Version(_normalize_version(tag))
    except InvalidVersion:
        return None


def read_installed_version() -> str | None:
    path = version_file()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def read_bundled_version() -> str | None:
    path = bundled_version_file()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def read_effective_version() -> str | None:
    """Version of the checker currently in use (updated copy or bundled)."""
    return read_installed_version() or read_bundled_version()


def write_installed_version(tag: str) -> None:
    version_file().write_text(tag.strip() + "\n", encoding="utf-8")


def write_bundled_version(tag: str, root: Path) -> None:
    path = root / bundled_version_file().name
    path.write_text(tag.strip() + "\n", encoding="utf-8")


def fetch_latest_release(timeout: float = 30.0) -> ReleaseInfo:
    response = requests.get(
        CHECKER_RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "eBrailleCheckerGUI"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    tag = data.get("tag_name") or ""
    assets = data.get("assets") or []
    zip_asset = None
    for asset in assets:
        name = asset.get("name") or ""
        if name.endswith(".zip") and "ebraille-checker" in name.lower():
            zip_asset = asset
            break
    if zip_asset is None:
        for asset in assets:
            if (asset.get("name") or "").endswith(".zip"):
                zip_asset = asset
                break
    if not tag or zip_asset is None:
        raise RuntimeError(
            "Could not find a downloadable eBraille Checker release zip. "
            f"See {CHECKER_RELEASES_PAGE}"
        )
    return ReleaseInfo(
        tag=tag,
        name=data.get("name") or tag,
        zip_url=zip_asset["browser_download_url"],
        zip_name=zip_asset["name"],
        html_url=data.get("html_url") or CHECKER_RELEASES_PAGE,
    )


def is_update_available(latest_tag: str, installed_tag: str | None) -> bool:
    if not installed_tag:
        return True
    latest = parse_version(latest_tag)
    installed = parse_version(installed_tag)
    if latest is None or installed is None:
        return _normalize_version(latest_tag) != _normalize_version(installed_tag)
    return latest > installed


def _clear_directory(root: Path, *, keep_names: frozenset[str] = frozenset()) -> None:
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.name in keep_names:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def _download_release_zip(release: ReleaseInfo, timeout: float = 120.0) -> bytes:
    response = requests.get(
        release.zip_url,
        headers={"User-Agent": "eBrailleCheckerGUI"},
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    data = io.BytesIO()
    for chunk in response.iter_content(chunk_size=1024 * 256):
        if chunk:
            data.write(chunk)
    return data.getvalue()


def _extract_release_to_root(data: bytes, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _clear_directory(root)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(root)
    jar = _find_jar_in(root)
    if jar is None:
        raise RuntimeError(
            "Downloaded release did not contain ebraille-checker.jar. "
            f"Contents are in {root}"
        )
    return jar


def _find_jar_in(root: Path) -> Path | None:
    from .paths import _find_jar_in_tree

    return _find_jar_in_tree(root)


def install_release(
    release: ReleaseInfo,
    progress: ProgressCallback | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download and extract a release into app data. Returns path to jar."""
    root = checker_dir()
    if progress:
        progress(f"Downloading {release.zip_name}…")
    data = _download_release_zip(release, timeout=timeout)
    if progress:
        progress("Extracting checker…")
    jar = _extract_release_to_root(data, root)
    write_installed_version(release.tag)
    if progress:
        progress(f"Installed checker {release.tag}")
    return jar


def bundle_checker_release(
    target_dir: Path,
    release: ReleaseInfo | None = None,
    progress: ProgressCallback | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download and extract a release for bundling with a packaged build."""
    release = release or fetch_latest_release()
    target_dir = target_dir.resolve()
    if progress:
        progress(f"Downloading {release.zip_name} for bundling…")
    data = _download_release_zip(release, timeout=timeout)
    if progress:
        progress("Extracting checker for bundle…")
    jar = _extract_release_to_root(data, target_dir)
    write_bundled_version(release.tag, target_dir)
    if progress:
        progress(f"Bundled checker {release.tag}")
    return jar


def ensure_checker_installed(progress: ProgressCallback | None = None) -> Path:
    jar = find_checker_jar()
    if jar is not None:
        return jar
    if progress:
        progress("Checker not found. Downloading latest release…")
    release = fetch_latest_release()
    return install_release(release, progress=progress)


def check_for_update() -> tuple[ReleaseInfo | None, str | None, bool]:
    """Return (latest, effective_installed_version, update_available)."""
    installed = read_effective_version()
    latest = fetch_latest_release()
    return latest, installed, is_update_available(latest.tag, installed)
