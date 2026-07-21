#!/usr/bin/env python3
"""Run the collector from a source checkout without installing dependencies."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from ai_usage_tracker.cli import main  # noqa: E402


raise SystemExit(main())

