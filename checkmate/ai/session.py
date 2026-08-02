"""Per-issue AI conversation session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..fido_settings import resolve_litellm_model_and_key
from .litellm_client import assistant_text_from_response, litellm_completion


@dataclass
class ExplainSession:
    """Holds message history for one issue explanation + follow-ups."""

    model: str
    api_key: str | None
    api_base: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create(cls) -> ExplainSession:
        model, key, base = resolve_litellm_model_and_key()
        if not model:
            raise RuntimeError("no_model")
        if not key:
            raise RuntimeError("no_key")
        return cls(model=model, api_key=key, api_base=base)

    def ask(self, *, system: str, user: str, max_tokens: int = 2500) -> str:
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._complete(max_tokens=max_tokens)

    def followup(self, user: str, max_tokens: int = 1500) -> str:
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
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        response = litellm_completion(**kwargs)
        text = assistant_text_from_response(response)
        self.messages.append({"role": "assistant", "content": text or ""})
        return text or ""
