# Roadmap

This roadmap communicates direction, not a promise of delivery dates. Provider
work proceeds only when an integration can meet the project's security model.

## Now: source-preview feedback

- [x] Cross-platform test suite on macOS, Windows, and Ubuntu
- [x] Windows and Ubuntu package smoke tests
- [x] Default-deny provider permissions
- [x] Offline synthetic demo mode
- [x] Product screenshots and a detailed-to-compact-to-settings demo
- [ ] Clean-machine source testing on all supported platforms
- [ ] Redacted setup diagnostics for compatibility reports
- [ ] Accessibility and keyboard-navigation review

## Next: first public binary release

- [ ] Hash-lock build inputs
- [ ] Generate and verify an SBOM for every release platform
- [ ] Complete per-platform runtime notices
- [ ] Sign Windows builds
- [ ] Sign and notarize macOS builds
- [ ] Validate a distributable Ubuntu package
- [ ] Automate checksums and release provenance
- [ ] Test installation, first launch, upgrade, and removal on clean systems

## Later: validated product improvements

- [ ] Document a stable provider-adapter interface
- [ ] Improve provider health and setup diagnostics
- [ ] Review high-DPI behavior on Windows and Linux
- [ ] Improve compact-mode accessibility
- [ ] Evaluate additional providers requested by the community

New provider requests should identify an official API, CLI, local process, or
documented hook when possible. The project will not add unsafe credential
scraping merely to increase the provider count.
