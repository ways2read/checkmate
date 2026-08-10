"""Helpers for running subprocesses without flashing a console on Windows."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


def hidden_run_kwargs() -> dict[str, Any]:
    """Extra kwargs so console tools (e.g. java.exe) don't flash a terminal."""
    if sys.platform != "win32":
        return {}
    # CREATE_NO_WINDOW = 0x08000000
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def format_elapsed(seconds: float) -> str:
    """Human-readable elapsed time for living progress (e.g. ``12s``, ``1m 05s``)."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60:02d}s"


# Don't show ``(Ns)`` until the check has been running this long.
ELAPSED_DISPLAY_AFTER_S = 5.0


def elapsed_progress_message(label: str, elapsed: float) -> str | None:
    """Label with timer suffix, or ``None`` if elapsed is still under the threshold."""
    if elapsed < ELAPSED_DISPLAY_AFTER_S:
        return None
    return f"{label} ({format_elapsed(elapsed)})"


def run_capturing(
    cmd: Sequence[str],
    *,
    timeout: float | None = 600,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    on_line: Callable[[str], None] | None = None,
    heartbeat: Callable[[float], None] | None = None,
    heartbeat_interval: float = 1.0,
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd``, capture stdout/stderr as text, optionally stream progress.

    ``on_line`` is called for each newline-delimited chunk from stdout or stderr
    (useful for Ace ``info:`` lines). ``heartbeat`` is called about every
    ``heartbeat_interval`` seconds with elapsed seconds since start (useful when
    a tool has no mid-run progress output).
    """
    kwargs = hidden_run_kwargs()
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=None if env is None else dict(env),
        cwd=None if cwd is None else str(cwd),
        **kwargs,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _reader(stream: Any, chunks: list[str]) -> None:
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
                if on_line is not None:
                    try:
                        on_line(line.rstrip("\r\n"))
                    except Exception:  # noqa: BLE001 — never break the reader
                        pass
        finally:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    t_out = threading.Thread(
        target=_reader, args=(proc.stdout, stdout_chunks), daemon=True
    )
    t_err = threading.Thread(
        target=_reader, args=(proc.stderr, stderr_chunks), daemon=True
    )
    t_out.start()
    t_err.start()

    start = time.monotonic()
    deadline = None if timeout is None else start + timeout
    last_beat = 0.0
    # Fire an immediate heartbeat so the UI shows early elapsed soon.
    if heartbeat is not None and heartbeat_interval > 0:
        try:
            heartbeat(0.0)
            last_beat = start
        except Exception:  # noqa: BLE001
            pass

    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                t_out.join(timeout=2)
                t_err.join(timeout=2)
                raise subprocess.TimeoutExpired(
                    cmd=list(cmd),
                    timeout=timeout if timeout is not None else 0,
                    output="".join(stdout_chunks),
                    stderr="".join(stderr_chunks),
                )
            if (
                heartbeat is not None
                and heartbeat_interval > 0
                and (now - last_beat) >= heartbeat_interval
            ):
                try:
                    heartbeat(now - start)
                except Exception:  # noqa: BLE001
                    pass
                last_beat = now
            time.sleep(0.2)
    finally:
        # Ensure readers drain after exit.
        t_out.join(timeout=30)
        t_err.join(timeout=30)

    return subprocess.CompletedProcess(
        args=list(cmd),
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )
