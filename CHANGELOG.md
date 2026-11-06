# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [0.7.0] - 2026-11-06

### Added

- **The OpenCode Go card**, this time reading the account rather than guessing.
  OpenCode publishes `GET /zen/go/v1/usage`, which returns the same rolling,
  weekly and monthly percentages and reset instants the console dashboard draws.
  The card reads that endpoint with a Go API key from
  `~/.local/share/ai-usage-desklet/opencode.env`, so it counts usage made on
  your other devices and cannot drift from the console. A window whose status
  is not `ok` is drawn as spent rather than trusting its floored percentage.
  The 0.6.0 removal stood on the claim that no usage API existed; that claim
  expired, so the card is back.
- **A Mistral card.** Reads the monthly Vibe Code and API budgets from
  Mistral's own billing endpoints, using the API key the Mistral CLI stores in
  your desktop keyring at sign-in (read only). Appears only when that key is
  present.
- **Plan renewal dates.** The plan pill is now followed by the date the plan
  clocks over, monthly or yearly, wherever the provider actually returns one:
  OpenCode publishes its monthly window's reset, Mistral its monthly budget
  reset, and ChatGPT carries a subscription end date inside its signed login
  token, shown only while that date is still in the future because the token's
  claims are rewritten only on re-login and can lag by months. Claude and
  OpenRouter publish nothing, so they show no date, and no date is ever
  computed locally. Available to every front end through two new optional
  `state.json` fields, `renews_at` and `renews_period`.
- **An XFCE version of the card** (`xfce/`): the same one-glass-card design as a
  standalone GTK 3 desktop window, with its own installer and autostart entry,
  reading the same poller state file, for desktops without Cinnamon.
- A **test suite for the renewal fields**, covering the stale ChatGPT claim
  (a past date must never be shown), the monthly and yearly period detection,
  and the OpenCode and Mistral wiring.

### Changed

- **One card for everything.** The Cinnamon desklet previously drew one card
  per provider; it now draws a single Orchis-Purple-Dark glass card with the
  shared header, provider sections separated by hairline rules, plan pills and
  thin rounded meters in the accent purple, exactly as the XFCE card is drawn.
  Sections sit in the order Claude, ChatGPT, OpenCode, Mistral, then any other
  provider. The previous per-provider glass fills with the blue palette are
  gone with them.
- **Meters restyled to match the XFCE card**: a plain translucent track and a
  horizontal fill shading from a lighter tone into the accent colour, grey when
  a section is stale. The countdown moved up beside the percentage on the row
  above the bar, where the XFCE card draws it.

### Removed

- **A retired provider's card.** One provider left this desklet's supported set
  before this version was published; its adapter, tests, fixtures, settings and
  every mention are gone from the code and the docs.

## [0.6.0] - 2026-08-28

### Added

- **OpenRouter card.** One account-wide bar of spend against total credits, plus the remaining
  balance. The API publishes no reset instant, so the card carries no countdown rather than a
  computed one. A per-key limit is a cap on the same shared account balance rather than a
  separate budget, so it is deliberately not shown as a bar; the per-key spend appears as a
  note instead.
- Two new settings switches for the new cards, and sign-in help text for each.
- **A test suite.** Unit tests covering the adapters against recorded API fixtures,
  with no network access.

### Changed

- **The meters are drawn as glass.** The three severity colours are unchanged; what changed is
  the lighting. A fill now runs from a tint of its colour along the top to a deeper tone along
  the bottom and carries a bright highlight over its top half, so it reads as a rounded tube
  rather than a flat block. The empty track is lit the opposite way, dark along the top lip and
  catching light at the bottom, which is how a channel cut into glass reads. On a light card
  the track's two stops swap, since there the groove is read from its shadow.
- **A meter's own label is set bold**, so the line above the bar reads as one name-and-number
  pair, matching the row the Android widget settled on.
- A provider you are not signed in to now produces no card at all rather than a dead one. This
  already applied to some cards and is now consistent across all four.

### Removed

- **The OpenCode Go card.** It computed usage locally against a single flat monthly cap, but
  Go gives each model its own quota and reports the sum of the per-model percentages, so the
  card read low and could not be fixed from the data available on this machine. Only the
  OpenCode web console has the real figures.

## [0.5.0] - 2026-08-13

### Added

- **Scoped weekly limits label themselves.** Anthropic's usage API now returns a
  third limit, `weekly_scoped`, carrying the model it applies to. The poller
  reads that model name from the response, so the bar renders as
  "Weekly (Fable)" today and follows whatever model the API names next, with the
  old hardcoded Opus special case kept only as a fallback.

### Fixed

- **ChatGPT credit balances render readably.** The API sends up to ten decimal
  places; the card now shows two (`509.43`, not `509.4273940000`).
- **The desklet no longer rebuilds its display when nothing changed.** It
  previously tore down and recreated every card on each 30-second refresh, which
  also destroyed any tooltip being read at that moment. It now skips the rebuild
  unless the provider data actually differs, and the "updated … ago" footer is
  kept live by the existing one-second countdown timer instead, so it ticks in
  real time rather than jumping twice a minute.

### Changed

- **Screenshot set replaced.** Now light, dark, all features enabled, minimal
  (active bars only), and single provider, showing the toggle range; the stale
  solid-style shot removed and the README settings section now shows the set.
- **Internal cleanup, no behaviour change.** The two providers' duplicated
  request/retry/error code merged into one shared helper, the poller's
  duplicated per-provider scheduling blocks merged into one routine,
  compatibility shims for cache files written by pre-0.3 builds removed, and
  leftover debug counters dropped from the desklet. About 150 lines net,
  removed while adding the features above.

## [0.4.0] - 2026-08-11

### Fixed

- **The plan name could be months out of date.** The poller took it from
  `subscriptionType` in the local Claude sign-in file. That field is written when you
  sign in and is *not* rewritten when the token silently refreshes, so an account
  upgraded from Pro to Max kept reporting Pro indefinitely. The plan is now read from
  the account profile, cached for an hour, with the local file used only as a fallback
  when the account cannot be reached. The rate-limit tier is read too, so Max 5x and
  Max 20x are told apart. Usage percentages were never affected by this; they are
  calculated by the provider against your real limits.

- **Idle slowdown misfired during active use.** Activity was judged from
  `~/.claude/history.jsonl`, which only the Claude CLI writes. Working in the desktop
  app left it untouched, so the poller decided you were idle and dropped to the 600 s
  interval while you were mid-session, making cards look up to ten minutes out of date.
  Activity is now judged from session transcript writes under `~/.claude/projects` and
  `~/.codex/sessions`, which both the CLI and the desktop app produce. This is the third
  approach to this signal; the two earlier ones keyed off process names and matched
  permanently running background processes.

### Added

- **A ? marker on each card**, explaining in plain language what the card is showing,
  why, and what to do about it: an expired sign-in names the command to fix it, a
  throttled card says the throttling is expected and when the next attempt is, an
  unreachable card gives the manual check command, a rolled-over window says it is
  holding the previous figure rather than guessing at zero, and a plan name that came
  from the local file says so. Clicking it opens a new **Help** page in settings.
- A **Show the ? marker** setting, on by default.
- Three additive fields in `state.json` — `plan_source`, `auth_state` and
  `next_attempt_at` — which is what lets the desklet distinguish a live plan name from
  a possibly stale one and say when a stalled card will be retried. Existing readers are
  unaffected.

## [0.3.0] - 2026-08-01

### Added

- **Automatic Claude token keep-alive, so the Claude card no longer dies when the
  token expires.** 0.2.0 made an expired token *legible* (a clear "token expired"
  badge), but you still had to open the Claude app by hand to recover. This release
  keeps the token fresh on its own.

  A small helper (`keepalive.py`) runs on a systemd user timer
  (`ai-usage-token-refresh.timer`, every 10 minutes). When the stored access token
  is expired or within 15 minutes of expiring, it asks the **official Claude CLI**
  (`claude auth status`) to make an authenticated call, which triggers the CLI's
  own supported token refresh. The desklet's poller still **never** reads, writes,
  or refreshes credentials beyond reading them; the Claude CLI remains the single
  authoritative writer of `~/.claude/.credentials.json`, so there is no
  second-writer / refresh-token-rotation race.

### Notes

- **Requires the Claude Code CLI.** The desklet's Claude card reads the CLI's own
  credential store (`~/.claude/.credentials.json`), so anyone using that card
  already has the CLI. If the CLI is genuinely absent, the keep-alive timer is
  skipped at install time and the helper is a safe no-op; the card behaves as in
  0.2.0 (works while the token is valid, shows "token expired" once it lapses).
- No Anthropic OAuth internals (endpoints, client ids, secrets) are hardcoded or
  committed. Refresh happens only through the official CLI.
- The helper reads only `expiresAt` to decide whether a refresh is due, and
  discards the CLI's output (it contains the account email) so nothing sensitive
  is ever logged.

**Upgrading:** pull the latest and re-run `install.sh`. It installs `keepalive.py`
and enables the new timer. To verify, run
`systemctl --user list-timers ai-usage-token-refresh.timer`.

## [0.2.0] - 2026-07-31

### Fixed

- **Claude card now explains an expired login instead of showing a cryptic `HTTP 401`.**
  In 0.1.0, the poller only checked that a Claude access token *existed*, never
  whether it had expired. Once your Claude access token lapsed (they are
  short-lived and refreshed by the Claude app itself), the poller kept sending
  the dead token, the server rejected it, and the card showed a bare **`HTTP 401`**
  badge with no hint of the cause or the fix. The card would sit stale
  indefinitely with no explanation.

  The Claude provider now reads the token's `expiresAt` from the credentials file
  and, if it has already passed, fails fast with a clear **`token expired`** badge
  and the message *"access token expired; sign in to Claude to refresh it."* Any
  genuine `HTTP 401`/`403` from the server maps to the same actionable message.

### Notes

- The fix is detection and reporting only. The poller still **never** writes or
  refreshes the credentials file; refreshing remains the Claude app's job. To
  clear the state, sign in to Claude again (e.g. launch Claude Code) and the next
  poll recovers on its own.
- Backwards compatible: if a credentials file has no `expiresAt` field, the poller
  behaves exactly as before, so older credential formats are unaffected.
- ChatGPT was never affected; its provider already degrades to a local snapshot on
  an auth failure.

**Upgrading:** pull the latest and re-run `install.sh` (or copy
`poller/providers/claude.py` into `~/.local/share/ai-usage-desklet/providers/`).
No settings or schema changes.

## [0.1.0] - 2026-07-30

- Initial release. Cinnamon desklet showing Claude and ChatGPT subscription usage
  as bars with live percentages, per-second reset countdowns, and credit balances.
  Per-provider polling intervals, `Retry-After`-aware backoff, local snapshot
  fallback, staleness indicators, eight live settings, and credential-safe polling
  (reads only, no token ever written to state, logs, or error strings).

[0.7.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/releases/tag/v0.1.0
