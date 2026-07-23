from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_usage_tracker.providers.antigravity import default_antigravity_database
from ai_usage_tracker.providers.cursor import default_cursor_database
from ai_usage_tracker.providers.devin import default_devin_database
from ai_usage_tracker.providers.github_copilot import default_copilot_cli_database
from ai_usage_tracker.storage import default_data_dir


class PlatformPathTests(unittest.TestCase):
    def test_copilot_home_override_must_be_absolute(self) -> None:
        with patch.dict(os.environ, {"COPILOT_HOME": "relative-copilot"}):
            self.assertEqual(
                default_copilot_cli_database(),
                Path.home() / ".copilot" / "session-store.db",
            )

    @unittest.skipUnless(os.name == "nt", "Windows-specific path contract")
    def test_windows_provider_paths_use_roaming_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roaming = Path(directory).resolve()
            with patch.dict(os.environ, {"APPDATA": str(roaming)}):
                self.assertEqual(
                    default_cursor_database(),
                    roaming
                    / "Cursor"
                    / "User"
                    / "globalStorage"
                    / "state.vscdb",
                )
                self.assertEqual(
                    default_devin_database(),
                    roaming
                    / "Devin"
                    / "User"
                    / "globalStorage"
                    / "state.vscdb",
                )
                self.assertEqual(
                    default_antigravity_database(),
                    roaming
                    / "Antigravity"
                    / "User"
                    / "globalStorage"
                    / "state.vscdb",
                )

    @unittest.skipUnless(os.name == "nt", "Windows-specific path contract")
    def test_windows_app_data_uses_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory).resolve()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                self.assertEqual(
                    default_data_dir(),
                    local / "AI Usage Tracker",
                )


if __name__ == "__main__":
    unittest.main()
