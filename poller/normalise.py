"""Shared normalisation helpers for the AI Usage Desklet poller."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


VALID_SEVERITIES = {"normal", "warning", "critical"}


def number(value: Any, default: float | None = None) -> float | None:
    """Return a finite float without allowing booleans through as numbers."""
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def integer(value: Any, default: int | None = None) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else default


def percent(value: Any) -> float:
    parsed = number(value, 0.0)
    assert parsed is not None
    parsed = min(100.0, max(0.0, parsed))
    return int(parsed) if parsed.is_integer() else round(parsed, 1)


def severity(value: Any, percentage: Any) -> str:
    if isinstance(value, str) and value.lower() in VALID_SEVERITIES:
        return value.lower()
    parsed = number(percentage, 0.0)
    assert parsed is not None
    if parsed >= 90:
        return "critical"
    if parsed >= 75:
        return "warning"
    return "normal"


def epoch_from_iso(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return round(parsed.timestamp())


def safe_id(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned or fallback


def claude_limit_label(kind: Any) -> str:
    if not isinstance(kind, str):
        return "Limit"
    lowered = kind.lower()
    if lowered == "session":
        return "Session"
    if lowered == "weekly_all":
        return "Weekly"
    if "opus" in lowered:
        return "Weekly (Opus)"
    return lowered.replace("_", " ").title()


def window_label(seconds: Any) -> str:
    duration = integer(seconds, 0) or 0
    if duration == 604_800:
        return "Weekly"
    if duration == 18_000:
        return "5 hourly"
    if duration > 0 and duration % 86_400 == 0:
        days = duration // 86_400
        return f"{days} day" if days == 1 else f"{days} days"
    if duration > 0 and duration % 3_600 == 0:
        hours = duration // 3_600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if duration > 0 and duration % 60 == 0:
        minutes = duration // 60
        return f"{minutes} min"
    return "Limit"


def format_money(amount_minor: Any, currency: Any, exponent: Any) -> str:
    code = currency.upper() if isinstance(currency, str) and currency else ""
    exp = integer(exponent, 2)
    if exp is None or not 0 <= exp <= 6:
        exp = 2
    try:
        amount = Decimal(str(amount_minor)) / (Decimal(10) ** exp)
        amount = amount.quantize(Decimal(1).scaleb(-exp))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal(0).quantize(Decimal(1).scaleb(-exp))

    symbols = {
        "AUD": "$",
        "CAD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "NZD": "$",
        "USD": "$",
    }
    symbol = symbols.get(code)
    rendered = f"{amount:,.{exp}f}"
    return f"{symbol}{rendered}" if symbol else f"{code} {rendered}".strip()


def provider_error(
    provider_id: str,
    label: str,
    message: str,
    error_short: str = "bad response",
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "label": label,
        "plan": "Unknown",
        "ok": False,
        "stale": True,
        "source": "api",
        "fetched_at": None,
        "error": message[:240],
        "error_short": error_short[:16],
        "bars": [],
        "credits": None,
    }


def state_document(providers: list[dict[str, Any]], generated_at: int) -> dict[str, Any]:
    return {
        "schema": 1,
        "generated_at": generated_at,
        "providers": providers,
    }
