"""File logging for installed (and source) CheckMate builds."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

from .paths import app_data_dir

LOG_FILENAME = "checkmatelog.txt"
_CONFIGURED = False

REDACTED = "[REDACTED]"
_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (
        (?:x[-_]?api[_-]?key|api[_-]?key|access[_-]?token|refresh[_-]?token|secret|authorization)
        ['"]?\s*[:=]\s*
        ['"]?
    )
    (?!\[REDACTED\])
    ([^'",\s}\]]+)
    """
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\b(bearer\s+)(?!\[REDACTED\])([A-Za-z0-9._~+/=-]+)")


def redact_sensitive_text(value: Any) -> str:
    text = str(value)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1" + REDACTED, text)
    text = _BEARER_TOKEN_RE.sub(r"\1" + REDACTED, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


def log_file_path():
    return app_data_dir() / LOG_FILENAME


def configure_logging(*, level: int = logging.DEBUG) -> None:
    """Attach a rotating file handler under the CheckMate app-data directory."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger()
    root.setLevel(level)

    path = log_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr)

    handler.setLevel(level)
    handler.setFormatter(
        RedactingFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root.addHandler(handler)

    # Console when not a frozen windowed build (useful for source runs).
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)
        console.setFormatter(
            RedactingFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        root.addHandler(console)

    logging.getLogger("checkmate").info("Logging started → %s", path)

    # LiteLLM / HTTP stacks are extremely noisy at DEBUG.
    for noisy in ("LiteLLM", "litellm", "httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
