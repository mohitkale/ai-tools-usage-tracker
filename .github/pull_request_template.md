## What changed?

<!-- Describe the focused change. -->

## Why is this needed?

<!-- Describe the user, maintainer, or security outcome. -->

## Security checklist

- [ ] No credential, cookie, token, prompt, raw provider payload, or personal usage data is committed.
- [ ] Provider permissions remain explicit and default-deny.
- [ ] New filesystem paths, processes, arguments, and hosts are documented.
- [ ] Tests and product assets use synthetic data.
- [ ] Errors remain static and redacted.
- [ ] Documentation reflects the current behavior and limitations.

## Verification

- [ ] `python3 scripts/security_audit.py`
- [ ] `PYTHONPATH=src python3 -m unittest discover -s tests -v`
- [ ] `python3 -m compileall -q src scripts tests`

Tested on:

- [ ] macOS
- [ ] Windows
- [ ] Linux
