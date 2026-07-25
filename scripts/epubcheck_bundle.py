#!/usr/bin/env python3
"""Download W3C EPUBCheck for bundling with packaged builds."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.updater import EPUBCHECK_TOOL, bundle_epubcheck_release, fetch_latest_release


def bundle_target_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "epubcheck"
    if output.is_dir():
        return output / "epubcheck"
    return output.parent / "epubcheck"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download EPUBCheck into epubcheck/ for packaging."
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Target epubcheck directory (default: ./epubcheck in project root)",
    )
    args = parser.parse_args()
    target = args.target or (ROOT / "epubcheck")

    def progress(msg: str) -> None:
        print(msg)

    release = fetch_latest_release(EPUBCHECK_TOOL)
    jar = bundle_epubcheck_release(target, release=release, progress=progress)
    print(f"Bundled EPUBCheck: {jar}")


if __name__ == "__main__":
    main()
