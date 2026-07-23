# Security and release-readiness audit

Audit date: 2026-07-23

## Executive decision

**Source publication: go.** No critical or high-severity code finding remains
open after the remediations in this review. The source is licensed under
Apache-2.0, third-party licenses have been inventoried, and the destination is
the public `mohitkale/ai-tools-usage-tracker` repository.

**Public binary release: no-go.** Windows and Ubuntu package jobs must first
pass on GitHub-hosted runners. Release artifacts must then be signed, checksummed,
and accompanied by an SBOM and hash-locked build inputs. CI artifacts are test
outputs, not endorsed release binaries.

The macOS DMG builder creates an ad-hoc-signed tester artifact with a checksum,
SPDX SBOM, and runtime notices. It is suitable for controlled testing, but it
does not change the public-release decision until Developer ID signing and
Apple notarization are configured.

## Scope

The review covered:

- All Python source, tests, scripts, provider declarations, documentation, and
  CI configuration.
- Provider filesystem, process, credential, and network access.
- Default-deny behavior and settings persistence.
- Error rendering, redaction, normalized storage, and cache parsing.
- macOS behavior observed locally and Windows/Ubuntu code paths exercised by
  platform-specific tests and CI definitions.
- Tracked, untracked publishable files, and Git patch history for common secret
  formats.

## Verified controls

- Every provider is disabled by default and omitted from the main UI when
  disabled.
- Only Cursor performs a direct network request. The destination is fixed to
  `api2.cursor.sh:443`, TLS uses the system trust store, redirects are not
  followed, and the response is size- and type-bounded.
- Codex is executed directly with a fixed argument array, a minimal environment,
  analytics disabled, no shell, bounded stdout, and a deadline.
- Claude receives its payload through the official status-line stdin contract.
  Raw status JSON is not retained.
- Copilot, Devin, and Antigravity databases are opened read-only/query-only and
  queried only for their exact numeric usage records.
- Provider database symlinks are rejected. Environment-supplied directories
  are ignored unless absolute.
- App-owned settings and snapshots are size-bounded, atomically replaced, reject
  symlinks/non-regular files, and use user-only POSIX permissions.
- Persisted snapshots are reconstructed through the strict normalized model on
  read. Extra fields, invalid types, booleans-as-numbers, excessive windows, and
  unbounded text fail closed.
- Unexpected errors are replaced with static UI text; provider payloads, paths,
  and raw exceptions are not rendered.
- The runtime uses only the Python standard library. PyInstaller is build-only,
  and its documented bundling exception permits generated applications under
  Apache-2.0. The reviewed inventory is in `THIRD_PARTY_NOTICES.md`.
- GitHub Actions have read-only repository permission, do not persist checkout
  credentials, pin third-party actions to immutable commits, and apply timeouts.

## Findings remediated in this audit

| Severity | Finding | Resolution |
| --- | --- | --- |
| High | Claude setup could render a raw `OSError`, potentially exposing a local path | Replaced with static allowlisted UI guidance |
| Medium | Disabled providers remained visible and forced a scroll container | Render only enabled providers; content-driven height; no scrollbar |
| Medium | Windows Claude hook could fail when PowerShell parsed a quoted executable path | Invoke PowerShell explicitly with escaped fixed arguments |
| Medium | Provider manifest silently ignored declared filesystem paths | Parse, validate, expose, test, and audit exact path declarations |
| Medium | Copilot, Devin, and Antigravity undocumented databases were classified as official payloads | Reclassified as default-disabled private experimental state |
| Medium | Cursor database followed a symlink | Reject symlink before read-only resolution |
| Medium | Relative environment directories could redirect local reads or executable discovery | Accept environment directories only when absolute |
| Medium | Snapshot loading checked only top-level keys | Reconstruct and validate the complete normalized model |
| Low | Redaction did not recognize provider-specific compound token keys or several common token formats | Expanded key canonicalization and token patterns |
| Low | No repeatable repository/history publication scan | Added a zero-dependency fail-closed audit and CI gate |

## Residual risks and release gates

| Risk | Severity | Treatment |
| --- | --- | --- |
| Cursor requires an existing bearer token to call an undocumented private RPC | Medium | Explicit opt-in, official-host pinning, ephemeral reference, private-experimental label |
| Copilot, Devin, and Antigravity cache schemas can change without notice | Medium | Exact queries, strict parsers, fail closed, default disabled |
| Codex app-server is documented as experimental | Low | Exact method, bounded protocol, fail closed |
| Claude Desktop Chat/free-tier account limits have no reviewed local source | Product limitation | Clearly disclose; do not scrape conversations, cookies, or credentials |
| Windows/Ubuntu packages have not yet executed in repository CI | Medium | Publishing source may proceed; claiming target validation or releasing binaries may not |
| Build dependency is version-pinned but not hash-locked and no SBOM is generated | Medium | Block public binary releases until release workflow is added |
| Binaries are not signed/notarized | High for binary distribution | Do not publish binaries until platform signing is configured |
| Platform bundles require complete notices for their exact collected native components | Medium | Do not publish binaries until notices and SBOM are generated and verified per platform |

## Reproduction

```bash
python3 scripts/security_audit.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
python3 scripts/usage_probe.py --pretty permissions
```

Expected result: repository audit `pass`, all tests green, compilation succeeds,
and every provider permission remains default-deny.
