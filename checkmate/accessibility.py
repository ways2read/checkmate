"""Screen-reader speech helpers (adapted from FIDO's accessibility_helpers).

Uses accessible_output2 on Windows to speak through NVDA/JAWS with interrupt
support so progress updates do not queue stale phrases. SAPI-only fallback is
disabled — sighted users already see the progress dialog text.
"""

from __future__ import annotations

import logging
import platform
import threading
import time

logger = logging.getLogger("checkmate.accessibility")

_last_announce_time: float = 0.0
_last_announce_text: str | None = None


class ScreenReaderProvider:
    def __init__(self) -> None:
        self.active = False
        self.speaker = None
        self.last_spoken_text: str | None = None
        self.last_spoken_time = 0.0
        if platform.system() != "Windows":
            logger.info("Screen reader provider only supports Windows")
            return
        try:
            from accessible_output2.outputs import auto

            self.speaker = auto.Auto()
            active_name = "Unknown"
            try:
                for o in self.speaker.outputs:
                    if o.is_active():
                        active_name = o.name
                        break
                logger.info(
                    "ScreenReaderProvider initialized. Active backend: %s",
                    active_name,
                )
                if "sapi" in active_name.lower():
                    # Avoid speaking through SAPI when no SR is running.
                    self.active = False
                    self.speaker = None
                else:
                    self.active = True
            except Exception as exc:
                logger.warning("Could not determine active backend: %s", exc)
                self.active = True
        except ImportError:
            logger.error(
                "accessible_output2 library not found. "
                "Screen reader announcements disabled."
            )
        except Exception as exc:
            logger.error(
                "Failed to initialize accessible_output2: %s", exc, exc_info=True
            )

    def speak(self, text: str, interrupt: bool = False) -> None:
        if not text or not self.active or not self.speaker:
            return
        current_time = time.time()
        if (
            self.last_spoken_text == text
            and (current_time - self.last_spoken_time) < 5.0
        ):
            logger.debug("Skipping duplicate speech: %r", text)
            return
        try:
            self.speaker.speak(text, interrupt=interrupt)
            logger.debug("Spoken: %r (interrupt=%s)", text, interrupt)
            self.last_spoken_text = text
            self.last_spoken_time = current_time
        except Exception as exc:
            logger.error("Failed to speak text: %s", exc, exc_info=True)

    def is_active(self) -> bool:
        return self.active


_instance: ScreenReaderProvider | None = None
_init_lock = threading.Lock()
_init_started = False


def _provider() -> ScreenReaderProvider:
    global _instance
    with _init_lock:
        if _instance is None:
            _instance = ScreenReaderProvider()
        return _instance


def schedule_screen_reader_init() -> None:
    """Probe NVDA/JAWS/SAPI on a worker so the first announce does not stall wx."""
    global _init_started
    if platform.system() != "Windows":
        return
    with _init_lock:
        if _instance is not None or _init_started:
            return
        _init_started = True

    def work() -> None:
        try:
            _provider()
        except Exception:
            logger.debug("Background screen-reader init failed", exc_info=True)

    threading.Thread(
        target=work, daemon=True, name="checkmate-screen-reader-init"
    ).start()


def speak(text: str, interrupt: bool = False) -> None:
    """Speak ``text`` through the active screen reader, if any."""
    _provider().speak(text, interrupt=interrupt)


def announce(text: str, *, progress: bool = False) -> None:
    """
    Announce text to the screen reader with interrupt.

    ``progress=True`` uses a shorter throttle (1.5s) so status phase changes
    are more likely to be spoken; otherwise 3.0s.
    """
    global _last_announce_time, _last_announce_text
    if not text:
        return
    if _last_announce_text == text:
        logger.debug("Skipping duplicate announcement: %r", text)
        return
    current_time = time.time()
    min_interval = 1.5 if progress else 3.0
    if (current_time - _last_announce_time) < min_interval:
        logger.debug("Throttling announcement: %r (too soon after previous)", text)
        return
    _last_announce_time = current_time
    _last_announce_text = text
    speak(text, interrupt=True)


def is_available() -> bool:
    return _provider().is_active()
