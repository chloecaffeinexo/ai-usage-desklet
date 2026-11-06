# AI Usage for XFCE

The same AI Usage card as the Cinnamon desklet, as a single GTK 3 desktop
window for XFCE sessions. It shows one glass card with your **Claude**,
**ChatGPT**, **OpenCode Go**, **Mistral** and **OpenRouter** usage bars, live
reset countdowns, plan pills, plan renewal dates where the provider publishes
one, and credit balances.

The card reads `~/.cache/ai-usage-desklet/state.json`, written by the same
Python poller the Cinnamon desklet uses (`../poller/`). The card makes no
network requests and reads no credentials; it only ever sees the summary the
poller writes.

## Install

```bash
./install.sh
```

That copies the card to `~/.local/share/ai-usage-xfce/`, installs the poller and
its systemd user timer if they are not already there, does one poll so the card
has data immediately, and adds an autostart entry so the card comes back at
login. Nothing runs as root.

Requirements: Python 3.9+, PyGObject with GTK 3 (`python3-gi` / `python3-gobject`),
and the poller's `requests` library. XFCE with a compositing window manager gives
the glass card; without a compositor the card turns solid rather than drawing
black boxes.

## Use

* **Drag** the card anywhere; the position saves itself.
* **Hover** a provider for absolute reset times, the renewal date and the
  credits detail; click a provider to open its usage page in a browser.
* **Configure** by editing `~/.config/ai-usage-xfce/config.json`, then
  restarting the card. Per provider, `show-<id>` toggles the provider
  (`claude`, `chatgpt`, `opencode`, `mistral`, `openrouter`); card-wide,
  `card-width` (220-480), `show-credits`, `show-inactive`, `show-help`,
  `time-format` (`24h`/`12h`), `window-mode` (`below`, `desktop` or `dock`) and
  `scale` (`auto` follows the desktop DPI, or a number such as `1.25`).

```
{
  "widgets": {
    "ai-usage": {
      "x": 80, "y": 80,
      "show-credits": true, "show-inactive": true, "show-help": true,
      "time-format": "24h"
    }
  },
  "card-width": 300, "window-mode": "below", "scale": "auto"
}
```

## Preview without a desktop

```bash
python3 ai-usage.py --preview /tmp/ai-usage-preview
```

renders the card against the live state file to a PNG, offscreen, with no
window shown.

## Uninstall

```bash
rm -f ~/.config/autostart/ai-usage-xfce.desktop
rm -rf ~/.local/share/ai-usage-xfce ~/.config/ai-usage-xfce
```

If nothing else uses the poller (the Cinnamon desklet does), remove it with
the repository's top-level `uninstall.sh`.
