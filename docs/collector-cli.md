# Collector CLI

The collector uses only the Python standard library so its behavior remains
easy to inspect and test.

## Credential-free commands

Run these directly from a source checkout:

```bash
python3 scripts/usage_probe.py --pretty permissions
python3 scripts/usage_probe.py --pretty discover
python3 scripts/usage_probe.py --pretty fixture
python3 scripts/usage_probe.py --pretty verify-fixtures
```

The `fixture` command emits synthetic snapshots for every supported provider.
It does not access the filesystem or network.

## Claude Code

Parse an official Claude Code status-line payload from stdin:

```bash
claude-status-command | python3 scripts/usage_probe.py --pretty claude-status
```

The widget can configure its capture command after explicit confirmation. It
preserves unrelated Claude settings and refuses to replace an existing
different status line.

To use the capture command directly:

```bash
python3 scripts/usage_probe.py claude-capture
```

It writes only the normalized latest snapshot to the user's standard
application-data directory and never stores the raw stdin payload. Read that
snapshot with:

```bash
python3 scripts/usage_probe.py --pretty snapshot --provider claude
```

## Codex

Query the official local Codex app-server:

```bash
python3 scripts/usage_probe.py --pretty codex-live --allow-official-process
```

The consent flag is mandatory. Codex uses its own saved login and official
provider connection; the tracker does not receive the credential. Analytics
are disabled for the child process.

A non-standard Codex installation can be selected explicitly:

```bash
python3 scripts/usage_probe.py --pretty codex-live \
  --allow-official-process \
  --executable /trusted/path/to/codex
```

## Cursor

Cursor individual usage has no public API. The experimental probe can read
exactly `cursorAuth/accessToken` from Cursor's SQLite state in read-only mode
and send it only to Cursor's pinned usage RPC:

```bash
python3 scripts/usage_probe.py --pretty cursor-live \
  --allow-private-cursor-session
```

The token is not printed, persisted, logged, or included in errors. The
interface is undocumented and can stop working after a Cursor update.

Current adapter boundaries are documented in
[data-verification.md](data-verification.md).
