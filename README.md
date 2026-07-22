# AI Tools Usage Tracker

A local-first, cross-platform usage aggregator for AI developer tools. It now
includes a compact always-on-top Python widget backed by the security-focused
collector.

## Current scope

- Detect supported tools without reading their credential files.
- Normalize quota and usage information into a provider-independent schema.
- Validate the schema with synthetic data and supported local interfaces.
- Make every filesystem, process, credential, and network permission explicit.

The project does **not** enumerate an operating-system keychain, persist
secrets, or send telemetry. The experimental Cursor probe reads one exact
session record only after explicit command-line consent.

## Security defaults

- All providers are default-deny until enabled by the user.
- Undocumented or private integrations are disabled by default.
- The collector stores normalized usage values only.
- Network requests must use an adapter-specific official-host allowlist.
- Cross-host redirects are rejected.
- Logs and errors are tested against credential canaries.

See [SECURITY.md](SECURITY.md) and [docs/threat-model.md](docs/threat-model.md)
before adding a provider integration.

## Desktop widget

Launch the widget from a source checkout:

```bash
python3 scripts/usage_widget.py
```

The first launch is default-deny: it does not read a provider session, start a
provider process, or make a network request. Open **Settings**, review each
provider's permission description, and explicitly enable only the collectors
you want. Enabled providers refresh in background threads so the window remains
responsive. The always-on-top behavior and refresh interval are configurable.

The source launcher requires Python 3.11 or newer with Tk support. Official
Windows Python installers normally include Tk. Ubuntu users can install the
distribution's `python3-tk` package; Homebrew Python users can install the
matching `python-tk@<version>` formula. Packaged builds include Python and Tk.

The widget currently displays:

- Cursor included usage, limit, percentage, remaining amount, and reset time.
- Codex rate-limit windows, percentages, and reset times.
- Claude Code windows captured by the existing official status-line hook.

Its exact local data flow and retained settings are documented in
[docs/widget-security.md](docs/widget-security.md).

## Packaging

PyInstaller is a pinned build-only dependency; the application itself has no
third-party Python runtime dependency. Build on the operating system and CPU
architecture you intend to distribute for:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python scripts/build_app.py
```

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python`. The default
output is an inspectable one-folder bundle under `dist/`; pass `--onefile` for a
single executable. PyInstaller does not cross-compile, so Windows, Ubuntu, and
macOS artifacts must each be produced on that target platform. Signing and
notarization remain a release step.

## Collector CLI

The collector is intentionally based on the Python standard library so its
behavior is easy to inspect and test.

Run the credential-free probes directly from a checkout:

```bash
python3 scripts/usage_probe.py --pretty permissions
python3 scripts/usage_probe.py --pretty discover
python3 scripts/usage_probe.py --pretty fixture
python3 scripts/usage_probe.py --pretty verify-fixtures
```

The `fixture` command emits synthetic quota data in the exact schema intended
for the future UI. To parse a real Claude status-line payload without reading a
credential or making a network request:

```bash
claude-status-command | python3 scripts/usage_probe.py --pretty claude-status
```

Configuring that official status-line command is intentionally left as a
separate, explicit user action.

For a future UI to consume Claude updates, use the capture command as the
status-line target:

```bash
python3 scripts/usage_probe.py claude-capture
```

It writes only the normalized latest snapshot to the current user's standard
application-data directory. It never stores the raw stdin payload. Read it with:

```bash
python3 scripts/usage_probe.py --pretty snapshot --provider claude
```

Codex can be queried through its official local app-server. This starts the
Codex binary with analytics disabled; Codex may use its own saved login and
official provider connection, but the probe never receives the credential:

```bash
python3 scripts/usage_probe.py --pretty codex-live --allow-official-process
```

The consent flag is mandatory so discovery alone can never start an
authenticated process.

The probe checks the standard Codex CLI install directory and the Codex binary
bundled with ChatGPT, in addition to `PATH`. A non-standard installation can be
selected explicitly:

```bash
python3 scripts/usage_probe.py --pretty codex-live \
  --allow-official-process \
  --executable /trusted/path/to/codex
```

Cursor individual usage has no public API. An experimental, default-disabled
probe can read exactly `cursorAuth/accessToken` from Cursor's SQLite state in
read-only mode and send it only to Cursor's own desktop usage RPC at
`https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage`:

```bash
python3 scripts/usage_probe.py --pretty cursor-live \
  --allow-private-cursor-session
```

The token is not printed, persisted, logged, or included in errors. Because the
interface is undocumented, it may stop working after a Cursor update.

Current collector coverage and live-test boundaries are recorded in
[docs/data-verification.md](docs/data-verification.md). No live usage values are
committed to the repository.
