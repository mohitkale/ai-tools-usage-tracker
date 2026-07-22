from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ai_usage_tracker.model import DataSource, SnapshotStatus
from ai_usage_tracker.providers.github_copilot import (
    _read_login,
    parse_premium_request_usage,
    read_copilot_usage,
)


class GitHubCopilotParserTests(unittest.TestCase):
    def test_normalizes_only_copilot_request_totals(self) -> None:
        payload = json.dumps(
            {
                "timePeriod": {"year": 2026, "month": 7},
                "user": "CANARY_ACCOUNT_NAME",
                "usageItems": [
                    {
                        "product": "Copilot",
                        "unitType": "requests",
                        "grossQuantity": 12,
                        "model": "CANARY_MODEL_DETAIL",
                    },
                    {
                        "product": "Copilot",
                        "unitType": "requests",
                        "grossQuantity": 3.5,
                    },
                    {
                        "product": "Actions",
                        "unitType": "minutes",
                        "grossQuantity": 999,
                    },
                ],
            }
        ).encode()

        snapshot = parse_premium_request_usage(
            payload, collected_at=datetime(2026, 7, 23, tzinfo=UTC)
        )

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.source, DataSource.OFFICIAL_LOCAL_PROCESS)
        self.assertEqual(snapshot.windows[0].used, 15.5)
        self.assertEqual(snapshot.windows[0].resets_at, datetime(2026, 8, 1, tzinfo=UTC))
        serialized = json.dumps(snapshot.to_dict())
        self.assertNotIn("CANARY", serialized)

    def test_empty_month_is_no_data(self) -> None:
        snapshot = parse_premium_request_usage(
            b'{"usageItems":[]}', collected_at=datetime(2026, 7, 23, tzinfo=UTC)
        )
        self.assertEqual(snapshot.status, SnapshotStatus.NO_DATA)


class GitHubCopilotProcessTests(unittest.TestCase):
    def test_validates_login_before_using_it_in_a_path(self) -> None:
        with patch(
            "ai_usage_tracker.providers.github_copilot._run_gh",
            return_value=b"bad/login\n",
        ):
            with self.assertRaises(Exception):
                _read_login(Path("/usr/bin/gh"))

    def test_delegates_authentication_to_gh(self) -> None:
        responses = [
            b"octocat\n",
            b'{"usageItems":[{"product":"Copilot","unitType":"requests","grossQuantity":4}]}',
        ]
        with patch(
            "ai_usage_tracker.providers.github_copilot._run_gh",
            side_effect=responses,
        ) as runner:
            snapshot = read_copilot_usage(
                Path("/usr/bin/gh"),
                collected_at=datetime(2026, 7, 23, tzinfo=UTC),
            )

        self.assertEqual(snapshot.windows[0].used, 4)
        flattened = " ".join(argument for call in runner.call_args_list for argument in call.args[1])
        self.assertNotIn("token", flattened.casefold())
        self.assertIn("users/octocat/settings/billing/premium_request/usage", flattened)
