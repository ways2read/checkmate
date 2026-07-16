"""Download and install Eclipse Temurin JRE for bundling with packaged builds."""

from __future__ import annotations

import io
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import requests

ADOPTIUM_API = "https://api.adoptium.net/v3/assets/latest/{version}/hotspot"
DEFAULT_JRE_VERSION = 17


def _platform_query() -> tuple[str, str]:
    system = sys.platform
    machine = platform.machine().lower()

    if system == "win32":
        os_name = "windows"
        arch = "x64" if machine in ("amd64", "x86_64") else "x86"
    elif system == "darwin":
        os_name = "mac"
        arch = "aarch64" if machine == "arm64" else "x64"
    else:
        os_name = "linux"
        if machine in ("aarch64", "arm64"):
            arch = "aarch64"
        elif machine in ("amd64", "x86_64"):
            arch = "x64"
        else:
            arch = "x86"
    return os_name, arch


def fetch_download_url(version: int = DEFAULT_JRE_VERSION) -> tuple[str, str]:
    os_name, arch = _platform_query()
    url = ADOPTIUM_API.format(version=version)
    params = {
        "os": os_name,
        "architecture": arch,
        "image_type": "jre",
    }
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    assets = response.json()
    if not assets:
        raise RuntimeError(
            f"No Temurin JRE {version} asset for {os_name}/{arch}. "
            "Try building on the target platform."
        )
    asset = assets[0]
    link = asset["binary"]["package"]["link"]
    name = asset["binary"]["package"]["name"]
    return link, name


def _find_java_home(extracted_root: Path) -> Path:
    """Return the JRE home directory (the folder that contains bin/java)."""
    exe_name = "java.exe" if sys.platform == "win32" else "java"
    for candidate in extracted_root.rglob(exe_name):
        if candidate.parent.name.lower() != "bin":
            continue
        home = candidate.parent.parent
        # macOS .jre bundle: .../Contents/Home/bin/java
        if (home / "Contents" / "Home" / "bin" / exe_name).is_file():
            return home / "Contents" / "Home"
        return home
    raise RuntimeError(f"No java executable found under {extracted_root}")


def _install_java_home(java_home: Path, target: Path) -> Path:
    target = target.resolve()
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(java_home, target)
    return target


def install_jre(target_dir: Path, version: int = DEFAULT_JRE_VERSION) -> Path:
    """Download Temurin JRE and install to target_dir (e.g. dist/.../runtime)."""
    link, name = fetch_download_url(version=version)
    print(f"Downloading {name}…")

    response = requests.get(link, timeout=300, stream=True)
    response.raise_for_status()
    data = io.BytesIO()
    total = int(response.headers.get("content-length", 0))
    downloaded = 0
    for chunk in response.iter_content(chunk_size=1024 * 256):
        if chunk:
            data.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f"\r  {pct}% ({downloaded // (1024 * 1024)} MB)", end="", flush=True)
    print()

    data.seek(0)
    staging = target_dir.parent / f".{target_dir.name}-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    print("Extracting…")
    if name.endswith(".zip"):
        with zipfile.ZipFile(data) as zf:
            zf.extractall(staging)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(fileobj=data, mode="r:gz") as tf:
            tf.extractall(staging)
    else:
        raise RuntimeError(f"Unsupported archive type: {name}")

    java_home = _find_java_home(staging)
    print(f"Installing JRE to {target_dir}…")
    _install_java_home(java_home, target_dir)
    shutil.rmtree(staging, ignore_errors=True)

    exe = target_dir / "bin" / ("java.exe" if sys.platform == "win32" else "java")
    if not exe.is_file():
        exe = target_dir / "Contents" / "Home" / "bin" / "java"
    if not exe.is_file():
        raise RuntimeError(f"JRE install incomplete: {target_dir}")
    print(f"Bundled Java: {exe}")
    return target_dir


def bundle_target_for_output(output: Path) -> Path:
    """Where runtime/ should live for a PyInstaller output path."""
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "runtime"
    if output.is_dir():
        return output / "runtime"
    return output.parent / "runtime"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Download Temurin JRE into runtime/")
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Target runtime directory (default: ./runtime in project root)",
    )
    parser.add_argument("--version", type=int, default=DEFAULT_JRE_VERSION)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = args.target or (root / "runtime")
    install_jre(target, version=args.version)


if __name__ == "__main__":
    main()
