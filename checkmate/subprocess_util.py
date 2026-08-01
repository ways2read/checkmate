"""Helpers for running subprocesses without flashing a console on Windows."""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def hidden_run_kwargs() -> dict[str, Any]:
    """Extra kwargs so console tools (e.g. java.exe) don't flash a terminal."""
    if sys.platform != "win32":
        return {}
    # CREATE_NO_WINDOW = 0x08000000
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
