"""Claude usage adapter."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from normalise import (
    claude_limit_label,
    epoch_from_iso,
    format_money,
    percent,
    safe_id,
    severity,
)


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
DEFAULT_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
REQUEST_TIMEOUT = (5, 15)
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {408, 500, 502, 503, 504}


class ClaudeError(Exception):
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


def _read_oauth(path: Path) -> dict[str, Any]:
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClaudeError(
            f"credentials unavailable ({type(exc).__name__})", "no auth"
        ) from None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict) or not isinstance(oauth.get("accessToken"), str):
        raise ClaudeError("credentials missing access token", "no auth")
    return oauth


# Treat a token that expires within this many seconds as already expired, so a
# request is never fired against a token that will be rejected mid-flight.
EXPIRY_SKEW_SECONDS = 60


def _check_not_expired(oauth: dict[str, Any]) -> None:
    """Fail early with an actionable message if the stored token has expired.

    The poller never refreshes tokens (that is the owning app's job); it only
    reads them. ``expiresAt`` is milliseconds since the epoch. When it is missing
    or unparseable we say nothing and let the request proceed, so older
    credential formats keep working.
    """
    raw = oauth.get("expiresAt")
    try:
        expires_at = float(raw) / 1000.0
    except (TypeError, ValueError):
        return
    if time.time() >= expires_at - EXPIRY_SKEW_SECONDS:
        raise ClaudeError(
            "access token expired; sign in to Claude to refresh it",
            "token expired",
            status_code=401,
        )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-cli/2.0.0 (external, cli)",
        "Accept": "application/json",
    }


def _request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
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
        raise ClaudeError(
            f"request failed ({type(last_exception).__name__})", "offline"
        ) from None
    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "unavailable"
        if response is not None and response.status_code == 429:
            raw_retry = response.headers.get("Retry-After")
            try:
                retry_after = max(0, int(float(raw_retry)))
            except (TypeError, ValueError, OverflowError):
                retry_after = 300
            raise ClaudeError(
                "request failed (HTTP 429)",
                "rate limited",
                status_code=429,
                retry_after=retry_after,
            )
        if status in (401, 403):
            raise ClaudeError(
                f"authentication rejected (HTTP {status}); sign in to Claude",
                "token expired",
                status_code=status,
            )
        short = f"HTTP {status}" if isinstance(status, int) else "offline"
        raise ClaudeError(
            f"request failed (HTTP {status})",
            short,
            status_code=status if isinstance(status, int) else None,
        )
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        raise ClaudeError("request returned invalid JSON", "bad response") from None
    if not isinstance(payload, dict):
        raise ClaudeError("request returned an unexpected shape", "bad response")
    return payload


def _plan_from_credentials(oauth: dict[str, Any]) -> str | None:
    raw = oauth.get("subscriptionType")
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = raw.strip().lower().removeprefix("claude_").replace("_", " ")
    return cleaned.title()


def _plan_from_profile(headers: dict[str, str]) -> str:
    payload = _request_json(PROFILE_URL, headers)
    account = payload.get("account")
    organization = payload.get("organization")
    plan = "Pro" if isinstance(account, dict) and account.get("has_claude_pro") else None
    if plan is None and isinstance(organization, dict):
        raw = organization.get("organization_type")
        if isinstance(raw, str) and raw:
            plan = raw.lower().removeprefix("claude_").replace("_", " ").title()
    return plan or "Unknown"


def _resolve_plan(
    oauth: dict[str, Any],
    headers: dict[str, str],
    cached_plan: tuple[str, int] | None,
) -> tuple[str, int]:
    now = int(time.time())
    plan = _plan_from_credentials(oauth)
    if plan is not None:
        return plan, now

    if cached_plan is not None:
        cached_label, cached_at = cached_plan
        if cached_label and now - cached_at < 3_600:
            return cached_label, cached_at

    try:
        return _plan_from_profile(headers), now
    except ClaudeError:
        # Cache the fallback decision for an hour too, so a profile outage cannot
        # turn every 30-second oneshot into an extra failing HTTP request.
        return (cached_plan[0] if cached_plan else "Unknown"), now


def _limit_bar(item: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("kind")
    reset = epoch_from_iso(item.get("resets_at"))
    if reset is None:
        return None
    pct = percent(item.get("percent"))
    bar_id = "weekly" if kind == "weekly_all" else safe_id(kind, f"limit_{index}")
    return {
        "id": bar_id,
        "label": claude_limit_label(kind),
        "percent": pct,
        "severity": severity(item.get("severity"), pct),
        "resets_at": reset,
        "active": bool(item.get("is_active", True)),
    }


def _fallback_bars(payload: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = (
        ("five_hour", "session", "Session"),
        ("seven_day", "weekly", "Weekly"),
    )
    bars: list[dict[str, Any]] = []
    for key, bar_id, label in definitions:
        item = payload.get(key)
        if not isinstance(item, dict):
            continue
        reset = epoch_from_iso(item.get("resets_at"))
        if reset is None:
            continue
        pct = percent(item.get("utilization"))
        bars.append(
            {
                "id": bar_id,
                "label": label,
                "percent": pct,
                "severity": severity(None, pct),
                "resets_at": reset,
                "active": True,
            }
        )
    return bars


def _credits(payload: dict[str, Any]) -> dict[str, Any] | None:
    spend = payload.get("spend")
    if not isinstance(spend, dict):
        return None
    used = spend.get("used")
    limit = spend.get("limit")
    if not isinstance(used, dict) or not isinstance(limit, dict):
        return None

    currency = used.get("currency") or limit.get("currency")
    pct = percent(spend.get("percent"))
    enabled = bool(spend.get("enabled"))
    used_text = format_money(
        used.get("amount_minor"), used.get("currency"), used.get("exponent")
    )
    return {
        "kind": "money",
        "enabled": enabled,
        "percent": pct,
        "used_text": used_text,
        "limit_text": format_money(
            limit.get("amount_minor"), limit.get("currency"), limit.get("exponent")
        ),
        "currency": currency if isinstance(currency, str) else None,
        "severity": severity(spend.get("severity"), pct),
        "note": None if enabled else "disabled",
        "short_text": used_text if enabled else "off",
    }


def fetch(
    credentials_path: Path = DEFAULT_CREDENTIALS,
    cached_plan: tuple[str, int] | None = None,
) -> tuple[dict[str, Any], int]:
    oauth = _read_oauth(credentials_path)
    _check_not_expired(oauth)
    headers = _headers(oauth["accessToken"])
    payload = _request_json(USAGE_URL, headers)
    fetched_at = int(time.time())

    raw_limits = payload.get("limits")
    bars = []
    if isinstance(raw_limits, list) and raw_limits:
        bars = [
            bar
            for index, item in enumerate(raw_limits)
            if (bar := _limit_bar(item, index)) is not None
        ]
    if not bars:
        bars = _fallback_bars(payload)
    if not bars:
        raise ClaudeError("usage response contained no valid limits", "bad response")

    plan, plan_fetched_at = _resolve_plan(oauth, headers, cached_plan)

    return (
        {
            "id": "claude",
            "label": "Claude",
            "plan": plan,
            "ok": True,
            "stale": False,
            "source": "api",
            "fetched_at": fetched_at,
            "error": None,
            "error_short": None,
            "bars": bars,
            "credits": _credits(payload),
        },
        plan_fetched_at,
    )
