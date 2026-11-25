"""CheckMate reads FIDO API keys from the OS credential store, falling back to api_keys.json."""

from __future__ import annotations

import json

import pytest

from checkmate import fido_settings as fs


class FakeBackend:
    def __init__(self, entries: dict[str, str]):
        self.entries = entries

    def get_password(self, service: str, name: str):
        assert service == "Fido"
        return self.entries.get(name)


@pytest.fixture
def fido_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "fido_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(fs, "_secret_backend_failed", False)
    fs.refresh_fido_secrets()
    yield tmp_path
    fs.refresh_fido_secrets()


def _use_backend(monkeypatch, entries):
    monkeypatch.setattr(fs, "_secret_backend", FakeBackend(entries))


def test_secure_store_key_wins_over_json(fido_dir, monkeypatch):
    (fido_dir / "api_keys.json").write_text(json.dumps({"openai_key": "from-json"}), "utf-8")
    _use_backend(monkeypatch, {"openai_key": "from-store"})
    assert fs.get_api_key("openai_key") == "from-store"


def test_falls_back_to_json_when_not_in_store(fido_dir, monkeypatch):
    (fido_dir / "api_keys.json").write_text(json.dumps({"openai_key": "from-json"}), "utf-8")
    _use_backend(monkeypatch, {})
    assert fs.get_api_key("openai_key") == "from-json"


def test_chunked_value_is_joined(fido_dir, monkeypatch):
    _use_backend(
        monkeypatch,
        {"google_key": "__fido_chunks__:2", "google_key#1": "abc", "google_key#2": "def"},
    )
    assert fs.get_api_key("google_key") == "abcdef"


def test_non_secret_names_ignore_store(fido_dir, monkeypatch):
    (fido_dir / "api_keys.json").write_text(json.dumps({"gateway_base_url": "http://x"}), "utf-8")
    _use_backend(monkeypatch, {"gateway_base_url": "should-not-be-used"})
    assert fs.get_persisted_api_key("gateway_base_url") == "http://x"


def test_unlock_code_from_store(fido_dir, monkeypatch):
    _use_backend(monkeypatch, {"unlock_code": "CODE"})
    assert fs.get_unlock_code() == "CODE"


def test_resolve_model_uses_store_key(fido_dir, monkeypatch):
    (fido_dir / "user_settings.json").write_text(
        json.dumps({"single_llm_for_all_services": True, "unified_llm_model": "OpenAI: gpt-5"}),
        "utf-8",
    )
    _use_backend(monkeypatch, {"openai_key": "sk-test"})
    lit, key, base = fs.resolve_litellm_model_and_key()
    assert lit == "openai/gpt-5"
    assert key == "sk-test"
    assert fs.credentials_status_label() == "fido_keys"
