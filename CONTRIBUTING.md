# Contributing

Thank you for helping improve AI Tools Usage Tracker.

## Before opening a change

1. Read `SECURITY.md` and `docs/threat-model.md`.
2. Keep provider access default-deny and narrowly scoped.
3. Do not add real credentials, account data, prompts, provider payloads,
   copied databases, or screenshots containing personal data.
4. Do not add telemetry, analytics, remote configuration, or automatic
   updates.
5. Document whether every provider interface is official, experimental, or
   private.

## Development

Python 3.11 or newer with Tk support is required. The application runtime uses
only the Python standard library.

```bash
python3 scripts/usage_widget.py
```

Run all publication checks before submitting a pull request:

```bash
python3 scripts/security_audit.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
```

Tests must use synthetic canary credentials. Live-provider verification must be
explicit, local, and excluded from committed fixtures and logs.

## Provider changes

A new or changed provider must declare every path, process, argument,
credential reference, and network hostname it can access. Prefer an official
local process or provider-supported status hook. Undocumented state must be
labeled private/experimental and disabled by default.

The UI may show only normalized quota values and static allowlisted errors.
Never render raw exceptions or persist raw provider responses.

## Licensing

Contributions are accepted under the Apache License 2.0. Do not contribute code
or assets that cannot be redistributed under that license. Provider logos and
other trademarked assets require a separate, documented permission review.
