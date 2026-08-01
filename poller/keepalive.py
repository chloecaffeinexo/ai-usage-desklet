#!/usr/bin/env python3
"""Keep the Claude Code OAuth token fresh so the desklet's Claude card stays live.

Why this exists
---------------
The poller reads ``~/.claude/.credentials.json`` and, by deliberate design, never
writes or refreshes it. Claude access tokens are short-lived (roughly 8 hours) and
are refreshed by the Claude Code CLI itself. If the CLI is not run for a while, the
token lapses, the poller starts getting HTTP 401s, and the Claude card shows
"token expired" until the next time the CLI happens to run.

This helper closes that gap without ever touching the credentials file itself. When
the stored token is expired or about to expire, it asks the **official Claude CLI**
to make an authenticated call, which triggers the CLI's own, supported token
refresh through its own credential store. The CLI stays the single authoritative
writer of the credentials, so there is no second-writer / refresh-token-rotation
race. This script only reads ``expiresAt`` to decide whether a nudge is due.

It is a safe no-op when the CLI is not installed or there are no credentials, so it
does nothing harmful on setups that differ from the author's.

Run it on a short systemd user timer (see systemd/ai-usage-token-refresh.timer).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")

# Nudge the CLI once the token is within this many seconds of expiry (or already
# expired). Kept comfortably below the ~8h token lifetime so a short timer always
# lands at least one run inside the window before the token actually lapses.
REFRESH_BUFFER_SECONDS = 15 * 60

# The CLI can make a network call; give it room but never hang the timer.
CLI_TIMEOUT_SECONDS = 60


def _find_claude() -> str | None:
    """Locate the Claude CLI, falling back to its usual per-user install path."""
    found = shutil.which("claude")
    if found:
        return found
    fallback = os.path.expanduser("~/.local/bin/claude")
    return fallback if os.path.exists(fallback) else None


def _token_due_for_refresh() -> bool:
    """True when the stored access token is expired or expiring soon.

    Returns False on any missing/unreadable/unknown credential shape, so we never
    act on setups this script does not understand.
    """
    try:
        with open(CREDENTIALS, encoding="utf-8") as handle:
            expires_at = json.load(handle)["claudeAiOauth"]["expiresAt"]
        return time.time() >= (float(expires_at) / 1000.0) - REFRESH_BUFFER_SECONDS
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main() -> int:
    claude = _find_claude()
    if claude is None:
        return 0  # desktop-app-only or no CLI: nothing we can do, and that's fine
    if not _token_due_for_refresh():
        return 0  # token still good for a while, or no credentials to refresh

    try:
        # An authenticated status check makes the CLI refresh an expired/expiring
        # token via its own supported path. Its output contains the account email
        # and org id, so it is discarded and never written to a log.
        subprocess.run(
            [claude, "auth", "status", "--json"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        pass  # a failed nudge just means the card stays stale until the next run

    return 0


if __name__ == "__main__":
    sys.exit(main())
