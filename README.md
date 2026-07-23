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
provider process, or make a network request. Open **Settings** and review each
provider's permission description before enabling it. Enabled providers refresh
in background threads so the window remains responsive. Disabled providers are
removed from the main screen, and the window automatically fits the selected
cards without a scrollbar. The always-on-top behavior and refresh interval are
configurable.

The source launcher requires Python 3.11 or newer with Tk support. Official
Windows Python installers normally include Tk. Ubuntu users can install the
distribution's `python3-tk` package; Homebrew Python users can install the
matching `python-tk@<version>` formula. Packaged builds include Python and Tk.

### Provider coverage

| Provider | Data shown | Source and freshness | Important limitation |
| --- | --- | --- | --- |
| Cursor | Total spend, total/Auto/API percentages, reset time | Live request to Cursor's pinned desktop usage RPC | Undocumented private interface; reads one existing access token after explicit opt-in |
| Codex | Rolling quota percentages and reset times | Live official local `codex app-server` process | App-server interface is documented as experimental |
| Claude Code | 5-hour/7-day limits or session context | Event-driven official status-line payload | Requires Claude Code; Claude Desktop Chat and the free Claude.ai plan do not expose this source |
| GitHub Copilot CLI | Aggregate AI credits consumed on this machine | Undocumented local CLI event database | Not the remaining account allowance |
| Devin | Daily/weekly quota and included usage | Undocumented local normalized cache | Fresh only when Devin updates its cache |
| Antigravity | Available AI credits | Undocumented local model-credit cache | Fresh only when Antigravity updates its cache |

Local cache cards are marked **Cached** after 30 minutes. Every provider remains
disabled until it is explicitly connected.

Claude Code supplies 5-hour and 7-day subscription limits only for eligible
Claude.ai subscribers after an API response. When rate limits are absent, the
hook can display Claude Code's current session-context percentage without
misrepresenting it as an account allowance. Claude Code itself requires a
supported paid or Console/API-backed account; the free Claude.ai plan does not
include it. See Anthropic's
[setup](https://code.claude.com/docs/en/getting-started) and
[status-line](https://code.claude.com/docs/en/statusline) documentation.

Claude Desktop Chat does not publish this status-line payload or a reviewed
local quota interface. Its card therefore says **Claude Code only** instead of
implying that a Desktop prompt should update it. The tracker does not inspect
Claude Desktop conversations, cookies, or credentials.

Copilot CLI records exact per-request AI-credit values in its own local event
database. The tracker totals only that numeric column and shows usage generated
on this machine. This is not an account balance: Copilot CLI does not currently
offer a reviewed non-interactive interface for retrieving an individual plan's
remaining allowance. The CLI itself supports Windows, macOS, and Linux; its
documented user configuration root is `~/.copilot` or `COPILOT_HOME`.

The header's minus button switches to a compact view with one balance summary
per provider; the plus button restores the detailed cards. Native window
minimize remains available, including Command-M on macOS.

Its exact local data flow and retained settings are documented in
[docs/widget-security.md](docs/widget-security.md).

## Provider names and logos

Provider names are used only to identify the services this independent utility
connects to. The project is not affiliated with, endorsed by, or sponsored by
the listed providers. Official provider logos are intentionally not bundled:
their trademark permissions differ by vendor and are not granted by this
project's eventual open-source code license. The compact letter marks in the UI
are neutral project-owned identifiers, not reproductions of provider logos.

Before accepting a branded asset, maintainers should record its official source,
applicable usage terms, required attribution, and whether redistribution inside
source and binary releases is permitted. Plain-text provider names remain the
safe default.

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
notarization remain a release step. CI runs the full suite on all three systems
and packages/smoke-tests Windows and Ubuntu bundles. A green Windows CI job is
required before calling a commit Windows-validated.

## Security verification

Run the local publication checks before every push:

```bash
python3 scripts/security_audit.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
```

The repository audit examines tracked and untracked publishable files, scans
Git history for common credential formats without printing matches, rejects
sensitive filenames and repository symlinks, validates provider permissions,
and verifies that the application has no third-party runtime dependency.
The latest CISO-style review and remaining release gates are recorded in
[docs/security-audit.md](docs/security-audit.md).

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

The widget's Claude card can configure that official status-line command after
an explicit confirmation. It preserves all unrelated settings and refuses to
overwrite an existing different status line.

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
