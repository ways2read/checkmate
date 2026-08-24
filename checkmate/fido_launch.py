"""Locate an installed Fido app so CheckMate can run ``image-report``."""

from __future__ import annotations

import logging
import mmap
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_FIDO = "CHECKMATE_FIDO"
ENV_FIDO_IMAGE_REPORT = "CHECKMATE_FIDO_IMAGE_REPORT"
_IMAGE_REPORT_MARKER = b"image-report"
# Fido build_counter when ``image-report`` landed (frozen exe compresses that string).
_IMAGE_REPORT_CLI_MIN_BUILD = 480
_cli_support_cache: dict[str, tuple[int, bool]] = {}
_WIN_EXE_NAMES = ("Fido.exe", "FIDO.exe")
_MAC_APP_NAMES = ("Fido.app", "FIDO.app")
_MAC_BIN_NAMES = ("Fido", "FIDO")
_PY_NAMES = ("FIDO.py", "Fido.py")


def find_fido_app() -> str | None:
    """Return a launch path for Fido, or None if it is not installed.

    Resolution order:
    ``CHECKMATE_FIDO`` (exe, ``FIDO.py``, or ``.app``), Windows Uninstall
    registry DisplayName **Fido**, ``%LOCALAPPDATA%\\Programs\\Fido``,
    Program Files, ``PATH``, then macOS ``/Applications`` / ``~/Applications``.
    """
    override = (os.environ.get(ENV_FIDO) or "").strip().strip('"')
    if override:
        resolved = _usable_fido_path(Path(override))
        if resolved:
            return resolved
        logger.warning("CHECKMATE_FIDO is set but not a usable Fido path: %s", override)

    if sys.platform == "win32":
        reg = _windows_registry_fido_exe()
        if reg:
            return reg
        local = os.environ.get("LOCALAPPDATA") or ""
        pf = os.environ.get("ProgramFiles") or r"C:\Program Files"
        pf86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
        for base in (
            Path(local) / "Programs" / "Fido",
            Path(local) / "Programs" / "FIDO",
            Path(pf) / "Fido",
            Path(pf) / "FIDO",
            Path(pf86) / "Fido",
            Path(pf86) / "FIDO",
        ):
            found = _exe_in_dir(base)
            if found:
                return found
        for name in _WIN_EXE_NAMES:
            which = shutil.which(name)
            if which and Path(which).is_file():
                return str(Path(which))
        return None

    if sys.platform == "darwin":
        home_apps = Path.home() / "Applications"
        for app_name in _MAC_APP_NAMES:
            for parent in (Path("/Applications"), home_apps):
                cand = parent / app_name
                if cand.is_dir():
                    return str(cand)
        for name in _MAC_BIN_NAMES:
            which = shutil.which(name)
            if which and Path(which).is_file():
                return str(Path(which))
        return None

    for name in ("fido", "Fido", "FIDO"):
        which = shutil.which(name)
        if which and Path(which).is_file():
            return str(Path(which))
    return None


def fido_cli_command(fido_path: str | None = None) -> list[str] | None:
    """Argv prefix that runs Fido CLI (before ``image-report``)."""
    path = fido_path or find_fido_app()
    if not path:
        return None
    p = Path(path)
    if p.suffix.lower() == ".py" and p.is_file():
        return [sys.executable, "-u", str(p)]
    if p.suffix.lower() == ".app" or (p.is_dir() and p.name.lower().endswith(".app")):
        exe = _macos_bundle_executable(p)
        return [str(exe)] if exe is not None else None
    if p.is_file():
        return [str(p)]
    if p.is_dir():
        found = _exe_in_dir(p)
        if found:
            return [found]
    return None


def fido_is_available() -> bool:
    return fido_cli_command() is not None


def fido_image_report_status() -> str:
    """``missing``, ``unsupported``, or ``ok`` for the installed Fido CLI."""
    if find_fido_app() is None:
        return "missing"
    if not fido_supports_image_report_cli():
        return "unsupported"
    return "ok"


def fido_supports_image_report_cli(fido_path: str | None = None) -> bool:
    """True when this Fido install understands the ``image-report`` command.

    Older Fido builds start the GUI for unknown argv. CheckMate must not
    launch those, so this inspects the install on disk (never spawns Fido).
    """
    override = (os.environ.get(ENV_FIDO_IMAGE_REPORT) or "").strip().lower()
    if override in {"0", "false", "no", "off"}:
        return False
    if override in {"1", "true", "yes", "on"}:
        return find_fido_app() is not None or bool(fido_path)

    path = fido_path or find_fido_app()
    if not path:
        return False
    launch = Path(path)
    try:
        stamp = launch.stat().st_mtime_ns
    except OSError:
        return False
    key = str(launch)
    cached = _cli_support_cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    ok = _install_has_image_report_cli(launch)
    _cli_support_cache[key] = (stamp, ok)
    return ok


def _reset_image_report_cli_cache() -> None:
    _cli_support_cache.clear()


def _install_has_image_report_cli(launch: Path) -> bool:
    files = _image_report_scan_files(launch)
    workflow = [
        path for path in files if path.name.lower().startswith("cli_workflows")
    ]
    if workflow:
        return any(_path_contains_image_report_marker(path) for path in workflow)
    build = _fido_build_number(launch)
    if build is not None:
        return build >= _IMAGE_REPORT_CLI_MIN_BUILD
    return any(_path_contains_image_report_marker(path) for path in files)


def _fido_install_dirs(launch: Path) -> list[Path]:
    dirs: list[Path] = []
    if launch.suffix.lower() == ".app" or (
        launch.is_dir() and launch.name.lower().endswith(".app")
    ):
        dirs.extend(
            (
                launch,
                launch / "Contents" / "Resources",
                launch / "Contents" / "MacOS",
            )
        )
    elif launch.is_file():
        dirs.append(launch.parent)
        dirs.append(launch.parent / "_internal")
    else:
        dirs.append(launch)
        dirs.append(launch / "_internal")
    ordered: list[Path] = []
    for folder in dirs:
        if folder not in ordered:
            ordered.append(folder)
    return ordered


def _fido_build_number(launch: Path) -> int | None:
    for folder in _fido_install_dirs(launch):
        for name in ("build_counter.txt", "version.txt"):
            path = folder / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            build = _parse_fido_build(text)
            if build is not None:
                return build
    return None


def _parse_fido_build(text: str) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return None
    first = raw.split()[0]
    if re.fullmatch(r"\d+", first):
        return int(first)
    match = re.search(r"\bbuild\s+(\d+)\b", raw, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\b\d+\.\d+\.\d+\.(\d+)\b", raw)
    if match:
        return int(match.group(1))
    return None


def _image_report_scan_files(launch: Path) -> list[Path]:
    files: list[Path] = []
    parent = launch.parent if launch.is_file() else launch
    if launch.suffix.lower() == ".app" or (
        launch.is_dir() and launch.name.lower().endswith(".app")
    ):
        parent = launch / "Contents" / "Resources"
        exe = _macos_bundle_executable(launch)
        if exe is not None:
            files.append(exe)
    elif launch.is_file():
        files.append(launch)
    for rel in (
        Path("fido") / "cli_workflows.py",
        Path("cli_workflows.py"),
        Path("_internal") / "fido" / "cli_workflows.py",
        Path("_internal") / "fido" / "cli_workflows.pyc",
    ):
        cand = parent / rel
        if cand.is_file():
            files.append(cand)
        if launch.suffix.lower() == ".app":
            res = launch / "Contents" / "Resources" / rel
            if res.is_file():
                files.append(res)
    # Prefer small Python sources over a huge frozen exe.
    py_first = [p for p in files if p.suffix.lower() in {".py", ".pyc"}]
    others = [p for p in files if p not in py_first]
    ordered: list[Path] = []
    for path in py_first + others:
        if path not in ordered:
            ordered.append(path)
    return ordered


def _path_contains_image_report_marker(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return "image-report" in text
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0:
        return False
    try:
        with path.open("rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return mm.find(_IMAGE_REPORT_MARKER) >= 0
    except (OSError, ValueError):
        try:
            return _IMAGE_REPORT_MARKER in path.read_bytes()
        except OSError:
            return False


# Alternate spellings used by fido_image_report and some call sites.
find_fido_app = find_fido_app
fido_cli_command = fido_cli_command


def _usable_fido_path(path: Path) -> str | None:
    try:
        path = path.expanduser()
    except OSError:
        return None
    if path.is_file():
        return str(path.resolve())
    if path.is_dir():
        if path.name.lower().endswith(".app"):
            return str(path.resolve())
        found = _exe_in_dir(path)
        if found:
            return found
        for py_name in _PY_NAMES:
            py = path / py_name
            if py.is_file():
                return str(py.resolve())
    return None


def _exe_in_dir(folder: Path) -> str | None:
    if not folder.is_dir():
        return None
    names = _WIN_EXE_NAMES if sys.platform == "win32" else _MAC_BIN_NAMES
    for name in names:
        cand = folder / name
        if cand.is_file():
            return str(cand.resolve())
    return None


def _macos_bundle_executable(app: Path) -> Path | None:
    macos = app / "Contents" / "MacOS"
    if not macos.is_dir():
        return None
    for name in _MAC_BIN_NAMES:
        cand = macos / name
        if cand.is_file():
            return cand
    try:
        for child in sorted(macos.iterdir()):
            if child.is_file() and os.access(child, os.X_OK):
                return child
    except OSError:
        return None
    return None


def _windows_registry_fido_exe() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    for hive, subkey in roots:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(root, name) as app_key:
                            display, _ = winreg.QueryValueEx(app_key, "DisplayName")
                            if not _display_name_is_fido(str(display)):
                                continue
                            install = ""
                            icon = ""
                            try:
                                install, _ = winreg.QueryValueEx(app_key, "InstallLocation")
                            except OSError:
                                pass
                            try:
                                icon, _ = winreg.QueryValueEx(app_key, "DisplayIcon")
                            except OSError:
                                pass
                            if install:
                                found = _exe_in_dir(Path(str(install).strip().rstrip("\\/")))
                                if found:
                                    return found
                            if icon:
                                cand = Path(str(icon).split(",")[0].strip().strip('"'))
                                if cand.suffix.lower() == ".exe" and cand.is_file():
                                    return str(cand)
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _display_name_is_fido(display: str) -> bool:
    name = display.strip().lower()
    if name == "fido":
        return True
    # Inno may append a version: "Fido 1.2.3"
    return name.startswith("fido ")
