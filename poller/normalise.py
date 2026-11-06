"""Shared normalisation and request helpers for the AI Usage Desklet poller."""

from __future__ import annotations

import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests


VALID_SEVERITIES = {"normal", "warning", "critical"}
REQUEST_TIMEOUT = (5, 15)
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {408, 500, 502, 503, 504}


class ProviderError(Exception):
    """A deliberately sanitised provider error."""

    def __init__(
        self,
        message: str,
        error_short: str = "bad response",
        *,
        status_code: int | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.error_short = error_short[:16]
        self.status_code = status_code
        self.retry_after = retry_after


def request_json(
    url: str,
    headers: dict[str, str],
    signin_hint: str = "sign in again",
    none_on_status: frozenset[int] = frozenset(),
) -> dict[str, Any] | None:
    """GET a JSON document with retries and sanitised errors.

    A status listed in ``none_on_status`` returns None instead of raising, so the
    caller can fall through to an alternative URL.
    """
    response = None
    last_exception: requests.RequestException | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            last_exception = None
        except requests.RequestException as exc:
            last_exception = exc
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(0.25 * (2**attempt))
                continue
            break
        if response.status_code not in RETRYABLE_STATUS or attempt + 1 == MAX_ATTEMPTS:
            break
        time.sleep(0.25 * (2**attempt))

    if last_exception is not None:
        raise ProviderError(
            f"request failed ({type(last_exception).__name__})", "offline"
        ) from None
    if response is None:
        raise ProviderError("request failed (unavailable)", "offline")
    status = response.status_code
    if status in none_on_status:
        return None
    if status == 429:
        try:
            retry_after = max(0, int(float(response.headers.get("Retry-After"))))
        except (TypeError, ValueError, OverflowError):
            retry_after = 300
        raise ProviderError(
            "request failed (HTTP 429)",
            "rate limited",
            status_code=429,
            retry_after=retry_after,
        )
    if status in (401, 403):
        raise ProviderError(
            f"authentication rejected (HTTP {status}); {signin_hint}",
            "token expired",
            status_code=status,
        )
    if status != 200:
        raise ProviderError(
            f"request failed (HTTP {status})", f"HTTP {status}", status_code=status
        )
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        raise ProviderError("request returned invalid JSON", "bad response") from None
    if not isinstance(payload, dict):
        raise ProviderError("request returned an unexpected shape", "bad response")
    return payload


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
    parsed = number(value, 0.0) or 0.0
    parsed = min(100.0, max(0.0, parsed))
    return int(parsed) if parsed.is_integer() else round(parsed, 1)


def severity(value: Any, percentage: Any) -> str:
    if isinstance(value, str) and value.lower() in VALID_SEVERITIES:
        return value.lower()
    parsed = number(percentage, 0.0) or 0.0
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


def claude_limit_label(kind: Any, scope: Any = None) -> str:
    if not isinstance(kind, str):
        return "Limit"
    lowered = kind.lower()
    if lowered == "session":
        return "Session"
    if lowered == "weekly_all":
        return "Weekly"
    # Scoped limits carry the model they apply to; use the API's own name so a
    # new model labels itself without a code change.
    model = scope.get("model") if isinstance(scope, dict) else None
    name = model.get("display_name") if isinstance(model, dict) else None
    if lowered.startswith("weekly") and isinstance(name, str) and name.strip():
        return f"Weekly ({name.strip()})"
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
        "plan_source": "unknown",
        "ok": False,
        "stale": True,
        "source": "api",
        "auth_state": "ok",
        "fetched_at": None,
        "next_attempt_at": None,
        "error": message[:240],
        "error_short": error_short[:16],
        "bars": [],
        "credits": None,
        "renews_at": None,
        "renews_period": None,
    }


def state_document(providers: list[dict[str, Any]], generated_at: int) -> dict[str, Any]:
    return {
        "schema": 1,
        "generated_at": generated_at,
        "providers": providers,
    }
