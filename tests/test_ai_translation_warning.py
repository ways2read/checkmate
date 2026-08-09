"""One-time AI translation warning preference."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from checkmate import settings as settings_mod


class AiTranslationWarningSettingsTests(unittest.TestCase):
    def test_defaults_false_then_marks_shown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertFalse(settings_mod.ai_translation_warning_shown())
                settings_mod.mark_ai_translation_warning_shown()
                self.assertTrue(settings_mod.ai_translation_warning_shown())
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(data.get("ai_translation_warning_shown"))


if __name__ == "__main__":
    unittest.main()
