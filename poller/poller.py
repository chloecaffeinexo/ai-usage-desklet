#!/usr/bin/env python3
"""Fetch and normalise Claude and ChatGPT subscription usage."""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from normalise import ProviderError, provider_error, state_document
from providers import chatgpt, claude


CACHE_DIR = Path.home() / ".cache" / "ai-usage-desklet"
STATE_PATH = CACHE_DIR / "state.json"
LAST_GOOD_PATH = CACHE_DIR / "last-good.json"
LOG_PATH = CACHE_DIR / "poller.log"
BACKOFF_SECONDS = 300
IDLE_INTERVAL_SECONDS = 600
ACTIVITY_WINDOW_SECONDS = 600
MAX_THROTTLE_INTERVAL_SECONDS = 600
BASE_INTERVALS = {"claude": 120, "chatgpt": 30}


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_last_good() -> dict[str, Any]:
    payload = _load_json(LAST_GOOD_PATH)
    if payload.get("schema") != 1 or not isinstance(payload.get("providers"), dict):
        return {"schema": 1, "providers": {}, "runtime": {}}
    if not isinstance(payload.get("runtime"), dict):
        payload["runtime"] = {}
    return payload


def _safe_log(message: str) -> None:
    CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CACHE_DIR, 0o700)
    clean = message.replace("\n", " ").replace("\r", " ")[:500]
    line = f"{int(time.time())} {clean}\n"
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size + len(line) > 1_048_576:
            existing = LOG_PATH.read_bytes()[-524_288:]
            fd, temp_name = tempfile.mkstemp(prefix=".poller.log.", dir=CACHE_DIR)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(existing)
                    handle.write(line.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, LOG_PATH)
            except BaseException:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
                raise
        else:
            with LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line)
            os.chmod(LOG_PATH, 0o600)
    except OSError:
        pass


def _last_good_entry(cache: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    providers = cache.get("providers")
    entry = providers.get(provider_id) if isinstance(providers, dict) else None
    return entry if isinstance(entry, dict) else None


def _last_good_reading(cache: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    entry = _last_good_entry(cache, provider_id)
    reading = entry.get("data") if entry else None
    return _ensure_display_fields(copy.deepcopy(reading)) if isinstance(reading, dict) else None


def _ensure_display_fields(reading: dict[str, Any]) -> dict[str, Any]:
    reading.setdefault("error_short", None)
    reading.setdefault("plan_source", "unknown")
    reading.setdefault("auth_state", "ok")
    reading.setdefault("next_attempt_at", None)
    return reading


def _save_success(
    cache: dict[str, Any],
    reading: dict[str, Any],
    now: int,
    plan_fetched_at: int | None = None,
    plan_source: str | None = None,
) -> None:
    providers = cache.setdefault("providers", {})
    entry: dict[str, Any] = {"saved_at": now, "data": copy.deepcopy(reading)}
    if plan_fetched_at is not None:
        entry["plan_fetched_at"] = plan_fetched_at
    if plan_source is not None:
        entry["plan_source"] = plan_source
    providers[reading["id"]] = entry


def _cached_claude_plan(cache: dict[str, Any]) -> tuple[str, int, str] | None:
    """Return the cached plan label as ``(label, fetched_at, source)``."""
    entry = _last_good_entry(cache, "claude")
    reading = entry.get("data") if entry else None
    plan = reading.get("plan") if isinstance(reading, dict) else None
    fetched_at = entry.get("plan_fetched_at") if entry else None
    source = entry.get("plan_source") if entry else None
    if not isinstance(source, str) or not source:
        source = "credentials"
    if isinstance(plan, str) and isinstance(fetched_at, int):
        return plan, fetched_at, source
    return None


AUTH_ERROR_SHORTS = {"token expired", "no auth"}


def _auth_state(status_code: int | None, error_short: str) -> str:
    """Whether this failure means the user has to sign in again.

    A missing or unreadable credentials file counts, because the remedy is the
    same: run the tool in a terminal and sign in.
    """
    if status_code in (401, 403) or error_short in AUTH_ERROR_SHORTS:
        return "expired"
    return "ok"


def _failure_reading(
    cache: dict[str, Any],
    provider_id: str,
    label: str,
    message: str,
    error_short: str,
    status_code: int | None = None,
) -> dict[str, Any]:
    previous = _last_good_reading(cache, provider_id)
    if previous is None:
        previous = provider_error(provider_id, label, message, error_short)
    else:
        previous["ok"] = False
        previous["stale"] = True
        previous["error"] = message[:240]
        previous["error_short"] = error_short[:16]
    previous["auth_state"] = _auth_state(status_code, error_short)
    return previous


def _runtime_provider(cache: dict[str, Any], provider_id: str) -> dict[str, Any]:
    providers = cache.setdefault("runtime", {}).setdefault("providers", {})
    status = providers.get(provider_id)
    if not isinstance(status, dict):
        status = {}
        providers[provider_id] = status
    status.setdefault("failures", 0)
    status.setdefault("throttle_streak", 0)
    status.setdefault("effective_interval", BASE_INTERVALS[provider_id])
    status.setdefault("next_poll_at", 0)
    return status


def _scheduled_interval(provider_id: str, status: dict[str, Any], idle: bool) -> int:
    base = BASE_INTERVALS[provider_id]
    throttled = status.get("effective_interval")
    if not isinstance(throttled, int):
        throttled = base
    return max(IDLE_INTERVAL_SECONDS if idle else base, throttled)


def _record_success(
    status: dict[str, Any],
    provider_id: str,
    now: int,
    idle: bool,
) -> None:
    base = BASE_INTERVALS[provider_id]
    status["failures"] = 0
    status["throttle_streak"] = 0
    status["effective_interval"] = base
    status["next_poll_at"] = now + (IDLE_INTERVAL_SECONDS if idle else base)


def _record_failure(
    status: dict[str, Any],
    provider_id: str,
    now: int,
    idle: bool,
    backoff_enabled: bool,
) -> None:
    base = BASE_INTERVALS[provider_id]
    failures = status.get("failures")
    failures = failures + 1 if isinstance(failures, int) else 1
    status["failures"] = failures
    status["throttle_streak"] = 0
    status["effective_interval"] = base
    interval = IDLE_INTERVAL_SECONDS if idle else base
    if backoff_enabled and failures >= 3:
        interval = max(interval, BACKOFF_SECONDS)
    status["next_poll_at"] = now + interval


def _record_throttle(
    status: dict[str, Any],
    provider_id: str,
    now: int,
    retry_after: int | None,
) -> int:
    base = BASE_INTERVALS[provider_id]
    streak = status.get("throttle_streak")
    streak = streak + 1 if isinstance(streak, int) else 1
    status["throttle_streak"] = streak

    previous = status.get("effective_interval")
    previous = previous if isinstance(previous, int) and previous > 0 else base
    effective = (
        base
        if streak == 1
        else min(MAX_THROTTLE_INTERVAL_SECONDS, max(base, previous * 2))
    )
    status["effective_interval"] = effective

    delay = retry_after if isinstance(retry_after, int) and retry_after >= 0 else BACKOFF_SECONDS
    if streak >= 2:
        delay = max(delay, effective)
    status["next_poll_at"] = now + delay
    return delay


def _newest_mtime_under(root: Path) -> float | None:
    """Newest mtime under a directory, or the file's own mtime if given a file."""
    try:
        if root.is_file():
            return root.stat().st_mtime
    except OSError:
        return None
    newest: float | None = None
    try:
        for directory, _, files in os.walk(root):
            for name in files:
                try:
                    modified = (Path(directory) / name).stat().st_mtime
                except OSError:
                    continue
                newest = modified if newest is None else max(newest, modified)
    except OSError:
        return newest
    return newest


def _quota_activity_recent(roots: list[Path], now: int) -> bool:
    """Has either tool consumed quota recently?

    Judged by transcript writes, because every earlier signal failed. Process
    presence was tried twice and matched permanently running background
    processes. ``~/.claude/history.jsonl`` was tried next and only the CLI writes
    it, so the desktop app looked idle while in use (verified 2026-08-11: ten
    hours stale mid-session). Session transcript files are written by both the
    CLI and the desktop app on every exchange, and by nothing when idle.

    Measured cost of the two walks on this machine: about 10 ms total across 433
    files, once per 30 s tick.
    """
    timestamps = [
        mtime
        for mtime in (_newest_mtime_under(root) for root in roots)
        if mtime is not None
    ]
    return bool(timestamps) and now - max(timestamps) <= ACTIVITY_WINDOW_SECONDS


def _state_provider(state: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    providers = state.get("providers")
    if not isinstance(providers, list):
        return None
    for provider in providers:
        if isinstance(provider, dict) and provider.get("id") == provider_id:
            return _ensure_display_fields(copy.deepcopy(provider))
    return None


def _mark_age(
    reading: dict[str, Any],
    now: int,
    stale_after: int,
) -> dict[str, Any]:
    fetched_at = reading.get("fetched_at")
    if isinstance(fetched_at, int) and now - fetched_at > stale_after:
        reading["stale"] = True
    return reading


def _skipped_reading(
    current_state: dict[str, Any],
    cache: dict[str, Any],
    provider_id: str,
    label: str,
    now: int,
    stale_after: int,
) -> dict[str, Any]:
    reading = _state_provider(current_state, provider_id)
    if reading is None:
        reading = _last_good_reading(cache, provider_id)
    if reading is None:
        reading = provider_error(provider_id, label, "waiting for next poll", "waiting")
    return _mark_age(reading, now, stale_after)


def _poll_provider(
    cache: dict[str, Any],
    current_state: dict[str, Any],
    provider_id: str,
    label: str,
    fetcher: Callable[[], tuple[dict[str, Any], dict[str, Any]]],
    now: int,
    idle: bool,
    force: bool,
    backoff_enabled: bool,
) -> dict[str, Any]:
    """Poll one provider if it is due, applying the shared scheduling rules.

    ``fetcher`` returns ``(reading, save_extra)`` where ``save_extra`` holds any
    provider-specific keyword arguments for ``_save_success``.
    """
    status = _runtime_provider(cache, provider_id)
    interval = _scheduled_interval(provider_id, status, idle)
    next_poll = status.get("next_poll_at")
    if not force and isinstance(next_poll, int) and next_poll > now:
        return _skipped_reading(
            current_state, cache, provider_id, label, now, interval * 3
        )

    try:
        reading, save_extra = fetcher()
    except ProviderError as exc:
        message = str(exc)
        if exc.status_code == 429:
            delay = _record_throttle(status, provider_id, now, exc.retry_after)
            _safe_log(f"{provider_id}: rate limited; retry in {delay}s")
        else:
            _record_failure(status, provider_id, now, idle, backoff_enabled)
            _safe_log(f"{provider_id}: {message}")
        return _failure_reading(
            cache, provider_id, label, message, exc.error_short, exc.status_code
        )
    except Exception as exc:
        message = f"unexpected failure ({type(exc).__name__})"
        _record_failure(status, provider_id, now, idle, backoff_enabled)
        _safe_log(f"{provider_id}: {message}")
        return _failure_reading(cache, provider_id, label, message, "bad response")

    poll_status = reading.pop("_poll_status_code", None)
    poll_retry_after = reading.pop("_poll_retry_after", None)
    reading = _ensure_display_fields(reading)
    # A non-stale reading is a full success whatever its source; the ChatGPT
    # local-snapshot fallback marks itself stale.
    if not reading.get("stale"):
        _record_success(status, provider_id, now, idle)
        _save_success(cache, reading, now, **save_extra)
    elif poll_status == 429:
        delay = _record_throttle(status, provider_id, now, poll_retry_after)
        _safe_log(f"{provider_id}: rate limited; retry in {delay}s; local fallback used")
    else:
        _record_failure(status, provider_id, now, idle, backoff_enabled)
        _safe_log(f"{provider_id}: {reading.get('error') or 'local fallback used'}")
    return reading


def collect(
    claude_credentials: Path,
    chatgpt_auth: Path,
    codex_sessions: Path,
    claude_sessions: Path,
    *,
    force: bool,
    backoff_enabled: bool,
    idle_slowdown_enabled: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    now = int(time.time())
    cache = _load_last_good()
    current_state = _load_json(STATE_PATH)
    runtime = cache.setdefault("runtime", {})
    idle = (
        idle_slowdown_enabled
        and not _quota_activity_recent([codex_sessions, claude_sessions], now)
    )
    runtime["freshness_idle"] = idle
    runtime["freshness_checked_at"] = now

    def fetch_claude() -> tuple[dict[str, Any], dict[str, Any]]:
        reading, plan_fetched_at, plan_source = claude.fetch(
            claude_credentials, _cached_claude_plan(cache)
        )
        return reading, {
            "plan_fetched_at": plan_fetched_at,
            "plan_source": plan_source,
        }

    def fetch_chatgpt() -> tuple[dict[str, Any], dict[str, Any]]:
        return chatgpt.fetch(chatgpt_auth, codex_sessions), {}

    providers = [
        _poll_provider(
            cache,
            current_state,
            provider_id,
            label,
            fetcher,
            now,
            idle,
            force,
            backoff_enabled,
        )
        for provider_id, label, fetcher in (
            ("claude", "Claude", fetch_claude),
            ("chatgpt", "ChatGPT", fetch_chatgpt),
        )
    ]

    # Stamp the next scheduled attempt on every card, including the ones skipped
    # this tick, so the desklet can say when a stalled provider will be retried
    # instead of leaving the user to guess.
    for reading in providers:
        next_poll_at = _runtime_provider(cache, reading["id"]).get("next_poll_at")
        reading["next_attempt_at"] = (
            next_poll_at if isinstance(next_poll_at, int) and next_poll_at > 0 else None
        )

    return state_document(providers, now), cache


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    state, cache = collect(
        args.claude_credentials,
        args.chatgpt_auth,
        args.codex_sessions,
        args.claude_sessions,
        force=args.force,
        backoff_enabled=not args.no_backoff,
        idle_slowdown_enabled=not args.no_idle_slowdown,
    )
    _atomic_json_write(LAST_GOOD_PATH, cache)
    _atomic_json_write(STATE_PATH, state)
    if args.print_output:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="fetch once and exit (default)")
    mode.add_argument("--daemon", action="store_true", help="poll continuously")
    parser.add_argument("--print", dest="print_output", action="store_true", help="print JSON")
    parser.add_argument(
        "--interval", type=float, default=30.0, help="daemon polling interval in seconds"
    )
    parser.add_argument(
        "--claude-credentials",
        type=Path,
        default=claude.DEFAULT_CREDENTIALS,
        help="Claude credentials path",
    )
    parser.add_argument(
        "--chatgpt-auth",
        type=Path,
        default=chatgpt.DEFAULT_AUTH,
        help="ChatGPT auth path",
    )
    parser.add_argument(
        "--codex-sessions",
        type=Path,
        default=Path.home() / ".codex" / "sessions",
        help="Codex session directory used only as an API-failure fallback",
    )
    parser.add_argument(
        "--claude-sessions",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Claude session transcript directory used only for activity freshness",
    )
    parser.add_argument("--force", action="store_true", help="poll both providers now")
    parser.add_argument(
        "--no-backoff", action="store_true", help="disable five-minute failure backoff"
    )
    parser.add_argument(
        "--no-idle-slowdown",
        action="store_true",
        help="disable 600-second freshness-based idle slowdown",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.daemon:
        run_once(args)
        return 0

    interval = max(1.0, args.interval)
    try:
        while True:
            started = time.monotonic()
            run_once(args)
            time.sleep(max(0.0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
