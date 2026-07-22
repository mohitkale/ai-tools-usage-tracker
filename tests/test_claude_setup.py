from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ai_usage_tracker.providers.claude_setup import (
    ClaudeSetupError,
    claude_status_line_state,
    format_status_command,
    install_claude_status_line,
)


class ClaudeSetupTests(unittest.TestCase):
    def test_installs_capture_without_changing_unrelated_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".claude" / "settings.json"
            path.parent.mkdir()
            path.write_text(
                json.dumps({"permissions": {"allow": ["Read"]}, "theme": "dark"}),
                encoding="utf-8",
            )

            changed = install_claude_status_line(
                ("/Applications/AI Usage Tracker", "--claude-capture"), path
            )
            document = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(changed)
            self.assertEqual(document["permissions"], {"allow": ["Read"]})
            self.assertEqual(document["theme"], "dark")
            self.assertEqual(document["statusLine"]["type"], "command")
            self.assertEqual(
                document["statusLine"]["command"],
                format_status_command(
                    ("/Applications/AI Usage Tracker", "--claude-capture")
                ),
            )
            self.assertEqual(
                claude_status_line_state(
                    ("/Applications/AI Usage Tracker", "--claude-capture"), path
                ),
                "installed",
            )

    def test_never_overwrites_an_existing_status_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {"statusLine": {"type": "command", "command": "my-status"}}
            path.write_text(json.dumps(original), encoding="utf-8")

            with self.assertRaises(ClaudeSetupError):
                install_claude_status_line(("tracker", "--claude-capture"), path)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_refuses_a_symlinked_settings_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "settings.json"
            link.symlink_to(target)

            with self.assertRaises(ClaudeSetupError):
                install_claude_status_line(("tracker", "--claude-capture"), link)
