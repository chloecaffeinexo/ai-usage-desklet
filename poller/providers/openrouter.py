"""OpenRouter credits adapter."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from normalise import (
    ProviderError,
    number,
    percent,
    request_json,
    severity,
)


CREDITS_URL = "https://openrouter.ai/api/v1/credits"
KEY_URL = "https://openrouter.ai/api/v1/key"
KEY_VARIABLE = "OPENROUTER_API_KEY"
DEFAULT_AUTH = Path.home() / ".local" / "share" / "ai-usage-desklet" / "openrouter.env"
SIGNIN_HINT = "Add your OpenRouter key to ~/.local/share/ai-usage-desklet/openrouter.env"


def _parse_key_file(text: str) -> str | None:
    """Return the OPENROUTER_API_KEY value from KEY=VALUE text, or None."""
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


def _request(url: str, key: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    return request_json(url, headers, SIGNIN_HINT)


def _bar(total_usage: float, total_credits: float) -> dict[str, Any]:
    pct = percent(total_usage / total_credits * 100)
    return {
        "id": "credits",
        "label": "Credits",
        "percent": pct,
        "severity": severity(None, pct),
        "resets_at": None,
        "active": True,
    }


def _credits(
    total_credits: float, total_usage: float, key_usage: float | None
) -> dict[str, Any]:
    balance_text = f"{total_credits - total_usage:,.2f}"
    return {
        "kind": "balance",
        "enabled": True,
        "balance_text": balance_text,
        "unlimited": False,
        "severity": "normal",
        "note": None if key_usage is None else f"this key: {key_usage:,.2f}",
        "short_text": balance_text,
    }


def fetch(auth_path: Path = DEFAULT_AUTH) -> dict[str, Any]:
    key = _read_auth(auth_path)
    credits_payload = _request(CREDITS_URL, key)
    # A failed key call is not fatal: it only annotates the note below.
    try:
        key_payload = _request(KEY_URL, key)
    except ProviderError:
        key_payload = None

    # `limit` and `limit_remaining` cap one key's share of a shared account
    # balance, so they must never reach a bar's percent; only this key's
    # `usage` is reported, and only as a note.
    key_usage = None
    if key_payload is not None:
        data = key_payload.get("data")
        if isinstance(data, dict):
            key_usage = number(data.get("usage"))

    data = credits_payload.get("data")
    if not isinstance(data, dict):
        raise ProviderError("usage response contained no valid limits", "bad response")
    total_credits = number(data.get("total_credits"))
    total_usage = number(data.get("total_usage"), 0.0)
    if not total_credits:
        raise ProviderError("usage response contained no valid limits", "bad response")

    return {
        "id": "openrouter",
        "label": "OpenRouter",
        "plan": "Credits",
        "plan_source": "unknown",
        "ok": True,
        "stale": False,
        "source": "api",
        "auth_state": "ok",
        "fetched_at": int(time.time()),
        "error": None,
        "error_short": None,
        "bars": [_bar(total_usage, total_credits)],
        "credits": _credits(total_credits, total_usage, key_usage),
    }


def available(path: Path = DEFAULT_AUTH) -> bool:
    try:
        return bool(_parse_key_file(path.read_text(encoding="utf-8")))
    except Exception:
        return False
