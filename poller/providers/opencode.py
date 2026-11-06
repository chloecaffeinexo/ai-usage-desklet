"""OpenCode Go usage adapter.

OpenCode publishes the plan's own usage at ``GET /zen/go/v1/usage`` on the Go
endpoint, authenticated with a Go API key, and returns the same rolling, weekly
and monthly percentages and reset instants the console dashboard draws. That
endpoint is the whole reason this card can exist again.

The card it replaces computed usage locally from OpenCode's own SQLite cost
records against one flat monthly cap, and was removed in 0.6.0 because it
under-read: Go gives each model its own quota, and the console reports the
percentage of the model that is furthest along, which local records cannot
reproduce. The API has no such gap, and it also counts usage made from other
devices, which the local reading could not see at all.

A Go subscription is a flat monthly fee, and the API publishes percentages
rather than dollars, so this card carries no credits row.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from normalise import (
    ProviderError,
    epoch_from_iso,
    percent,
    request_json,
    severity,
)


USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
KEY_VARIABLE = "OPENCODE_API_KEY"
DEFAULT_AUTH = Path.home() / ".local" / "share" / "ai-usage-desklet" / "opencode.env"
SIGNIN_HINT = "Add your OpenCode Go key to ~/.local/share/ai-usage-desklet/opencode.env"

# The window names are the API's own keys, in the order the console lists them.
WINDOWS = (
    ("rolling", "5 hourly"),
    ("weekly", "Weekly"),
    ("monthly", "Monthly"),
)

# Identifies this client, as the Go endpoint asks. It is not an inference call,
# but naming ourselves costs nothing and keeps the traffic legible to them.
CLIENT_NAME = "ai-usage-desklet/0.7.0"


def _parse_key_file(text: str) -> str | None:
    """Return the OPENCODE_API_KEY value from KEY=VALUE text, or None."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, _, value = stripped.partition("=")
        if name.strip() != KEY_VARIABLE:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value or None
    return None


def _read_auth(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        # Only the exception type is reported, so no path fragment can leak.
        raise ProviderError(
            f"credentials unavailable ({type(exc).__name__})", "no auth"
        ) from None
    key = _parse_key_file(text)
    if not key:
        raise ProviderError("credentials missing api key", "no auth")
    return key


def _request(key: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": CLIENT_NAME,
        "x-opencode-session": CLIENT_NAME,
    }
    payload = request_json(USAGE_URL, headers, SIGNIN_HINT)
    # None is only reachable through none_on_status, which this call does not
    # use, but saying so keeps the return type honest.
    if payload is None:
        raise ProviderError("usage endpoint returned no document", "bad response")
    return payload


def _bar(window_id: str, label: str, window: Any) -> dict[str, Any] | None:
    if not isinstance(window, dict):
        return None
    pct = percent(window.get("percent"))
    # A window the provider has already capped keeps reporting its last
    # percentage, which can be floored a point or two short of spent, so a
    # status other than "ok" is drawn as spent rather than trusted as a number.
    spent = isinstance(window.get("status"), str) and window["status"] != "ok"
    return {
        "id": window_id,
        "label": label,
        "percent": pct,
        "severity": "critical" if spent else severity(None, pct),
        "resets_at": epoch_from_iso(window.get("resetsAt")),
        "active": True,
    }


def fetch(auth_path: Path = DEFAULT_AUTH) -> dict[str, Any]:
    key = _read_auth(auth_path)
    payload = _request(key)
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise ProviderError("usage response contained no windows", "bad response")

    bars = []
    for window_id, label in WINDOWS:
        bar = _bar(window_id, label, usage.get(window_id))
        if bar is not None:
            bars.append(bar)
    if not bars:
        raise ProviderError("usage response contained no windows", "bad response")

    # Go has no subscription endpoint (probed 6 Nov 2026: subscription,
    # subscriptions, billing and account all 404), but the monthly window's
    # reset instant is published and is the plan's monthly clock-over.
    monthly = usage.get("monthly") if isinstance(usage.get("monthly"), dict) else {}
    renews_at = epoch_from_iso(monthly.get("resetsAt"))

    return {
        "id": "opencode",
        "label": "OpenCode",
        "plan": "Go",
        "plan_source": "api",
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


def available(path: Path = DEFAULT_AUTH) -> bool:
    try:
        return bool(_parse_key_file(path.read_text(encoding="utf-8")))
    except Exception:
        return False
