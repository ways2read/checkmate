"""UI sound effects for check start and completion."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .models import Verdict
from .paths import sounds_dir
from .settings import sound_scheme

logger = logging.getLogger(__name__)


def _sound_path(name: str) -> Path | None:
    path = sounds_dir() / name
    return path if path.is_file() else None


def _play_wav(path: Path) -> None:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return
    import wx.adv

    sound = wx.adv.Sound(str(path))
    if sound.IsOk():
        sound.Play(wx.adv.SOUND_ASYNC)


def _play_named(stem: str) -> None:
    """Play ``{stem}-{scheme}.wav`` for the selected scheme, or no-op if off."""
    scheme = sound_scheme()
    if scheme == "off":
        return
    path = _sound_path(f"{stem}-{scheme}.wav")
    if path is None:
        logger.debug("Sound missing: %s (scheme %s)", stem, scheme)
        return
    try:
        _play_wav(path)
    except Exception:
        logger.debug("Could not play sound %s", stem, exc_info=True)


def play_started_sound() -> None:
    """Play a short chime when a check begins."""
    _play_named("check-started")


def play_completion_sound(verdict: Verdict) -> None:
    """Play a short pass/fail chime when a check finishes."""
    if verdict in (Verdict.PASSED, Verdict.PASSED_WITH_WARNINGS):
        _play_named("check-passed")
    else:
        _play_named("check-failed")
