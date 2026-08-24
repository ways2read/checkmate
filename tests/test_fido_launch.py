"""Locate an installed Fido app."""

from __future__ import annotations

from pathlib import Path

from checkmate.fido_launch import (
    ENV_FIDO,
    ENV_FIDO_IMAGE_REPORT,
    _reset_image_report_cli_cache,
    fido_cli_command,
    fido_image_report_status,
    fido_supports_image_report_cli,
    find_fido_app,
)


def test_env_override_exe(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "Fido.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(ENV_FIDO, str(exe))
    found = find_fido_app()
    assert found is not None
    assert Path(found).resolve() == exe.resolve()
    cmd = fido_cli_command()
    assert cmd == [str(exe.resolve())]


def test_env_override_python_entry(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "FIDO.py"
    script.write_text("# fido\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FIDO, str(script))
    found = find_fido_app()
    assert found is not None
    assert Path(found).resolve() == script.resolve()
    cmd = fido_cli_command()
    assert cmd is not None
    assert cmd[0] == __import__("sys").executable
    assert "-u" in cmd
    assert cmd[-1] == str(script.resolve())


def test_missing_fido_returns_none(monkeypatch) -> None:
    monkeypatch.delenv(ENV_FIDO, raising=False)
    monkeypatch.setattr("checkmate.fido_launch._windows_registry_fido_exe", lambda: None)
    monkeypatch.setattr("checkmate.fido_launch.shutil.which", lambda _name: None)
    monkeypatch.setattr("checkmate.fido_launch.sys.platform", "linux")
    assert find_fido_app() is None
    assert fido_cli_command() is None
    assert fido_image_report_status() == "missing"
    assert fido_supports_image_report_cli() is False


def test_image_report_cli_from_python_source(tmp_path: Path, monkeypatch) -> None:
    _reset_image_report_cli_cache()
    monkeypatch.delenv(ENV_FIDO_IMAGE_REPORT, raising=False)
    script = tmp_path / "FIDO.py"
    script.write_text("# launcher\n", encoding="utf-8")
    workflows = tmp_path / "fido"
    workflows.mkdir()
    (workflows / "cli_workflows.py").write_text(
        'COMMANDS = {"image-report"}\n', encoding="utf-8"
    )
    assert fido_supports_image_report_cli(str(script)) is True


def test_old_cli_workflows_without_image_report(
    tmp_path: Path, monkeypatch
) -> None:
    _reset_image_report_cli_cache()
    monkeypatch.delenv(ENV_FIDO_IMAGE_REPORT, raising=False)
    script = tmp_path / "FIDO.py"
    script.write_text("# launcher\n", encoding="utf-8")
    workflows = tmp_path / "fido"
    workflows.mkdir()
    (workflows / "cli_workflows.py").write_text(
        'COMMANDS = {"convert"}\n', encoding="utf-8"
    )
    assert fido_supports_image_report_cli(str(script)) is False


def test_frozen_exe_uses_build_counter(tmp_path: Path, monkeypatch) -> None:
    _reset_image_report_cli_cache()
    monkeypatch.delenv(ENV_FIDO_IMAGE_REPORT, raising=False)
    exe = tmp_path / "Fido.exe"
    exe.write_bytes(b"MZ this is a frozen GUI build")
    (tmp_path / "build_counter.txt").write_text("482\n", encoding="utf-8")
    assert fido_supports_image_report_cli(str(exe)) is True
    (tmp_path / "build_counter.txt").write_text("479\n", encoding="utf-8")
    _reset_image_report_cli_cache()
    assert fido_supports_image_report_cli(str(exe)) is False


def test_frozen_exe_uses_version_txt(tmp_path: Path, monkeypatch) -> None:
    _reset_image_report_cli_cache()
    monkeypatch.delenv(ENV_FIDO_IMAGE_REPORT, raising=False)
    exe = tmp_path / "Fido.exe"
    exe.write_bytes(b"MZ")
    (tmp_path / "version.txt").write_text(
        "0.9.7 build 482 24/08/2026 15:03:13.95\n", encoding="utf-8"
    )
    assert fido_supports_image_report_cli(str(exe)) is True


def test_frozen_exe_marker_without_version(tmp_path: Path, monkeypatch) -> None:
    _reset_image_report_cli_cache()
    monkeypatch.delenv(ENV_FIDO_IMAGE_REPORT, raising=False)
    exe = tmp_path / "Fido.exe"
    exe.write_bytes(b"MZ\x00image-report\x00GUI")
    assert fido_supports_image_report_cli(str(exe)) is True
    old = tmp_path / "old" / "Fido.exe"
    old.parent.mkdir()
    old.write_bytes(b"MZ this is an old GUI build")
    assert fido_supports_image_report_cli(str(old)) is False


def test_image_report_cli_env_override(tmp_path: Path, monkeypatch) -> None:
    _reset_image_report_cli_cache()
    exe = tmp_path / "Fido.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(ENV_FIDO, str(exe))
    monkeypatch.setenv(ENV_FIDO_IMAGE_REPORT, "0")
    assert fido_supports_image_report_cli() is False
    monkeypatch.setenv(ENV_FIDO_IMAGE_REPORT, "1")
    assert fido_supports_image_report_cli() is True
    assert fido_image_report_status() == "ok"
