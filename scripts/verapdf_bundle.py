#!/usr/bin/env python3
"""Download veraPDF for bundling with packaged builds."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.updater import VERAPDF_TOOL, bundle_verapdf_release, fetch_latest_release


def bundle_target_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "verapdf"
    if output.is_dir():
        return output / "verapdf"
    return output.parent / "verapdf"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download veraPDF into verapdf/ for packaging."
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Target verapdf directory (default: ./verapdf in project root)",
    )
    args = parser.parse_args()
    target = args.target or (ROOT / "verapdf")

    def progress(msg: str) -> None:
        print(msg)

    release = fetch_latest_release(VERAPDF_TOOL)
    jar = bundle_verapdf_release(target, release=release, progress=progress)
    print(f"Bundled veraPDF: {jar}")


if __name__ == "__main__":
    main()
