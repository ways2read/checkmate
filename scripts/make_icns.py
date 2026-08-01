#!/usr/bin/env python3
"""Build installer/CheckMate.icns for macOS (iconutil).

Defaults to the Windows .ico master so Dock / Finder match the flatter
installer icon. Use --from-png for installer/icon.png instead.

    uv run python scripts/make_icns.py
    uv run python scripts/make_icns.py --from-png
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_PNG = ROOT / "installer" / "icon.png"
SRC_ICO = ROOT / "installer" / "CheckMate.ico"
OUT = ROOT / "installer" / "CheckMate.icns"

# (pixel size, iconset filename) — standard iconutil set only
SIZES: list[tuple[int, str]] = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-png",
        action="store_true",
        help="Use installer/icon.png as the master instead of the .ico.",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("This script is for macOS (iconutil).", file=sys.stderr)
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="checkmate-iconset-"))
    iconset = tmp / "CheckMate.iconset"
    try:
        iconset.mkdir()
        master = tmp / "master.png"
        if args.from_png:
            if not SRC_PNG.is_file():
                print(f"Not found: {SRC_PNG}", file=sys.stderr)
                return 1
            shutil.copy2(SRC_PNG, master)
            print(f"Icon master: {SRC_PNG}")
        else:
            if not SRC_ICO.is_file():
                print(f"Not found: {SRC_ICO}", file=sys.stderr)
                return 1
            subprocess.run(
                ["sips", "-s", "format", "png", str(SRC_ICO), "--out", str(master)],
                check=True,
                capture_output=True,
            )
            print(f"Icon master: {SRC_ICO}")

        for size, name in SIZES:
            dest = iconset / name
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(master), "--out", str(dest)],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)],
            check=True,
        )
        print(f"Created: {OUT}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
