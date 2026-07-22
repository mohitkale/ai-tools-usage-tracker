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
    UsageWidget,
    disabled_display,
    display_from_snapshot,
    error_display,
    planned_display,
)


class WidgetFormattingTests(unittest.TestCase):
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

    def test_scroll_bindtag_is_prioritized_for_every_card_widget(self) -> None:
        class FakeWidget:
            def __init__(self, children=()) -> None:
                self.tags = ("widget", "class", "all")
                self.children = children

            def bindtags(self, value=None):
                if value is not None:
                    self.tags = value
                return self.tags

            def winfo_children(self):
                return self.children

        child = FakeWidget()
        parent = FakeWidget((child,))
        widget = UsageWidget.__new__(UsageWidget)

        widget._bind_mousewheel_tree(parent)

        self.assertEqual(parent.tags[0], UsageWidget.SCROLL_BINDTAG)
        self.assertEqual(child.tags[0], UsageWidget.SCROLL_BINDTAG)

    def test_manual_refresh_cooldown_avoids_duplicate_collection(self) -> None:
        widget = UsageWidget.__new__(UsageWidget)
        widget.closed = False
        widget.last_refresh_started = time.monotonic()
        widget.settings = SimpleNamespace(enabled_providers={"cursor"})
        widget.in_progress = set()
        widget.displays = {}

        widget.refresh_all()

        self.assertEqual(widget.in_progress, set())

    def test_mouse_wheel_scrolls_when_pointer_is_over_child_content(self) -> None:
        calls = []
        widget = UsageWidget.__new__(UsageWidget)
        widget.cards_canvas = SimpleNamespace(
            yview_scroll=lambda units, mode: calls.append((units, mode))
        )

        result = widget._on_mousewheel(SimpleNamespace(delta=-1, num=None))

        self.assertEqual(result, "break")
        self.assertEqual(calls, [(1, "units")])

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
        self.assertEqual(display.display_name, "Claude Code")
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
        self.assertEqual(disabled_display("github_copilot").status_text, "Off")


class WidgetCollectorTests(unittest.TestCase):
    def test_known_github_failure_returns_static_safe_guidance(self) -> None:
        with patch(
            "ai_usage_tracker.widget.read_copilot_usage",
            side_effect=GitHubCopilotProbeError("GitHub CLI was not found"),
        ):
            display = ProviderCollector().collect("github_copilot")

        self.assertEqual(display.status, "error")
        self.assertEqual(display.detail, "Install GitHub CLI and sign in, then retry.")

    def test_github_sign_in_failure_has_an_explicit_status(self) -> None:
        with patch(
            "ai_usage_tracker.widget.read_copilot_usage",
            side_effect=GitHubCopilotProbeError("GitHub CLI is not signed in"),
        ):
            display = ProviderCollector().collect("github_copilot")

        self.assertEqual(display.status_text, "Sign-in required")
        self.assertIn("gh auth login", display.detail)

    def test_unexpected_github_failure_does_not_reach_the_ui(self) -> None:
        with patch(
            "ai_usage_tracker.widget.read_copilot_usage",
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
        self.assertEqual(display.status_text, "Setup required")


if __name__ == "__main__":
    unittest.main()
