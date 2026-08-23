"""Tests for LiteLLM cost / usage extraction (logger-side)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from checkmate.ai.litellm_client import cost_and_usage_from_response


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class CostAndUsageTests(unittest.TestCase):
    def test_reads_hidden_params_cost_and_usage(self) -> None:
        response = SimpleNamespace(
            usage=_FakeUsage(100, 40),
            _hidden_params={"response_cost": 0.001234},
        )
        metrics = cost_and_usage_from_response(response)
        self.assertAlmostEqual(metrics["cost_usd"], 0.001234)
        self.assertEqual(metrics["prompt_tokens"], 100)
        self.assertEqual(metrics["completion_tokens"], 40)
        self.assertEqual(metrics["total_tokens"], 140)

    def test_missing_cost_still_returns_tokens(self) -> None:
        response = SimpleNamespace(usage=_FakeUsage(10, 5), _hidden_params={})
        metrics = cost_and_usage_from_response(response)
        self.assertEqual(metrics["prompt_tokens"], 10)
        self.assertEqual(metrics["completion_tokens"], 5)
        # cost_usd may be None (no pricing) or estimated via litellm.completion_cost
        self.assertTrue(
            metrics["cost_usd"] is None or isinstance(metrics["cost_usd"], float)
        )

    def test_unmapped_model_cost_does_not_raise(self) -> None:
        def boom(**_kwargs):
            raise Exception(
                "This model isn't mapped yet. Add it here - "
                "https://github.com/BerriAI/litellm/blob/main/"
                "model_prices_and_context_window.json"
            )

        response = SimpleNamespace(
            usage=_FakeUsage(10, 5),
            _hidden_params={},
        )
        with mock.patch(
            "checkmate.ai.litellm_client._get_litellm"
        ) as get_mod:
            get_mod.return_value = SimpleNamespace(completion_cost=boom)
            metrics = cost_and_usage_from_response(response)
        self.assertIsNone(metrics["cost_usd"])
        self.assertEqual(metrics["prompt_tokens"], 10)
        self.assertEqual(metrics["completion_tokens"], 5)

    def test_none_response(self) -> None:
        metrics = cost_and_usage_from_response(None)
        self.assertIsNone(metrics["cost_usd"])
        self.assertIsNone(metrics["prompt_tokens"])


if __name__ == "__main__":
    unittest.main()
