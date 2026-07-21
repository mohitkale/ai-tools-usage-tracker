from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import unittest
from unittest.mock import patch

from ai_usage_tracker.model import DataSource, SnapshotStatus
from ai_usage_tracker.providers.codex import _minimal_environment, parse_rate_limits_result


class CodexRateLimitParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 22, tzinfo=UTC)

    def test_normalizes_windows_and_discards_account_metadata(self) -> None:
        result = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 17,
                    "windowDurationMins": 10080,
                    "resetsAt": 1785110400,
                },
                "secondary": {
                    "usedPercent": 22,
                    "windowDurationMins": 300,
                    "resetsAt": 1784689200,
                },
                "planType": "CANARY_SECRET_PLAN",
                "credits": {"balance": "CANARY_SECRET_BALANCE"},
            },
            "rateLimitResetCredits": {
                "availableCount": 1,
                "credits": [{"id": "CANARY_SECRET_CREDIT_ID"}],
            },
        }

        snapshot = parse_rate_limits_result(result, collected_at=self.now)

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.source, DataSource.OFFICIAL_LOCAL_PROCESS)
        self.assertEqual([window.label for window in snapshot.windows], ["7 day", "5 hour"])
        self.assertEqual(snapshot.windows[0].used_percent, 17)
        serialized = json.dumps(snapshot.to_dict())
        self.assertNotIn("CANARY_SECRET", serialized)
        self.assertNotIn("planType", serialized)
        self.assertNotIn("rateLimitResetCredits", serialized)

    def test_missing_snapshot_returns_no_data(self) -> None:
        snapshot = parse_rate_limits_result({}, collected_at=self.now)
        self.assertEqual(snapshot.status, SnapshotStatus.NO_DATA)

    def test_rejects_invalid_window_duration(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_rate_limits_result(
                {
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 1,
                            "windowDurationMins": 0,
                        }
                    }
                },
                collected_at=self.now,
            )

    def test_child_environment_excludes_authentication_variables(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HOME": "/safe/home",
                "PATH": "/safe/bin",
                "CODEX_ACCESS_TOKEN": "CANARY_SECRET_CODEX_ACCESS",
                "OPENAI_API_KEY": "CANARY_SECRET_OPENAI_KEY",
            },
            clear=True,
        ):
            environment = _minimal_environment()

        self.assertEqual(environment["HOME"], "/safe/home")
        self.assertNotIn("CODEX_ACCESS_TOKEN", environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("CANARY_SECRET", json.dumps(environment))


if __name__ == "__main__":
    unittest.main()
