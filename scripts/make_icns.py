#!/usr/bin/env python3
"""Build installer/CheckMate.icns (and .ico) from installer/icon.png.

The Dock / installer mark is the blue CheckMate artwork on a white
background (``installer/icon.png``). The green PNGs under ``images/`` are
in-app status graphics only and must not be used as the app icon.

    uv run python scripts/make_icns.py
    uv run python scripts/make_icns.py --from-png
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_PNG = ROOT / "installer" / "icon.png"
SRC_ICO = ROOT / "installer" / "CheckMate.ico"
OUT = ROOT / "installer" / "CheckMate.icns"
_WX_APP = None


def _ensure_wx_app():
    """wx.Image requires a living wx.App; an unreferenced App is collected immediately."""
    global _WX_APP
    import wx

    try:
        if wx.GetApp() is not None:
            return
    except Exception:
        pass
    _WX_APP = wx.App(False)


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


def _write_ico_from_png(png_path: Path, ico_path: Path) -> None:
    """Write a Vista+ ICO that embeds the PNG (no Pillow required)."""
    data = png_path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG: {png_path}")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    w = 0 if width >= 256 else width
    h = 0 if height >= 256 else height
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), 22)
    ico_path.write_bytes(header + entry + data)


def _restore_png_from_git() -> bool:
    """Recover installer/icon.png from HEAD if the working copy was overwritten."""
    proc = subprocess.run(
        ["git", "show", "HEAD:installer/icon.png"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.startswith(b"\x89PNG"):
        return False
    SRC_PNG.write_bytes(proc.stdout)
    print(f"Restored {SRC_PNG} from git HEAD")
    return True


def _png_is_green_mark(path: Path) -> bool:
    """True when non-background pixels are green (in-app status art, not the app icon)."""
    try:
        import wx
    except ImportError:
        return False
    _ensure_wx_app()
    img = wx.Image(str(path), wx.BITMAP_TYPE_PNG)
    if not img.IsOk():
        return False
    data = img.GetData()
    green = blue = count = 0
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        if r < 40 and g < 40 and b < 40:
            continue
        if r > 240 and g > 240 and b > 240:
            continue
        green += g
        blue += b
        count += 1
    return count > 0 and green > blue * 2


def _fill_black_background_white(path: Path) -> None:
    """Replace the connected black background with white; leave the blue mark."""
    import wx

    _ensure_wx_app()
    img = wx.Image(str(path), wx.BITMAP_TYPE_PNG)
    if not img.IsOk():
        raise RuntimeError(f"Could not read {path}")
    w, h = img.GetWidth(), img.GetHeight()
    data = bytearray(img.GetData())

    def idx(x: int, y: int) -> int:
        return (y * w + x) * 3

    def is_bg(x: int, y: int) -> bool:
        i = idx(x, y)
        return data[i] < 28 and data[i + 1] < 28 and data[i + 2] < 28

    seen = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))
    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        p = y * w + x
        if seen[p]:
            continue
        seen[p] = 1
        if not is_bg(x, y):
            continue
        i = idx(x, y)
        data[i : i + 3] = b"\xff\xff\xff"
        q.append((x - 1, y))
        q.append((x + 1, y))
        q.append((x, y - 1))
        q.append((x, y + 1))
    img.SetData(bytes(data))
    img.SaveFile(str(path), wx.BITMAP_TYPE_PNG)
    print(f"Set white background: {path}")


def _prepare_icon_png() -> Path:
    if SRC_PNG.is_file() and _png_is_green_mark(SRC_PNG):
        print(f"Refusing green in-app artwork as the app icon: {SRC_PNG}")
        if not _restore_png_from_git():
            raise SystemExit(
                "installer/icon.png is the green status graphic. Restore the "
                "blue CheckMate artwork (git checkout -- installer/icon.png)."
            )
    if not SRC_PNG.is_file():
        if not _restore_png_from_git():
            raise SystemExit(f"Not found: {SRC_PNG}")
    _fill_black_background_white(SRC_PNG)
    return SRC_PNG


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-png",
        action="store_true",
        help="Use installer/icon.png as the master (default on macOS packaging).",
    )
    args = parser.parse_args()

    master: Path | None = None
    if args.from_png or not SRC_ICO.is_file():
        args.from_png = True
        master = _prepare_icon_png()
        try:
            _write_ico_from_png(master, SRC_ICO)
            print(f"Created: {SRC_ICO}")
        except Exception as exc:
            print(f"Warning: could not write {SRC_ICO}: {exc}", file=sys.stderr)

    if sys.platform != "darwin":
        if not args.from_png:
            print("This script builds .icns on macOS (iconutil).", file=sys.stderr)
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="checkmate-iconset-"))
    iconset = tmp / "CheckMate.iconset"
    try:
        iconset.mkdir()
        png_master = tmp / "master.png"
        if args.from_png:
            assert master is not None
            shutil.copy2(master, png_master)
            print(f"Icon master: {master}")
        else:
            subprocess.run(
                ["sips", "-s", "format", "png", str(SRC_ICO), "--out", str(png_master)],
                check=True,
                capture_output=True,
            )
            print(f"Icon master: {SRC_ICO}")

        for size, name in SIZES:
            dest = iconset / name
            subprocess.run(
                ["sips", "-z", str(size), str(size), str(png_master), "--out", str(dest)],
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
