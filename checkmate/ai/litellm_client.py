"""LiteLLM completion helper for CheckMate."""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable

try:
    import litellm
except ImportError:
    litellm = None  # type: ignore

logger = logging.getLogger(__name__)

_CONFIGURED = False

# Keep well under LiteLLM's ~600s default so UI never looks permanently hung.
DEFAULT_COMPLETION_TIMEOUT_SEC = 180
CONNECTION_CHECK_TIMEOUT_SEC = 30
# Gemini Flash / GPT-class models: leave headroom for rationale + markup snippets.
# Too-low caps truncate mid-JSON and can yield bad Fix proposals.
DEFAULT_EXPLAIN_MAX_TOKENS = 8192
DEFAULT_FOLLOWUP_MAX_TOKENS = 4096
DEFAULT_FIX_MAX_TOKENS = 8192

StatusCallback = Callable[[str], None]


def configure_litellm_defaults() -> None:
    global _CONFIGURED
    if _CONFIGURED or litellm is None:
        return
    litellm.drop_params = True
    _CONFIGURED = True


def litellm_available() -> bool:
    return litellm is not None


def completion_output_kwargs(model: str | None, max_tokens: int) -> dict[str, Any]:
    """
    Build output-limit kwargs for ``litellm.completion``.

    OpenAI GPT-5.x (direct or via ``openrouter/openai/...``) rejects ``max_tokens``
    on Chat Completions; callers must send ``max_completion_tokens``. With
    ``drop_params=True``, a rejected ``max_tokens`` is silently dropped — which
    often yields a short truncated reply.
    """
    try:
        n = int(max_tokens)
    except (TypeError, ValueError):
        n = DEFAULT_EXPLAIN_MAX_TOKENS
    if n < 0:
        n = 0

    out: dict[str, Any] = {"max_tokens": n}
    if not model or not isinstance(model, str):
        return out

    m = model.lower()
    if "gpt-5" in m and (
        m.startswith("openrouter/openai/") or m.startswith("openai/")
    ):
        out = {"max_completion_tokens": max(n, 16)}

    # Prefer no thinking budget so output isn't eaten (native Gemini 2.5+ / 3.x).
    # OpenRouter rejects ``reasoning_effort``; disable via extra_body instead.
    if "gemini" in m and re.search(r"gemini-(2\.[5-9]|3)\b", m):
        out = dict(out)
        if m.startswith("openrouter/"):
            extra = dict(out.get("extra_body") or {})
            extra.setdefault("enable_thinking", False)
            out["extra_body"] = extra
        else:
            out["reasoning_effort"] = "none"
    return out


def litellm_completion(**kwargs: Any) -> Any:
    if litellm is None:
        raise RuntimeError("litellm is not installed")
    configure_litellm_defaults()
    out = dict(kwargs)
    out["drop_params"] = True
    if "timeout" not in out:
        out["timeout"] = DEFAULT_COMPLETION_TIMEOUT_SEC

    model = out.get("model")
    # Prefer explicit max_completion_tokens; otherwise map max_tokens for GPT-5 etc.
    if "max_completion_tokens" not in out and "max_tokens" in out:
        mapped = completion_output_kwargs(
            model if isinstance(model, str) else None,
            int(out.pop("max_tokens")),
        )
        out.update(mapped)
    elif "max_tokens" not in out and "max_completion_tokens" not in out:
        out.update(completion_output_kwargs(model if isinstance(model, str) else None, DEFAULT_EXPLAIN_MAX_TOKENS))
    elif isinstance(model, str) and "reasoning_effort" not in out:
        # Still attach Gemini thinking-disable when caller only set max_completion_tokens.
        extra = completion_output_kwargs(
            model,
            int(
                out.get("max_completion_tokens")
                or out.get("max_tokens")
                or DEFAULT_EXPLAIN_MAX_TOKENS
            ),
        )
        if "reasoning_effort" in extra:
            out["reasoning_effort"] = extra["reasoning_effort"]
        if "extra_body" in extra:
            merged = dict(out.get("extra_body") or {})
            merged.update(extra["extra_body"])
            out["extra_body"] = merged

    logger.debug(
        "litellm.completion model=%s timeout=%s max_tokens=%s max_completion_tokens=%s",
        out.get("model"),
        out.get("timeout"),
        out.get("max_tokens"),
        out.get("max_completion_tokens"),
    )
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
        logger.exception("Failed to parse LiteLLM assistant text")
        return ""


def classify_provider_error(exc: BaseException) -> tuple[str, str]:
    """Map a provider/LiteLLM exception to (error_key, detail)."""
    detail = str(exc) or type(exc).__name__
    name = type(exc).__name__.lower()
    msg = detail.lower()

    timeout_type = False
    if litellm is not None:
        timeout_cls = getattr(litellm, "Timeout", None)
        if timeout_cls is not None and isinstance(exc, timeout_cls):
            timeout_type = True
    if (
        timeout_type
        or "timeout" in name
        or "timed out" in msg
        or "timeout" in msg
        or name.endswith("timeouterror")
    ):
        return "timeout", detail

    if litellm is not None:
        auth_cls = getattr(litellm, "AuthenticationError", None)
        if auth_cls is not None and isinstance(exc, auth_cls):
            return "no_key", detail
        conn_cls = getattr(litellm, "APIConnectionError", None)
        if conn_cls is not None and isinstance(exc, conn_cls):
            return "network", detail
        not_found = getattr(litellm, "NotFoundError", None)
        if not_found is not None and isinstance(exc, not_found):
            return "no_model", detail

    if "api key" in msg or "authentication" in msg or "unauthorized" in msg:
        return "no_key", detail
    if "connection" in msg or "connect" in msg or "name or service not known" in msg:
        return "network", detail
    return "provider_error", detail


def check_provider_connection(
    *,
    model: str,
    api_key: str | None,
    api_base: str | None = None,
    timeout: float = CONNECTION_CHECK_TIMEOUT_SEC,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str | None, str]:
    """
    Minimal completion to verify model + key + network before a full prompt.

    Returns (ok, error_key, detail).
    """
    if cancel_event is not None and cancel_event.is_set():
        return False, "cancelled", ""
    if not litellm_available():
        return False, "no_litellm", ""
    if not model:
        return False, "no_model", ""
    if not api_key:
        return False, "no_key", ""

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}],
        "api_key": api_key,
        "max_tokens": 5,
        "timeout": timeout,
    }
    if api_base:
        kwargs["api_base"] = api_base

    logger.info("AI connection check starting model=%s", model)
    try:
        litellm_completion(**kwargs)
    except Exception as exc:
        key, detail = classify_provider_error(exc)
        logger.exception("AI connection check failed (%s): %s", key, detail)
        return False, key, detail

    if cancel_event is not None and cancel_event.is_set():
        return False, "cancelled", ""
    logger.info("AI connection check ok model=%s", model)
    return True, None, ""


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
        logger.info("Refreshing unlock credentials")
        result = refresh_unlock()
        if not result.get("ok"):
            reason = str(result.get("reason") or "unlock_failed")
            logger.warning("Unlock refresh failed: %s", reason)
            return False, reason
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
