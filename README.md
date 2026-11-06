# AI Usage Desklet

A desklet that shows your **Claude**, **ChatGPT**, **OpenCode Go**,
**Mistral** and **OpenRouter** usage at a glance: usage limits as bars with
percentages, live reset countdowns, plan pills with the date each plan clocks
over (where the provider publishes one), and any credit balance. One glass card,
styled in the Orchis-Purple-Dark palette, that sits on your desktop and updates
itself. Every card can be hidden, and a provider you are not signed in to
produces no card at all, so it works just as well with one provider as with five.

Two desktop front ends share one poller:

- a **Cinnamon desklet** (the original, with a settings dialog), and
- a **GTK card for XFCE**, added in 0.7.0, matching the same design.

![The AI Usage card, dark glass](screenshots/dark.png)

## What it shows

- **Usage bars** for each limit window (Claude session + weeklies, ChatGPT
  5-hour + weekly, OpenCode Go rolling + weekly + monthly, Mistral Vibe + API
  monthly, OpenRouter credit spend), with a live percentage coloured by severity
  (purple → amber → red as a limit fills).
- **Reset countdowns** that tick every second and roll over on their own,
  wherever the provider publishes a reset instant. OpenRouter does not, so that
  card shows a bar without a countdown rather than a time this desklet made up.
- **The plan you are on**, read from your account rather than from a local file,
  so an upgrade shows up straight away.
- **The plan's renewal date** beside the plan pill, wherever the provider
  actually returns one: OpenCode publishes its monthly window's reset, Mistral
  its monthly budget reset, and ChatGPT puts a subscription end date inside its
  signed login token (shown while that date is still in the future, because the
  token's claims can lag a re-login by months). Claude and OpenRouter publish
  nothing: Claude's API exposes no renewal date at all, in its usage or account
  payloads or at any billing endpoint, so Claude can never show one — a missing
  date beside the plan pill is the API being silent, not the desklet failing.
  The same rule applies everywhere: no date is ever computed locally.
- **Credit balance / spend** where the provider reports one.
- A per-card **staleness indicator**: if a reading can't be refreshed, the card
  dims and shows how long ago it was last good, rather than lying or going
  blank.
- A **? on each card** (Cinnamon) that explains, in plain language, what the
  card is currently showing, why, and what to do about it. Click it for the full
  help. The XFCE card carries the same text as its hover tooltip.

## How it works

Two small pieces, so a rendering hiccup can never take down your desktop shell:

1. A short-lived **Python poller** reads the credential files that Claude Code
   and the Codex CLI already keep on disk, plus the key files you create for
   OpenRouter and OpenCode Go and the key the Mistral CLI keeps in your keyring.
   It calls each service's usage endpoint,
   normalises them all into one small JSON file
   (`~/.cache/ai-usage-desklet/state.json`), and exits. A **systemd user timer**
   runs it on a schedule. Those files are only ever read, never written, and no
   token, account id or email is copied into the state file.
2. The **desklet** (or the XFCE card) reads that JSON file and draws the cards.
   Neither front end makes any network calls or writes any files.

Poll rate is per-provider: ChatGPT every 30 s, Claude every 120 s (its usage
endpoint rate limits, so a slower poll keeps it healthy), OpenCode Go and
Mistral every 120-300 s and OpenRouter every 300 s (none of them move faster
than a job or a spend). The countdowns tick locally every second either way, so
the display always looks live. When nothing has used your quota for a while,
polling slows down on its own to save requests; "a while" is judged by whether
either tool has written a session transcript recently, which is the only signal
that reliably distinguishes a tool you are using from one that merely has a
background process running.

Plan names come from your provider account, checked at most once an hour and
cached in between. If the account can't be reached, the poller falls back to the
plan recorded in the local sign-in file and says so, because that file is written
when you sign in and does not change when you change plan. Either way the
percentages are unaffected: they are calculated by the provider against your real
limits.

A third piece keeps the **Claude** card alive on its own. Claude access tokens
are short-lived (about 8 hours) and are refreshed by the Claude Code CLI, so if
you don't open Claude for a while the token lapses and the card would otherwise
show "token expired" until you next run it. A small keep-alive helper
(`keepalive.py`) runs on its own systemd timer and, when the token is expired or
about to be, asks the **official Claude CLI** to refresh it through the CLI's own
supported path. The poller and desklet still never write your credentials; the
CLI stays the only thing that does. If the Claude CLI isn't installed, the
keep-alive is simply skipped.

## Requirements

- **A Linux desktop: either Cinnamon or XFCE.**
  - The Cinnamon desklet needs **Cinnamon 5.4+** (developed on 6.6).
  - The XFCE card needs **GTK 3** through PyGObject (`python3-gi` /
    `python3-gobject`), and a compositing window manager for the glass card;
    without one it turns solid rather than drawing black boxes.
- **Python 3.9+** and the **`requests`** library. Nothing else outside the
  standard library.
- **systemd** for the automatic timer (optional — you can run the poller yourself
  instead; see below).
- **At least one data source, logged in:**
  - The **Claude** card needs [Claude Code](https://claude.com/claude-code)
    installed and signed in (it creates `~/.claude/.credentials.json`).
  - The **ChatGPT** card needs the **Codex CLI** (or the ChatGPT desktop app's
    Codex) signed in (it creates `~/.codex/auth.json`).
  - The **OpenCode Go** card reads a key file you create at
    `~/.local/share/ai-usage-desklet/opencode.env` containing
    `OPENCODE_API_KEY=...`.
  - The **OpenRouter** card reads a key file you create at
    `~/.local/share/ai-usage-desklet/openrouter.env` containing
    an `OPENROUTER_API_KEY=...`.
  - The **Mistral** card appears if the Mistral CLI (`vibe`) has stored an API
    key in your desktop keyring.
  - If a source is absent, its card simply does not appear.

The desklet **reads** those token files and never modifies them. See
[Privacy & security](#privacy--security).

## Install

### Cinnamon

```bash
git clone https://github.com/chloecaffeinexo/ai-usage-desklet.git
cd ai-usage-desklet
./install.sh
```

Then add it to your desktop: right-click the desktop → **Add Desklets** →
**AI Usage** → Add, or open **Menu → Desklets**. Drag it where you like.

The installer copies the desklet, the poller, and the systemd units into your
home directory, starts the timer, and does one immediate poll so the cards have
data straight away. It does not touch anything as root.

### XFCE

```bash
cd xfce
./install.sh
```

That installs the card to `~/.local/share/ai-usage-xfce/`, reuses the poller and
its timer if the Cinnamon install already put them there, does one poll, and
adds an autostart entry so the card comes back at login. Everything it installs
lives in your home directory. See `xfce/README.md` for configuration.

### Manual install

If you'd rather not run the scripts:

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

- **Card style** — dark glass (default, pictured at the top), light glass, or
  solid.
- **Density** — compact (default) or standard spacing.
- **Card width**.
- **Show / hide** each provider card, the credits row, and inactive limit bars.
- **Reset-time format** — 24-hour or 12-hour (used in the tooltip).
- **Show the ? marker** on each card.

The toggles let the desklet be as detailed or as quiet as you like — everything
on with the credits rows, pared back to just the active bars, or a single
provider if you only use one:

| Default | Everything on | Minimal | One provider |
|---|---|---|---|
| ![Dark card style, the default](screenshots/dark.png) | ![All features enabled](screenshots/all-features.png) | ![Minimal: active bars only](screenshots/minimal.png) | ![Claude card only](screenshots/single-provider.png) |

Hover a card for a tooltip with the renewal date, absolute reset times and full
figures. Click a card to open that provider's usage page in your browser.

### The ? marker

Each card carries a small **?** in its top right. Hover it and it tells you what
that card is showing and what, if anything, you should do:

| Card state | What the ? tells you |
|---|---|
| Sign-in expired | That the figures are frozen, and the exact command to sign in again |
| Being throttled | That this is normal, and when the next attempt happens |
| Unreachable | The reason, the age of the figures shown, and how to test the poller by hand |
| Window rolled over | That it is holding the previous figure rather than guessing at zero |
| Plan name may be stale | That the name came from the local sign-in file, and how to refresh it |
| Healthy | The plan, the age, and when each limit resets |

Clicking the **?** opens the desklet's settings on a **Help** page with the
sign-in commands, the manual check command, and the log location.

## Compatibility

**Desktop environment — Cinnamon or XFCE.**

- The **Cinnamon desklet** runs only under Cinnamon. That is
  distribution-independent: it works on any distro running Cinnamon, including
  Linux Mint (Cinnamon edition), Fedora (Cinnamon spin), Ubuntu/Debian with the
  `cinnamon` package, Arch/Manjaro with Cinnamon, and openSUSE with the Cinnamon
  pattern. Developed and tested on X11; the desklet uses nothing X11-specific
  and should behave the same on Cinnamon's Wayland session.
- The **XFCE card** runs anywhere GTK 3 does, and was developed and tested on
  Linux Mint's XFCE edition.

**The poller is portable.** The Python poller and the systemd user units are the
only distro-sensitive parts, and they're undemanding:

- **systemd distros** (Mint, Ubuntu, Debian, Fedora, Arch, Manjaro, openSUSE, …)
  use the included user timer, no extra work.
- **Non-systemd distros** (Void, Artix, Devuan, Gentoo/OpenRC, …) can skip the
  units entirely. The poller has a built-in loop — run
  `python3 ~/.local/share/ai-usage-desklet/poller.py --daemon` from your session
  autostart, or call `--once` from cron. The card only cares that `state.json`
  gets refreshed; it doesn't care what refreshes it.

**Python.** 3.9 or newer (Mint 21, Ubuntu 20.04, Debian 11, and anything more
recent all qualify). The only third-party dependency is `requests`
(`python3-requests` / `pip install requests`). The Mistral card additionally
uses the `keyring` library if it is installed; without it, that one card simply
does not appear.

**Data availability.** The numbers come from each provider's own account
endpoints, reached through the tools you already sign in to. If you don't use a
provider, its card simply does not appear — that's expected, not a bug.

## Privacy & security

- The poller **only reads** the sign-in files your tools already keep
  (`~/.claude/.credentials.json`, `~/.codex/auth.json`, your keyring). It never
  writes to them and never performs an OAuth refresh — the official tools own
  those files.
- Your tokens and keys are used solely to authenticate to the official usage
  endpoints of each service and are **never written** to any output file, log,
  or error message, and never sent anywhere else. There is no telemetry.
- `state.json` and the cache files are written with `0600` permissions and
  contain only usage percentages, reset times, plan names and renewal dates —
  no tokens, emails, or account identifiers.
- The desklets make no network requests and write no files; they only read the
  state file and (on click) open a usage page in your browser.

## A note on the endpoints

Every service's usage endpoint here is **undocumented / unofficial**. They work
well today but could change or disappear without notice. The code degrades
gracefully when that happens — a card shows "unavailable" rather than breaking —
but if a provider changes its API, that card may need an update.

## Uninstall

Cinnamon: `./uninstall.sh`, then remove the desklet from your desktop
(right-click it → Remove). XFCE: see `xfce/README.md`.

## License

MIT — see [LICENSE](LICENSE).

This is an independent project and is not affiliated with, endorsed by, or
supported by Anthropic, OpenAI, OpenCode, Mistral or OpenRouter. Product names
are trademarks of their respective owners.
