# Collector data verification

## UI data contract

Before a desktop UI is added, an available provider snapshot must supply:

- Stable provider identifier and display label.
- Status and source classification.
- UTC collection time for freshness display.
- At least one labeled quota window.
- A percentage, or a used value paired with its limit.
- Reset time and window duration when exposed by the provider.

The future UI must handle absent reset times and providers that report only
consumption rather than a percentage.

Run the deterministic contract check with:

```bash
python3 scripts/usage_probe.py --pretty verify-fixtures
```

## Verified adapters

### Claude Code

The parser follows Anthropic's official status-line payload: `rate_limits`
contains `five_hour` and `seven_day` windows with `used_percentage` and
`resets_at`. The parser is covered with synthetic fixtures because obtaining a
live payload requires the user to explicitly configure a Claude status-line
command. It reads the payload from stdin and has no credential or network
permission.

### Codex

The local probe was exercised against the installed official Codex app-server.
It successfully returned an available quota snapshot containing a usage
percentage, rolling-window duration, and reset time. Live values were observed
only in process output and were not written to this repository.

The adapter:

- Starts Codex directly without a shell.
- Uses JSONL over local stdio, not a listening socket.
- Disables app-server analytics.
- Does not read `auth.json` or request authentication tokens.
- Discards initialization metadata, notifications, plan information, balances,
  reset-credit identifiers, and raw responses.
- Returns only the normalized snapshot.

The method is experimental in Codex, so schema changes must fail closed and be
covered by new fixtures before release.

## Pending adapters

- GitHub Copilot requires a separately scoped official API credential and was
  not detected in the current VS Code profile during research.
- Devin requires an official account/API capability before live verification.
- Antigravity does not yet have a reviewed scriptable usage interface.
- Cursor's known approach depends on private state and remains disabled.

These gaps do not block validation of the shared UI schema, but their adapters
must be verified before the UI claims live support for them.

