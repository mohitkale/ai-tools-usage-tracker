# AI Tools Usage Tracker

A local-first, cross-platform usage aggregator for AI developer tools. The
project is currently building and validating a security-focused collector CLI
before any desktop UI is introduced.

## Current scope

- Detect supported tools without reading their credential files.
- Normalize quota and usage information into a provider-independent schema.
- Validate the schema with synthetic data and supported local interfaces.
- Make every filesystem, process, credential, and network permission explicit.

The project does **not** currently read credentials, enumerate an operating
system keychain, persist secrets, or send telemetry.

## Security defaults

- All providers are default-deny until enabled by the user.
- Undocumented or private integrations are disabled by default.
- The collector stores normalized usage values only.
- Network requests must use an adapter-specific official-host allowlist.
- Cross-host redirects are rejected.
- Logs and errors are tested against credential canaries.

See [SECURITY.md](SECURITY.md) and [docs/threat-model.md](docs/threat-model.md)
before adding a provider integration.

## Development status

No UI or packaging dependencies are installed yet. The initial collector is
intentionally based on the Python standard library so its behavior is easy to
inspect and test.

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

Current collector coverage and live-test boundaries are recorded in
[docs/data-verification.md](docs/data-verification.md). No live usage values are
committed to the repository.
