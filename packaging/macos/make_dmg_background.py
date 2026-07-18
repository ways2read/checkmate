#!/usr/bin/env python3
"""Build packaging/macos/dmg_background.png (660×400).

Re-run from the repo root:

    python3 packaging/macos/make_dmg_background.py

Replace the PNG with branded artwork of the same size if you prefer;
scripts/build_macos_dmg.sh expects 660×400 (or resizes with a warning).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

W, H = 660, 400
OUT = Path(__file__).resolve().parent / "dmg_background.png"


def rgb(y: int) -> tuple[int, int, int]:
    t = y / max(H - 1, 1)
    return (int(244 - 28 * t), int(246 - 24 * t), int(252 - 20 * t))


def set_px(rr: bytearray, px: int, py: int, col: tuple[int, int, int]) -> None:
    if 0 <= px < W and 0 <= py < H:
        i = py * (1 + W * 3) + 1 + px * 3
        rr[i : i + 3] = bytes(col)


def point_in_triangle(
    x: float,
    y: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
) -> bool:
    def cross(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> float:
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)

    d1 = cross(x, y, ax, ay, bx, by)
    d2 = cross(x, y, bx, by, cx, cy)
    d3 = cross(x, y, cx, cy, ax, ay)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def draw_arrow_right(
    rr: bytearray,
    stem_left: int,
    stem_right: int,
    cy: int,
    stem_half: int,
    tip_x: int,
    head_back: int,
    head_half_h: int,
    col: tuple[int, int, int],
) -> None:
    for y in range(cy - stem_half, cy + stem_half + 1):
        for x in range(stem_left, min(stem_right, W)):
            set_px(rr, x, y, col)
    ax, ay = float(tip_x), float(cy)
    bx, by = float(tip_x - head_back), float(cy - head_half_h)
    cx_, cy_ = float(tip_x - head_back), float(cy + head_half_h)
    minx = int(max(0, min(ax, bx, cx_)))
    maxx = int(min(W - 1, max(ax, bx, cx_)))
    miny = int(max(0, min(ay, by, cy_)))
    maxy = int(min(H - 1, max(ay, by, cy_)))
    for y in range(miny, maxy + 1):
        for x in range(minx, maxx + 1):
            if point_in_triangle(float(x) + 0.5, float(y) + 0.5, ax, ay, bx, by, cx_, cy_):
                set_px(rr, x, y, col)


def main() -> None:
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        for _x in range(W):
            raw.extend(rgb(y))

    draw_arrow_right(
        raw,
        stem_left=120,
        stem_right=400,
        cy=H // 2,
        stem_half=3,
        tip_x=455,
        head_back=55,
        head_half_h=38,
        col=(90, 98, 118),
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    comp = zlib.compress(bytes(raw), 9)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    OUT.write_bytes(png)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
