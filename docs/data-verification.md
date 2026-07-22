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
status-line summary. It does not retain the incoming payload. The widget can add
the capture command after confirmation. The installer uses an atomic user-only
file update, preserves unrelated settings, and refuses to replace an existing
status line or follow a settings symlink.


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
Cursor desktop client and returned an available billing-cycle snapshot with a
total-usage amount, Cursor's total/Auto/API percentages, window duration, and
reset time. Live
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

### GitHub Copilot

The adapter calls GitHub's personal premium-request billing endpoint through
the official `gh` process. It needs a `gh` login with read access to the user's
Plan data. The current machine has `gh` installed but not signed in, so the
adapter was parser/process tested and correctly fails closed until login.

### Devin

The installed Devin desktop app was verified to cache a normalized plan record
containing daily/weekly quota values and included usage counters. The adapter
selects that exact record only, ignores all text/account metadata, and marks old
cache data as cached in the widget.

### Antigravity

The installed Antigravity app was verified to cache an exact model-credit
record separately from its OAuth record. The adapter decodes only the available
credit integer and marks old cache data as cached. Antigravity's documented
baseline model-quota view remains interactive and is not scraped.
