# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

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

[0.3.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/releases/tag/v0.1.0
