#!/usr/bin/env python3
"""Build a standalone eBraille Checker GUI binary with PyInstaller.

Usage (from the project root):

    uv sync --extra dev
    uv run python scripts/package.py
    uv run python scripts/package.py --onefile
    uv run python scripts/package.py --clean
    uv run python scripts/package.py --no-bundle-java

Output is written to dist/. Packaged builds include a bundled Temurin JRE by
default so end users do not need Java on their PATH.
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "eBrailleChecker"
ENTRY = ROOT / "run.py"
APP_ICON_ICO = ROOT / "installer" / "eBrailleChecker.ico"
APP_ICON_ICNS = ROOT / "installer" / "eBrailleChecker.icns"


def _app_icon() -> Path | None:
    if sys.platform == "darwin" and APP_ICON_ICNS.is_file():
        return APP_ICON_ICNS
    if APP_ICON_ICO.is_file():
        return APP_ICON_ICO
    return None


def _project_version() -> str:
    init = ROOT / "app" / "__init__.py"
    if init.is_file():
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                # __version__ = "0.1.0"
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip("\"'")
    return "0.0.0"


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed.", file=sys.stderr)
        print("Install build dependencies with:", file=sys.stderr)
        print("  uv sync --extra dev", file=sys.stderr)
        print("  # or: pip install -r requirements-dev.txt", file=sys.stderr)
        sys.exit(1)


def _resolve_output(dist_dir: Path, onefile: bool) -> Path:
    if onefile:
        if sys.platform == "win32":
            return dist_dir / f"{APP_NAME}.exe"
        return dist_dir / APP_NAME
    if sys.platform == "darwin":
        app_bundle = dist_dir / f"{APP_NAME}.app"
        if app_bundle.exists():
            return app_bundle
    return dist_dir / APP_NAME


def _runtime_dir_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "runtime"
    if output.is_dir():
        return output / "runtime"
    return output.parent / "runtime"


def _checker_dir_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "checker"
    if output.is_dir():
        return output / "checker"
    return output.parent / "checker"


def _epubcheck_dir_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "epubcheck"
    if output.is_dir():
        return output / "epubcheck"
    return output.parent / "epubcheck"


def _verapdf_dir_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "verapdf"
    if output.is_dir():
        return output / "verapdf"
    return output.parent / "verapdf"


def _load_info_plist(app_bundle: Path) -> tuple[Path, dict] | None:
    info_plist = app_bundle / "Contents" / "Info.plist"
    if not info_plist.is_file():
        print(f"Warning: Info.plist not found at {info_plist}", file=sys.stderr)
        return None
    with info_plist.open("rb") as fh:
        return info_plist, plistlib.load(fh)


def _save_info_plist(info_plist: Path, info: dict) -> None:
    with info_plist.open("wb") as fh:
        plistlib.dump(info, fh)


def _patch_macos_document_types(app_bundle: Path) -> None:
    """Declare .ebrl/.epub/.pdf for Finder Open With (Alternate — not the default)."""
    loaded = _load_info_plist(app_bundle)
    if loaded is None:
        return
    info_plist, info = loaded

    info["CFBundleDocumentTypes"] = [
        {
            "CFBundleTypeName": "eBraille Publication",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["org.daisy.ebraille"],
            "CFBundleTypeExtensions": ["ebrl"],
        },
        {
            "CFBundleTypeName": "EPUB Publication",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["org.idpf.epub-container"],
            "CFBundleTypeExtensions": ["epub"],
        },
        {
            "CFBundleTypeName": "PDF Document",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["com.adobe.pdf"],
            "CFBundleTypeExtensions": ["pdf"],
        },
    ]
    info["UTExportedTypeDeclarations"] = [
        {
            "UTTypeIdentifier": "org.daisy.ebraille",
            "UTTypeDescription": "eBraille Publication",
            "UTTypeConformsTo": ["public.data", "public.composite-content"],
            "UTTypeTagSpecification": {
                "public.filename-extension": ["ebrl"],
            },
        }
    ]
    # Ensure the process can receive Apple Events for document open
    info["NSAppleScriptEnabled"] = True

    _save_info_plist(info_plist, info)
    print(f"Registered .ebrl/.epub/.pdf document types in {info_plist}")


def _patch_macos_bundle_version(app_bundle: Path, version: str) -> None:
    loaded = _load_info_plist(app_bundle)
    if loaded is None:
        return
    info_plist, info = loaded
    info["CFBundleShortVersionString"] = version
    info["CFBundleVersion"] = version
    _save_info_plist(info_plist, info)
    print(f"Set bundle version {version} in {info_plist}")


def build(
    onefile: bool,
    clean: bool,
    bundle_java: bool,
    bundle_checker: bool,
    bundle_epubcheck: bool,
    bundle_verapdf: bool,
) -> Path:
    _ensure_pyinstaller()

    if not ENTRY.is_file():
        print(f"Entry point not found: {ENTRY}", file=sys.stderr)
        sys.exit(1)

    dist_dir = ROOT / "dist"
    build_dir = ROOT / "build"
    work_dir = build_dir / "pyinstaller"

    if clean:
        for path in (dist_dir / APP_NAME, dist_dir / f"{APP_NAME}.exe", work_dir):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file():
                path.unlink(missing_ok=True)
        for leftover in dist_dir.glob(f"{APP_NAME}*"):
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            else:
                leftover.unlink(missing_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        APP_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(build_dir),
        "--hidden-import",
        "wx",
        "--hidden-import",
        "wx.adv",
        "--hidden-import",
        "wx.lib.newevent",
        "--hidden-import",
        "requests",
        "--hidden-import",
        "packaging",
        "--hidden-import",
        "packaging.version",
        "--hidden-import",
        "fitz",
        "--collect-all",
        "pymupdf",
        "--collect-submodules",
        "app",
    ]

    icon = _app_icon()
    if icon is not None:
        cmd.extend(["--icon", str(icon)])
    else:
        print(
            f"Warning: app icon not found "
            f"(looked for {APP_ICON_ICNS.name} / {APP_ICON_ICO.name})",
            file=sys.stderr,
        )

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.append(str(ENTRY))

    print("Running:")
    print(" ", " ".join(cmd))
    print()

    subprocess.run(cmd, cwd=ROOT, check=True)
    output = _resolve_output(dist_dir, onefile)

    if (
        sys.platform == "darwin"
        and output.is_dir()
        and output.suffix == ".app"
        and not onefile
    ):
        print()
        print("Registering .ebrl/.epub/.pdf document types in Info.plist…")
        _patch_macos_document_types(output)
        _patch_macos_bundle_version(output, _project_version())

    if bundle_java:
        if onefile:
            print(
                "Warning: --onefile with bundled Java is not supported; "
                "place a runtime/ folder next to the executable manually, "
                "or use onedir (default).",
                file=sys.stderr,
            )
        else:
            runtime_dir = _runtime_dir_for_output(output)
            print()
            print(f"Bundling Temurin JRE into {runtime_dir}…")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "jre_bundle.py"),
                    str(runtime_dir),
                ],
                cwd=ROOT,
                check=True,
            )

    if bundle_checker:
        if onefile:
            print(
                "Warning: --onefile with bundled checker is not supported; "
                "use onedir (default).",
                file=sys.stderr,
            )
        else:
            checker_dir = _checker_dir_for_output(output)
            print()
            print(f"Bundling eBraille Checker into {checker_dir}…")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "checker_bundle.py"),
                    str(checker_dir),
                ],
                cwd=ROOT,
                check=True,
            )

    if bundle_epubcheck:
        if onefile:
            print(
                "Warning: --onefile with bundled EPUBCheck is not supported; "
                "use onedir (default).",
                file=sys.stderr,
            )
        else:
            epubcheck_dir = _epubcheck_dir_for_output(output)
            print()
            print(f"Bundling EPUBCheck into {epubcheck_dir}…")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "epubcheck_bundle.py"),
                    str(epubcheck_dir),
                ],
                cwd=ROOT,
                check=True,
            )

    if bundle_verapdf:
        if onefile:
            print(
                "Warning: --onefile with bundled veraPDF is not supported; "
                "use onedir (default).",
                file=sys.stderr,
            )
        else:
            verapdf_dir = _verapdf_dir_for_output(output)
            print()
            print(f"Bundling veraPDF into {verapdf_dir}…")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "verapdf_bundle.py"),
                    str(verapdf_dir),
                ],
                cwd=ROOT,
                check=True,
            )

    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package eBraille Checker GUI with PyInstaller."
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Build a single executable (slower start; default is onedir).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove previous dist/build outputs for this app before packaging.",
    )
    parser.add_argument(
        "--no-bundle-java",
        action="store_true",
        help="Skip downloading Temurin JRE (users must have Java on PATH).",
    )
    parser.add_argument(
        "--no-bundle-checker",
        action="store_true",
        help="Skip bundling eBraille Checker (checker downloaded on first run).",
    )
    parser.add_argument(
        "--no-bundle-epubcheck",
        action="store_true",
        help="Skip bundling EPUBCheck (downloaded on first run).",
    )
    parser.add_argument(
        "--no-bundle-verapdf",
        action="store_true",
        help="Skip bundling veraPDF (downloaded on first PDF check).",
    )
    args = parser.parse_args()

    try:
        output = build(
            onefile=args.onefile,
            clean=args.clean,
            bundle_java=not args.no_bundle_java,
            bundle_checker=not args.no_bundle_checker,
            bundle_epubcheck=not args.no_bundle_epubcheck,
            bundle_verapdf=not args.no_bundle_verapdf,
        )
    except subprocess.CalledProcessError as exc:
        print(f"\nPyInstaller failed with exit code {exc.returncode}.", file=sys.stderr)
        sys.exit(exc.returncode)
    except Exception as exc:  # noqa: BLE001 — show bundling errors clearly
        print(f"\nPackaging failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print()
    print("Build complete.")
    print(f"Output: {output}")
    if output.is_dir():
        exe = output / (f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME)
        if exe.exists():
            print(f"Launch:  {exe}")
    elif output.suffix == ".app":
        exe = output / "Contents" / "MacOS" / APP_NAME
        if exe.exists():
            print(f"Launch:  {exe}")

    print()
    if args.no_bundle_java:
        print("Note: Java was not bundled. End users need a system JRE on PATH.")
    else:
        print("Bundled Temurin JRE is included (runtime/). No system Java required.")
    if args.no_bundle_checker:
        print("Note: eBraille Checker was not bundled. It will be downloaded on first run.")
    else:
        print(
            "Bundled eBraille Checker is included (checker/). "
            "Updates install to application data when a newer release exists."
        )
    if args.no_bundle_epubcheck:
        print("Note: EPUBCheck was not bundled. It will be downloaded on first run.")
    else:
        print(
            "Bundled EPUBCheck is included (epubcheck/). "
            "Updates install to application data when a newer release exists."
        )
    if args.no_bundle_verapdf:
        print(
            "Note: veraPDF was not bundled. It will be downloaded on first PDF check."
        )
    else:
        print(
            "Bundled veraPDF is included (verapdf/). "
            "Updates install to application data when a newer release exists."
        )


if __name__ == "__main__":
    main()
