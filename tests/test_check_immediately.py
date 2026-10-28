"""Start checking immediately setting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import checkmate.settings as settings_mod


class CheckImmediatelyTests(unittest.TestCase):
    def test_defaults_on_and_persists_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertTrue(settings_mod.check_immediately())
                settings_mod.update_settings(check_immediately=False)
                self.assertFalse(settings_mod.check_immediately())
                settings_mod.update_settings(check_immediately=True)
                self.assertTrue(settings_mod.check_immediately())

    def test_autosave_edits_defaults_off_and_persists_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertFalse(settings_mod.autosave_edits())
                settings_mod.update_settings(autosave_edits=True)
                self.assertTrue(settings_mod.autosave_edits())


if __name__ == "__main__":
    unittest.main()
