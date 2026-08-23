#!/usr/bin/env python3
"""Download the W3C Nu HTML Checker (vnu.jar) for bundling with packaged builds."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from checkmate.paths import BUNDLED_VERSION_FILE
from checkmate.vnu_check import download_vnu_jar


def bundle_target_for_output(output: Path) -> Path:
    output = output.resolve()
    if sys.platform == "darwin" and output.suffix == ".app":
        return output / "Contents" / "vnu"
    if output.is_dir():
        return output / "vnu"
    return output.parent / "vnu"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download Nu HTML Checker (vnu.jar) into vnu/ for packaging."
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="Target vnu directory (default: ./vnu in project root)",
    )
    args = parser.parse_args()
    target = args.target or (ROOT / "vnu")

    def progress(msg: str) -> None:
        print(msg)

    jar = download_vnu_jar(target, progress=progress)
    version_file = target / BUNDLED_VERSION_FILE
    print(f"Bundled Nu HTML Checker: {jar}")
    if version_file.is_file():
        print(f"Version: {version_file.read_text(encoding='utf-8').strip()}")


if __name__ == "__main__":
    main()
