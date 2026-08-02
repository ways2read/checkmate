"""Read FIDO app-data settings and API keys (no FIDO package import)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Fallback when FIDO services.json is missing (Provider prefix → litellm prefix, key name).
_PROVIDER_FALLBACKS: dict[str, tuple[str, str]] = {
    "Google Gemini": ("gemini", "google_key"),
    "Google": ("gemini", "google_key"),
    "OpenAI": ("openai", "openai_key"),
    "Anthropic": ("anthropic", "anthropic_key"),
    "OpenRouter": ("openrouter", "openrouter_key"),
    "DeepSeek": ("deepseek", "deepseek_key"),
    "Mistral AI": ("mistral", "mistralai_key"),
    "Mistral": ("mistral", "mistralai_key"),
    "Groq": ("groq", "groq_key"),
}


def fido_app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os_environ_localappdata())
        return base / "fido"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "fido"
    return Path.home() / ".fido"


def os_environ_localappdata() -> str:
    import os

    return os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")


def fido_settings_present() -> bool:
    """True when FIDO has been set up (api_keys.json or user_settings.json exists)."""
    d = fido_app_data_dir()
    return (d / "api_keys.json").is_file() or (d / "user_settings.json").is_file()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def read_api_keys() -> dict[str, Any]:
    return _read_json(fido_app_data_dir() / "api_keys.json")


def read_user_settings() -> dict[str, Any]:
    return _read_json(fido_app_data_dir() / "user_settings.json")


def read_services_catalog() -> list[dict[str, Any]]:
    path = fido_app_data_dir() / "services.json"
    data = _read_json(path)
    if not data:
        return []
    if isinstance(data.get("models"), list):
        return [m for m in data["models"] if isinstance(m, dict)]
    return []


def get_user_setting(key: str, default: Any = None) -> Any:
    return read_user_settings().get(key, default)


def get_persisted_api_key(key: str, default: str | None = None) -> str | None:
    """Value from api_keys.json only (no unlock overlay)."""
    val = read_api_keys().get(key, default)
    if isinstance(val, str) and val.strip():
        return val.strip()
    if val is None:
        return default
    return str(val).strip() or default


def get_api_key(key: str, default: str | None = None) -> str | None:
    """
    Resolve an API key: non-empty persisted FIDO value, else in-memory unlock overlay.
    Never reads FIDO's process memory — unlock must be refreshed in this process.
    """
    persisted = get_persisted_api_key(key)
    if persisted:
        return persisted
    try:
        from .ai.unlock import get_unlock_api_overlay

        ov = get_unlock_api_overlay().get(key)
        if isinstance(ov, str) and ov.strip():
            return ov.strip()
    except Exception:
        pass
    return default


def get_unlock_code() -> str:
    code = read_api_keys().get("unlock_code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    # Optional CheckMate-local code (settings.json), never keys.
    try:
        from .settings import read_settings

        local = read_settings().get("unlock_code")
        if isinstance(local, str) and local.strip():
            return local.strip()
    except Exception:
        pass
    return ""


def selected_model_service_string() -> str:
    """
    Return ``Provider: model id`` for Explain with AI.

    Selection order:
    1. If ``single_llm_for_all_services`` is true (FIDO default): ``unified_llm_model``
    2. Else ``checkmate_model`` (CheckMate-specific FIDO setting)
    3. Else ``describer_model``
    4. Else in-memory unlock session model, if any
    """
    settings = read_user_settings()
    unified_mode = settings.get("single_llm_for_all_services", True)
    if isinstance(unified_mode, str):
        unified_mode = unified_mode.strip().lower() in ("1", "true", "yes")

    def _str_setting(key: str) -> str:
        val = settings.get(key)
        return val.strip() if isinstance(val, str) and val.strip() else ""

    if unified_mode:
        unified = _str_setting("unified_llm_model")
        if unified:
            return unified
    else:
        checkmate = _str_setting("checkmate_model")
        if checkmate:
            return checkmate
        describer = _str_setting("describer_model")
        if describer:
            return describer

    # Session fallback from last unlock payload (in memory).
    try:
        from .ai.unlock import get_unlock_session_model

        session = get_unlock_session_model()
        if session:
            return session
    except Exception:
        pass
    return ""


def _lookup_in_catalog(
    service: str, model: str
) -> tuple[str | None, str | None]:
    """Return (litellm_model, api_key_field) from FIDO services.json."""
    for svc in read_services_catalog():
        if svc.get("provider") != service:
            continue
        sid = str(svc.get("id") or "")
        sname = str(svc.get("name") or "").strip().lower()
        if sid == model or sname == model.strip().lower():
            lit = svc.get("litellm")
            key_name = svc.get("api_key")
            lit_s = str(lit).strip() if lit else None
            key_s = str(key_name).strip() if key_name else None
            return lit_s or None, key_s or None
    return None, None


def _fallback_litellm_and_key(service: str, model: str) -> tuple[str | None, str | None]:
    if service == "Gateway":
        if model and model != "openai-compatible-gateway":
            lit = model if model.startswith("openai/") else f"openai/{model}"
            return lit, "gateway_key"
        gm = get_api_key("gateway_model") or get_persisted_api_key("gateway_model")
        if gm:
            lit = gm if gm.startswith("openai/") else f"openai/{gm}"
            return lit, "gateway_key"
        return None, "gateway_key"
    tip = _PROVIDER_FALLBACKS.get(service)
    if not tip:
        # Case-insensitive provider match
        for name, pair in _PROVIDER_FALLBACKS.items():
            if name.lower() == service.lower():
                tip = pair
                break
    if not tip:
        return None, None
    prefix, key_name = tip
    if model.startswith(f"{prefix}/"):
        return model, key_name
    return f"{prefix}/{model}", key_name


def resolve_litellm_model_and_key(
    service_str: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Resolve (litellm_model, api_key, api_base) for Explain with AI.

    ``api_base`` is set for Gateway / LM Studio / Ollama when configured in FIDO keys.
    """
    svc_str = (service_str or selected_model_service_string() or "").strip()
    if not svc_str or ":" not in svc_str:
        return None, None, None
    service, model = svc_str.split(":", 1)
    service = service.strip()
    model = model.strip()

    lit: str | None
    key_field: str | None
    lit, key_field = _lookup_in_catalog(service, model)
    if not lit:
        lit, key_field = _fallback_litellm_and_key(service, model)

    api_key = get_api_key(key_field) if key_field else None
    api_base: str | None = None

    if service == "Gateway":
        api_base = _normalize_openai_compat_base(
            get_api_key("gateway_base_url") or get_persisted_api_key("gateway_base_url") or ""
        )
        if not api_key:
            api_key = "not-needed"
    elif service in ("LM Studio", "LMStudio"):
        api_base = _normalize_openai_compat_base(
            get_api_key("lmstudio_api_baseurl")
            or get_persisted_api_key("lmstudio_api_baseurl")
            or get_api_key("lmstudio_local_url")
            or get_persisted_api_key("lmstudio_local_url")
            or ""
        )
        if not api_key:
            api_key = "not-needed"
    elif service == "Ollama" or (lit and lit.startswith("ollama/")):
        raw = (
            get_api_key("ollama_api_baseurl")
            or get_persisted_api_key("ollama_api_baseurl")
            or get_api_key("ollama_local_url")
            or get_persisted_api_key("ollama_local_url")
            or ""
        )
        api_base = _normalize_openai_compat_base(raw) if raw else None
        if not api_key:
            api_key = "not-needed"

    return lit, api_key, api_base


def _normalize_openai_compat_base(raw: str) -> str | None:
    base_url = (raw or "").strip()
    if not base_url:
        return None
    clean = base_url.rstrip("/")
    if clean.endswith("/v1"):
        clean = clean[:-3]
    return f"{clean}/v1"


def credentials_status_label() -> str:
    """Short status for Preferences / diagnostics (no secrets)."""
    if not fido_settings_present():
        return "none"
    if any(
        get_persisted_api_key(k)
        for k in (
            "openai_key",
            "google_key",
            "anthropic_key",
            "openrouter_key",
            "gateway_key",
        )
    ):
        return "fido_keys"
    if get_unlock_code():
        return "unlock_code"
    return "incomplete"
