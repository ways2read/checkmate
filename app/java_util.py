"""Locate a usable Java runtime (bundled first, then system)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import bundled_java_dir
from .subprocess_util import hidden_run_kwargs


@dataclass
class JavaInfo:
    path: str
    version_text: str
    source: str = "system"  # "bundled" or "system"

    @property
    def short_version(self) -> str:
        # "java version \"1.8.0_471\"" or "openjdk version \"17.0.2\""
        for line in self.version_text.splitlines():
            lower = line.lower()
            if "version" in lower and '"' in line:
                start = line.find('"') + 1
                end = line.find('"', start)
                if start > 0 and end > start:
                    return line[start:end]
        return self.version_text.splitlines()[0] if self.version_text else "unknown"

    @property
    def label(self) -> str:
        version = self.short_version
        if self.source == "bundled":
            return f"Java {version} (bundled)"
        return f"Java {version}"


def _java_executable_name() -> str:
    return "java.exe" if sys.platform == "win32" else "java"


def _bundled_java_paths() -> list[str]:
    root = bundled_java_dir()
    if not root.is_dir():
        return []
    exe_name = _java_executable_name()
    direct = root / "bin" / exe_name
    if direct.is_file():
        return [str(direct)]
    # macOS Temurin layout: runtime/Contents/Home/bin/java
    home_java = root / "Contents" / "Home" / "bin" / exe_name
    if home_java.is_file():
        return [str(home_java)]
    found: list[str] = []
    for candidate in root.rglob(exe_name):
        if candidate.parent.name.lower() == "bin":
            found.append(str(candidate))
    return found


def _system_candidate_paths() -> list[str]:
    found: list[str] = []
    which = shutil.which("java")
    if which:
        found.append(which)

    env_java_home = os.environ.get("JAVA_HOME")
    if env_java_home:
        candidate = Path(env_java_home) / "bin" / _java_executable_name()
        if candidate.is_file():
            found.append(str(candidate))

    if sys.platform == "win32":
        program_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        for root in program_files:
            for pattern in ("Java", "Eclipse Adoptium", "Microsoft", "Amazon Corretto"):
                base = Path(root) / pattern
                if base.is_dir():
                    for java_exe in base.rglob("java.exe"):
                        if java_exe.parent.name.lower() == "bin":
                            found.append(str(java_exe))
    elif sys.platform == "darwin":
        home_tool = Path("/usr/libexec/java_home")
        if home_tool.is_file():
            try:
                out = subprocess.run(
                    [str(home_tool)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                    **hidden_run_kwargs(),
                )
                if out.returncode == 0 and out.stdout.strip():
                    found.append(
                        str(Path(out.stdout.strip()) / "bin" / _java_executable_name())
                    )
            except (OSError, subprocess.SubprocessError):
                pass
        jvm_root = Path("/Library/Java/JavaVirtualMachines")
        if jvm_root.is_dir():
            for java_bin in jvm_root.glob("*/Contents/Home/bin/java"):
                found.append(str(java_bin))
    else:
        for path in (
            "/usr/bin/java",
            "/usr/lib/jvm/default-java/bin/java",
            "/usr/lib/jvm/java-17-openjdk/bin/java",
            "/usr/lib/jvm/java-11-openjdk/bin/java",
        ):
            if Path(path).is_file():
                found.append(path)

    return found


def _candidate_paths() -> list[tuple[str, str]]:
    """Return (path, source) pairs in priority order."""
    ordered: list[tuple[str, str]] = []
    for path in _bundled_java_paths():
        ordered.append((path, "bundled"))
    for path in _system_candidate_paths():
        ordered.append((path, "system"))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for path, source in ordered:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append((path, source))
    return unique


_UNSET = object()
_java_cache: JavaInfo | None | object = _UNSET


def detect_java() -> JavaInfo | None:
    global _java_cache
    for path, source in _candidate_paths():
        try:
            proc = subprocess.run(
                [path, "-version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                **hidden_run_kwargs(),
            )
            text = (proc.stderr or proc.stdout or "").strip()
            if proc.returncode == 0 or text:
                info = JavaInfo(path=path, version_text=text, source=source)
                _java_cache = info
                return info
        except (OSError, subprocess.SubprocessError):
            continue
    _java_cache = None
    return None


def cached_java() -> JavaInfo | None:
    """Return last detect_java() result without spawning a process when possible."""
    if _java_cache is _UNSET:
        return detect_java()
    return _java_cache  # type: ignore[return-value]


def has_bundled_java() -> bool:
    return bool(_bundled_java_paths())
