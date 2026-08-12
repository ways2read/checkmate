"""LiteLLM completion helper for CheckMate."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Keep well under LiteLLM's ~600s default so UI never looks permanently hung.
DEFAULT_COMPLETION_TIMEOUT_SEC = 180
CONNECTION_CHECK_TIMEOUT_SEC = 30
# Gemini Flash / GPT-class models: leave headroom for rationale + markup snippets.
# Too-low caps truncate mid-JSON and can yield bad Fix proposals.
DEFAULT_EXPLAIN_MAX_TOKENS = 8192
DEFAULT_FOLLOWUP_MAX_TOKENS = 4096
DEFAULT_FIX_MAX_TOKENS = 8192

StatusCallback = Callable[[str], None]

_CONFIGURED = False
_litellm: Any = None
_litellm_import_error: BaseException | None = None
_import_lock = threading.Lock()
_preload_started = False


def _ensure_local_model_cost_map() -> None:
    """Avoid LiteLLM's import-time GitHub fetch (can hang in frozen builds)."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def _bundled_tiktoken_cache_dir() -> str | None:
    """Directory of pre-downloaded tiktoken BPE files inside a frozen build."""
    import sys
    from pathlib import Path

    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "tiktoken_cache")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "_internal" / "tiktoken_cache")
        candidates.append(Path(sys.executable).resolve().parent / "tiktoken_cache")
    for path in candidates:
        if path.is_dir():
            return str(path)
    return None


def _ensure_tiktoken_ready() -> None:
    """
    Make ``tiktoken.get_encoding("cl100k_base")`` work in frozen builds.

    PyInstaller does not expose ``tiktoken_ext`` to ``pkgutil.iter_modules``, so
    plugin discovery finds nothing. Register constructors explicitly, and prefer
    a bundled BPE cache so import does not need Azure Blob downloads.
    """
    cache = _bundled_tiktoken_cache_dir()
    if cache:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", cache)
        logger.info("Using bundled tiktoken cache: %s", cache)

    try:
        import tiktoken.registry as reg
        import tiktoken_ext.openai_public as openai_public
    except Exception:
        logger.exception("Could not import tiktoken encoding plugins")
        raise

    constructors = getattr(openai_public, "ENCODING_CONSTRUCTORS", None)
    if not isinstance(constructors, dict) or not constructors:
        raise RuntimeError("tiktoken_ext.openai_public has no ENCODING_CONSTRUCTORS")

    with reg._lock:
        # Replace (do not merge into None) so get_encoding skips broken discovery.
        reg.ENCODING_CONSTRUCTORS = dict(constructors)
    logger.info(
        "Registered tiktoken encodings: %s",
        ", ".join(sorted(constructors)),
    )


def _get_litellm() -> Any:
    """Import litellm lazily so env flags are applied first."""
    global _litellm, _litellm_import_error
    if _litellm is not None:
        return _litellm
    if _litellm_import_error is not None:
        raise _litellm_import_error
    with _import_lock:
        if _litellm is not None:
            return _litellm
        if _litellm_import_error is not None:
            raise _litellm_import_error
        _ensure_local_model_cost_map()
        t0 = time.perf_counter()
        logger.info("Importing litellm…")
        try:
            _ensure_tiktoken_ready()
            import litellm as _mod
        except Exception as exc:
            _litellm_import_error = exc
            logger.exception("Failed to import litellm")
            raise
        _litellm = _mod
        logger.info(
            "Imported litellm in %.1fs from %s",
            time.perf_counter() - t0,
            getattr(_mod, "__file__", "?"),
        )
        return _litellm


def litellm_available() -> bool:
    try:
        return _get_litellm() is not None
    except Exception:
        return False


def litellm_ready() -> bool:
    """True when litellm has already been imported (no I/O)."""
    return _litellm is not None


def preload_litellm() -> tuple[bool, str]:
    """
    Import litellm on the calling thread (UI or worker).

    Prefer a worker thread when a progress dialog is visible so the UI can
    paint and screen readers can announce it. Returns ``(ok, detail)``.
    Safe to call more than once.
    """
    try:
        _get_litellm()
        configure_litellm_defaults()
        return True, ""
    except Exception as exc:
        return False, str(exc) or type(exc).__name__


def schedule_background_preload() -> None:
    """Start a one-shot daemon import so the first AI action is not cold.

    Safe to call repeatedly; no-ops if already loaded or a preload is running.
    Failures are logged only — the next user AI action still retries.
    """
    global _preload_started
    if litellm_ready():
        return
    with _import_lock:
        if litellm_ready() or _preload_started:
            return
        _preload_started = True

    def work() -> None:
        ok, detail = preload_litellm()
        if ok:
            logger.info("Background LiteLLM preload finished")
        else:
            logger.warning("Background LiteLLM preload failed: %s", detail)

    threading.Thread(
        target=work, daemon=True, name="checkmate-litellm-preload"
    ).start()


def ai_libraries_status_message() -> str:
    """Progress text: skip cold-load wording when LiteLLM is already imported."""
    from ..i18n import _

    if litellm_ready():
        return _("Starting AI…")
    return _("Loading AI libraries…")


def configure_litellm_defaults() -> None:
    global _CONFIGURED
    mod = _get_litellm()
    if _CONFIGURED or mod is None:
        return
    mod.drop_params = True
    _CONFIGURED = True


# LiteLLM 1.95+ picks Google AI Studio ``v1alpha`` for Gemini 3+; that returns
# 403 for many consumer API keys. Google's public docs and Fido's native
# Gemini client use ``v1beta``.
_GEMINI_AI_STUDIO_V1BETA_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _force_native_gemini3_v1beta_api_base(out: dict[str, Any]) -> None:
    """Override LiteLLM's Gemini 3 → v1alpha default when no custom api_base is set."""
    if out.get("api_base"):
        return
    model = out.get("model")
    if not isinstance(model, str):
        return
    m = model.lower().strip()
    # Native Google AI Studio only (not openrouter/, vertex_ai/, …).
    if not m.startswith("gemini/"):
        return
    if not re.search(r"gemini-3\b", m):
        return
    out["api_base"] = _GEMINI_AI_STUDIO_V1BETA_BASE


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
    mod = _get_litellm()
    if mod is None:
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

    _force_native_gemini3_v1beta_api_base(out)

    logger.debug(
        "litellm.completion model=%s timeout=%s max_tokens=%s max_completion_tokens=%s api_base=%s",
        out.get("model"),
        out.get("timeout"),
        out.get("max_tokens"),
        out.get("max_completion_tokens"),
        out.get("api_base"),
    )
    return mod.completion(**out)


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


def cost_and_usage_from_response(response: Any) -> dict[str, Any]:
    """
    Extract LiteLLM cost (USD) and token usage from a completion response.

    Cost comes from ``response._hidden_params['response_cost']`` when present,
    otherwise ``litellm.completion_cost(completion_response=...)`` when available.
    Missing pricing (local / unknown models) yields ``cost_usd=None``.
    """
    out: dict[str, Any] = {
        "cost_usd": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }
    if response is None:
        return out

    usage = getattr(response, "usage", None)
    if usage is not None:
        try:
            pt = getattr(usage, "prompt_tokens", None)
            ct = getattr(usage, "completion_tokens", None)
            tt = getattr(usage, "total_tokens", None)
            if pt is None and isinstance(usage, dict):
                pt = usage.get("prompt_tokens")
                ct = usage.get("completion_tokens")
                tt = usage.get("total_tokens")
            out["prompt_tokens"] = int(pt) if pt is not None else None
            out["completion_tokens"] = int(ct) if ct is not None else None
            if tt is not None:
                out["total_tokens"] = int(tt)
            elif out["prompt_tokens"] is not None and out["completion_tokens"] is not None:
                out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
        except Exception:
            logger.debug("Could not read usage from LiteLLM response", exc_info=True)

    cost: float | None = None
    try:
        hidden = getattr(response, "_hidden_params", None) or {}
        if isinstance(hidden, dict) and hidden.get("response_cost") is not None:
            cost = float(hidden["response_cost"])
    except Exception:
        cost = None

    if cost is None:
        try:
            mod = _get_litellm()
            completion_cost = getattr(mod, "completion_cost", None) if mod else None
            if callable(completion_cost):
                estimated = completion_cost(completion_response=response)
                if estimated is not None:
                    cost = float(estimated)
        except Exception:
            logger.debug("LiteLLM completion_cost unavailable", exc_info=True)

    out["cost_usd"] = cost
    return out


def log_completion_cost(
    *,
    model: str | None,
    response: Any = None,
    operation: str = "completion",
    session_total_usd: float | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Log cost/usage at INFO (logger only). Returns the extracted metrics dict."""
    data = metrics if metrics is not None else cost_and_usage_from_response(response)
    cost = data.get("cost_usd")
    cost_s = f"{cost:.6f}" if isinstance(cost, (int, float)) else "n/a"
    total_s = (
        f"{session_total_usd:.6f}"
        if isinstance(session_total_usd, (int, float))
        else "n/a"
    )
    logger.info(
        "AI %s cost model=%s cost_usd=%s session_total_usd=%s "
        "prompt_tokens=%s completion_tokens=%s total_tokens=%s",
        operation,
        model or "?",
        cost_s,
        total_s,
        data.get("prompt_tokens"),
        data.get("completion_tokens"),
        data.get("total_tokens"),
    )
    return data


def _sanitize_provider_error_detail(detail: str) -> str:
    """Turn Google/LiteLLM HTML error pages into a short readable message."""
    text = (detail or "").strip()
    if not text:
        return text
    lower = text.lower()
    # Google often returns a full HTML 403 page via LiteLLM.
    if "<!doctype html" in lower or "<html" in lower:
        m = re.search(
            r"does not have permission to get URL\s*<code>([^<]+)</code>",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            path = m.group(1).strip()
            return (
                f"Google AI returned HTTP 403 Forbidden for {path}. "
                "Check the API key, billing/quota for this Google project, "
                "and whether a VPN or network policy is blocking Gemini."
            )
        if "403" in text or "forbidden" in lower:
            return (
                "Google AI returned HTTP 403 Forbidden. "
                "Check the API key, billing/quota for this Google project, "
                "and whether a VPN or network policy is blocking Gemini."
            )
        # Avoid dumping huge HTML into MessageBox / logs consumers.
        return "AI provider returned an HTML error page (request was rejected)."
    # Truncate extreme noise while keeping useful API messages.
    if len(text) > 800:
        return text[:797] + "…"
    return text


def classify_provider_error(exc: BaseException) -> tuple[str, str]:
    """Map a provider/LiteLLM exception to (error_key, detail)."""
    detail = _sanitize_provider_error_detail(str(exc) or type(exc).__name__)
    name = type(exc).__name__.lower()
    msg = detail.lower()
    raw = (str(exc) or "").lower()

    timeout_type = False
    mod = _litellm
    if mod is not None:
        timeout_cls = getattr(mod, "Timeout", None)
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

    if mod is not None:
        auth_cls = getattr(mod, "AuthenticationError", None)
        if auth_cls is not None and isinstance(exc, auth_cls):
            return "no_key", detail
        conn_cls = getattr(mod, "APIConnectionError", None)
        if conn_cls is not None and isinstance(exc, conn_cls):
            return "network", detail
        not_found = getattr(mod, "NotFoundError", None)
        if not_found is not None and isinstance(exc, not_found):
            return "no_model", detail

    if "api key" in msg or "authentication" in msg or "unauthorized" in msg:
        return "no_key", detail
    if "connection" in msg or "connect" in msg or "name or service not known" in msg:
        return "network", detail
    if "403" in raw or "forbidden" in raw:
        return "provider_error", detail
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
        response = litellm_completion(**kwargs)
    except Exception as exc:
        key, detail = classify_provider_error(exc)
        logger.exception("AI connection check failed (%s): %s", key, detail)
        return False, key, detail

    if cancel_event is not None and cancel_event.is_set():
        return False, "cancelled", ""
    try:
        log_completion_cost(model=model, response=response, operation="connection_check")
    except Exception:
        logger.debug("Connection-check cost logging failed", exc_info=True)
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
