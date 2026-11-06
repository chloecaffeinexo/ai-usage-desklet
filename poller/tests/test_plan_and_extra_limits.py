"""Ultra plan labelling and ChatGPT's extra named limits (added 6 Nov 2026)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import providers.chatgpt as chatgpt  # noqa: E402
import providers.claude as claude  # noqa: E402


class UltraPlanTest(unittest.TestCase):
    def plan(self, tier, has_max=True):
        payload = {"account": {"has_claude_max": has_max},
                   "organization": {"rate_limit_tier": tier}}
        with mock.patch.object(claude, "request_json", return_value=payload):
            return claude._plan_from_profile({})

    def test_ultra_tier_is_not_called_max(self) -> None:
        self.assertEqual(self.plan("default_claude_ultra"), "Ultra")

    def test_known_tiers_unchanged(self) -> None:
        self.assertEqual(self.plan("default_claude_max_5x"), "Max 5x")
        self.assertEqual(self.plan("default_claude_ai", has_max=False), "Pro")


class AdditionalLimitsTest(unittest.TestCase):
    WINDOW = {"used_percent": 40, "limit_window_seconds": 604800, "reset_at": 1796083200}

    def test_absent_or_null_adds_nothing(self) -> None:
        self.assertEqual(chatgpt._additional_bars(None), [])
        self.assertEqual(chatgpt._additional_bars([]), [])

    def test_named_entry_with_rate_limit(self) -> None:
        bars = chatgpt._additional_bars(
            [{"limit_name": "shared_allowance", "rate_limit": {"primary_window": self.WINDOW}}])
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0]["percent"], 40)
        self.assertEqual(bars[0]["resets_at"], 1796083200)
        self.assertTrue(bars[0]["label"].startswith("Shared allowance · "))

    def test_unrecognised_shape_is_skipped(self) -> None:
        self.assertEqual(chatgpt._additional_bars([{"something": 1}, "text"]), [])


if __name__ == "__main__":
    unittest.main()
