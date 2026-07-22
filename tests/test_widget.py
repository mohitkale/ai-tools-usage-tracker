from __future__ import annotations

from datetime import UTC, datetime
import unittest

from ai_usage_tracker.model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus
from ai_usage_tracker.widget import (
    ProviderCollector,
    disabled_display,
    display_from_snapshot,
)


class WidgetFormattingTests(unittest.TestCase):
    def test_formats_cursor_money_percentage_and_local_reset(self) -> None:
        snapshot = ProviderSnapshot(
            provider_id="cursor",
            display_name="Cursor",
            status=SnapshotStatus.AVAILABLE,
            source=DataSource.PRIVATE_PROVIDER_API,
            collected_at=datetime(2026, 7, 22, tzinfo=UTC),
            windows=(
                QuotaWindow(
                    id="billing_cycle",
                    label="Included usage",
                    unit="currency_cents",
                    used=500,
                    limit=2000,
                    remaining=1500,
                    used_percent=25,
                    resets_at=datetime(2026, 8, 1, tzinfo=UTC),
                ),
            ),
        )

        display = display_from_snapshot(snapshot)

        self.assertEqual(display.status_text, "Live")
        self.assertEqual(display.windows[0].amount_text, "$5.00 of $20.00")
        self.assertEqual(display.windows[0].used_percent, 25)
        self.assertTrue(display.windows[0].reset_text.startswith("Resets "))

    def test_ignores_provider_message_content(self) -> None:
        display = display_from_snapshot(
            {
                "provider_id": "claude",
                "display_name": "CANARY_SECRET_NAME",
                "status": "error",
                "message": "CANARY_SECRET_MESSAGE",
                "windows": [],
            }
        )
        self.assertEqual(display.display_name, "Claude Code")
        self.assertEqual(display.status_text, "Unavailable")
        self.assertNotIn("CANARY", repr(display))

    def test_disabled_provider_has_no_access_side_effect(self) -> None:
        display = disabled_display("codex")
        self.assertEqual(display.status, "disabled")
        self.assertEqual(display.status_text, "Not enabled")


class WidgetCollectorTests(unittest.TestCase):
    def test_missing_claude_snapshot_is_nonfatal(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            display = ProviderCollector(Path(directory)).collect("claude")
        self.assertEqual(display.status, "no_data")
        self.assertEqual(display.status_text, "Waiting for status snapshot")


if __name__ == "__main__":
    unittest.main()
