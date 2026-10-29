"""Open a publication file in an external code editor at a line and column.

Supports Visual Studio Code and Sublime Text (Windows, macOS, Linux),
Notepad++ (Windows), and a custom command with ``{file}``, ``{line}``,
and ``{column}`` placeholders.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .settings import (
    EXTERNAL_CUSTOM,
    EXTERNAL_NONE,
    EXTERNAL_NOTEPADPP,
    EXTERNAL_SUBLIME,
    EXTERNAL_VSCODE,
    external_editor,
    external_editor_command,
)

# Shown in Settings (Notepad++ is Windows-only).
def available_editors() -> tuple[str, ...]:
    editors = [EXTERNAL_NONE, EXTERNAL_VSCODE]
    if sys.platform == "win32":
        editors.append(EXTERNAL_NOTEPADPP)
    editors += [EXTERNAL_SUBLIME, EXTERNAL_CUSTOM]
    return tuple(editors)


EDITOR_NAMES = {
    EXTERNAL_VSCODE: "Visual Studio Code",
    EXTERNAL_NOTEPADPP: "Notepad++",
    EXTERNAL_SUBLIME: "Sublime Text",
}

# Launch outcomes (the UI maps these to translated messages).
LAUNCH_NOT_CONFIGURED = "not_configured"
LAUNCH_NOT_FOUND = "not_found"
LAUNCH_BAD_COMMAND = "bad_command"
LAUNCH_FAILED = "failed"


@dataclass
class LaunchOutcome:
    ok: bool
    editor_name: str = ""
    error: str = ""
    detail: str = ""


def _env_dir(var: str) -> Path | None:
    value = os.environ.get(var)
    return Path(value) if value else None


def _windows_candidates(editor: str) -> list[Path]:
    local = _env_dir("LOCALAPPDATA")
    program_dirs = [d for d in (_env_dir("ProgramFiles"), _env_dir("ProgramFiles(x86)")) if d]
    found: list[Path] = []
    if editor == EXTERNAL_VSCODE:
        if local:
            found.append(local / "Programs" / "Microsoft VS Code" / "Code.exe")
        found += [d / "Microsoft VS Code" / "Code.exe" for d in program_dirs]
    elif editor == EXTERNAL_NOTEPADPP:
        registry = _notepadpp_registry_dir()
        if registry:
            found.append(registry / "notepad++.exe")
        found += [d / "Notepad++" / "notepad++.exe" for d in program_dirs]
    elif editor == EXTERNAL_SUBLIME:
        for d in program_dirs:
            found += [d / "Sublime Text" / "subl.exe", d / "Sublime Text 3" / "subl.exe"]
    return found


def _notepadpp_registry_dir() -> Path | None:
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Notepad++") as key:
                    value, _kind = winreg.QueryValueEx(key, "")
                    if value:
                        return Path(value)
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _mac_candidates(editor: str) -> list[Path]:
    roots = [Path("/Applications"), Path.home() / "Applications"]
    if editor == EXTERNAL_VSCODE:
        return [
            r / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code"
            for r in roots
        ]
    if editor == EXTERNAL_SUBLIME:
        return [r / "Sublime Text.app" / "Contents" / "SharedSupport" / "bin" / "subl" for r in roots]
    return []


def _linux_candidates(editor: str) -> list[Path]:
    if editor == EXTERNAL_VSCODE:
        return [Path("/usr/bin/code"), Path("/usr/share/code/bin/code"), Path("/snap/bin/code")]
    if editor == EXTERNAL_SUBLIME:
        return [Path("/usr/bin/subl"), Path("/opt/sublime_text/sublime_text"), Path("/snap/bin/subl")]
    return []


_PATH_NAMES = {
    EXTERNAL_VSCODE: ("code",),
    EXTERNAL_NOTEPADPP: ("notepad++",),
    EXTERNAL_SUBLIME: ("subl",),
}


def find_editor(editor: str) -> Path | None:
    """Where *editor* is installed, or None."""
    if sys.platform == "win32":
        candidates = _windows_candidates(editor)
    elif sys.platform == "darwin":
        candidates = _mac_candidates(editor)
    else:
        candidates = _linux_candidates(editor)
    for path in candidates:
        if path.is_file():
            return path
    for name in _PATH_NAMES.get(editor, ()):
        found = shutil.which(name)
        if not found:
            continue
        path = Path(found)
        if sys.platform == "win32" and path.suffix.lower() in (".cmd", ".bat", ""):
            # Never route file paths through cmd.exe; use the real program.
            if editor == EXTERNAL_VSCODE and (path.parent.parent / "Code.exe").is_file():
                return path.parent.parent / "Code.exe"
            continue
        return path
    return None


def split_command(template: str) -> list[str]:
    """Split a custom command into arguments (Windows keeps backslashes)."""
    if sys.platform == "win32":
        parts = shlex.split(template, posix=False)
        return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
    return shlex.split(template)


def build_command(
    editor: str,
    program: Path | str,
    file: Path,
    line: int,
    column: int,
    *,
    template: str = "",
) -> list[str]:
    """Arguments that open *file* at *line*/*column* (1-based) in *editor*."""
    line, column = max(1, int(line)), max(1, int(column))
    if editor == EXTERNAL_VSCODE:
        return [str(program), "--goto", f"{file}:{line}:{column}"]
    if editor == EXTERNAL_NOTEPADPP:
        return [str(program), f"-n{line}", f"-c{column}", str(file)]
    if editor == EXTERNAL_SUBLIME:
        return [str(program), f"{file}:{line}:{column}"]
    args = split_command(template)
    values = {"file": str(file), "line": str(line), "column": str(column)}
    out = [_fill(arg, values) for arg in args]
    if "{file}" not in template:
        out.append(str(file))
    return out


def _fill(arg: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        arg = arg.replace("{" + key + "}", value)
    return arg


def configured() -> bool:
    editor = external_editor()
    if editor == EXTERNAL_CUSTOM:
        return bool(external_editor_command().strip())
    return editor != EXTERNAL_NONE


def editor_name() -> str:
    """Display name of the configured editor (custom: its program name)."""
    editor = external_editor()
    if editor == EXTERNAL_CUSTOM:
        args = split_command(external_editor_command()) if external_editor_command().strip() else []
        return Path(args[0]).stem if args else ""
    return EDITOR_NAMES.get(editor, "")


def open_at(file: Path, line: int | None, column: int | None) -> LaunchOutcome:
    """Open *file* in the configured external editor at *line*/*column*."""
    editor = external_editor()
    name = editor_name()
    if not configured():
        return LaunchOutcome(False, name, LAUNCH_NOT_CONFIGURED)
    if editor == EXTERNAL_CUSTOM:
        template = external_editor_command()
        try:
            program = split_command(template)[0]
        except (ValueError, IndexError) as exc:
            return LaunchOutcome(False, name, LAUNCH_BAD_COMMAND, str(exc))
        args = build_command(editor, program, file, line or 1, column or 1, template=template)
    else:
        program_path = find_editor(editor)
        if program_path is None:
            return LaunchOutcome(False, name, LAUNCH_NOT_FOUND)
        args = build_command(editor, program_path, file, line or 1, column or 1)
    try:
        launch(args)
    except OSError as exc:
        return LaunchOutcome(False, name, LAUNCH_FAILED, str(exc))
    return LaunchOutcome(True, name)


def launch(args: list[str]) -> None:
    """Start the editor detached from CheckMate, without a console window."""
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


def outcome_message(outcome: LaunchOutcome, file: str, line: int) -> str:
    """What to tell the user after trying to open *file* at *line*."""
    from .i18n import _

    if outcome.ok:
        return _(
            "Opened {file} in {editor} at line {line}.",
            file=file,
            editor=outcome.editor_name,
            line=line,
        )
    if outcome.error == LAUNCH_NOT_CONFIGURED:
        return _("Choose an external editor in Settings, on the Editing tab.")
    if outcome.error == LAUNCH_NOT_FOUND:
        return _(
            "{editor} was not found on this computer. Choose another external "
            "editor in Settings, on the Editing tab.",
            editor=outcome.editor_name,
        )
    if outcome.error == LAUNCH_BAD_COMMAND:
        return _(
            "The external editor command in Settings could not be read: {detail}",
            detail=outcome.detail,
        )
    return _(
        "Could not start {editor}: {detail}",
        editor=outcome.editor_name or _("the external editor"),
        detail=outcome.detail,
    )

