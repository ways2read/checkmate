"""OS-specific application data paths and checker/tool locations."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP_NAME = "CheckMate"

CHECKER_REPO = "daisy/ebraille-checker"
CHECKER_RELEASES_API = (
    f"https://api.github.com/repos/{CHECKER_REPO}/releases/latest"
)
CHECKER_RELEASES_PAGE = f"https://github.com/{CHECKER_REPO}/releases"
CHECKER_REPO_PAGE = f"https://github.com/{CHECKER_REPO}"

EPUBCHECK_REPO = "w3c/epubcheck"
EPUBCHECK_RELEASES_API = (
    f"https://api.github.com/repos/{EPUBCHECK_REPO}/releases/latest"
)
EPUBCHECK_RELEASES_PAGE = f"https://github.com/{EPUBCHECK_REPO}/releases"
EPUBCHECK_REPO_PAGE = f"https://github.com/{EPUBCHECK_REPO}"

# veraPDF ships installers from its download site (not GitHub release assets).
VERAPDF_HOME_PAGE = "https://verapdf.org/"
VERAPDF_DOWNLOAD_PAGE = "https://software.verapdf.org/rel/"
VERAPDF_RELEASES_PAGE = VERAPDF_DOWNLOAD_PAGE
VERAPDF_INSTALLER_ZIP_URL = (
    "https://software.verapdf.org/rel/verapdf-installer.zip"
)

DAISY_WEBSITE = "https://daisy.org/"
EBRAILLE_STANDARD_PAGE = "https://daisy.org/activities/standards/ebraille/"
EBRAILLE_SPEC_URL = "https://daisy.org/s/ebraille/"

BUNDLED_JAVA_DIRNAME = "runtime"
BUNDLED_CHECKER_DIRNAME = "checker"
BUNDLED_EPUBCHECK_DIRNAME = "epubcheck"
BUNDLED_VERAPDF_DIRNAME = "verapdf"
BUNDLED_ACE_DIRNAME = "ace"
BUNDLED_VERSION_FILE = "bundled_version.txt"
BUNDLED_CHECKER_VERSION_FILE = BUNDLED_VERSION_FILE


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def application_dir() -> Path:
    """Directory containing the app bundle (exe, .app Contents, or project root)."""
    if is_frozen():
        exe = Path(sys.executable).resolve()
        if sys.platform == "darwin" and exe.parent.name == "MacOS":
            return exe.parent.parent  # .app/Contents
        return exe.parent
    return Path(__file__).resolve().parents[1]


def bundled_java_dir() -> Path:
    return application_dir() / BUNDLED_JAVA_DIRNAME


def bundled_checker_dir() -> Path:
    return application_dir() / BUNDLED_CHECKER_DIRNAME


def bundled_epubcheck_dir() -> Path:
    return application_dir() / BUNDLED_EPUBCHECK_DIRNAME


def bundled_verapdf_dir() -> Path:
    return application_dir() / BUNDLED_VERAPDF_DIRNAME


def bundled_ace_dir() -> Path:
    return application_dir() / BUNDLED_ACE_DIRNAME


def images_dir() -> Path:
    """Directory with UI PNGs (result status icons, etc.)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass is not None:
            bundled = Path(meipass) / "images"
            if bundled.is_dir():
                return bundled
        beside = application_dir() / "images"
        if beside.is_dir():
            return beside
        if meipass is not None:
            return Path(meipass) / "images"
    return application_dir() / "images"


def sounds_dir() -> Path:
    """Directory with UI WAV sound effects (check completion chimes)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass is not None:
            bundled = Path(meipass) / "sounds"
            if bundled.is_dir():
                return bundled
        beside = application_dir() / "sounds"
        if beside.is_dir():
            return beside
        if meipass is not None:
            return Path(meipass) / "sounds"
    return application_dir() / "sounds"


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def checker_dir() -> Path:
    path = app_data_dir() / "checker"
    path.mkdir(parents=True, exist_ok=True)
    return path


def epubcheck_dir() -> Path:
    path = app_data_dir() / "epubcheck"
    path.mkdir(parents=True, exist_ok=True)
    return path


def verapdf_dir() -> Path:
    path = app_data_dir() / "verapdf"
    path.mkdir(parents=True, exist_ok=True)
    return path


def version_file() -> Path:
    return checker_dir() / "installed_version.txt"


def epubcheck_version_file() -> Path:
    return epubcheck_dir() / "installed_version.txt"


def verapdf_version_file() -> Path:
    return verapdf_dir() / "installed_version.txt"


def bundled_version_file() -> Path:
    return bundled_checker_dir() / BUNDLED_VERSION_FILE


def bundled_epubcheck_version_file() -> Path:
    return bundled_epubcheck_dir() / BUNDLED_VERSION_FILE


def bundled_verapdf_version_file() -> Path:
    return bundled_verapdf_dir() / BUNDLED_VERSION_FILE


def _find_jar_in_tree(
    root: Path,
    *,
    preferred_names: tuple[str, ...],
    name_pattern: str,
) -> Path | None:
    if not root.is_dir():
        return None
    for name in preferred_names:
        direct = root / name
        if direct.is_file():
            return direct
        matches = sorted(root.rglob(name))
        if matches:
            return matches[0]
    jar_candidates = [
        p
        for p in root.rglob("*.jar")
        if re.search(name_pattern, p.name, re.I)
        and "javadoc" not in p.name.lower()
        and "sources" not in p.name.lower()
    ]
    return jar_candidates[0] if jar_candidates else None


def find_ebraille_jar_in_tree(root: Path) -> Path | None:
    return _find_jar_in_tree(
        root,
        preferred_names=("ebraille-checker.jar",),
        name_pattern=r"ebraille-checker",
    )


def find_epubcheck_jar_in_tree(root: Path) -> Path | None:
    return _find_jar_in_tree(
        root,
        preferred_names=("epubcheck.jar",),
        name_pattern=r"^epubcheck",
    )


def find_verapdf_cli_jar_in_tree(root: Path) -> Path | None:
    """Locate the veraPDF CLI jar produced by the greenfield installer."""
    if not root.is_dir():
        return None

    from packaging.version import InvalidVersion, Version

    def _cli_version(path: Path) -> Version | None:
        match = re.search(r"cli-(\d+(?:\.\d+)*)\.jar$", path.name, re.I)
        if not match:
            return None
        try:
            return Version(match.group(1))
        except InvalidVersion:
            return None

    candidates: list[Path] = []
    bin_dir = root / "bin"
    if bin_dir.is_dir():
        candidates.extend(p for p in bin_dir.glob("cli-*.jar") if p.is_file())
    if not candidates:
        candidates = [
            p
            for p in root.rglob("cli-*.jar")
            if p.is_file() and "javadoc" not in p.name.lower()
        ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda p: (_cli_version(p) is not None, _cli_version(p) or p.name),
    )


def find_app_data_checker_jar() -> Path | None:
    return find_ebraille_jar_in_tree(checker_dir())


def find_bundled_checker_jar() -> Path | None:
    return find_ebraille_jar_in_tree(bundled_checker_dir())


def find_checker_jar() -> Path | None:
    """Resolve eBraille checker jar: app-data copy first, then bundled copy."""
    return find_app_data_checker_jar() or find_bundled_checker_jar()


def checker_uses_bundled_copy() -> bool:
    return find_app_data_checker_jar() is None and find_bundled_checker_jar() is not None


def find_app_data_epubcheck_jar() -> Path | None:
    return find_epubcheck_jar_in_tree(epubcheck_dir())


def find_bundled_epubcheck_jar() -> Path | None:
    return find_epubcheck_jar_in_tree(bundled_epubcheck_dir())


def find_epubcheck_jar() -> Path | None:
    """Resolve EPUBCheck jar: app-data copy first, then bundled copy."""
    return find_app_data_epubcheck_jar() or find_bundled_epubcheck_jar()


def epubcheck_uses_bundled_copy() -> bool:
    return (
        find_app_data_epubcheck_jar() is None
        and find_bundled_epubcheck_jar() is not None
    )


def find_app_data_verapdf_jar() -> Path | None:
    return find_verapdf_cli_jar_in_tree(verapdf_dir())


def find_bundled_verapdf_jar() -> Path | None:
    return find_verapdf_cli_jar_in_tree(bundled_verapdf_dir())


def find_verapdf_jar() -> Path | None:
    """Resolve veraPDF CLI jar: app-data copy first, then bundled copy."""
    return find_app_data_verapdf_jar() or find_bundled_verapdf_jar()


def verapdf_uses_bundled_copy() -> bool:
    return (
        find_app_data_verapdf_jar() is None and find_bundled_verapdf_jar() is not None
    )
