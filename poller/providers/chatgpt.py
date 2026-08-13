"""ChatGPT subscription usage adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from normalise import (
    ProviderError,
    epoch_from_iso,
    integer,
    number,
    percent,
    request_json,
    severity,
    window_label,
)


USAGE_URLS = (
    "https://chatgpt.com/backend-api/codex/usage",
    "https://chatgpt.com/backend-api/wham/usage",
)
DEFAULT_AUTH = Path.home() / ".codex" / "auth.json"
SIGNIN_HINT = "run codex login"


def _read_auth(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderError(
            f"credentials unavailable ({type(exc).__name__})", "no auth"
        ) from None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        raise ProviderError("credentials missing tokens", "no auth")
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not isinstance(access_token, str) or not isinstance(account_id, str):
        raise ProviderError("credentials missing access token or account id", "no auth")
    return access_token, account_id


def _request_usage(access_token: str, account_id: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "User-Agent": "codex-cli",
        "originator": "codex_cli_rs",
        "Accept": "application/json",
    }
    # The primary endpoint may 404 on some accounts; fall through to the spare.
    last = len(USAGE_URLS) - 1
    for index, url in enumerate(USAGE_URLS):
        payload = request_json(
            url,
            headers,
            SIGNIN_HINT,
            none_on_status=frozenset() if index == last else frozenset({404}),
        )
        if payload is not None:
            return payload
    raise ProviderError("usage endpoint unavailable", "bad response")


def _reverse_lines(path: Path):
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        remainder = b""
        while position:
            chunk_size = min(65_536, position)
            position -= chunk_size
            handle.seek(position)
            block = handle.read(chunk_size) + remainder
            lines = block.split(b"\n")
            remainder = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if remainder:
            yield remainder.decode("utf-8", errors="replace")


def _newest_local_rate_limits(
    sessions_dir: Path,
) -> tuple[dict[str, Any], int] | None:
    try:
        files = sorted(
            sessions_dir.glob("**/rollout-*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None
    for path in files:
        try:
            lines = _reverse_lines(path)
            for line in lines:
                if '"rate_limits"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                payload = event.get("payload") if isinstance(event, dict) else None
                limits = payload.get("rate_limits") if isinstance(payload, dict) else None
                timestamp = epoch_from_iso(event.get("timestamp")) if isinstance(event, dict) else None
                if isinstance(limits, dict) and timestamp is not None:
                    return limits, timestamp
        except OSError:
            continue
    return None


def _local_window(item: Any, bar_id: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    reset = integer(item.get("resets_at"))
    if reset is None:
        return None
    pct = percent(item.get("used_percent"))
    minutes = integer(item.get("window_minutes"), 0) or 0
    return {
        "id": bar_id,
        "label": window_label(minutes * 60),
        "percent": pct,
        "severity": severity(None, pct),
        "resets_at": reset,
        "active": True,
    }


def _local_fallback(sessions_dir: Path) -> dict[str, Any] | None:
    found = _newest_local_rate_limits(sessions_dir)
    if found is None:
        return None
    limits, timestamp = found
    bars = []
    primary = _local_window(limits.get("primary"), "primary")
    secondary = _local_window(limits.get("secondary"), "secondary")
    if primary is not None:
        bars.append(primary)
    if secondary is not None:
        bars.append(secondary)
    if not bars:
        return None

    raw_plan = limits.get("plan_type")
    plan = raw_plan.replace("_", " ").title() if isinstance(raw_plan, str) else "Unknown"
    return {
        "id": "chatgpt",
        "label": "ChatGPT",
        "plan": plan,
        "plan_source": "local",
        "ok": True,
        "stale": True,
        "source": "local",
        "auth_state": "ok",
        "fetched_at": timestamp,
        "error": "live API unavailable; using local snapshot",
        "error_short": "stale local",
        "bars": bars,
        "credits": _credits({"credits": limits.get("credits")}),
    }


def _window(item: Any, bar_id: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    reset = integer(item.get("reset_at"))
    if reset is None:
        return None
    pct = percent(item.get("used_percent"))
    return {
        "id": bar_id,
        "label": window_label(item.get("limit_window_seconds")),
        "percent": pct,
        "severity": severity(None, pct),
        "resets_at": reset,
        "active": True,
    }


def _balance_text(balance: Any) -> str:
    """Render a credit balance readably: the API sends up to ten decimals."""
    parsed = number(balance)
    if parsed is not None:
        return f"{parsed:,.2f}"
    return str(balance) if balance is not None else "0"


def _credits(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("credits")
    credits = raw if isinstance(raw, dict) else {}
    unlimited = bool(credits.get("unlimited"))
    reached = bool(credits.get("overage_limit_reached"))
    balance_text = _balance_text(credits.get("balance"))
    return {
        "kind": "balance",
        "enabled": bool(credits.get("has_credits")) or unlimited,
        "balance_text": balance_text,
        "unlimited": unlimited,
        "severity": "critical" if reached else "normal",
        "note": "limit reached" if reached else None,
        "short_text": "unlimited" if unlimited else balance_text,
    }


def fetch(
    auth_path: Path = DEFAULT_AUTH,
    sessions_dir: Path | None = None,
) -> dict[str, Any]:
    access_token, account_id = _read_auth(auth_path)
    try:
        payload = _request_usage(access_token, account_id)
    except ProviderError as exc:
        fallback = _local_fallback(sessions_dir or (Path.home() / ".codex" / "sessions"))
        if fallback is not None:
            fallback["error"] = f"live API unavailable ({exc}); using local snapshot"
            fallback["_poll_status_code"] = exc.status_code
            fallback["_poll_retry_after"] = exc.retry_after
            return fallback
        raise
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        raise ProviderError("usage response missing rate limit", "bad response")

    bars = []
    primary = _window(rate_limit.get("primary_window"), "primary")
    secondary = _window(rate_limit.get("secondary_window"), "secondary")
    if primary is not None:
        bars.append(primary)
    if secondary is not None:
        bars.append(secondary)
    if not bars:
        raise ProviderError("usage response contained no valid limits", "bad response")

    raw_plan = payload.get("plan_type")
    plan = raw_plan.replace("_", " ").title() if isinstance(raw_plan, str) else "Unknown"
    return {
        "id": "chatgpt",
        "label": "ChatGPT",
        "plan": plan,
        "plan_source": "api",
        "ok": True,
        "stale": False,
        "source": "api",
        "auth_state": "ok",
        "fetched_at": int(time.time()),
        "error": None,
        "error_short": None,
        "bars": bars,
        "credits": _credits(payload),
    }
