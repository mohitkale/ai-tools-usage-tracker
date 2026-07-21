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

The `claude-capture` command can serve as that status-line target. It writes one
normalized snapshot atomically with user-only permissions and prints a short
status-line summary. It does not retain the incoming payload. The project does
not automatically edit `~/.claude/settings.json` or its platform equivalents.


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

### Cursor

The private, default-disabled adapter was exercised against the installed
Cursor desktop client and returned an available billing-cycle snapshot with an
included-usage amount, limit, percentage, window duration, and reset time. Live
values were observed only in process output and were not written to this
repository.

The adapter:

- Requires `--allow-private-cursor-session` before reading any credential.
- Opens only Cursor's `User/globalStorage/state.vscdb` in SQLite read-only and
  query-only modes.
- Selects only the exact `cursorAuth/accessToken` key; it does not read the
  refresh token, profile, conversations, prompts, or source code.
- Sends an empty protobuf request only to the official `api2.cursor.sh` host
  and does not follow redirects.
- Parses only billing-cycle, plan-usage, and spend-limit numeric fields from a
  size-bounded response.
- Does not print, persist, log, or include the token or raw response in errors.

This RPC is the interface used by the installed Cursor desktop application, but
it is undocumented. The adapter therefore remains experimental and disabled by
default; a Cursor schema or authentication change must fail closed.

## Pending adapters

- GitHub Copilot requires a separately scoped official API credential and was
  not detected in the current VS Code profile during research.
- Devin requires an official account/API capability before live verification.
- Antigravity does not yet have a reviewed scriptable usage interface.

These gaps do not block validation of the shared UI schema, but their adapters
must be verified before the UI claims live support for them.
