#!/usr/bin/env bash
# Install the AI Usage Desklet: desklet, poller, and systemd user timer.
# Everything is installed under $HOME. No root required.
set -euo pipefail

UUID="ai-usage@desklet"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DESKLET_DST="$HOME/.local/share/cinnamon/desklets"
POLLER_DST="$HOME/.local/share/ai-usage-desklet"
SYSTEMD_DST="$HOME/.config/systemd/user"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }

# --- checks ---------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required but not found." >&2
    exit 1
fi

if ! python3 -c 'import requests' >/dev/null 2>&1; then
    warn "the Python 'requests' library is not installed."
    warn "install it first, e.g.  sudo dnf install python3-requests"
    warn "                   or   sudo apt install python3-requests"
    warn "                   or   pip install --user requests"
fi

case "${XDG_CURRENT_DESKTOP:-}" in
    *Cinnamon*|*cinnamon*) : ;;
    *) warn "this doesn't look like a Cinnamon session (XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-unset})."
       warn "the poller will still work, but the desklet only runs under Cinnamon." ;;
esac

# --- desklet --------------------------------------------------------------
say "Installing desklet to $DESKLET_DST/$UUID"
mkdir -p "$DESKLET_DST"
rm -rf "${DESKLET_DST:?}/$UUID"
cp -r "$SRC/desklet/$UUID" "$DESKLET_DST/"

# --- poller ---------------------------------------------------------------
say "Installing poller to $POLLER_DST"
mkdir -p "$POLLER_DST/providers"
cp "$SRC/poller/poller.py" "$SRC/poller/normalise.py" "$SRC/poller/keepalive.py" "$POLLER_DST/"
cp "$SRC/poller/providers/"*.py "$POLLER_DST/providers/"

# --- systemd timer --------------------------------------------------------
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    say "Installing and starting the systemd user timer"
    mkdir -p "$SYSTEMD_DST"
    cp "$SRC/systemd/ai-usage-poller.service" "$SRC/systemd/ai-usage-poller.timer" "$SYSTEMD_DST/"
    cp "$SRC/systemd/ai-usage-token-refresh.service" "$SRC/systemd/ai-usage-token-refresh.timer" "$SYSTEMD_DST/"
    systemctl --user daemon-reload
    systemctl --user enable --now ai-usage-poller.timer
    # Keeps the Claude token fresh via the official CLI so the card never sits on
    # a stale "token expired" reading. Harmless if the Claude CLI isn't installed.
    if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
        systemctl --user enable --now ai-usage-token-refresh.timer
    else
        warn "the Claude CLI was not found; skipping the token keep-alive timer."
        warn "the Claude card will still work while your token is valid, but will"
        warn "show 'token expired' once it lapses until you next run the Claude app."
    fi
else
    warn "systemd user services are unavailable; skipping the timer."
    warn "run the poller yourself instead, e.g. from autostart:"
    warn "    python3 $POLLER_DST/poller.py --daemon"
fi

# --- seed the first reading ----------------------------------------------
say "Fetching the first usage reading"
python3 "$POLLER_DST/poller.py" --once || warn "initial poll failed; the cards will populate on the next run."

cat <<EOF

Done.

Add the desklet to your desktop:
  right-click the desktop -> Add Desklets -> "AI Usage" -> Add
  (or open Menu -> Desklets)

Configure it by right-clicking the desklet -> Configure.
EOF
