"""Claude usage adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from normalise import (
    ProviderError,
    claude_limit_label,
    epoch_from_iso,
    format_money,
    percent,
    request_json,
    safe_id,
    severity,
)


USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
DEFAULT_CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
SIGNIN_HINT = "sign in to Claude"
PLAN_TTL_SECONDS = 3_600
PLAN_BY_RATE_LIMIT_TIER = {
    "default_claude_ai": "Pro",
    "default_claude_max_5x": "Max 5x",
    "default_claude_max_20x": "Max 20x",
}

# Treat a token that expires within this many seconds as already expired, so a
# request is never fired against a token that will be rejected mid-flight.
EXPIRY_SKEW_SECONDS = 60


def _read_oauth(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError(
            f"credentials unavailable ({type(exc).__name__})", "no auth"
        ) from None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    if not isinstance(oauth, dict) or not isinstance(oauth.get("accessToken"), str):
        raise ProviderError("credentials missing access token", "no auth")
    return oauth


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
        raise ProviderError(
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


def _plan_from_credentials(oauth: dict[str, Any]) -> str | None:
    """Read the plan label out of the local sign-in file.

    Only a fallback. Claude Code writes ``subscriptionType`` when the user signs
    in and does not rewrite it on a silent token refresh, so it keeps whatever
    plan was in force at sign-in time and can lag a plan change indefinitely.
    Verified on 2026-08-11: the file said "pro" while the account had been on Max
    since July.
    """
    raw = oauth.get("subscriptionType")
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = raw.strip().lower().removeprefix("claude_").replace("_", " ")
    return cleaned.title()


def _plan_from_profile(headers: dict[str, str]) -> str | None:
    """Read the plan label from the account itself. Returns None if unreadable.

    ``rate_limit_tier`` is tried first because it is the only field that
    separates the Max tiers from each other.
    """
    payload = request_json(PROFILE_URL, headers, SIGNIN_HINT)
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    organization = (
        payload.get("organization")
        if isinstance(payload.get("organization"), dict)
        else {}
    )

    tier = organization.get("rate_limit_tier")
    if isinstance(tier, str):
        mapped = PLAN_BY_RATE_LIMIT_TIER.get(tier.strip().lower())
        if mapped:
            return mapped
        # Ultra (added late Oct 2026) has no published tier string yet. Checked
        # before has_claude_max so an Ultra account is never labelled Max.
        if "ultra" in tier.lower():
            return "Ultra"

    if account.get("has_claude_max"):
        return "Max"

    org_type = organization.get("organization_type")
    if isinstance(org_type, str) and org_type.strip():
        return org_type.strip().lower().removeprefix("claude_").replace("_", " ").title()

    if account.get("has_claude_pro"):
        return "Pro"

    return None


def _resolve_plan(
    oauth: dict[str, Any],
    headers: dict[str, str],
    cached_plan: tuple[str, int, str] | None,
) -> tuple[str, int, str]:
    """Resolve the plan label, preferring the account over the local file.

    Returns ``(label, fetched_at, source)``. ``source`` is ``profile`` whenever
    the value originated at the account, whether it was read this tick or cached
    within the TTL, and ``credentials`` when it came from the local sign-in file
    and may therefore be out of date. The desklet uses that distinction to decide
    whether to warn about the label.

    The profile endpoint is consulted at most once per TTL, so at the 120 s Claude
    interval this costs one extra request per thirty ticks. A profile failure
    never fails the provider: the usage numbers do not depend on it.
    """
    now = int(time.time())

    if cached_plan is not None:
        cached_label, cached_at, cached_source = cached_plan
        if (
            cached_label
            and cached_source == "profile"
            and 0 <= now - cached_at < PLAN_TTL_SECONDS
        ):
            return cached_label, cached_at, cached_source

    try:
        plan = _plan_from_profile(headers)
    except ProviderError:
        plan = None
    if plan:
        return plan, now, "profile"

    # A previously seen account value, even an old one, still beats the local
    # file, which can be months stale.
    if cached_plan is not None and cached_plan[0]:
        return cached_plan

    plan = _plan_from_credentials(oauth)
    if plan:
        return plan, now, "credentials"
    return "Unknown", now, "unknown"


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
        "label": claude_limit_label(kind, item.get("scope")),
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
    cached_plan: tuple[str, int, str] | None = None,
) -> tuple[dict[str, Any], int, str]:
    oauth = _read_oauth(credentials_path)
    _check_not_expired(oauth)
    headers = _headers(oauth["accessToken"])
    payload = request_json(USAGE_URL, headers, SIGNIN_HINT)
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
        raise ProviderError("usage response contained no valid limits", "bad response")

    plan, plan_fetched_at, plan_source = _resolve_plan(oauth, headers, cached_plan)

    return (
        {
            "id": "claude",
            "label": "Claude",
            "plan": plan,
            "plan_source": plan_source,
            "ok": True,
            "stale": False,
            "source": "api",
            "auth_state": "ok",
            "fetched_at": fetched_at,
            "error": None,
            "error_short": None,
            "bars": bars,
            "credits": _credits(payload),
        },
        plan_fetched_at,
        plan_source,
    )
