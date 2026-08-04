#!/usr/bin/env python3
"""Download Node.js + Ace by DAISY (CLI) for bundling with packaged builds.

Produces a self-contained ``ace/`` directory:

    ace/
      node/             portable Node.js runtime
      node_modules/     @daisy/ace-cli and dependencies
      puppeteer/        pinned Chrome for Testing (Puppeteer cache)
      bundled_version.txt

``@daisy/ace-cli`` is bundled instead of ``@daisy/ace``: it exposes the same
CLI (bin/ace.js) but uses the Puppeteer axe runner only, which avoids
shipping Electron (~250 MB) that the default runner would pull in.

The build machine does not need Node installed — the downloaded portable
Node's own npm performs the install.
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import requests

NODE_DIST_INDEX = "https://nodejs.org/dist/index.json"
ACE_PACKAGE = "@daisy/ace-cli"


def _node_platform() -> tuple[str, str]:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win", "arm64" if machine == "arm64" else "x64"
    if sys.platform == "darwin":
        return "darwin", "arm64" if machine == "arm64" else "x64"
    return "linux", "arm64" if machine in ("aarch64", "arm64") else "x64"


def _latest_lts_version() -> str:
    response = requests.get(NODE_DIST_INDEX, timeout=60)
    response.raise_for_status()
    for entry in response.json():  # newest first
        if entry.get("lts"):
            return entry["version"].lstrip("v")
    raise RuntimeError("No LTS release found in Node.js dist index")


def _node_archive(version: str) -> tuple[str, str]:
    os_name, arch = _node_platform()
    if os_name == "win":
        name = f"node-v{version}-win-{arch}.zip"
    elif os_name == "darwin":
        name = f"node-v{version}-darwin-{arch}.tar.gz"
    else:
        name = f"node-v{version}-linux-{arch}.tar.xz"
    return f"https://nodejs.org/dist/v{version}/{name}", name


def _download(url: str, label: str) -> bytes:
    print(f"Downloading {label}…")
    try:
        response = requests.get(url, timeout=600, stream=True)
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
                    print(
                        f"\r  {pct}% ({downloaded // (1024 * 1024)} MB)",
                        end="",
                        flush=True,
                    )
        print()
        return data.getvalue()
    except (requests.RequestException, OSError) as exc:
        # Some Windows networks reset long streaming requests; curl is more reliable.
        if sys.platform != "win32":
            raise
        print(f"\n  requests download failed ({exc}); retrying with curl…")
        return _download_with_curl(url, label)


def _download_with_curl(url: str, label: str) -> bytes:
    curl = shutil.which("curl") or shutil.which("curl.exe")
    if not curl:
        raise RuntimeError(f"Download failed for {label} and curl was not found")
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False, suffix=".download") as tmp:
        dest = Path(tmp.name)
    try:
        proc = subprocess.run(
            [
                curl,
                "-L",
                "--fail",
                "--retry",
                "5",
                "--retry-all-errors",
                "--connect-timeout",
                "30",
                "-o",
                str(dest),
                url,
            ],
            check=False,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"curl failed downloading {label} (exit {proc.returncode})"
            )
        data = dest.read_bytes()
        print(f"  downloaded {len(data) // (1024 * 1024)} MB via curl")
        return data
    finally:
        dest.unlink(missing_ok=True)


def _install_node(target: Path, version: str) -> Path:
    """Download portable Node into ``target`` and return the node executable."""
    url, name = _node_archive(version)
    data = _download(url, name)

    staging = target.parent / f".{target.name}-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    print("Extracting Node…")
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(staging)
    elif name.endswith(".tar.gz"):
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(staging)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
            tf.extractall(staging)

    roots = [p for p in staging.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"Unexpected Node archive layout in {staging}")
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(roots[0]), str(target))
    shutil.rmtree(staging, ignore_errors=True)

    exe = node_executable(target)
    if exe is None:
        raise RuntimeError(f"Node install incomplete: {target}")
    return exe


def node_executable(node_dir: Path) -> Path | None:
    for candidate in (node_dir / "node.exe", node_dir / "bin" / "node"):
        if candidate.is_file():
            return candidate
    return None


def _npm_command(node_dir: Path) -> list[str]:
    if sys.platform == "win32":
        return [str(node_dir / "npm.cmd")]
    return [str(node_dir / "bin" / "npm")]


def _npm_env(ace_dir: Path, node_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Portable node must resolve itself; the Puppeteer postinstall must place
    # Chrome inside the bundle instead of the user-level cache.
    env["PATH"] = os.pathsep.join(
        [str(node_dir), str(node_dir / "bin"), env.get("PATH", "")]
    )
    env["PUPPETEER_CACHE_DIR"] = str(ace_dir / "puppeteer")
    # Ace launches full Chrome (headless: true); the separate headless shell
    # would only add ~340 MB of dead weight.
    env["PUPPETEER_CHROME_HEADLESS_SHELL_SKIP_DOWNLOAD"] = "true"
    return env


def _install_ace(ace_dir: Path, node_dir: Path, package_spec: str) -> str:
    """npm-install Ace into the bundle. Returns the installed version."""
    (ace_dir / "package.json").write_text(
        json.dumps({"name": "ace-bundle", "private": True}) + "\n",
        encoding="utf-8",
    )
    print(f"Installing {package_spec} (npm)…")
    proc = subprocess.run(
        [
            *_npm_command(node_dir),
            "install",
            "--no-audit",
            "--no-fund",
            "--loglevel=error",
            package_spec,
        ],
        cwd=ace_dir,
        env=_npm_env(ace_dir, node_dir),
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"npm install failed (exit {proc.returncode})")

    pkg_json = ace_dir / "node_modules" / ACE_PACKAGE / "package.json"
    if not pkg_json.is_file():
        raise RuntimeError(f"{ACE_PACKAGE} missing after install: {pkg_json}")
    return json.loads(pkg_json.read_text(encoding="utf-8"))["version"]


def _ensure_chromium(ace_dir: Path, node_exe: Path) -> Path:
    """Make sure Chrome for Testing is inside the bundle's Puppeteer cache."""
    cache = ace_dir / "puppeteer"

    def find_chrome() -> Path | None:
        if not cache.is_dir():
            return None
        exe_name = "chrome.exe" if sys.platform == "win32" else "chrome"
        for candidate in cache.rglob(exe_name):
            if candidate.is_file():
                return candidate
        # macOS app bundle
        for candidate in cache.rglob("Google Chrome for Testing"):
            if candidate.is_file():
                return candidate
        return None

    chrome = find_chrome()
    if chrome is None:
        # npm ran the postinstall already in most cases; this is the fallback
        # (e.g. postinstall skipped). puppeteer ships an install script.
        install_script = ace_dir / "node_modules" / "puppeteer" / "install.mjs"
        if not install_script.is_file():
            raise RuntimeError(
                f"Chrome missing from {cache} and no puppeteer install script "
                f"at {install_script}"
            )
        print("Downloading Chrome for Testing (puppeteer install)…")
        proc = subprocess.run(
            [str(node_exe), str(install_script)],
            cwd=install_script.parent,
            env=_npm_env(ace_dir, node_exe.parent),
            check=False,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"puppeteer install failed (exit {proc.returncode})")
        chrome = find_chrome()
    if chrome is None:
        raise RuntimeError(f"Chrome for Testing not found under {cache}")
    return chrome


def _verify(ace_dir: Path, node_exe: Path) -> str:
    ace_js = ace_dir / "node_modules" / ACE_PACKAGE / "bin" / "ace.js"
    if not ace_js.is_file():
        raise RuntimeError(f"Ace CLI entry point missing: {ace_js}")
    proc = subprocess.run(
        [str(node_exe), str(ace_js), "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=_npm_env(ace_dir, node_exe.parent),
    )
    version = (proc.stdout or proc.stderr or "").strip().splitlines()
    if proc.returncode != 0 or not version:
        raise RuntimeError(
            f"Bundled Ace failed to run (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()}"
        )
    return version[-1].strip()


def install_ace_bundle(
    target_dir: Path,
    node_version: str | None = None,
    package_spec: str = f"{ACE_PACKAGE}@latest",
) -> Path:
    """Build the self-contained Ace bundle in ``target_dir``."""
    target_dir = target_dir.resolve()
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)

    version = node_version or _latest_lts_version()
    print(f"Using Node.js v{version} (LTS)")
    node_dir = target_dir / "node"
    node_exe = _install_node(node_dir, version)

    ace_version = _install_ace(target_dir, node_dir, package_spec)
    chrome = _ensure_chromium(target_dir, node_exe)
    # Belt and braces: drop the headless shell if a puppeteer version
    # downloaded it despite the skip env var.
    shutil.rmtree(target_dir / "puppeteer" / "chrome-headless-shell", ignore_errors=True)
    reported = _verify(target_dir, node_exe)

    (target_dir / "bundled_version.txt").write_text(
        ace_version + "\n", encoding="utf-8"
    )
    print(f"Bundled Ace {ace_version} (reports: {reported})")
    print(f"  node:   {node_exe}")
    print(f"  chrome: {chrome}")
    return target_dir


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download Node + Ace (Puppeteer CLI) into ace/ for packaging."
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Target ace directory (default: ./ace in project root)",
    )
    parser.add_argument(
        "--node-version",
        help="Node.js version to bundle (default: latest LTS)",
    )
    parser.add_argument(
        "--package",
        default=f"{ACE_PACKAGE}@latest",
        help=f"npm package spec to install (default: {ACE_PACKAGE}@latest)",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = args.target or (root / "ace")
    install_ace_bundle(
        target, node_version=args.node_version, package_spec=args.package
    )


if __name__ == "__main__":
    main()
