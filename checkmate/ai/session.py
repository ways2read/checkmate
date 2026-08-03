"""Per-issue AI conversation session."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from ..fido_settings import resolve_litellm_model_and_key
from .litellm_client import (
    DEFAULT_COMPLETION_TIMEOUT_SEC,
    DEFAULT_EXPLAIN_MAX_TOKENS,
    DEFAULT_FOLLOWUP_MAX_TOKENS,
    assistant_text_from_response,
    check_provider_connection,
    classify_provider_error,
    litellm_completion,
)

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Classified AI provider failure with a UI error_key and detail text."""

    def __init__(self, error_key: str, detail: str = "") -> None:
        super().__init__(detail or error_key)
        self.error_key = error_key
        self.detail = detail


@dataclass
class ExplainSession:
    """Holds message history for one issue explanation + follow-ups."""

    model: str
    api_key: str | None
    api_base: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_finish_reason: str | None = None

    @classmethod
    def create(cls) -> ExplainSession:
        model, key, base = resolve_litellm_model_and_key()
        if not model:
            raise RuntimeError("no_model")
        if not key:
            raise RuntimeError("no_key")
        return cls(model=model, api_key=key, api_base=base)

    def check_connection(
        self,
        *,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bool, str | None, str]:
        return check_provider_connection(
            model=self.model,
            api_key=self.api_key,
            api_base=self.api_base,
            cancel_event=cancel_event,
        )

    def ask(self, *, system: str, user: str, max_tokens: int = DEFAULT_EXPLAIN_MAX_TOKENS) -> str:
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        self.last_finish_reason = None
        return self._complete(max_tokens=max_tokens)

    def followup(self, user: str, max_tokens: int = DEFAULT_FOLLOWUP_MAX_TOKENS) -> str:
        if not self.messages:
            raise RuntimeError("no_session")
        self.messages.append({"role": "user", "content": user})
        return self._complete(max_tokens=max_tokens)

    def _complete(self, *, max_tokens: int) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "api_key": self.api_key,
            "max_tokens": max_tokens,
            "timeout": DEFAULT_COMPLETION_TIMEOUT_SEC,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        try:
            response = litellm_completion(**kwargs)
        except Exception as exc:
            key, detail = classify_provider_error(exc)
            logger.exception("LiteLLM completion failed (%s)", key)
            raise ProviderError(key, detail) from exc
        text = assistant_text_from_response(response)
        try:
            self.last_finish_reason = getattr(
                response.choices[0], "finish_reason", None
            )
        except Exception:
            self.last_finish_reason = None
        self.messages.append({"role": "assistant", "content": text or ""})
        return text or ""
