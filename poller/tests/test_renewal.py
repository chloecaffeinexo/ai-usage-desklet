"""Renewal dates beside the plan pill (added 6 Nov 2026).

Each provider's billing clock-over is taken only from a field the provider
publishes, never computed from a calendar:

  ChatGPT  the id_token's own signed claims, and only while the claimed
           "active until" is still in the future, because the claims are
           rewritten only on re-authentication and can sit months stale.
  OpenCode the monthly window's reset instant; no subscription endpoint
           exists (subscription/billing/account all 404).
  Mistral  the Vibe budget's reset instant; billing/subscription publishes
           no renewal date.
  Claude and OpenRouter publish nothing, so they carry no date at all.
"""

from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import providers.chatgpt as chatgpt  # noqa: E402
import providers.mistral as mistral  # noqa: E402
import providers.opencode as opencode  # noqa: E402
from normalise import epoch_from_iso  # noqa: E402

DAY = 86_400


def claim_date(epoch: int) -> str:
    """The claim format OpenAI actually signs: 2026-07-13T07:50:57+00:00."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def fake_id_token(start: int | None, until: int | None) -> str:
    """A dummy JWT whose payload carries the subscription claims.

    The token is unsigned and the values are synthetic dates; nothing here can
    be mistaken for a credential.
    """

    def enc(value):
        text = json.dumps(value).encode()
        return base64.urlsafe_b64encode(text).decode().rstrip("=")

    auth = {}
    if start is not None:
        auth["chatgpt_subscription_active_start"] = claim_date(start)
    if until is not None:
        auth["chatgpt_subscription_active_until"] = claim_date(until)
    claims = {"email": "test@example.invalid", "https://api.openai.com/auth": auth}
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def token_file(start: int | None, until: int | None) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="renewal-test-"))
    path = tmp / "auth.json"
    path.write_text(
        json.dumps({"tokens": {"id_token": fake_id_token(start, until)}}),
        encoding="utf-8",
    )
    return path


class ChatGPTRenewalTest(unittest.TestCase):
    def test_monthly(self) -> None:
        now = int(time.time())
        path = token_file(now - 15 * DAY, now + 15 * DAY)
        self.assertEqual(chatgpt._renewal(path), (now + 15 * DAY, "monthly"))

    def test_yearly(self) -> None:
        now = int(time.time())
        path = token_file(now - 350 * DAY, now + 15 * DAY)
        self.assertEqual(chatgpt._renewal(path), (now + 15 * DAY, "yearly"))

    def test_stale_claim_is_no_date(self) -> None:
        # Measured on the live account: a July "until" while the plan was
        # still active in November, because the claims lag a re-login. A past
        # value must never be shown.
        now = int(time.time())
        path = token_file(now - 40 * DAY, now - 10 * DAY)
        self.assertEqual(chatgpt._renewal(path), (None, None))

    def test_unmeasured_span_keeps_the_date_without_a_period(self) -> None:
        now = int(time.time())
        path = token_file(now - 60 * DAY, now + 15 * DAY)
        self.assertEqual(chatgpt._renewal(path), (now + 15 * DAY, None))

    def test_no_start_keeps_future_until(self) -> None:
        now = int(time.time())
        path = token_file(None, now + 15 * DAY)
        self.assertEqual(chatgpt._renewal(path), (now + 15 * DAY, None))

    def test_missing_or_malformed_file(self) -> None:
        self.assertEqual(chatgpt._renewal(Path("/nonexistent/auth.json")), (None, None))
        path = token_file(None, None)
        self.assertEqual(chatgpt._renewal(path), (None, None))


class OpenCodeRenewalTest(unittest.TestCase):
    def test_renews_with_the_monthly_window(self) -> None:
        fixtures = Path(__file__).resolve().parent / "fixtures"
        payload = json.loads((fixtures / "opencode-usage.json").read_text())
        monthly = payload["usage"]["monthly"]
        expected = epoch_from_iso(monthly["resetsAt"])
        self.assertIsNotNone(expected)
        tmp = Path(tempfile.mkdtemp(prefix="renewal-test-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        auth = tmp / "opencode.env"
        auth.write_text(f"{opencode.KEY_VARIABLE}=fake-key-for-tests-only\n", encoding="utf-8")
        with mock.patch.object(opencode, "request_json", return_value=payload):
            result = opencode.fetch(auth)
        self.assertEqual(result["renews_at"], round(expected))
        self.assertEqual(result["renews_period"], "monthly")

    def test_no_monthly_window_no_date(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="renewal-test-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        auth = tmp / "opencode.env"
        auth.write_text(f"{opencode.KEY_VARIABLE}=fake-key-for-tests-only\n", encoding="utf-8")
        payload = {
            "usage": {
                "rolling": {"status": "ok", "percent": 0, "resetsAt": "2026-11-06T18:00:00Z"},
            }
        }
        with mock.patch.object(opencode, "request_json", return_value=payload):
            result = opencode.fetch(auth)
        self.assertIsNone(result["renews_at"])
        self.assertIsNone(result["renews_period"])


class MistralRenewalTest(unittest.TestCase):
    def test_renews_with_the_vibe_budget(self) -> None:
        budget = {
            "vibe_budget": {"usage_percentage": 0.0, "initial_budget": 300.0,
                             "currency": "USD", "reset_at": "2026-12-01T00:00:00Z"},
            "api_budget": {"usage_percentage": 0.0, "initial_budget": 30.0,
                           "currency": "USD", "reset_at": "2026-12-01T00:00:00Z"},
        }
        with mock.patch.object(mistral, "_read_key", return_value="fake-key-for-tests-only"), \
                mock.patch.object(mistral, "request_json", side_effect=[budget, {"plan_name": "INDIVIDUAL"}]):
            result = mistral.fetch()
        self.assertEqual(result["renews_at"], 1796083200)
        self.assertEqual(result["renews_period"], "monthly")

    def test_missing_reset_carries_no_date(self) -> None:
        budget = {
            "vibe_budget": {"usage_percentage": 0.0, "initial_budget": 300.0,
                             "currency": "USD", "reset_at": None},
            "api_budget": {"usage_percentage": 0.0, "initial_budget": 30.0,
                           "currency": "USD", "reset_at": "2026-12-01T00:00:00Z"},
        }
        with mock.patch.object(mistral, "_read_key", return_value="fake-key-for-tests-only"), \
                mock.patch.object(mistral, "request_json", side_effect=[budget, {"plan_name": "INDIVIDUAL"}]):
            result = mistral.fetch()
        self.assertIsNone(result["renews_at"])
        self.assertIsNone(result["renews_period"])


if __name__ == "__main__":
    unittest.main()
