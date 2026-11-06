"""Tests for the OpenRouter credits adapter."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import providers.openrouter as openrouter  # noqa: E402
from normalise import ProviderError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Deliberately not key-shaped, so a credential scanner cannot mistake it for one.
TEST_KEY = "fake-key-for-tests-only"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class OpenRouterAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.mkdtemp(prefix="openrouter-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.auth_path = Path(tmp) / "openrouter.env"
        self.auth_path.write_text(
            f"{openrouter.KEY_VARIABLE}={TEST_KEY}\n", encoding="utf-8"
        )
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fake_request(self, credits_payload, key_payload):
        def fake(url, headers, signin_hint="", none_on_status=frozenset()):
            self.calls.append((url, headers))
            payload = (
                credits_payload if url == openrouter.CREDITS_URL else key_payload
            )
            if isinstance(payload, Exception):
                raise payload
            return payload

        return fake

    def fetch(self, credits_payload=None, key_payload=None) -> dict:
        credits_payload = (
            load_fixture("openrouter-credits.json")
            if credits_payload is None
            else credits_payload
        )
        key_payload = (
            load_fixture("openrouter-key.json") if key_payload is None else key_payload
        )
        with mock.patch.object(
            openrouter, "request_json", self.fake_request(credits_payload, key_payload)
        ):
            return openrouter.fetch(self.auth_path)

    def test_full_fetch(self) -> None:
        result = self.fetch()
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["bars"]), 1)
        self.assertEqual(result["plan"], "Credits")
        self.assertEqual(result["plan_source"], "unknown")
        self.assertEqual(result["id"], "openrouter")
        self.assertEqual(result["label"], "OpenRouter")
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["auth_state"], "ok")
        self.assertFalse(result["stale"])
        self.assertIsNone(result["error"])
        self.assertIsNone(result["error_short"])
        self.assertIsInstance(result["fetched_at"], int)
        bar = result["bars"][0]
        self.assertEqual(bar["id"], "credits")
        self.assertEqual(bar["label"], "Credits")
        self.assertTrue(bar["active"])
        self.assertEqual(bar["severity"], "normal")
        self.assertEqual(result["credits"]["kind"], "balance")
        self.assertTrue(result["credits"]["enabled"])
        self.assertFalse(result["credits"]["unlimited"])
        self.assertEqual(result["credits"]["short_text"], "75.00")
        [(credits_url, credits_headers), (key_url, key_headers)] = self.calls
        self.assertEqual(credits_url, openrouter.CREDITS_URL)
        self.assertEqual(key_url, openrouter.KEY_URL)
        self.assertEqual(credits_headers["Authorization"], f"Bearer {TEST_KEY}")
        self.assertEqual(credits_headers["Accept"], "application/json")
        self.assertEqual(key_headers["Authorization"], f"Bearer {TEST_KEY}")

    def test_bar_percent_and_reset(self) -> None:
        bar = self.fetch()["bars"][0]
        self.assertEqual(bar["percent"], 25)
        self.assertIsNone(bar["resets_at"])

    def test_balance_text(self) -> None:
        self.assertEqual(self.fetch()["credits"]["balance_text"], "75.00")

    def test_note_lane_split(self) -> None:
        self.assertEqual(self.fetch()["credits"]["note"], "this key: 10.00")

    def test_failed_key_call_not_fatal(self) -> None:
        result = self.fetch(
            key_payload=ProviderError("request failed (HTTP 500)", "HTTP 500")
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["bars"]), 1)
        self.assertIsNone(result["credits"]["note"])
        self.assertEqual(result["credits"]["balance_text"], "75.00")

    def test_failed_credits_call_fatal(self) -> None:
        with self.assertRaises(ProviderError):
            self.fetch(
                credits_payload=ProviderError("request failed (HTTP 500)", "HTTP 500")
            )

    def test_zero_total_credits_fatal(self) -> None:
        credits_payload = load_fixture("openrouter-credits.json")
        credits_payload["data"]["total_credits"] = 0
        with self.assertRaises(ProviderError) as ctx:
            self.fetch(credits_payload=credits_payload)
        self.assertEqual(ctx.exception.error_short, "bad response")

    def test_key_file_parser(self) -> None:
        parse = openrouter._parse_key_file
        self.assertEqual(parse("# comment\nOPENROUTER_API_KEY=abc123"), "abc123")
        self.assertEqual(parse("\n\nOPENROUTER_API_KEY=abc123\n"), "abc123")
        self.assertEqual(parse('OPENROUTER_API_KEY="abc123"'), "abc123")
        self.assertEqual(parse("OPENROUTER_API_KEY='abc123'"), "abc123")
        self.assertEqual(parse("  OPENROUTER_API_KEY=  abc123  "), "abc123")

    def test_missing_variable_is_no_auth(self) -> None:
        self.auth_path.write_text(
            "# only a comment\nOTHER=secret-value\n", encoding="utf-8"
        )
        with self.assertRaises(ProviderError) as ctx:
            openrouter.fetch(self.auth_path)
        message = str(ctx.exception)
        self.assertEqual(ctx.exception.error_short, "no auth")
        self.assertNotIn(str(self.auth_path), message)
        self.assertNotIn("secret-value", message)
        self.assertNotIn(TEST_KEY, message)
        self.auth_path.unlink()
        with self.assertRaises(ProviderError) as ctx:
            openrouter.fetch(self.auth_path)
        self.assertEqual(ctx.exception.error_short, "no auth")
        self.assertNotIn(str(self.auth_path), str(ctx.exception))

    def test_available(self) -> None:
        missing = self.auth_path.parent / "absent.env"
        self.assertFalse(openrouter.available(missing))
        self.assertTrue(openrouter.available(self.auth_path))


if __name__ == "__main__":
    unittest.main()
