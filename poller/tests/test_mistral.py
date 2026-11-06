"""Tests for the Mistral Vibe usage adapter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import providers.mistral as mistral  # noqa: E402
from normalise import ProviderError  # noqa: E402

# Deliberately not key-shaped, so a credential scanner cannot mistake it for one.
TEST_KEY = "fake-key-for-tests-only"

# Shape measured from the live endpoint on 6 Nov 2026, figures changed.
BUDGET = {
    "vibe_budget": {"usage_percentage": 12.34, "initial_budget": 300.0,
                    "currency": "USD", "reset_at": "2026-12-01T00:00:00Z", "payg_enabled": False},
    "api_budget": {"usage_percentage": 0.0, "initial_budget": 30.0,
                   "currency": "USD", "reset_at": "2026-12-01T00:00:00Z", "payg_enabled": False},
    "usage_percentage": 0.0, "initial_budget": 30.0, "currency": "USD",
    "reset_at": "2026-12-01T00:00:00Z",
}
SUBSCRIPTION = {"plan_name": "INDIVIDUAL", "payg_enabled": False, "vibe_payg_enabled": False}


class MistralAdapterTest(unittest.TestCase):
    def fetch(self, budget=BUDGET, subscription=SUBSCRIPTION, key=TEST_KEY) -> dict:
        def fake(url, headers, signin_hint="", none_on_status=frozenset()):
            self.assertEqual(headers["Authorization"], f"Bearer {key}")
            payload = budget if url.endswith("billing/budget") else subscription
            if isinstance(payload, Exception):
                raise payload
            return payload

        with mock.patch.object(mistral, "_read_key", return_value=key), \
                mock.patch.object(mistral, "request_json", fake):
            return mistral.fetch()

    def test_full_fetch(self) -> None:
        result = self.fetch()
        self.assertTrue(result["ok"])
        self.assertEqual(result["id"], "mistral")
        self.assertEqual(result["plan"], "Individual")
        self.assertEqual([b["id"] for b in result["bars"]], ["vibe", "api"])
        self.assertEqual(result["bars"][0]["percent"], 12.3)
        self.assertEqual(result["bars"][0]["resets_at"], 1796083200)
        self.assertLess(result["bars"][0]["resets_at"], 2_000_000_000)

    def test_over_allowance_is_critical(self) -> None:
        budget = dict(BUDGET, vibe_budget=dict(BUDGET["vibe_budget"], usage_percentage=104.0))
        bar = self.fetch(budget=budget)["bars"][0]
        self.assertEqual(bar["percent"], 100)
        self.assertEqual(bar["severity"], "critical")

    def test_subscription_failure_keeps_bars(self) -> None:
        result = self.fetch(subscription=ProviderError("down", "offline"))
        self.assertEqual(result["plan"], "Unknown")
        self.assertEqual(len(result["bars"]), 2)

    def test_no_key_raises(self) -> None:
        with mock.patch.object(mistral, "_read_key", return_value=None):
            with self.assertRaises(ProviderError):
                mistral.fetch()

    def test_empty_budget_raises(self) -> None:
        with self.assertRaises(ProviderError):
            self.fetch(budget={})


if __name__ == "__main__":
    unittest.main()
