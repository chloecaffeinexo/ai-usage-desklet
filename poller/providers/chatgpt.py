"""ChatGPT subscription usage adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from normalise import epoch_from_iso, integer, percent, severity, window_label


USAGE_URLS = (
    "https://chatgpt.com/backend-api/codex/usage",
    "https://chatgpt.com/backend-api/wham/usage",
)
DEFAULT_AUTH = Path.home() / ".codex" / "auth.json"
REQUEST_TIMEOUT = (5, 15)
MAX_ATTEMPTS = 3
RETRYABLE_STATUS = {408, 500, 502, 503, 504}


class ChatGPTError(Exception):
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


def _read_auth(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChatGPTError(
            f"credentials unavailable ({type(exc).__name__})", "no auth"
        ) from None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        raise ChatGPTError("credentials missing tokens", "no auth")
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not isinstance(access_token, str) or not isinstance(account_id, str):
        raise ChatGPTError("credentials missing access token or account id", "no auth")
    return access_token, account_id


def _request_usage(access_token: str, account_id: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "User-Agent": "codex-cli",
        "originator": "codex_cli_rs",
        "Accept": "application/json",
    }
    for index, url in enumerate(USAGE_URLS):
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
            raise ChatGPTError(
                f"request failed ({type(last_exception).__name__})", "offline"
            ) from None
        if response is None:
            raise ChatGPTError("request failed (unavailable)", "offline")
        if response.status_code == 404 and index == 0:
            continue
        if response.status_code != 200:
            if response.status_code == 429:
                raw_retry = response.headers.get("Retry-After")
                try:
                    retry_after = max(0, int(float(raw_retry)))
                except (TypeError, ValueError, OverflowError):
                    retry_after = 300
                raise ChatGPTError(
                    "request failed (HTTP 429)",
                    "rate limited",
                    status_code=429,
                    retry_after=retry_after,
                )
            raise ChatGPTError(
                f"request failed (HTTP {response.status_code})",
                f"HTTP {response.status_code}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            raise ChatGPTError("request returned invalid JSON", "bad response") from None
        if not isinstance(payload, dict):
            raise ChatGPTError("request returned an unexpected shape", "bad response")
        return payload
    raise ChatGPTError("usage endpoint unavailable", "bad response")


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
        "ok": True,
        "stale": True,
        "source": "local",
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


def _credits(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("credits")
    credits = raw if isinstance(raw, dict) else {}
    unlimited = bool(credits.get("unlimited"))
    reached = bool(credits.get("overage_limit_reached"))
    balance = credits.get("balance")
    balance_text = str(balance) if balance is not None else "0"
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
    except ChatGPTError as exc:
        fallback = _local_fallback(sessions_dir or (Path.home() / ".codex" / "sessions"))
        if fallback is not None:
            fallback["error"] = f"live API unavailable ({exc}); using local snapshot"
            fallback["_poll_status_code"] = exc.status_code
            fallback["_poll_retry_after"] = exc.retry_after
            return fallback
        raise
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        raise ChatGPTError("usage response missing rate limit", "bad response")

    bars = []
    primary = _window(rate_limit.get("primary_window"), "primary")
    secondary = _window(rate_limit.get("secondary_window"), "secondary")
    if primary is not None:
        bars.append(primary)
    if secondary is not None:
        bars.append(secondary)
    if not bars:
        raise ChatGPTError("usage response contained no valid limits", "bad response")

    raw_plan = payload.get("plan_type")
    plan = raw_plan.replace("_", " ").title() if isinstance(raw_plan, str) else "Unknown"
    return {
        "id": "chatgpt",
        "label": "ChatGPT",
        "plan": plan,
        "ok": True,
        "stale": False,
        "source": "api",
        "fetched_at": int(time.time()),
        "error": None,
        "error_short": None,
        "bars": bars,
        "credits": _credits(payload),
    }
