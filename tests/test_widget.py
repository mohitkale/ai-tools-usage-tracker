from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from ai_usage_tracker.model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus
from ai_usage_tracker.providers.github_copilot import GitHubCopilotProbeError
from ai_usage_tracker.widget import (
    PROVIDER_ORDER,
    ProviderCollector,
    ProviderDisplay,
    UsageWidget,
    WindowDisplay,
    disabled_display,
    display_from_snapshot,
    error_display,
    planned_display,
)


class WidgetFormattingTests(unittest.TestCase):
    def test_progress_width_maps_percentage_exactly(self) -> None:
        self.assertEqual(UsageWidget._progress_fill_width(400, 76), 304)
        self.assertEqual(UsageWidget._progress_fill_width(400, -10), 0)
        self.assertEqual(UsageWidget._progress_fill_width(400, 150), 400)

    def test_compact_summary_reports_remaining_percentage(self) -> None:
        display = ProviderDisplay(
            "codex",
            "Codex",
            "available",
            "Live",
            (WindowDisplay("7 day", 76, "76% used", None),),
        )

        self.assertEqual(UsageWidget._compact_summary(display), "24% left")

    def test_compact_window_is_materially_narrower(self) -> None:
        widget = UsageWidget.__new__(UsageWidget)
        widget.compact_mode = False
        self.assertEqual(
            widget._current_window_width(),
            UsageWidget.DETAIL_WINDOW_WIDTH,
        )

        widget.compact_mode = True
        self.assertEqual(
            widget._current_window_width(),
            UsageWidget.COMPACT_WINDOW_WIDTH,
        )
        self.assertLess(
            UsageWidget.COMPACT_WINDOW_WIDTH,
            UsageWidget.DETAIL_WINDOW_WIDTH - 100,
        )

    def test_retry_bypasses_global_cooldown_and_shows_immediate_feedback(self) -> None:
        scheduled = []
        updates = []
        widget = UsageWidget.__new__(UsageWidget)
        widget.closed = False
        widget.settings = SimpleNamespace(enabled_providers={"github_copilot"})
        widget.in_progress = set()
        widget.displays = {"github_copilot": error_display("github_copilot")}
        widget.updated_text = SimpleNamespace(set=updates.append)
        widget.root = SimpleNamespace(
            after=lambda delay, callback: scheduled.append((delay, callback)),
            update_idletasks=lambda: None,
        )

        with patch.object(UsageWidget, "_render_cards") as render:
            widget.retry_provider("github_copilot")

        self.assertEqual(widget.displays["github_copilot"].status, "loading")
        self.assertIn("github_copilot", widget.in_progress)
        self.assertEqual(updates, ["Retrying GitHub Copilot…"])
        self.assertEqual(scheduled[0][0], UsageWidget.RETRY_FEEDBACK_DELAY_MS)
        render.assert_called_once_with()

    def test_only_enabled_providers_are_visible_in_provider_order(self) -> None:
        visible = UsageWidget._visible_provider_ids(
            frozenset({"github_copilot", "cursor", "devin"})
        )

        self.assertEqual(visible, ("cursor", "devin", "github_copilot"))

    def test_manual_refresh_cooldown_avoids_duplicate_collection(self) -> None:
        widget = UsageWidget.__new__(UsageWidget)
        widget.closed = False
        widget.last_refresh_started = time.monotonic()
        widget.settings = SimpleNamespace(enabled_providers={"cursor"})
        widget.in_progress = set()
        widget.displays = {}

        widget.refresh_all()

        self.assertEqual(widget.in_progress, set())

    def test_formats_cursor_money_percentage_and_local_reset(self) -> None:
        snapshot = ProviderSnapshot(
            provider_id="cursor",
            display_name="Cursor",
            status=SnapshotStatus.AVAILABLE,
            source=DataSource.PRIVATE_PROVIDER_API,
            collected_at=datetime.now(UTC),
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

        self.assertEqual(display.status_text, "Live API")
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
        self.assertEqual(display.display_name, "Claude")
        self.assertEqual(display.status_text, "Unavailable")
        self.assertNotIn("CANARY", repr(display))

    def test_disabled_provider_has_no_access_side_effect(self) -> None:
        display = disabled_display("codex")
        self.assertEqual(display.status, "ready")
        self.assertEqual(display.status_text, "Off")

    def test_all_requested_providers_have_visible_list_entries(self) -> None:
        self.assertEqual(
            set(PROVIDER_ORDER),
            {
                "claude",
                "codex",
                "cursor",
                "github_copilot",
                "devin",
                "antigravity",
            },
        )
        self.assertEqual(PROVIDER_ORDER[-1], "github_copilot")
        self.assertEqual(disabled_display("github_copilot").status_text, "Off")


class WidgetCollectorTests(unittest.TestCase):
    def test_known_github_failure_returns_static_safe_guidance(self) -> None:
        with patch(
            "ai_usage_tracker.widget.read_copilot_cli_usage",
            side_effect=GitHubCopilotProbeError(
                "Copilot CLI local usage could not be read safely"
            ),
        ):
            display = ProviderCollector().collect("github_copilot")

        self.assertEqual(display.status, "error")
        self.assertEqual(display.status_text, "Local data unavailable")
        self.assertEqual(
            display.detail,
            "The Copilot CLI usage database could not be opened read-only.",
        )

    def test_unexpected_github_failure_does_not_reach_the_ui(self) -> None:
        with patch(
            "ai_usage_tracker.widget.read_copilot_cli_usage",
            side_effect=RuntimeError("CANARY_SECRET provider output"),
        ):
            display = ProviderCollector().collect("github_copilot")

        self.assertNotIn("CANARY", repr(display))

    def test_missing_claude_snapshot_is_nonfatal(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "ai_usage_tracker.widget.claude_status_line_state",
                return_value="absent",
            ):
                display = ProviderCollector(Path(directory)).collect("claude")
        self.assertEqual(display.status, "no_data")
        self.assertEqual(display.status_text, "Code setup required")


if __name__ == "__main__":
    unittest.main()
