#!/usr/bin/env python3
"""Download eBraille Checker for bundling with packaged builds."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.updater import bundle_checker_release, fetch_latest_release


def bundle_target_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "checker"
    if output.is_dir():
        return output / "checker"
    return output.parent / "checker"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download eBraille Checker into checker/ for packaging."
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Target checker directory (default: ./checker in project root)",
    )
    args = parser.parse_args()
    target = args.target or (ROOT / "checker")

    def progress(msg: str) -> None:
        print(msg)

    release = fetch_latest_release()
    jar = bundle_checker_release(target, release=release, progress=progress)
    print(f"Bundled checker: {jar}")


if __name__ == "__main__":
    main()
