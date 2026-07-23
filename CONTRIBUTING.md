# Contributing

Thank you for helping improve AI Tools Usage Tracker. Bug reports, platform
testing, documentation, accessibility improvements, and carefully reviewed
provider work are all valuable.

## Before opening a change

1. Read [SECURITY.md](SECURITY.md) and
   [docs/threat-model.md](docs/threat-model.md).
2. Keep provider access explicit, narrowly scoped, and default-deny.
3. Use synthetic data in tests, screenshots, examples, and diagnostics.
4. Never submit credentials, account data, prompts, raw provider payloads,
   copied databases, or unredacted screenshots.
5. Do not add telemetry, analytics, remote configuration, or automatic updates.
6. Label every provider interface as official, experimental, or private.

## Development setup

Python 3.11 or newer with Tk support is required. The application runtime uses
only the Python standard library.

```bash
git clone https://github.com/mohitkale/ai-tools-usage-tracker.git
cd ai-tools-usage-tracker
python3 scripts/usage_widget.py --demo
```

Demo mode is the safest way to work on the interface. It uses in-memory
synthetic data and cannot access providers or persist settings.

## Repository structure

| Path | Purpose |
| --- | --- |
| `src/ai_usage_tracker/` | Collector, normalized model, storage, and widget |
| `src/ai_usage_tracker/providers/` | Provider-specific adapters and parsers |
| `config/providers.json` | Reviewed provider permissions and resources |
| `scripts/` | Source launchers, packaging, and security audit |
| `tests/` | Synthetic unit and contract tests |
| `docs/` | Security, verification, capture, and maintainer documentation |

## Run the checks

```bash
python3 scripts/security_audit.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
```

Run these before opening a pull request. Live-provider verification must be
explicit, local, and excluded from committed fixtures and logs.

## Provider changes

A new or changed provider must declare every path, process, argument,
credential reference, and network hostname it can access. Prefer an official
local process or provider-supported status hook. Undocumented state must be
labeled private/experimental and disabled by default.

The UI may show only normalized quota values and static allowlisted errors.
Never render raw exceptions or persist raw provider responses.

Provider pull requests should include:

- a link to public interface documentation when one exists;
- an updated `config/providers.json` declaration;
- strict size and type bounds;
- synthetic parser and permission tests;
- user-facing limitations and freshness behavior; and
- confirmation that unrelated records, credentials, and payload fields are
  never selected or retained.

## UI and documentation changes

- Use `python3 scripts/usage_widget.py --demo` for UI work.
- Keep screenshots synthetic and display the demo footer.
- Include meaningful alt text for new images.
- Preserve accurate provider limitations.
- Documentation-only pull requests do not need live-provider testing.

## Pull requests

- Keep each pull request focused.
- Explain what changed, why it is needed, and how it was verified.
- Update documentation when behavior, permissions, or data sources change.
- Do not mix unrelated formatting or refactors into a provider change.
- Confirm that the security audit, tests, and compilation checks pass.

Issues labeled `good first issue` should be independently reproducible, avoid
credential access, and include clear acceptance criteria.

## Licensing

Contributions are accepted under the Apache License 2.0. Do not contribute code
or assets that cannot be redistributed under that license. Provider logos and
other trademarked assets require a separate documented permission review.
