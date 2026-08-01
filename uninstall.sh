#!/usr/bin/env bash
# Remove the AI Usage Desklet, poller, systemd units, and cache.
set -euo pipefail

UUID="ai-usage@desklet"
DESKLET_DST="$HOME/.local/share/cinnamon/desklets/$UUID"
POLLER_DST="$HOME/.local/share/ai-usage-desklet"
SYSTEMD_DST="$HOME/.config/systemd/user"
CACHE_DST="$HOME/.cache/ai-usage-desklet"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    say "Stopping and disabling the timers"
    systemctl --user disable --now ai-usage-poller.timer 2>/dev/null || true
    systemctl --user disable --now ai-usage-token-refresh.timer 2>/dev/null || true
    rm -f "$SYSTEMD_DST/ai-usage-poller.service" "$SYSTEMD_DST/ai-usage-poller.timer" \
          "$SYSTEMD_DST/ai-usage-token-refresh.service" "$SYSTEMD_DST/ai-usage-token-refresh.timer"
    systemctl --user daemon-reload 2>/dev/null || true
fi

say "Removing installed files"
rm -rf "$DESKLET_DST" "$POLLER_DST" "$CACHE_DST"

cat <<EOF

Done.

If the desklet is still on your desktop, right-click it -> Remove.
EOF
