"""Sound scheme preference and legacy sounds_enabled migration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from checkmate import settings as settings_mod


class SoundSchemeSettingsTests(unittest.TestCase):
    def test_defaults_to_scheme_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertEqual(settings_mod.sound_scheme(), "1")
                self.assertTrue(settings_mod.sounds_enabled())

    def test_persists_scheme_and_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                settings_mod.update_settings(sound_scheme="2")
                self.assertEqual(settings_mod.sound_scheme(), "2")
                self.assertTrue(settings_mod.sounds_enabled())
                settings_mod.update_settings(sound_scheme="off")
                self.assertEqual(settings_mod.sound_scheme(), "off")
                self.assertFalse(settings_mod.sounds_enabled())
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data.get("sound_scheme"), "off")
                self.assertNotIn("sounds_enabled", data)

    def test_migrates_legacy_sounds_enabled_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps({"sounds_enabled": False}) + "\n", encoding="utf-8"
            )
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertEqual(settings_mod.sound_scheme(), "off")
                # Unrelated write should persist the migrated value.
                settings_mod.update_settings(show_issues_always=True)
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data.get("sound_scheme"), "off")
                self.assertNotIn("sounds_enabled", data)
                self.assertEqual(settings_mod.sound_scheme(), "off")


if __name__ == "__main__":
    unittest.main()
