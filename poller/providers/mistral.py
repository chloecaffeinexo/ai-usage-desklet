"""Mistral Vibe usage adapter.

Mistral publishes the monthly Vibe Code allowance at ``GET /v1/billing/budget``
and the plan at ``GET /v1/billing/subscription``, the same two calls Vibe 3.1
makes for its own footer (``vibe/app_server/_billing.py``). ``usage_percentage``
is already a percentage (Vibe notes usage past 100 is billed, not paused), and
``reset_at`` is an absolute ISO instant, so nothing is computed locally.

The key is the one Vibe stored at sign-in, read from the desktop keyring under
Vibe's own service name. It is read, never written, and never logged.
"""

from __future__ import annotations

import time
from typing import Any

from normalise import (
    ProviderError,
    epoch_from_iso,
    percent,
    request_json,
    severity,
)


API_BASE = "https://api.mistral.ai/v1/"
KEYRING_SERVICES = ("ai.mistral.vibe", "vibe")
KEY_NAME = "MISTRAL_API_KEY"
SIGNIN_HINT = "Open a terminal, run vibe and sign in"

# Budget keys in the order the card shows them.
BUDGETS = (
    ("vibe_budget", "vibe", "Vibe monthly"),
    ("api_budget", "api", "API monthly"),
)


def _read_key() -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    for service in KEYRING_SERVICES:
        try:
            value = keyring.get_password(service, KEY_NAME)
        except Exception:
            # Locked or absent keyring: no card rather than a crash.
            continue
        if value:
            return value
    return None


def _get(key: str, path: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    payload = request_json(API_BASE + path, headers, SIGNIN_HINT)
    if payload is None:
        raise ProviderError(f"{path} returned no document", "bad response")
    return payload


def _bar(bar_id: str, label: str, detail: Any) -> dict[str, Any] | None:
    if not isinstance(detail, dict) or detail.get("usage_percentage") is None:
        return None
    pct = percent(detail.get("usage_percentage"))
    return {
        "id": bar_id,
        "label": label,
        "percent": pct,
        "severity": severity(None, pct),
        "resets_at": epoch_from_iso(detail.get("reset_at")),
        "active": True,
    }


def _plan(subscription: dict[str, Any]) -> str:
    name = subscription.get("plan_name")
    if not isinstance(name, str) or not name.strip():
        return "Unknown"
    return name.strip().replace("_", " ").title()


def fetch() -> dict[str, Any]:
    key = _read_key()
    if not key:
        raise ProviderError("credentials missing api key", "no auth")
    budget = _get(key, "billing/budget")
    bars = [b for b in (_bar(bid, label, budget.get(field)) for field, bid, label in BUDGETS) if b]
    if not bars:
        raise ProviderError("budget response contained no allowance", "bad response")
    try:
        plan = _plan(_get(key, "billing/subscription"))
        plan_source = "api"
    except ProviderError:
        plan, plan_source = "Unknown", "unknown"
    # The Individual plan carries no published billing date; the Vibe budget's
    # monthly reset instant is the documented clock-over, so that is the date
    # shown beside the plan. Measured 6 Nov 2026: no renewal field exists in
    # billing/subscription, and budget.reset_at is an absolute ISO instant.
    vibe = budget.get("vibe_budget") if isinstance(budget.get("vibe_budget"), dict) else {}
    renews_at = epoch_from_iso(vibe.get("reset_at"))
    return {
        "id": "mistral",
        "label": "Mistral",
        "plan": plan,
        "plan_source": plan_source,
        "ok": True,
        "stale": False,
        "source": "api",
        "auth_state": "ok",
        "fetched_at": int(time.time()),
        "error": None,
        "error_short": None,
        "bars": bars,
        "credits": None,
        "renews_at": renews_at,
        "renews_period": "monthly" if renews_at else None,
    }


def available() -> bool:
    return bool(_read_key())
