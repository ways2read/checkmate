"""Single-instance helpers: activate an existing window and forward open paths."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Match title bar even when a status suffix is appended ("CheckMate — …").
_WINDOW_TITLE_PREFIX = "CheckMate"
_QUEUE_DIRNAME = "open_queue"
_MAX_REQUEST_AGE_SEC = 120
_POLL_MS = 400

logger = logging.getLogger(__name__)


def bring_checkmate_window_to_front() -> bool:
    """Activate an existing CheckMate main window if one is found."""
    if sys.platform == "win32":
        return _bring_to_front_win32()
    if sys.platform == "darwin":
        return _bring_to_front_darwin()
    if sys.platform.startswith("linux"):
        return _bring_to_front_linux()
    return False


def send_open_paths(paths: list[str]) -> bool:
    """
    Ask the running CheckMate instance to open *paths*.

    Writes a short-lived request under the app-data open queue. Returns True
    when the request file was written (the primary may still reject a path).
    """
    cleaned = [str(Path(p).expanduser()) for p in paths if str(p).strip()]
    if not cleaned:
        return False
    try:
        queue = _queue_dir()
        queue.mkdir(parents=True, exist_ok=True)
        payload = {
            "paths": cleaned,
            "ts": time.time(),
            "pid": os.getpid(),
        }
        # Unique name avoids clobbering concurrent second launches.
        name = f"{int(time.time() * 1000)}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        dest = queue / name
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(dest)
        return True
    except OSError as exc:
        logger.warning("Could not queue open paths for running CheckMate: %s", exc)
        return False


class OpenRequestWatcher:
    """Poll the open-queue directory and deliver paths to the primary UI."""

    def __init__(
        self,
        owner: Any,
        on_paths: Callable[[list[str]], None],
        *,
        poll_ms: int = _POLL_MS,
    ) -> None:
        import wx

        self._on_paths = on_paths
        self._timer = wx.Timer(owner)
        owner.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
        self._timer.Start(poll_ms)
        # Catch requests written during startup before the timer fires.
        wx.CallAfter(self._drain)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.Stop()
            self._timer = None

    def _on_timer(self, _event: Any) -> None:
        self._drain()

    def _drain(self) -> None:
        try:
            queue = _queue_dir()
            if not queue.is_dir():
                return
            files = sorted(queue.glob("*.json"))
        except OSError:
            return
        now = time.time()
        for path in files:
            try:
                age = now - path.stat().st_mtime
                if age > _MAX_REQUEST_AGE_SEC:
                    path.unlink(missing_ok=True)
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                path.unlink(missing_ok=True)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            raw = data.get("paths") if isinstance(data, dict) else None
            if not isinstance(raw, list):
                continue
            paths = [str(p) for p in raw if isinstance(p, str) and p.strip()]
            if not paths:
                continue
            try:
                self._on_paths(paths)
            except Exception:  # noqa: BLE001 — never break the poll loop
                logger.exception("Open-request callback failed")


def _queue_dir() -> Path:
    from .paths import app_data_dir

    return app_data_dir() / _QUEUE_DIRNAME


def _bring_to_front_win32() -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = [False]
        sw_restore = 9

        def enum_callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd) + 1
            if length <= 1:
                return True
            buf = ctypes.create_unicode_buffer(length)
            if not user32.GetWindowTextW(hwnd, buf, length):
                return True
            title = buf.value or ""
            if not title.startswith(_WINDOW_TITLE_PREFIX):
                return True
            found[0] = True
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, sw_restore)
            user32.SetForegroundWindow(hwnd)
            return False

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return found[0]
    except Exception:
        return False


def _bring_to_front_darwin() -> bool:
    try:
        script = f'''
        tell application "System Events"
            repeat with p in (every process whose background only is false)
                try
                    repeat with w in (every window of p)
                        try
                            if name of w starts with "{_WINDOW_TITLE_PREFIX}" then
                                set frontmost of p to true
                                return true
                            end if
                        end try
                    end repeat
                end try
            end repeat
        end tell
        return false
        '''
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.returncode == 0 and "true" in (out.stdout or "").strip().lower()
    except Exception:
        return False


def _bring_to_front_linux() -> bool:
    try:
        r = subprocess.run(
            ["wmctrl", "-a", _WINDOW_TITLE_PREFIX],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(
            [
                "xdotool",
                "search",
                "--name",
                f"^{_WINDOW_TITLE_PREFIX}",
                "windowactivate",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False
