"""OS-specific application data paths."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP_NAME = "eBrailleCheckerGUI"
CHECKER_REPO = "daisy/ebraille-checker"
CHECKER_RELEASES_API = (
    f"https://api.github.com/repos/{CHECKER_REPO}/releases/latest"
)
CHECKER_RELEASES_PAGE = f"https://github.com/{CHECKER_REPO}/releases"
CHECKER_REPO_PAGE = f"https://github.com/{CHECKER_REPO}"
DAISY_WEBSITE = "https://daisy.org/"
EBRAILLE_STANDARD_PAGE = "https://daisy.org/activities/standards/ebraille/"
EBRAILLE_SPEC_URL = "https://daisy.org/s/ebraille/"
BUNDLED_JAVA_DIRNAME = "runtime"
BUNDLED_CHECKER_DIRNAME = "checker"
BUNDLED_CHECKER_VERSION_FILE = "bundled_version.txt"


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


def version_file() -> Path:
    return checker_dir() / "installed_version.txt"


def bundled_version_file() -> Path:
    return bundled_checker_dir() / BUNDLED_CHECKER_VERSION_FILE


def _find_jar_in_tree(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    direct = root / "ebraille-checker.jar"
    if direct.is_file():
        return direct
    matches = sorted(root.rglob("ebraille-checker.jar"))
    if matches:
        return matches[0]
    jar_candidates = [
        p
        for p in root.rglob("*.jar")
        if re.search(r"ebraille-checker|epubcheck", p.name, re.I)
        and "javadoc" not in p.name.lower()
        and "sources" not in p.name.lower()
    ]
    return jar_candidates[0] if jar_candidates else None


def find_app_data_checker_jar() -> Path | None:
    """Checker jar installed/updated under application data."""
    return _find_jar_in_tree(checker_dir())


def find_bundled_checker_jar() -> Path | None:
    """Checker jar shipped alongside the packaged application."""
    return _find_jar_in_tree(bundled_checker_dir())


def find_checker_jar() -> Path | None:
    """Resolve checker jar: app-data copy first, then bundled copy."""
    return find_app_data_checker_jar() or find_bundled_checker_jar()


def checker_uses_bundled_copy() -> bool:
    return find_app_data_checker_jar() is None and find_bundled_checker_jar() is not None
