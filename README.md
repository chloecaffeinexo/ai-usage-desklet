# AI Usage Desklet

A Cinnamon desklet that shows your **Claude** and **ChatGPT** subscription usage at a glance:
usage limits as bars with percentages, live reset countdowns, and any credit balance. It sits
on your desktop, styled to stay readable over any wallpaper or theme, and updates itself.
Either provider card can be hidden, so it works just as well with one provider as with both.

![Dark card style, the default](screenshots/dark.png)

## What it shows

- **Usage bars** for each limit window (Claude session + weeklies, ChatGPT weekly) with a
  live percentage, coloured by severity (blue → amber → red as a limit fills).
- **Reset countdowns** that tick every second and roll over on their own.
- **Credit balance / spend** where the provider reports one.
- A per-card **staleness indicator**: if a reading can't be refreshed, the card dims and shows
  how long ago it was last good, rather than lying or going blank.
- The **plan you are actually on**, read from your account rather than from a local file, so an
  upgrade shows up straight away.
- A **? on each card** that explains, in plain language, what the card is currently showing,
  why, and what to do about it. Click it for the full help.

## How it works

Two small pieces, so a rendering hiccup can never take down your desktop shell:

1. A short-lived **Python poller** reads the OAuth token files that Claude Code and the Codex
   CLI already keep on disk, calls each service's usage endpoint, normalises both into one
   small JSON file (`~/.cache/ai-usage-desklet/state.json`), and exits. A **systemd user
   timer** runs it on a schedule.
2. The **desklet** reads that JSON file and draws the cards. It makes no network calls and
   writes no files.

Poll rate is per-provider: ChatGPT every 30 s, Claude every 120 s (its usage endpoint rate
limits, so a slower poll keeps it healthy). The countdowns tick locally every second either
way, so the display always looks live. When nothing has used your quota for a while, polling
slows down on its own to save requests; "a while" is judged by whether either tool has written
a session transcript recently, which is the only signal that reliably distinguishes a tool you
are using from one that merely has a background process running.

Plan names come from your provider account, checked at most once an hour and cached in between.
If the account can't be reached, the poller falls back to the plan recorded in the local sign-in
file and says so, because that file is written when you sign in and does not change when you
change plan. Either way the percentages are unaffected: they are calculated by the provider
against your real limits.

A third piece keeps the **Claude** card alive on its own. Claude access tokens are short-lived
(about 8 hours) and are refreshed by the Claude Code CLI, so if you don't open Claude for a
while the token lapses and the card would otherwise show "token expired" until you next run it.
A small keep-alive helper (`keepalive.py`) runs on its own systemd timer and, when the token is
expired or about to be, asks the **official Claude CLI** to refresh it through the CLI's own
supported path. The poller and desklet still never write your credentials; the CLI stays the
only thing that does. If the Claude CLI isn't installed, the keep-alive is simply skipped.

## Requirements

- **The Cinnamon desktop environment.** This is a Cinnamon desklet; it does not run on GNOME,
  KDE Plasma, XFCE, MATE, or others. See [Compatibility](#compatibility).
- **Python 3.9+** and the **`requests`** library. Nothing else outside the standard library.
- **systemd** for the automatic timer (optional — you can run the poller yourself instead; see
  [Compatibility](#compatibility)).
- **At least one of the two data sources, logged in:**
  - The **Claude** card needs [Claude Code](https://claude.com/claude-code) installed and
    signed in (it creates `~/.claude/.credentials.json`).
  - The **ChatGPT** card needs the **Codex CLI** (or the ChatGPT desktop app's Codex) signed
    in (it creates `~/.codex/auth.json`).
  - If only one is present, that card works and the other shows as unavailable.

The desklet **reads** those token files and never modifies them. See
[Privacy & security](#privacy--security).

## Install

```bash
git clone https://github.com/<your-username>/ai-usage-desklet.git
cd ai-usage-desklet
./install.sh
```

Then add it to your desktop: right-click the desktop → **Add Desklets** → **AI Usage** → Add,
or open **Menu → Desklets**. Drag it where you like.

The installer copies the desklet, the poller, and the systemd units into your home directory,
starts the timer, and does one immediate poll so the cards have data straight away. It does not
touch anything as root.

### Manual install

If you'd rather not run the script:

```bash
# desklet
cp -r desklet/ai-usage@desklet ~/.local/share/cinnamon/desklets/

# poller
mkdir -p ~/.local/share/ai-usage-desklet/providers
cp poller/poller.py poller/normalise.py ~/.local/share/ai-usage-desklet/
cp poller/providers/*.py ~/.local/share/ai-usage-desklet/providers/

# systemd timer
cp systemd/ai-usage-poller.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ai-usage-poller.timer

# seed the first reading
python3 ~/.local/share/ai-usage-desklet/poller.py --once
```

## Settings

Right-click the desklet → **Configure**:

- **Card style** — dark glass (default, pictured at the top), light glass, or solid.

  ![Light card style](screenshots/light.png)

- **Density** — compact (default) or standard spacing.
- **Card width**.
- **Show / hide** each provider card, the credits row, and inactive limit bars.
- **Reset-time format** — 24-hour or 12-hour (used in the tooltip).
- **Show the ? marker** on each card.

The toggles let the desklet be as detailed or as quiet as you like — everything on with the
help markers, pared back to just the active bars, or a single provider if you only use one:

| Everything on | Minimal | One provider |
|---|---|---|
| ![All features enabled](screenshots/all-features.png) | ![Minimal: active bars only](screenshots/minimal.png) | ![Claude card only](screenshots/single-provider.png) |

Hover a card for a tooltip with absolute reset times and full figures. Click a card to open
that provider's usage page in your browser.

### The ? marker

Each card carries a small **?** in its top right. Hover it and it tells you what that card is
showing and what, if anything, you should do:

| Card state | What the ? tells you |
|---|---|
| Sign-in expired | That the figures are frozen, and the exact command to sign in again |
| Being throttled | That this is normal, and when the next attempt happens |
| Unreachable | The reason, the age of the figures shown, and how to test the poller by hand |
| Window rolled over | That it is holding the previous figure rather than guessing at zero |
| Plan name may be stale | That the name came from the local sign-in file, and how to refresh it |
| Healthy | The plan, the age, and when each limit resets |

Clicking the **?** opens the desklet's settings on a **Help** page with the sign-in commands,
the manual check command, and the log location.

## Compatibility

**Desktop environment — Cinnamon only.** Desklets are a Cinnamon feature, so the on-desktop
part runs *only* under Cinnamon. That is distribution-independent: it works on any distro
running Cinnamon, including

- **Linux Mint** (Cinnamon edition)
- **Fedora** (Cinnamon spin) — this is where it was developed and tested
- **Ubuntu / Debian** with the `cinnamon` package
- **Arch / Manjaro / EndeavourOS** with Cinnamon
- **openSUSE** with the Cinnamon pattern

It does **not** work on GNOME, KDE Plasma, XFCE, MATE, Budgie, or any non-Cinnamon desktop —
those have no concept of Cinnamon desklets. Developed on Cinnamon 6.6; it uses only stable
St/CJS APIs and should work on Cinnamon 5.4+.

**Session type.** Developed and tested on **X11**. Cinnamon's Wayland session is still
experimental upstream, but the desklet uses nothing X11-specific and should behave the same
there.

**The poller is portable.** The Python poller and the two systemd units are the only distro-
sensitive parts, and they're undemanding:

- **systemd distros** (Mint, Ubuntu, Debian, Fedora, Arch, Manjaro, openSUSE, …) use the
  included user timer, no extra work.
- **Non-systemd distros** (Void, Artix, Devuan, Gentoo/OpenRC, …) can skip the units entirely.
  The poller has a built-in loop — run `python3 ~/.local/share/ai-usage-desklet/poller.py
  --daemon` from your session autostart, or call `--once` from cron. The desklet only cares
  that `state.json` gets refreshed; it doesn't care what refreshes it.

**Python.** 3.9 or newer (Mint 21, Ubuntu 20.04, Debian 11, and anything more recent all
qualify). The only third-party dependency is `requests`
(`python3-requests` / `pip install requests`).

**Data availability.** The numbers come from Claude Code and the Codex CLI. If you don't use
one of those tools, its card simply shows as unavailable — that's expected, not a bug.

## Privacy & security

- The poller **only reads** `~/.claude/.credentials.json` and `~/.codex/auth.json`. It never
  writes to them and never performs an OAuth refresh — the official tools own those files.
- Your tokens are used solely to authenticate to the two official usage endpoints
  (`api.anthropic.com` and `chatgpt.com`) and are **never written** to any output file, log,
  or error message, and never sent anywhere else. There is no telemetry.
- `state.json` and the cache files are written with `0600` permissions and contain only usage
  percentages, reset times, and plan names — no tokens, emails, or account identifiers.
- The desklet itself makes no network requests and writes no files; it only reads the state
  file and (on click) opens a usage page in your browser.

## A note on the endpoints

Both services' usage endpoints are **undocumented / unofficial**. They work well today but
could change or disappear without notice. The code degrades gracefully when that happens —
a card shows "unavailable" rather than breaking — but if a provider changes its API, that card
may need an update.

## Uninstall

```bash
./uninstall.sh
```

Then remove the desklet from your desktop (right-click it → Remove).

## License

MIT — see [LICENSE](LICENSE).

This is an independent project and is not affiliated with, endorsed by, or supported by
Anthropic or OpenAI. "Claude" and "ChatGPT" are trademarks of their respective owners.
