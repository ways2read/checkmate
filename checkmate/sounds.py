"""Completion sound effects for check results."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .models import Verdict
from .paths import sounds_dir
from .settings import sounds_enabled

logger = logging.getLogger(__name__)


def _sound_path(name: str) -> Path | None:
    path = sounds_dir() / name
    return path if path.is_file() else None


def play_completion_sound(verdict: Verdict) -> None:
    """Play a short pass/fail chime when sounds are enabled."""
    if not sounds_enabled():
        return
    if verdict in (Verdict.PASSED, Verdict.PASSED_WITH_WARNINGS):
        path = _sound_path("check-passed.wav")
    else:
        path = _sound_path("check-failed.wav")
    if path is None:
        logger.debug("Completion sound missing for verdict %s", verdict)
        return
    try:
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
    except Exception:
        logger.debug("Could not play completion sound", exc_info=True)
