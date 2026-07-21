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

