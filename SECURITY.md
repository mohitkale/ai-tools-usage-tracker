# Security policy

## Protected data

The tracker treats authentication tokens, cookies, API keys, keychain values,
session contents, prompts, source code, account identifiers, and raw provider
responses as sensitive.

## Non-negotiable rules

1. Do not enumerate the user's keychain, credential manager, secret service,
   home directory, browser storage, or editor databases.
2. Prefer an official authenticated local process or status hook over reading
   a credential file.
3. A provider adapter must declare every filesystem path, executable, argument,
   credential record, and outbound hostname it can access.
4. An adapter may access only the resources in its reviewed declaration.
5. Existing provider credentials must never be copied into tracker settings or
   its database.
6. App-owned credentials may be persisted only in the operating system's secure
   credential store, after explicit opt-in. There is no plaintext fallback.
7. A credential may be sent only to its official provider hostname. Cross-host
   redirects are forbidden.
8. Raw provider payloads are ephemeral. Persistent data is restricted to the
   normalized usage schema.
9. Logs, exceptions, diagnostics, exports, and crash data must be redacted.
10. Telemetry, analytics, remote configuration, and automatic updates are off.

## Permission levels

- `local_metadata`: installation and exact-path existence checks only.
- `local_process`: direct execution of a reviewed binary with fixed arguments.
- `delegated_provider_network`: an official local process may use its own
  authentication and network stack; the tracker never receives its credential.
- `local_payload`: usage payload received from a supported local hook or stdin.
- `provider_network`: HTTPS to an adapter's official-host allowlist.
- `credential_reference`: an exact named credential lookup; never enumeration.
- `private_state`: undocumented application state. Disabled by default.

Each elevation must be visible in the CLI or UI and require a deliberate user
action. Enabling one provider does not grant permission to another.

## Development and testing

- Tests use synthetic credentials containing recognizable canary markers.
- Tests fail if a canary appears in serialized state, logs, or diagnostics.
- Live provider tests are separate from unit tests and require explicit flags.
- Fixtures must not be derived from real accounts without complete redaction.
- `python3 scripts/security_audit.py` must pass before publication.
- CI must pass on macOS, Ubuntu, and Windows; binary release jobs must also pass
  their target-platform smoke tests.
- Dependency versions and hashes will be locked before distributable builds.

## Reporting a vulnerability

Do not include a real token, credential file, database, prompt, or session in a
report. Use GitHub's private vulnerability-reporting feature when it is
available for this repository. Otherwise, open a minimal issue requesting a
private contact channel without disclosing the vulnerability. Reproduce with a
synthetic canary and describe the affected adapter and data flow.
