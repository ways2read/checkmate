"""Optional Fido settings bridge for vendored doc_images (CheckMate)."""

from __future__ import annotations

import tempfile
from pathlib import Path

# Prefer a dedicated CheckMate temp area when present.
_TEMP = Path(tempfile.gettempdir()) / "checkmate" / "doc_images"
_TEMP.mkdir(parents=True, exist_ok=True)
TEMP_DIR = str(_TEMP)

# Minimal stand-in when Fido user_settings are unavailable.
user_settings: dict = {}
