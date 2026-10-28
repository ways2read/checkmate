"""Code editor line/column helpers and the editor choice setting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import checkmate.settings as settings_mod
from checkmate.code_editor import line_column_of, offset_of


class LineColumnTests(unittest.TestCase):
    def test_round_trip_every_offset(self) -> None:
        text = "<p>\n  héllo 😀\n\nend"
        for offset in range(len(text) + 1):
            line, column = line_column_of(text[:offset])
            self.assertEqual(offset_of(text, line, column), offset)

    def test_counts_logical_lines_and_characters(self) -> None:
        self.assertEqual(line_column_of(""), (1, 1))
        self.assertEqual(line_column_of("ab\ncd"), (2, 3))
        self.assertEqual(line_column_of("😀x"), (1, 3))

    def test_offset_clamps_to_line_and_text(self) -> None:
        text = "ab\ncde\nf"
        self.assertEqual(offset_of(text, 2, 99), 6)
        self.assertEqual(offset_of(text, 99, 1), len(text))
        self.assertEqual(offset_of(text, 0, 0), 0)


class CodeEditorSettingTests(unittest.TestCase):
    def test_defaults_to_native_and_rejects_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            with mock.patch.object(settings_mod, "settings_path", return_value=path):
                self.assertEqual(settings_mod.code_editor(), settings_mod.EDITOR_NATIVE)
                settings_mod.update_settings(code_editor=settings_mod.EDITOR_SCINTILLA)
                self.assertEqual(settings_mod.code_editor(), settings_mod.EDITOR_SCINTILLA)
                settings_mod.update_settings(code_editor="notepad")
                self.assertEqual(settings_mod.code_editor(), settings_mod.EDITOR_NATIVE)


if __name__ == "__main__":
    unittest.main()
