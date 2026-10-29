"""External editor: finding VS Code / Notepad++ / Sublime Text and their command lines."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import checkmate.external_editor as ee
import checkmate.settings as settings_mod
from checkmate.settings import (
    EXTERNAL_CUSTOM,
    EXTERNAL_NONE,
    EXTERNAL_NOTEPADPP,
    EXTERNAL_SUBLIME,
    EXTERNAL_VSCODE,
)

FILE = Path("C:/Books/My book/EPUB/ch1.xhtml") if sys.platform == "win32" else Path("/books/My book/EPUB/ch1.xhtml")


class CommandTests(unittest.TestCase):
    def test_vscode_goto(self) -> None:
        self.assertEqual(
            ee.build_command(EXTERNAL_VSCODE, "code", FILE, 150, 4),
            ["code", "--goto", f"{FILE}:150:4"],
        )

    def test_notepadpp_line_and_column_flags(self) -> None:
        self.assertEqual(
            ee.build_command(EXTERNAL_NOTEPADPP, "notepad++.exe", FILE, 150, 4),
            ["notepad++.exe", "-n150", "-c4", str(FILE)],
        )

    def test_sublime_file_line_column(self) -> None:
        self.assertEqual(
            ee.build_command(EXTERNAL_SUBLIME, "subl", FILE, 150, 4),
            ["subl", f"{FILE}:150:4"],
        )

    def test_missing_location_starts_at_one(self) -> None:
        self.assertEqual(ee.build_command(EXTERNAL_SUBLIME, "subl", FILE, 0, 0), ["subl", f"{FILE}:1:1"])

    def test_custom_placeholders_stay_one_argument(self) -> None:
        template = '"C:\\Tools\\My Editor\\ed.exe" "{file}" --line {line} --col {column}'
        if sys.platform != "win32":
            template = '"/opt/My Editor/ed" "{file}" --line {line} --col {column}'
        args = ee.build_command(EXTERNAL_CUSTOM, "", FILE, 7, 3, template=template)
        self.assertEqual(args[1:], [str(FILE), "--line", "7", "--col", "3"])
        self.assertTrue(args[0].endswith(("ed.exe", "ed")))
        self.assertIn("My Editor", args[0])

    def test_custom_without_file_placeholder_appends_file(self) -> None:
        args = ee.build_command(EXTERNAL_CUSTOM, "", FILE, 7, 3, template="ed --line {line}")
        self.assertEqual(args, ["ed", "--line", "7", str(FILE)])


class FindTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows install locations")
    def test_finds_vscode_user_install_and_never_a_cmd_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = Path(tmp) / "Programs" / "Microsoft VS Code" / "Code.exe"
            code.parent.mkdir(parents=True)
            code.write_bytes(b"")
            env = {"LOCALAPPDATA": tmp, "ProgramFiles": tmp + "\\none", "ProgramFiles(x86)": tmp + "\\none"}
            with mock.patch.dict(os.environ, env), mock.patch.object(ee.shutil, "which", return_value=None):
                self.assertEqual(ee.find_editor(EXTERNAL_VSCODE), code)
            # Only code.cmd on PATH: use the Code.exe beside its bin folder.
            wrapper = code.parent / "bin" / "code.cmd"
            wrapper.parent.mkdir()
            wrapper.write_bytes(b"")
            env["LOCALAPPDATA"] = tmp + "\\elsewhere"
            with mock.patch.dict(os.environ, env), mock.patch.object(ee.shutil, "which", return_value=str(wrapper)):
                self.assertEqual(ee.find_editor(EXTERNAL_VSCODE), code)

    def test_not_installed(self) -> None:
        with mock.patch.object(ee, "_windows_candidates", return_value=[]), mock.patch.object(
            ee, "_mac_candidates", return_value=[]
        ), mock.patch.object(ee, "_linux_candidates", return_value=[]), mock.patch.object(
            ee.shutil, "which", return_value=None
        ):
            self.assertIsNone(ee.find_editor(EXTERNAL_SUBLIME))

    def test_notepadpp_offered_only_on_windows(self) -> None:
        self.assertEqual(EXTERNAL_NOTEPADPP in ee.available_editors(), sys.platform == "win32")


class OpenAtTests(unittest.TestCase):
    def _settings(self, tmp: str, **values) -> mock._patch:
        path = Path(tmp) / "settings.json"
        patcher = mock.patch.object(settings_mod, "settings_path", return_value=path)
        patcher.start()
        self.addCleanup(patcher.stop)
        settings_mod.update_settings(**values)

    def test_not_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._settings(tmp, external_editor=EXTERNAL_NONE)
            self.assertFalse(ee.configured())
            self.assertEqual(ee.open_at(FILE, 1, 1).error, ee.LAUNCH_NOT_CONFIGURED)

    def test_launches_found_editor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._settings(tmp, external_editor=EXTERNAL_SUBLIME)
            with mock.patch.object(ee, "find_editor", return_value=Path("subl")), mock.patch.object(
                ee, "launch"
            ) as launch:
                outcome = ee.open_at(FILE, 12, 5)
            self.assertTrue(outcome.ok)
            self.assertEqual(outcome.editor_name, "Sublime Text")
            launch.assert_called_once_with(["subl", f"{FILE}:12:5"])

    def test_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._settings(tmp, external_editor=EXTERNAL_VSCODE)
            with mock.patch.object(ee, "find_editor", return_value=None):
                outcome = ee.open_at(FILE, 1, 1)
            self.assertEqual((outcome.ok, outcome.error), (False, ee.LAUNCH_NOT_FOUND))
            self.assertIn("Visual Studio Code", ee.outcome_message(outcome, "ch1.xhtml", 1))


if __name__ == "__main__":
    unittest.main()
