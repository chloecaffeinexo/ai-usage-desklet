"""Tests for the OpenCode Go usage adapter."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import providers.opencode as opencode  # noqa: E402
from normalise import ProviderError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Deliberately not key-shaped, so a credential scanner cannot mistake it for one.
TEST_KEY = "fake-key-for-tests-only"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class OpenCodeAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.mkdtemp(prefix="opencode-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.auth_path = Path(tmp) / "opencode.env"
        self.auth_path.write_text(
            f"# a comment\n{opencode.KEY_VARIABLE}={TEST_KEY}\n", encoding="utf-8"
        )
        self.calls: list[tuple[str, dict[str, str]]] = []

    def fake_request(self, payload):
        def fake(url, headers, signin_hint="", none_on_status=frozenset()):
            self.calls.append((url, headers))
            if isinstance(payload, Exception):
                raise payload
            return payload

        return fake

    def fetch(self, payload=None) -> dict:
        payload = (
            load_fixture("opencode-usage.json") if payload is None else payload
        )
        with mock.patch.object(
            opencode, "request_json", self.fake_request(payload)
        ):
            return opencode.fetch(self.auth_path)

    def test_full_fetch(self) -> None:
        result = self.fetch()
        self.assertTrue(result["ok"])
        self.assertFalse(result["stale"])
        self.assertEqual(result["id"], "opencode")
        self.assertEqual(result["label"], "OpenCode")
        self.assertEqual(result["plan"], "Go")
        self.assertEqual(result["plan_source"], "api")
        self.assertEqual(result["source"], "api")
        self.assertEqual(result["auth_state"], "ok")
        self.assertIsNone(result["error"])
        self.assertIsNone(result["error_short"])
        self.assertIsNone(result["credits"])
        self.assertIsInstance(result["fetched_at"], int)
        self.assertEqual([bar["id"] for bar in result["bars"]], ["rolling", "weekly", "monthly"])
        self.assertEqual([bar["label"] for bar in result["bars"]], ["5 hourly", "Weekly", "Monthly"])
        self.assertTrue(all(bar["active"] for bar in result["bars"]))
        url, headers = self.calls[0]
        self.assertEqual(url, opencode.USAGE_URL)
        self.assertEqual(headers["Authorization"], f"Bearer {TEST_KEY}")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["User-Agent"], opencode.CLIENT_NAME)

    def test_percentages_and_reset_instants(self) -> None:
        bars = {bar["id"]: bar for bar in self.fetch()["bars"]}
        self.assertEqual(bars["rolling"]["percent"], 12)
        self.assertEqual(bars["weekly"]["percent"], 34)
        self.assertEqual(bars["monthly"]["percent"], 56)
        # 2026-10-19T00:00:00Z, parsed as a real instant rather than a string.
        self.assertEqual(bars["weekly"]["resets_at"], 1792368000)
        self.assertEqual(bars["rolling"]["resets_at"], 1791877965)
        self.assertEqual(bars["monthly"]["severity"], "normal")

    def test_severity_follows_the_percentage(self) -> None:
        payload = load_fixture("opencode-usage.json")
        payload["usage"]["monthly"]["percent"] = 91
        bars = {bar["id"]: bar for bar in self.fetch(payload)["bars"]}
        self.assertEqual(bars["monthly"]["severity"], "critical")

    def test_a_capped_window_reads_as_spent(self) -> None:
        # The API floors a capped window's percentage, so the status decides.
        payload = load_fixture("opencode-usage.json")
        payload["usage"]["rolling"] = {
            "status": "rate-limited",
            "percent": 97,
            "resetsAt": "2026-10-13T07:52:45.000Z",
        }
        bar = self.fetch(payload)["bars"][0]
        self.assertEqual(bar["percent"], 97)
        self.assertEqual(bar["severity"], "critical")

    def test_missing_window_is_skipped(self) -> None:
        payload = load_fixture("opencode-usage.json")
        del payload["usage"]["weekly"]
        ids = [bar["id"] for bar in self.fetch(payload)["bars"]]
        self.assertEqual(ids, ["rolling", "monthly"])

    def test_no_windows_is_fatal(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self.fetch({"usage": {}})
        self.assertEqual(ctx.exception.error_short, "bad response")

    def test_missing_usage_block_is_fatal(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self.fetch({"something": "else"})
        self.assertEqual(ctx.exception.error_short, "bad response")

    def test_unparsable_reset_becomes_no_reset(self) -> None:
        payload = load_fixture("opencode-usage.json")
        payload["usage"]["monthly"]["resetsAt"] = "not-a-timestamp"
        bars = {bar["id"]: bar for bar in self.fetch(payload)["bars"]}
        self.assertIsNone(bars["monthly"]["resets_at"])

    def test_failed_call_is_fatal(self) -> None:
        with self.assertRaises(ProviderError):
            self.fetch(ProviderError("request failed (HTTP 500)", "HTTP 500"))

    def test_key_file_parser(self) -> None:
        parse = opencode._parse_key_file
        self.assertEqual(parse("# c\nOPENCODE_API_KEY=abc123"), "abc123")
        self.assertEqual(parse('OPENCODE_API_KEY="abc123"'), "abc123")
        self.assertEqual(parse("OPENCODE_API_KEY='abc123'"), "abc123")
        self.assertEqual(parse("  OPENCODE_API_KEY=  abc123  "), "abc123")
        self.assertIsNone(parse("OPENCODE_GO_API_KEY=abc123"))

    def test_missing_variable_is_no_auth(self) -> None:
        self.auth_path.write_text("# only a comment\nOTHER=secret-value\n", encoding="utf-8")
        with self.assertRaises(ProviderError) as ctx:
            opencode.fetch(self.auth_path)
        self.assertEqual(ctx.exception.error_short, "no auth")
        self.assertNotIn(str(self.auth_path), str(ctx.exception))
        self.assertNotIn("secret-value", str(ctx.exception))
        self.auth_path.unlink()
        with self.assertRaises(ProviderError) as ctx:
            opencode.fetch(self.auth_path)
        self.assertEqual(ctx.exception.error_short, "no auth")

    def test_available(self) -> None:
        missing = self.auth_path.parent / "absent.env"
        self.assertFalse(opencode.available(missing))
        self.assertTrue(opencode.available(self.auth_path))


if __name__ == "__main__":
    unittest.main()
