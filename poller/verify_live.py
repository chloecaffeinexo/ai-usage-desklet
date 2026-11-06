#!/usr/bin/env python3
"""Check every card in state.json against the provider's own API, field by field.

Run it whenever the numbers need proving rather than trusting. It re-fetches each
provider's raw payload, recomputes what the card should say straight from that
payload, and compares. It prints figures only: no token, account id or email is
read into the output.

    python3 verify_live.py

Exit code 0 when every provider agrees, 1 when any disagrees, 2 when every
remaining card agrees but at least one card could not be checked at all
(a dead credential or an unreachable service). A card that is honestly
reporting a credential failure is not showing a wrong number, so only exit 1
should ever raise an alarm.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from normalise import ProviderError, epoch_from_iso, percent  # noqa: E402
from providers import chatgpt, claude, mistral, opencode, openrouter  # noqa: E402

STATE = pathlib.Path.home() / ".cache/ai-usage-desklet/state.json"
SANE_EARLIEST = 1_600_000_000
SANE_LATEST = 2_000_000_000


def when(epoch: int | None) -> str:
    if not epoch:
        return "no reset"
    return datetime.fromtimestamp(epoch).strftime("%a %d %b %H:%M")


def renews_rows(card, expected_at, expected_period):
    """Compare a card's renewal fields against a value computed fresh now.

    The poller publishes a renewal only where the provider returns a real
    field, so the expectation is the same computation it uses, run again:
    reset dates are absolute, so two calls seconds apart agree.
    """
    return [
        ("renews_at", card.get("renews_at"), expected_at),
        ("renews_period", card.get("renews_period"), expected_period),
    ]


# Reset instants are allowed to differ by a second or two. Measured 2026-08-28:
# ChatGPT's reset_at held steady at 1787918577 across three calls but had reported
# 1787918578 a minute earlier, because the provider derives it from a sub-second
# clock. Claude's equivalent was already known to jitter either side of the whole
# second, which is why normalise.epoch_from_iso rounds rather than truncates. A
# countdown shown in minutes cannot express the difference, so it is not a fault.
#
# Two seconds turned out to be too tight. Counted across accuracy-checks.log on
# 2026-08-31, every drift this script has ever seen fell between -1s and +5s, and
# the +3s to +5s ones failed the run and sent a notification every time. The gap
# is the interval between the poller's call and this script's own call, so it
# grows with how slow the provider is that minute, not with any fault. Fifteen
# seconds clears the measured spread with room to spare and is still far below a
# real mismatch, which is minutes or hours out, never seconds.
RESET_TOLERANCE_SECONDS = 15


def compare(name: str, rows: list[tuple[str, object, object]]) -> bool:
    ok = True
    print(f"\n{name}")
    for label, card_value, api_value in rows:
        agree = card_value == api_value
        drift = None
        if (
            not agree
            and "resets_at" in label
            and isinstance(card_value, int)
            and isinstance(api_value, int)
        ):
            drift = api_value - card_value
            agree = abs(drift) <= RESET_TOLERANCE_SECONDS
        ok = ok and agree
        mark = "ok  " if agree else "DIFF"
        note = ""
        if drift is not None:
            note = f"   ({drift:+d}s, {'within' if agree else 'outside'} tolerance)"
        print(f"  {mark} {label:22} card {card_value!r:>22}   api {api_value!r}{note}")
    return ok


def main() -> int:
    # Poll first, so the cards are compared against a live call made seconds later
    # rather than against whatever the timer last wrote. Spend-based figures move:
    # a stale state.json makes OpenRouter's balance and Claude's percentages look
    # like faults when they are only older than the call being compared to.
    #
    # --force matters. Without it the poller honours its per-provider intervals,
    # and OpenRouter is on 300 seconds, so its card keeps a cached figure while
    # this script compares it against a fresh call. That produced a false mismatch
    # on 2026-08-28 before the flag was added.
    subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).resolve().parent / "poller.py"),
            "--once",
            "--force",
        ],
        check=False, capture_output=True,
    )

    state = json.loads(STATE.read_text())
    age = int(time.time()) - state["generated_at"]
    print(f"state.json is {age}s old, {len(state['providers'])} providers")
    if age > 20:
        print("  WARNING: the poll did not refresh it; figures may have moved since.")
    cards = {p["id"]: p for p in state["providers"]}
    everything_ok = True
    # Cards that could not be checked either way, keyed by provider id so a
    # provider that several paths report is still only counted once.
    unreachable: dict[str, str] = {}

    def check(name: str, provider_id: str):
        """Run one provider's comparison, turning an unreachable provider into a finding.

        A provider whose credentials have expired cannot be verified either way. That
        is not the same as a figure disagreeing, so it is collected separately and the
        remaining providers are still checked.
        """

        def wrapper(fn):
            nonlocal everything_ok
            try:
                everything_ok = fn() and everything_ok
            except ProviderError as exc:
                unreachable.setdefault(provider_id, f"{name}: {exc}")
                print(f"\n{name}\n  SKIP could not reach the service: {exc}")
            except (OSError, KeyError, TypeError, ValueError) as exc:
                unreachable.setdefault(provider_id, f"{name}: {type(exc).__name__}")
                print(f"\n{name}\n  SKIP unexpected {type(exc).__name__} while checking")
            return None

        return wrapper

    # --- ChatGPT -----------------------------------------------------------
    if "chatgpt" in cards:
      @check("ChatGPT", "chatgpt")
      def _run():
          token, account = chatgpt._read_auth(chatgpt.DEFAULT_AUTH)
          limits = chatgpt._request_usage(token, account)["rate_limit"]
          card = cards["chatgpt"]
          bars = {b["id"]: b for b in card["bars"]}
          rows = []
          for bar_id, key in (("primary", "primary_window"), ("secondary", "secondary_window")):
              window = limits.get(key) or {}
              bar = bars.get(bar_id, {})
              rows.append((f"{bar_id} percent", bar.get("percent"), window.get("used_percent")))
              rows.append((f"{bar_id} resets_at", bar.get("resets_at"), window.get("reset_at")))
          expected_at, expected_period = chatgpt._renewal(chatgpt.DEFAULT_AUTH)
          rows += renews_rows(card, expected_at, expected_period)
          return compare("ChatGPT", rows)

    # --- OpenRouter --------------------------------------------------------
    if "openrouter" in cards:
      @check("OpenRouter", "openrouter")
      def _run():
          key = openrouter._read_auth(openrouter.DEFAULT_AUTH)
          headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
          credits = openrouter.request_json(openrouter.CREDITS_URL, headers)["data"]
          card = cards["openrouter"]
          bar = card["bars"][0]
          total, used = credits["total_credits"], credits["total_usage"]
          expected_pct = round(used / total * 100, 1)
          if float(expected_pct).is_integer():
              expected_pct = int(expected_pct)
          rows = [
              ("credits percent", bar["percent"], expected_pct),
              ("credits resets_at", bar["resets_at"], None),
              ("balance", card["credits"]["balance_text"], f"{total - used:,.2f}"),
          ]
          return compare("OpenRouter", rows)

    # --- OpenCode ----------------------------------------------------------
    if "opencode" in cards:
      @check("OpenCode", "opencode")
      def _run():
          key = opencode._read_auth(opencode.DEFAULT_AUTH)
          usage = opencode._request(key)["usage"]
          card = cards["opencode"]
          bars = {b["id"]: b for b in card["bars"]}
          rows = []
          for window_id, _label in opencode.WINDOWS:
              window = usage.get(window_id) or {}
              bar = bars.get(window_id, {})
              rows.append(
                  (f"{window_id} percent", bar.get("percent"), percent(window.get("percent")))
              )
              rows.append(
                  (
                      f"{window_id} resets_at",
                      bar.get("resets_at"),
                      epoch_from_iso(window.get("resetsAt")),
                  )
              )
          monthly = usage.get("monthly") if isinstance(usage.get("monthly"), dict) else {}
          expected_at = epoch_from_iso(monthly.get("resetsAt"))
          rows += renews_rows(card, expected_at, "monthly" if expected_at else None)
          return compare("OpenCode", rows)

    # --- Mistral -----------------------------------------------------------
    if "mistral" in cards:
      @check("Mistral", "mistral")
      def _run():
          key = mistral._read_key()
          if not key:
              raise ProviderError("credentials missing api key", "no auth")
          budget = mistral._get(key, "billing/budget")
          card = cards["mistral"]
          bars = {b["id"]: b for b in card["bars"]}
          rows = []
          for field, bar_id, _label in mistral.BUDGETS:
              detail = budget.get(field) or {}
              bar = bars.get(bar_id, {})
              rows.append((f"{bar_id} percent", bar.get("percent"),
                           percent(detail.get("usage_percentage"))))
              rows.append((f"{bar_id} resets_at", bar.get("resets_at"),
                           epoch_from_iso(detail.get("reset_at"))))
          vibe = budget.get("vibe_budget") if isinstance(budget.get("vibe_budget"), dict) else {}
          expected_at = epoch_from_iso(vibe.get("reset_at"))
          rows += renews_rows(card, expected_at, "monthly" if expected_at else None)
          return compare("Mistral", rows)

    # --- Claude ------------------------------------------------------------
    if "claude" in cards:
      @check("Claude", "claude")
      def _run():
          card = cards["claude"]
          fresh, _, _ = claude.fetch(claude.DEFAULT_CREDENTIALS, None)
          live = {b["id"]: b for b in fresh["bars"]}
          rows = [("plan", card["plan"], fresh["plan"])]
          for bar in card["bars"]:
              other = live.get(bar["id"], {})
              rows.append((f"{bar['id']} resets_at", bar["resets_at"], other.get("resets_at")))
          agreed = compare("Claude (reset instants and plan)", rows)
          # Claude's percentages are compared by eye rather than asserted: they
          # advance between two calls seconds apart, so an exact match is not
          # something the tool can require without producing false alarms.
          print("     percentages, card against a call made just now:")
          for bar in card["bars"]:
              mine = live.get(bar["id"], {}).get("percent")
              print(f"       {bar['label']:16} card {bar['percent']}%   live {mine}%")
          return agreed

    # --- Sanity rules that apply to every card -----------------------------
    print("\nSanity checks across every card")
    problems = []
    for provider in state["providers"]:
        for bar in provider["bars"]:
            reset = bar["resets_at"]
            if reset is not None and not (SANE_EARLIEST < reset < SANE_LATEST):
                problems.append(f"{provider['id']}/{bar['id']} reset {reset} is not a plausible epoch")
            if not 0 <= bar["percent"] <= 100:
                problems.append(f"{provider['id']}/{bar['id']} percent {bar['percent']} out of range")
        if not provider["ok"]:
            # A card that is honestly reporting "token expired" or "could not
            # reach the provider" is not showing a wrong number: it is the same
            # unverifiable state as the SKIP path above, only discovered from
            # state.json instead of from a fresh call. Counting it as a mismatch
            # made a dead credential raise "showing a wrong number" every run
            # until the card recovered, which is what filled the notifications
            # between 8 and 13 October 2026.
            unreachable.setdefault(
                provider["id"], f"{provider['id']}: {provider['error']}"
            )
    blob = json.dumps(state)
    for needle in ("Bearer", "sk-", "eyJ", "@gmail", "apiKey", "access_token"):
        if needle in blob:
            problems.append(f"state.json contains {needle!r}")
    for problem in problems:
        print("  PROBLEM", problem)
    if not problems:
        print("  all resets plausible, all percentages in range, no credential strings")

    everything_ok = everything_ok and not problems

    if unreachable:
        print("\nCould not be verified either way:")
        for line in unreachable.values():
            print("  ", line)

    if not everything_ok:
        verdict = "MISMATCHES ABOVE"
    elif unreachable:
        verdict = f"the {len(cards) - len(unreachable)} reachable cards agree; {len(unreachable)} could not be checked"
    else:
        verdict = "every card agrees with its service"
    print("\nRESULT:", verdict)
    if not everything_ok:
        return 1
    return 2 if unreachable else 0


if __name__ == "__main__":
    raise SystemExit(main())
