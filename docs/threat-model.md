# Threat model

## Goal

Display useful AI-tool quota information without becoming a credential
aggregator or creating a new path to prompts, code, sessions, or account data.

## Trust boundaries

The trusted computing base is limited to the local Python collector, reviewed
provider adapters, the operating system credential service, and TLS connections
to explicitly allowlisted official provider hosts.

The following inputs are untrusted:

- Provider JSON and API responses.
- Editor databases and cache files.
- Executable discovery results and environment variables.
- Command output from provider CLIs.
- Local configuration and future UI messages.
- Dependency installation and release artifacts.

## Primary threats and controls

| Threat | Control |
| --- | --- |
| Credential exfiltration | Default-deny network policy and per-provider official-host allowlists |
| Secret persistence | Normalized schema allowlist; no raw-response storage |
| Keychain overreach | Exact service/account lookups only; no enumeration |
| Malicious redirect | Reject redirects that change scheme, host, or port |
| Command injection | Provider processes use fixed argument arrays with `shell=False`; the consented Claude status hook writes one fixed, escaped command for Claude Code to invoke |
| Cache schema changes | Versioned parsers, size limits, strict types, fail closed |
| Sensitive diagnostics | Structured redaction and canary tests |
| Supply-chain compromise | Locked hashes, vendored release inputs, SBOM, minimal dependencies |
| Local privilege expansion | No root, accessibility, screen recording, or full-disk permissions |
| Undocumented API breakage | Experimental adapters disabled by default and isolated from stable adapters |
| Malicious environment paths | Environment-provided directories are accepted only when absolute |
| Repository secret exposure | Local audit scans publishable files and Git history without printing matched values |

## Data retained

The persistent model may contain only:

- Provider identifier and non-sensitive display label.
- Usage amount, unit, limit, and percentage.
- Window duration and reset timestamp.
- Collection timestamp, freshness, and source classification.
- Non-sensitive adapter status and error code.

It must not contain raw headers, response bodies, tokens, cookies, email
addresses, prompts, repository paths, session identifiers, or conversation
content.

## Security review gate for an adapter

An adapter cannot become enabled-by-default until tests prove its resource
declaration, outbound host enforcement, response size limit, parser behavior,
redaction, and persistent output. Undocumented integrations cannot pass this
gate; they remain explicit experiments.
