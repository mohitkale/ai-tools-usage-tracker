from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from ai_usage_tracker.widget_settings import WidgetSettings, WidgetSettingsStore


class WidgetSettingsStoreTests(unittest.TestCase):
    def test_defaults_do_not_enable_any_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = WidgetSettingsStore(Path(directory)).load()
        self.assertEqual(settings.enabled_providers, frozenset())
        self.assertTrue(settings.always_on_top)

    def test_round_trip_contains_only_non_sensitive_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WidgetSettingsStore(Path(directory))
            expected = WidgetSettings(
                enabled_providers=frozenset({"codex", "cursor"}),
                refresh_minutes=10,
                always_on_top=False,
            )
            path = store.save(expected)
            document = json.loads(path.read_bytes())

            self.assertEqual(store.load(), expected)
            self.assertEqual(
                set(document),
                {
                    "schema_version",
                    "enabled_providers",
                    "refresh_minutes",
                    "always_on_top",
                },
            )
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_unknown_provider(self) -> None:
        with self.assertRaises(ValueError):
            WidgetSettings(enabled_providers=frozenset({"unknown"}))

    def test_rejects_symlinked_settings(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink behavior requires additional Windows privileges")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensitive = root / "sensitive.json"
            sensitive.write_text("{}", encoding="utf-8")
            (root / "widget-settings.json").symlink_to(sensitive)
            with self.assertRaises(ValueError):
                WidgetSettingsStore(root).load()


if __name__ == "__main__":
    unittest.main()
