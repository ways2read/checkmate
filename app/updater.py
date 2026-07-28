"""Download and update eBraille Checker, EPUBCheck, and veraPDF distributions."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
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
    VERAPDF_DOWNLOAD_PAGE,
    VERAPDF_INSTALLER_ZIP_URL,
    VERAPDF_RELEASES_PAGE,
    BUNDLED_VERSION_FILE,
    bundled_epubcheck_version_file,
    bundled_verapdf_version_file,
    bundled_version_file,
    checker_dir,
    epubcheck_dir,
    epubcheck_version_file,
    find_checker_jar,
    find_ebraille_jar_in_tree,
    find_epubcheck_jar,
    find_epubcheck_jar_in_tree,
    find_verapdf_cli_jar_in_tree,
    find_verapdf_jar,
    verapdf_dir,
    verapdf_version_file,
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

VERAPDF_TOOL = ToolSpec(
    key="verapdf",
    display_name="veraPDF",
    releases_api=VERAPDF_DOWNLOAD_PAGE,
    releases_page=VERAPDF_RELEASES_PAGE,
    asset_name_contains="verapdf-greenfield",
    find_jar=find_verapdf_cli_jar_in_tree,
    app_data_dir=verapdf_dir,
    installed_version_path=verapdf_version_file,
    bundled_version_path=bundled_verapdf_version_file,
    find_installed_jar=find_verapdf_jar,
)

# Eagerly ensure these on startup; veraPDF is installed lazily on first PDF check.
STARTUP_TOOLS: tuple[ToolSpec, ...] = (EBRAILLE_TOOL, EPUBCHECK_TOOL)
ALL_TOOLS: tuple[ToolSpec, ...] = (EBRAILLE_TOOL, EPUBCHECK_TOOL, VERAPDF_TOOL)


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


def fetch_latest_verapdf_release(timeout: float = 30.0) -> ReleaseInfo:
    """Resolve the newest greenfield installer from software.verapdf.org/rel/."""
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "eBrailleCheckerGUI",
    }
    listing = requests.get(VERAPDF_DOWNLOAD_PAGE, headers=headers, timeout=timeout)
    listing.raise_for_status()
    folders = sorted(
        {
            match.group(1)
            for match in re.finditer(r'href="/rel/(\d+\.\d+)(?:/|")', listing.text)
        },
        key=Version,
    )
    if not folders:
        raise RuntimeError(
            f"Could not find veraPDF version folders on {VERAPDF_DOWNLOAD_PAGE}"
        )
    series = folders[-1]
    series_url = f"{VERAPDF_DOWNLOAD_PAGE.rstrip('/')}/{series}/"
    series_page = requests.get(series_url, headers=headers, timeout=timeout)
    series_page.raise_for_status()
    zips = re.findall(
        r'href="([^"]*verapdf-greenfield-(\d+\.\d+\.\d+)-installer\.zip)"',
        series_page.text,
    )
    if not zips:
        # Fall back to the rolling latest symlink.
        return ReleaseInfo(
            tag="latest",
            name="veraPDF latest",
            zip_url=VERAPDF_INSTALLER_ZIP_URL,
            zip_name="verapdf-installer.zip",
            html_url=VERAPDF_RELEASES_PAGE,
            tool_key="verapdf",
        )
    href, tag = max(zips, key=lambda item: Version(item[1]))
    if href.startswith("http"):
        zip_url = href
    elif href.startswith("/"):
        zip_url = f"https://software.verapdf.org{href}"
    else:
        zip_url = f"{series_url.rstrip('/')}/{href}"
    return ReleaseInfo(
        tag=tag,
        name=f"veraPDF {tag}",
        zip_url=zip_url,
        zip_name=Path(href).name,
        html_url=series_url,
        tool_key="verapdf",
    )


def fetch_latest_release(
    tool: ToolSpec | None = None, timeout: float = 30.0
) -> ReleaseInfo:
    tool = tool or EBRAILLE_TOOL
    if tool.key == "verapdf":
        return fetch_latest_verapdf_release(timeout=timeout)
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


def _verapdf_auto_install_xml(install_dir: Path) -> str:
    """IzPack auto-install script selecting the CLI pack (and docs if bundled)."""
    path = install_dir.resolve().as_posix()
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
    <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
        <installpath>{path}</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select">
        <pack index="0" name="veraPDF GUI" selected="false"/>
        <pack index="1" name="veraPDF CLI" selected="true"/>
    </com.izforge.izpack.panels.packs.PacksPanel>
    <com.izforge.izpack.panels.install.InstallPanel id="install"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
"""


def _resolve_java_for_installer() -> str:
    from .java_util import detect_java

    java = detect_java()
    if java is None:
        raise RuntimeError(
            "Java is required to install veraPDF. Install a JRE or use a "
            "packaged build that includes a bundled runtime."
        )
    return java.path


def _install_verapdf_zip_into(
    data: bytes,
    root: Path,
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    """Unpack the veraPDF installer zip and run an unattended IzPack install."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    _clear_directory(root)

    with tempfile.TemporaryDirectory(prefix="verapdf-installer-") as tmp:
        tmp_path = Path(tmp)
        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(extract_dir)
        installer_jars = sorted(extract_dir.rglob("verapdf-izpack-installer-*.jar"))
        if not installer_jars:
            raise RuntimeError(
                "veraPDF installer zip did not contain verapdf-izpack-installer-*.jar"
            )
        installer_jar = installer_jars[0]
        auto_xml = tmp_path / "auto-install.xml"
        auto_xml.write_text(_verapdf_auto_install_xml(root), encoding="utf-8")
        java_path = _resolve_java_for_installer()
        if progress:
            progress("Running veraPDF installer…")
        from .subprocess_util import hidden_run_kwargs

        proc = subprocess.run(
            [java_path, "-jar", str(installer_jar), str(auto_xml)],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
            **hidden_run_kwargs(),
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"veraPDF installer failed (exit {proc.returncode})"
                + (f": {detail}" if detail else "")
            )

    jar = find_verapdf_cli_jar_in_tree(root)
    if jar is None:
        raise RuntimeError(
            f"veraPDF install finished but no CLI jar was found under {root}"
        )
    return jar


def _tag_from_verapdf_jar(jar: Path, fallback: str) -> str:
    match = re.search(r"cli-(\d+(?:\.\d+)*)\.jar$", jar.name, re.I)
    return match.group(1) if match else fallback


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
    data = _download_release_zip(
        release, timeout=max(timeout, 300.0) if tool.key == "verapdf" else timeout
    )
    if tool.key == "verapdf":
        if progress:
            progress("Installing veraPDF…")
        jar = _install_verapdf_zip_into(data, root, progress=progress)
        tag = _tag_from_verapdf_jar(jar, release.tag)
        write_installed_version(tag, tool)
        if progress:
            progress(f"Installed {tool.display_name} {tag}")
        return jar
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
    data = _download_release_zip(
        release, timeout=max(timeout, 300.0) if tool.key == "verapdf" else timeout
    )
    if tool.key == "verapdf":
        if progress:
            progress("Installing veraPDF for bundle…")
        jar = _install_verapdf_zip_into(data, target_dir, progress=progress)
        tag = _tag_from_verapdf_jar(jar, release.tag)
        write_bundled_version(tag, target_dir, tool)
        if progress:
            progress(f"Bundled {tool.display_name} {tag}")
        return jar
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


def bundle_verapdf_release(
    target_dir: Path,
    release: ReleaseInfo | None = None,
    progress: ProgressCallback | None = None,
    timeout: float = 180.0,
) -> Path:
    return bundle_tool_release(
        target_dir,
        VERAPDF_TOOL,
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


def ensure_verapdf_installed(progress: ProgressCallback | None = None) -> Path:
    return ensure_tool_installed(VERAPDF_TOOL, progress=progress)


def ensure_tools_installed(progress: ProgressCallback | None = None) -> None:
    for tool in STARTUP_TOOLS:
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
