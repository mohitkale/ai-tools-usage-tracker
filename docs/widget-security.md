# Widget security boundary

## Default behavior

The desktop widget starts with every provider disabled. Before the user enables
a provider, opening the widget does not read a provider database or credential,
start a provider process, or make a provider network request.

The Settings dialog and the per-card Connect confirmation are the permission
boundary. Both explain an adapter's access before enabling it and store only
these non-sensitive preferences:

- Enabled provider identifiers.
- Refresh interval.
- Always-on-top preference.
- Settings schema version.

The settings file is size-bounded, rejects symlinks and non-regular files, and
uses user-only directory and file permissions on POSIX systems. It never stores
tokens, cookies, account details, usage responses, paths, or provider payloads.

## Refresh behavior

Enabled collectors run on background daemon threads. UI updates pass only the
normalized `ProviderSnapshot` fields through an in-memory queue. Refreshes do
not persist Cursor, Codex, GitHub, Devin, or Antigravity responses. Claude uses
the already-normalized local snapshot produced by its explicit status-line
capture hook.

Unexpected collector exceptions are replaced with a generic `Needs attention`
status. Exception messages, response bodies, local paths, and account metadata
are not rendered. Disabling a provider prevents future scheduled collection;
results from an in-flight refresh are discarded if the provider was disabled
before it completed.

## Provider-specific access

- **Claude Code:** reads only the normalized app-owned snapshot. With explicit
  confirmation, the widget adds its capture command only when no status line
  exists; unrelated settings are preserved and raw status JSON is not retained.
- **Codex:** starts the reviewed official local app-server with analytics
  disabled. Codex retains control of its own authentication.
- **Cursor:** reads only `cursorAuth/accessToken` from Cursor's SQLite state in
  read-only/query-only mode and sends an empty usage request only to the pinned
  `api2.cursor.sh` endpoint.
- **GitHub Copilot:** starts the official `gh` process and requests the personal
  premium-request usage report. Authentication and GitHub network access remain
  inside `gh`; the widget does not receive its token or retain the username.
- **Devin:** selects only `windsurf.settings.cachedPlanInfo` from Devin's local
  SQLite database in read-only/query-only mode. Auth and session rows are not
  selected.
- **Antigravity:** selects only
  `antigravityUnifiedStateSync.modelCredits` from Antigravity's local SQLite
  database in read-only/query-only mode. Its separate OAuth row is not selected.

No adapter enumerates the operating-system keychain. The widget has no
telemetry, crash uploader, remote configuration, automatic update client, or
third-party service connection.

## Packaging boundary

The runtime application uses Python's standard library, including Tkinter,
SQLite, TLS, and HTTP support. PyInstaller is used only during packaging and is
not imported by the running application. Its default one-folder output makes
the bundled files easier to inventory than a self-extracting one-file build.

Builds are platform-specific. A public release still requires dependency hash
locks, artifact checksums, an SBOM, and the target platform's signing process.
