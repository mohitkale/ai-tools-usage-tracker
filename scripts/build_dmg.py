#!/usr/bin/env python3
"""Build a self-contained, auditable macOS tester DMG."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"
APP_PATH = DIST_DIR / "AI Usage Tracker.app"
RELEASE_DIR = DIST_DIR / "releases"


class DmgBuildError(RuntimeError):
    """Raised when a release prerequisite cannot be verified."""


def _run(command: list[str], *, capture: bool = False) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DmgBuildError(f"Command failed: {command[0]}") from exc
    return completed.stdout.strip() if capture else ""


def _project_version() -> str:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as project_file:
        document = tomllib.load(project_file)
    version = document.get("project", {}).get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z.+-]+", version):
        raise DmgBuildError("The project version is invalid")
    return version


def _brew_prefix(formula: str) -> Path:
    prefix = Path(_run(["brew", "--prefix", formula], capture=True)).resolve()
    if not prefix.is_dir():
        raise DmgBuildError(f"Homebrew formula is unavailable: {formula}")
    return prefix


def _copy_first(
    prefix: Path,
    candidates: tuple[str, ...],
    destination: Path,
) -> None:
    for candidate in candidates:
        source = prefix / candidate
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, destination)
            return
    raise DmgBuildError(f"Required license file was not found under {prefix.name}")


def _copy_runtime_licenses(destination: Path) -> dict[str, str]:
    destination.mkdir()
    python_formula = f"python@{sys.version_info.major}.{sys.version_info.minor}"
    prefixes = {
        "python": _brew_prefix(python_formula),
        "tcl-tk": _brew_prefix("tcl-tk"),
        "openssl": _brew_prefix("openssl@3"),
        "libtommath": _brew_prefix("libtommath"),
        "mpdecimal": _brew_prefix("mpdecimal"),
        "xz": _brew_prefix("xz"),
        "zstd": _brew_prefix("zstd"),
        "sqlite": _brew_prefix("sqlite"),
    }
    _copy_first(prefixes["python"], ("LICENSE", "LICENSE.txt"), destination / "Python.txt")
    _copy_first(
        prefixes["tcl-tk"],
        ("license.terms", "LICENSE"),
        destination / "Tcl-Tk.txt",
    )
    _copy_first(
        prefixes["openssl"],
        ("LICENSE.txt", "LICENSE"),
        destination / "OpenSSL.txt",
    )
    _copy_first(
        prefixes["libtommath"],
        ("LICENSE", "LICENSE.txt"),
        destination / "LibTomMath.txt",
    )
    _copy_first(
        prefixes["mpdecimal"],
        ("COPYRIGHT.txt", "share/doc/mpdecimal/COPYRIGHT.txt"),
        destination / "mpdecimal.txt",
    )
    copied_xz = 0
    for name in (
        "COPYING",
        "COPYING.0BSD",
        "COPYING.GPLv2",
        "COPYING.GPLv3",
        "COPYING.LGPLv2.1",
    ):
        source = prefixes["xz"] / name
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, destination / f"XZ-{name}.txt")
            copied_xz += 1
    if copied_xz == 0:
        raise DmgBuildError("Required XZ license files were not found")
    copied_zstd = 0
    for name in ("LICENSE", "COPYING"):
        source = prefixes["zstd"] / name
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, destination / f"Zstandard-{name}.txt")
            copied_zstd += 1
    if copied_zstd == 0:
        raise DmgBuildError("Required Zstandard license files were not found")
    (destination / "SQLite.txt").write_text(
        "SQLite is in the public domain.\n"
        "Official declaration: https://www.sqlite.org/copyright.html\n",
        encoding="utf-8",
    )
    return {
        component: prefix.name
        for component, prefix in prefixes.items()
    }


def _spdx_package(
    identifier: str,
    name: str,
    version: str,
    license_id: str,
) -> dict[str, object]:
    return {
        "SPDXID": identifier,
        "name": name,
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": license_id,
        "licenseDeclared": license_id,
        "copyrightText": "NOASSERTION",
    }


def _write_sbom(destination: Path, version: str, versions: dict[str, str]) -> None:
    app_id = "SPDXRef-Package-AIUsageTracker"
    packages = [
        _spdx_package(app_id, "AI Tools Usage Tracker", version, "Apache-2.0"),
        _spdx_package(
            "SPDXRef-Package-Python",
            "Python",
            versions["python"],
            "Python-2.0",
        ),
        _spdx_package(
            "SPDXRef-Package-TclTk",
            "Tcl/Tk",
            versions["tcl-tk"],
            "TCL",
        ),
        _spdx_package(
            "SPDXRef-Package-OpenSSL",
            "OpenSSL",
            versions["openssl"],
            "Apache-2.0",
        ),
        _spdx_package(
            "SPDXRef-Package-LibTomMath",
            "LibTomMath",
            versions["libtommath"],
            "Unlicense",
        ),
        _spdx_package(
            "SPDXRef-Package-mpdecimal",
            "mpdecimal",
            versions["mpdecimal"],
            "BSD-2-Clause",
        ),
        _spdx_package(
            "SPDXRef-Package-XZ",
            "XZ Utils liblzma",
            versions["xz"],
            "NOASSERTION",
        ),
        _spdx_package(
            "SPDXRef-Package-Zstandard",
            "Zstandard",
            versions["zstd"],
            "BSD-3-Clause",
        ),
        _spdx_package(
            "SPDXRef-Package-SQLite",
            "SQLite",
            versions["sqlite"],
            "LicenseRef-Public-Domain",
        ),
    ]
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": app_id,
        }
    ]
    relationships.extend(
        {
            "spdxElementId": app_id,
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": package["SPDXID"],
        }
        for package in packages[1:]
    )
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"AI-Usage-Tracker-{version}-macOS",
        "documentNamespace": (
            "https://github.com/mohitkale/ai-tools-usage-tracker/"
            f"spdx/{version}/{uuid.uuid4()}"
        ),
        "creationInfo": {
            "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "creators": ["Tool: scripts/build_dmg.py"],
        },
        "packages": packages,
        "relationships": relationships,
        "hasExtractedLicensingInfos": [
            {
                "licenseId": "LicenseRef-Public-Domain",
                "extractedText": (
                    "SQLite is dedicated to the public domain. "
                    "See https://www.sqlite.org/copyright.html"
                ),
            }
        ],
    }
    destination.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a license-complete macOS tester DMG"
    )
    parser.add_argument(
        "--skip-app-build",
        action="store_true",
        help="package the existing app bundle instead of rebuilding it",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        print("DMG packaging must run on macOS.")
        return 2
    if shutil.which("hdiutil") is None or shutil.which("codesign") is None:
        print("DMG packaging requires the macOS hdiutil and codesign tools.")
        return 2
    try:
        if not args.skip_app_build:
            _run([sys.executable, str(PROJECT_ROOT / "scripts" / "build_app.py")])
        if not APP_PATH.is_dir() or APP_PATH.is_symlink():
            raise DmgBuildError("The application bundle is unavailable")
        _run(
            [
                "codesign",
                "--verify",
                "--deep",
                "--strict",
                str(APP_PATH),
            ]
        )
        version = _project_version()
        architecture = re.sub(r"[^0-9A-Za-z_-]", "-", platform.machine())
        RELEASE_DIR.mkdir(parents=True, exist_ok=True)
        dmg_path = (
            RELEASE_DIR
            / f"AI-Usage-Tracker-{version}-macOS-{architecture}.dmg"
        )
        with tempfile.TemporaryDirectory(prefix="ai-usage-tracker-dmg-") as temp:
            staging = Path(temp) / "Mohit's AI Usage Tracker"
            staging.mkdir()
            shutil.copytree(
                APP_PATH,
                staging / APP_PATH.name,
                symlinks=True,
            )
            os.symlink("/Applications", staging / "Applications")
            shutil.copy2(PROJECT_ROOT / "LICENSE", staging / "LICENSE.txt")
            shutil.copy2(
                PROJECT_ROOT / "THIRD_PARTY_NOTICES.md",
                staging / "THIRD_PARTY_NOTICES.md",
            )
            versions = _copy_runtime_licenses(staging / "Licenses")
            _write_sbom(staging / "SBOM.spdx.json", version, versions)
            (staging / "INSTALL.txt").write_text(
                "Drag AI Usage Tracker.app to Applications.\n\n"
                "This tester build has a valid ad-hoc signature but is not "
                "Apple-notarized. macOS may require a control-click followed "
                "by Open on first launch. Do not bypass a warning if the DMG "
                "checksum does not match the accompanying .sha256 file.\n",
                encoding="utf-8",
            )
            _run(
                [
                    "hdiutil",
                    "create",
                    "-volname",
                    "Mohit's AI Usage Tracker",
                    "-srcfolder",
                    str(staging),
                    "-ov",
                    "-format",
                    "UDZO",
                    str(dmg_path),
                ]
            )
        _run(["hdiutil", "verify", str(dmg_path)])
        checksum = _sha256(dmg_path)
        checksum_path = dmg_path.with_suffix(dmg_path.suffix + ".sha256")
        checksum_path.write_text(
            f"{checksum}  {dmg_path.name}\n",
            encoding="ascii",
        )
    except (DmgBuildError, OSError, ValueError) as exc:
        print(f"DMG build failed: {exc}")
        return 1

    print(f"DMG: {dmg_path}")
    print(f"SHA-256: {checksum}")
    print(f"Checksum file: {checksum_path}")
    print("Signing: ad-hoc only; not notarized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
