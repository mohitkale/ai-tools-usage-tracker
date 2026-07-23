# Third-party notices

This source project has no third-party Python runtime package dependency. It
uses modules shipped with Python, including Tkinter/Tcl/Tk, SQLite, and the TLS
implementation supplied by the Python distribution.

The following inventory was reviewed for the Apache-2.0 source release on
2026-07-23. It is informational and does not replace the upstream license
texts.

| Component | Role | License | Distribution note |
| --- | --- | --- | --- |
| Python | Runtime | Python Software Foundation License Version 2 and incorporated-component licenses | Compatible with an Apache-2.0 application; a packaged binary must include Python's applicable notices |
| Tcl/Tk | Tkinter GUI runtime | BSD-style Tcl/Tk license | Permits use and redistribution when its copyright and license notice are retained |
| SQLite | Local read-only database access | Public domain | No license restriction on the deliverable SQLite library |
| OpenSSL 3.x | TLS used by Python | Apache-2.0 | Compatible; the exact binary distribution determines whether OpenSSL is bundled or supplied by the OS |
| PyInstaller 6.21.0 | Build tool | GPL-2.0-or-later with PyInstaller's bundling exception; selected files Apache-2.0 | The exception permits generated bundles to use this project's license |
| pyinstaller-hooks-contrib 2026.6 | Transitive build tool | Standard hooks GPL-2.0-or-later; bundled runtime hooks Apache-2.0 | Standard hooks run only during the build; runtime hooks that can enter bundles are Apache-2.0 |
| altgraph 0.17.5 | Transitive build tool | MIT | Build-time only |
| macholib 1.16.4 | Transitive macOS build tool | MIT | Build-time only |
| packaging 26.2 | Transitive build tool | Apache-2.0 OR BSD-2-Clause | Build-time only |
| setuptools 83.0.0 | Source build backend | MIT | Build-time only |
| pip 26.1.2 | Local installation tool | MIT | Not a project dependency or bundled runtime |

No provider SDK, provider logo, font, image, or copied provider source code is
included in this repository.

## Binary-release requirement

The repository's CI may build and smoke-test platform bundles, but those
outputs are not release artifacts. Before publishing a binary, generate a
platform-specific software bill of materials and ship the complete license and
copyright files for the exact Python, Tcl/Tk, OpenSSL, and other native
components collected on that build runner. The binary-release gate in
`docs/security-audit.md` remains closed until that work, signing, checksums, and
hash-locked build inputs are complete.

Upstream license references:

- Python: <https://docs.python.org/3/license.html>
- Tcl/Tk: <https://www.tcl-lang.org/software/tcltk/license.html>
- SQLite: <https://www.sqlite.org/copyright.html>
- OpenSSL: <https://openssl-library.org/source/license/index.html>
- PyInstaller: <https://pyinstaller.org/en/stable/license.html>
- PyInstaller community hooks:
  <https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/v2026.6/LICENSE>
