#!/usr/bin/env python3
"""Build a native bundle for the current operating system with PyInstaller."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINT = PROJECT_ROOT / "scripts" / "usage_widget.py"
SOURCE_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package AI Usage Tracker for the current operating system"
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="produce one executable instead of the default inspectable one-folder bundle",
    )
    args = parser.parse_args()

    if sys.version_info < (3, 11):
        print("Packaging requires Python 3.11 or newer.")
        return 2
    from ai_usage_tracker.widget import WidgetRuntimeError, load_tk_modules

    try:
        load_tk_modules()
    except WidgetRuntimeError:
        print("Packaging requires the Tk module matching this Python installation.")
        return 2
    os.environ.setdefault(
        "PYINSTALLER_CONFIG_DIR", str(PROJECT_ROOT / ".cache" / "pyinstaller")
    )
    try:
        import PyInstaller.__main__
    except ImportError:
        print(
            "PyInstaller is not installed. Run: "
            "python3 -m pip install -r requirements-build.txt"
        )
        return 2

    mode = "--onefile" if args.onefile else "--onedir"
    options = [
        str(ENTRY_POINT),
        mode,
        "--windowed",
        "--disable-windowed-traceback",
        "--noupx",
        "--clean",
        "--noconfirm",
        "--name",
        "AI Usage Tracker",
        "--paths",
        str(SOURCE_ROOT),
        "--distpath",
        str(PROJECT_ROOT / "dist"),
        "--workpath",
        str(PROJECT_ROOT / "build"),
        "--specpath",
        str(PROJECT_ROOT / "build"),
    ]
    if sys.platform == "darwin":
        options.extend(
            [
                "--osx-bundle-identifier",
                "com.mohitkale.ai-usage-tracker",
            ]
        )
    PyInstaller.__main__.run(options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
