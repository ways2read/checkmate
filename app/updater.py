"""Download and update eBraille Checker and W3C EPUBCheck distributions."""

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
    EPUBCHECK_RELEASES_API,
    EPUBCHECK_RELEASES_PAGE,
    BUNDLED_VERSION_FILE,
    bundled_epubcheck_version_file,
    bundled_version_file,
    checker_dir,
    epubcheck_dir,
    epubcheck_version_file,
    find_checker_jar,
    find_ebraille_jar_in_tree,
    find_epubcheck_jar,
    find_epubcheck_jar_in_tree,
    version_file,
)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class ToolSpec:
    key: str
    display_name: str
    releases_api: str
    releases_page: str
    asset_name_contains: str
    find_jar: Callable[[Path], Path | None]
    app_data_dir: Callable[[], Path]
    installed_version_path: Callable[[], Path]
    bundled_version_path: Callable[[], Path]
    find_installed_jar: Callable[[], Path | None]


@dataclass
class ReleaseInfo:
    tag: str
    name: str
    zip_url: str
    zip_name: str
    html_url: str
    tool_key: str = ""


@dataclass
class ToolUpdateInfo:
    tool: ToolSpec
    latest: ReleaseInfo | None
    installed: str | None
    available: bool
    error: str | None = None


EBRAILLE_TOOL = ToolSpec(
    key="ebraille",
    display_name="eBraille Checker",
    releases_api=CHECKER_RELEASES_API,
    releases_page=CHECKER_RELEASES_PAGE,
    asset_name_contains="ebraille-checker",
    find_jar=find_ebraille_jar_in_tree,
    app_data_dir=checker_dir,
    installed_version_path=version_file,
    bundled_version_path=bundled_version_file,
    find_installed_jar=find_checker_jar,
)

EPUBCHECK_TOOL = ToolSpec(
    key="epubcheck",
    display_name="EPUBCheck",
    releases_api=EPUBCHECK_RELEASES_API,
    releases_page=EPUBCHECK_RELEASES_PAGE,
    asset_name_contains="epubcheck",
    find_jar=find_epubcheck_jar_in_tree,
    app_data_dir=epubcheck_dir,
    installed_version_path=epubcheck_version_file,
    bundled_version_path=bundled_epubcheck_version_file,
    find_installed_jar=find_epubcheck_jar,
)

ALL_TOOLS: tuple[ToolSpec, ...] = (EBRAILLE_TOOL, EPUBCHECK_TOOL)


def tool_by_key(key: str) -> ToolSpec:
    for tool in ALL_TOOLS:
        if tool.key == key:
            return tool
    raise KeyError(f"Unknown tool: {key}")


def _normalize_version(tag: str) -> str:
    return tag.lstrip("vV").strip()


def parse_version(tag: str) -> Version | None:
    try:
        return Version(_normalize_version(tag))
    except InvalidVersion:
        return None


def _read_version_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def read_installed_version(tool: ToolSpec | None = None) -> str | None:
    tool = tool or EBRAILLE_TOOL
    return _read_version_text(tool.installed_version_path())


def read_bundled_version(tool: ToolSpec | None = None) -> str | None:
    tool = tool or EBRAILLE_TOOL
    return _read_version_text(tool.bundled_version_path())


def read_effective_version(tool: ToolSpec | None = None) -> str | None:
    """Version of the tool currently in use (updated copy or bundled)."""
    tool = tool or EBRAILLE_TOOL
    return read_installed_version(tool) or read_bundled_version(tool)


def write_installed_version(tag: str, tool: ToolSpec | None = None) -> None:
    tool = tool or EBRAILLE_TOOL
    tool.installed_version_path().write_text(tag.strip() + "\n", encoding="utf-8")


def write_bundled_version(
    tag: str, root: Path, tool: ToolSpec | None = None
) -> None:
    del tool  # version file name is shared
    path = root / BUNDLED_VERSION_FILE
    path.write_text(tag.strip() + "\n", encoding="utf-8")


def fetch_latest_release(
    tool: ToolSpec | None = None, timeout: float = 30.0
) -> ReleaseInfo:
    tool = tool or EBRAILLE_TOOL
    response = requests.get(
        tool.releases_api,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "eBrailleCheckerGUI",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    tag = data.get("tag_name") or ""
    assets = data.get("assets") or []
    zip_asset = None
    needle = tool.asset_name_contains.lower()
    for asset in assets:
        name = asset.get("name") or ""
        if name.endswith(".zip") and needle in name.lower():
            zip_asset = asset
            break
    if zip_asset is None:
        for asset in assets:
            if (asset.get("name") or "").endswith(".zip"):
                zip_asset = asset
                break
    if not tag or zip_asset is None:
        raise RuntimeError(
            f"Could not find a downloadable {tool.display_name} release zip. "
            f"See {tool.releases_page}"
        )
    return ReleaseInfo(
        tag=tag,
        name=data.get("name") or tag,
        zip_url=zip_asset["browser_download_url"],
        zip_name=zip_asset["name"],
        html_url=data.get("html_url") or tool.releases_page,
        tool_key=tool.key,
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


def _extract_release_to_root(data: bytes, root: Path, tool: ToolSpec) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _clear_directory(root)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(root)
    jar = tool.find_jar(root)
    if jar is None:
        raise RuntimeError(
            f"Downloaded release did not contain a {tool.display_name} jar. "
            f"Contents are in {root}"
        )
    return jar


def install_release(
    release: ReleaseInfo,
    progress: ProgressCallback | None = None,
    timeout: float = 120.0,
    tool: ToolSpec | None = None,
) -> Path:
    """Download and extract a release into app data. Returns path to jar."""
    if tool is None:
        tool = tool_by_key(release.tool_key) if release.tool_key else EBRAILLE_TOOL
    root = tool.app_data_dir()
    if progress:
        progress(f"Downloading {release.zip_name}…")
    data = _download_release_zip(release, timeout=timeout)
    if progress:
        progress(f"Extracting {tool.display_name}…")
    jar = _extract_release_to_root(data, root, tool)
    write_installed_version(release.tag, tool)
    if progress:
        progress(f"Installed {tool.display_name} {release.tag}")
    return jar


def bundle_tool_release(
    target_dir: Path,
    tool: ToolSpec,
    release: ReleaseInfo | None = None,
    progress: ProgressCallback | None = None,
    timeout: float = 120.0,
) -> Path:
    """Download and extract a release for bundling with a packaged build."""
    release = release or fetch_latest_release(tool)
    target_dir = target_dir.resolve()
    if progress:
        progress(f"Downloading {release.zip_name} for bundling…")
    data = _download_release_zip(release, timeout=timeout)
    if progress:
        progress(f"Extracting {tool.display_name} for bundle…")
    jar = _extract_release_to_root(data, target_dir, tool)
    write_bundled_version(release.tag, target_dir, tool)
    if progress:
        progress(f"Bundled {tool.display_name} {release.tag}")
    return jar


def bundle_checker_release(
    target_dir: Path,
    release: ReleaseInfo | None = None,
    progress: ProgressCallback | None = None,
    timeout: float = 120.0,
) -> Path:
    return bundle_tool_release(
        target_dir, EBRAILLE_TOOL, release=release, progress=progress, timeout=timeout
    )


def bundle_epubcheck_release(
    target_dir: Path,
    release: ReleaseInfo | None = None,
    progress: ProgressCallback | None = None,
    timeout: float = 120.0,
) -> Path:
    return bundle_tool_release(
        target_dir,
        EPUBCHECK_TOOL,
        release=release,
        progress=progress,
        timeout=timeout,
    )


def ensure_tool_installed(
    tool: ToolSpec, progress: ProgressCallback | None = None
) -> Path:
    jar = tool.find_installed_jar()
    if jar is not None:
        return jar
    if progress:
        progress(f"{tool.display_name} not found. Downloading latest release…")
    release = fetch_latest_release(tool)
    return install_release(release, progress=progress, tool=tool)


def ensure_checker_installed(progress: ProgressCallback | None = None) -> Path:
    return ensure_tool_installed(EBRAILLE_TOOL, progress=progress)


def ensure_epubcheck_installed(progress: ProgressCallback | None = None) -> Path:
    return ensure_tool_installed(EPUBCHECK_TOOL, progress=progress)


def ensure_tools_installed(progress: ProgressCallback | None = None) -> None:
    for tool in ALL_TOOLS:
        ensure_tool_installed(tool, progress=progress)


def check_for_update(
    tool: ToolSpec | None = None,
) -> tuple[ReleaseInfo | None, str | None, bool]:
    """Return (latest, effective_installed_version, update_available) for one tool."""
    tool = tool or EBRAILLE_TOOL
    installed = read_effective_version(tool)
    latest = fetch_latest_release(tool)
    return latest, installed, is_update_available(latest.tag, installed)


def check_for_updates() -> list[ToolUpdateInfo]:
    """Probe all validation tools for newer GitHub releases."""
    results: list[ToolUpdateInfo] = []
    for tool in ALL_TOOLS:
        installed = read_effective_version(tool)
        try:
            latest = fetch_latest_release(tool)
            results.append(
                ToolUpdateInfo(
                    tool=tool,
                    latest=latest,
                    installed=installed,
                    available=is_update_available(latest.tag, installed),
                )
            )
        except Exception as exc:  # noqa: BLE001 — report per tool
            results.append(
                ToolUpdateInfo(
                    tool=tool,
                    latest=None,
                    installed=installed,
                    available=False,
                    error=str(exc),
                )
            )
    return results
