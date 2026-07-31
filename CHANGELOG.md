# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

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

[0.2.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chloecaffeinexo/ai-usage-desklet/releases/tag/v0.1.0
