from __future__ import annotations

from datetime import UTC, datetime
import json
import unittest

from ai_usage_tracker.model import DataSource, SnapshotStatus
from ai_usage_tracker.providers.claude import MAX_STATUS_PAYLOAD_BYTES, parse_status_payload


class ClaudeStatusParserTests(unittest.TestCase):
    def test_free_tier_falls_back_to_session_context_usage(self) -> None:
        snapshot = parse_status_payload(
            b'{"context_window":{"used_percentage":37.5}}',
            collected_at=datetime(2026, 7, 22, tzinfo=UTC),
        )

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.windows[0].id, "session_context")
        self.assertEqual(snapshot.windows[0].used_percent, 37.5)

    def setUp(self) -> None:
        self.now = datetime(2026, 7, 22, tzinfo=UTC)

    def test_parses_supported_rate_limit_windows(self) -> None:
        payload = json.dumps(
            {
                "rate_limits": {
                    "five_hour": {"used_percentage": 25, "resets_at": 1784689200},
                    "seven_day": {"used_percentage": 50.5, "resets_at": 1785110400},
                },
                "session_id": "CANARY_SECRET_REAL_SHAPED_VALUE",
            }
        ).encode()

        snapshot = parse_status_payload(payload, collected_at=self.now)

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.source, DataSource.OFFICIAL_LOCAL_PAYLOAD)
        self.assertEqual([window.id for window in snapshot.windows], ["five_hour", "seven_day"])
        self.assertEqual(snapshot.windows[0].used_percent, 25)
        serialized = json.dumps(snapshot.to_dict())
        self.assertNotIn("CANARY_SECRET", serialized)
        self.assertNotIn("session_id", serialized)

    def test_absent_rate_limits_returns_no_data(self) -> None:
        snapshot = parse_status_payload(b"{}", collected_at=self.now)
        self.assertEqual(snapshot.status, SnapshotStatus.NO_DATA)
        self.assertEqual(snapshot.windows, ())

    def test_rejects_boolean_percentage(self) -> None:
        payload = b'{"rate_limits":{"five_hour":{"used_percentage":true}}}'
        with self.assertRaisesRegex(ValueError, "must be a number"):
            parse_status_payload(payload, collected_at=self.now)

    def test_rejects_percentage_above_one_hundred(self) -> None:
        payload = b'{"rate_limits":{"five_hour":{"used_percentage":101}}}'
        with self.assertRaisesRegex(ValueError, "cannot exceed 100"):
            parse_status_payload(payload, collected_at=self.now)

    def test_rejects_oversized_payload_before_parsing(self) -> None:
        payload = b"{" + (b" " * MAX_STATUS_PAYLOAD_BYTES) + b"}"
        with self.assertRaisesRegex(ValueError, "size limit"):
            parse_status_payload(payload, collected_at=self.now)


if __name__ == "__main__":
    unittest.main()
