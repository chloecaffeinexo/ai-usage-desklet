#!/usr/bin/env bash
# Install AI Usage for XFCE: the GTK card, the shared poller, and its timer.
# Everything is installed under $HOME. No root required.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CARD_DST="$HOME/.local/share/ai-usage-xfce"
POLLER_DST="$HOME/.local/share/ai-usage-desklet"
SYSTEMD_DST="$HOME/.config/systemd/user"
AUTOSTART_DST="$HOME/.config/autostart"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required but not found." >&2
    exit 1
fi
if ! python3 -c 'import gi; gi.require_version("Gtk", "3.0")' >/dev/null 2>&1; then
    warn "PyGObject with GTK 3 is not installed."
    warn "install it first, e.g.  sudo apt install python3-gi"
    warn "                   or   sudo dnf install python3-gobject"
fi
case "${XDG_CURRENT_DESKTOP:-}" in
    *XFCE*|*xfce*) : ;;
    *) warn "this doesn't look like an XFCE session (XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP:-unset})."
       warn "the card will still install, but it is meant for XFCE desktops." ;;
esac

# --- the card --------------------------------------------------------------
say "Installing the card to $CARD_DST"
mkdir -p "$CARD_DST"
cp "$SRC/ai-usage.py" "$SRC/style.css" "$CARD_DST/"

# --- poller (shared with the Cinnamon desklet) ------------------------------
if [ ! -f "$POLLER_DST/poller.py" ]; then
    say "Installing the poller to $POLLER_DST"
    mkdir -p "$POLLER_DST/providers"
    cp "$SRC/poller/poller.py" "$SRC/poller/normalise.py" "$SRC/poller/keepalive.py" "$POLLER_DST/"
    cp "$SRC/poller/providers/"*.py "$POLLER_DST/providers/"
else
    say "Poller already installed at $POLLER_DST; leaving it as it is."
fi

# --- systemd timer ----------------------------------------------------------
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    if [ ! -f "$SYSTEMD_DST/ai-usage-poller.timer" ]; then
        say "Installing and starting the systemd user timer"
        mkdir -p "$SYSTEMD_DST"
        cp "$SRC/systemd/ai-usage-poller.service" "$SRC/systemd/ai-usage-poller.timer" "$SYSTEMD_DST/"
        systemctl --user daemon-reload
        systemctl --user enable --now ai-usage-poller.timer
    else
        say "Poller timer already installed; leaving it as it is."
    fi
else
    warn "systemd user services are unavailable; the poller is not scheduled."
    warn "run it yourself instead, e.g. from autostart:"
    warn "    python3 $POLLER_DST/poller.py --daemon"
fi

# --- seed the first reading --------------------------------------------------
if [ -f "$POLLER_DST/poller.py" ]; then
    say "Fetching the first usage reading"
    python3 "$POLLER_DST/poller.py" --once || warn "initial poll failed; the card will populate on the next run."
fi

# --- autostart ----------------------------------------------------------------
say "Installing the autostart entry"
mkdir -p "$AUTOSTART_DST"
cat > "$AUTOSTART_DST/ai-usage-xfce.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AI Usage (XFCE card)
Exec=python3 $CARD_DST/ai-usage.py
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

cat <<EOF

Done.

The card starts on your next login. To start it now:
  python3 $CARD_DST/ai-usage.py &

Drag it wherever you like; the position is saved on its own.
EOF
