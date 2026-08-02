"""LiteLLM completion helper for CheckMate."""

from __future__ import annotations

from typing import Any, Optional

try:
    import litellm
except ImportError:
    litellm = None  # type: ignore

_CONFIGURED = False


def configure_litellm_defaults() -> None:
    global _CONFIGURED
    if _CONFIGURED or litellm is None:
        return
    litellm.drop_params = True
    _CONFIGURED = True


def litellm_available() -> bool:
    return litellm is not None


def litellm_completion(**kwargs: Any) -> Any:
    if litellm is None:
        raise RuntimeError("litellm is not installed")
    configure_litellm_defaults()
    out = dict(kwargs)
    out["drop_params"] = True
    return litellm.completion(**out)


def assistant_text_from_response(response: Any) -> str:
    try:
        choice = response.choices[0]
        msg = choice.message
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif hasattr(block, "text"):
                    parts.append(str(getattr(block, "text") or ""))
            return "".join(parts)
        return str(content or "")
    except Exception:
        return ""


def ensure_credentials_ready() -> tuple[bool, str | None]:
    """
    Refresh unlock overlay if needed, then check that a model+key can be resolved.

    Returns (ok, error_reason_key).
    """
    from ..fido_settings import (
        get_unlock_code,
        resolve_litellm_model_and_key,
        selected_model_service_string,
    )
    from .unlock import get_unlock_api_overlay, refresh_unlock

    model, key, _base = resolve_litellm_model_and_key()
    if model and key:
        return True, None

    if get_unlock_code():
        result = refresh_unlock()
        if not result.get("ok"):
            return False, str(result.get("reason") or "unlock_failed")
        model, key, _base = resolve_litellm_model_and_key()
        if model and key:
            return True, None
        if not get_unlock_api_overlay():
            return False, "unlock_empty"
        if not selected_model_service_string():
            return False, "no_model"
        return False, "no_key"

    if not selected_model_service_string():
        return False, "no_model"
    if not model:
        return False, "no_model"
    return False, "no_key"
