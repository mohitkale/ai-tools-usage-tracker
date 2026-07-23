from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ai_usage_tracker.model import DataSource, SnapshotStatus
from ai_usage_tracker.providers.devin import DEVIN_PLAN_KEY, parse_cached_plan, read_devin_usage


class DevinUsageTests(unittest.TestCase):
    def test_normalizes_quota_and_included_usage_without_strings(self) -> None:
        payload = json.dumps(
            {
                "planName": "CANARY_PRIVATE_PLAN",
                "quotaUsage": {
                    "dailyRemainingPercent": 75,
                    "dailyResetAtUnix": 1784937600,
                    "weeklyRemainingPercent": 40,
                    "weeklyResetAtUnix": 1785369600,
                },
                "usage": {
                    "messages": 2500,
                    "usedMessages": 250,
                    "flowActions": 500,
                    "usedFlowActions": 25,
                    "flexCredits": 0,
                    "usedFlexCredits": 0,
                },
            }
        ).encode()
        snapshot = parse_cached_plan(
            payload, collected_at=datetime(2026, 7, 23, tzinfo=UTC)
        )

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.source, DataSource.PRIVATE_LOCAL_STATE)
        self.assertEqual(snapshot.windows[0].used_percent, 25)
        self.assertEqual(snapshot.windows[1].used_percent, 60)
        self.assertEqual(snapshot.windows[2].used, 250)
        self.assertNotIn("CANARY", json.dumps(snapshot.to_dict()))

    def test_reads_only_exact_cached_plan_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.vscdb"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value TEXT)")
            connection.execute(
                "INSERT INTO ItemTable VALUES (?, ?)",
                (DEVIN_PLAN_KEY, '{"usage":{"messages":10,"usedMessages":2}}'),
            )
            connection.execute(
                "INSERT INTO ItemTable VALUES (?, ?)",
                ("secret://devin-auth", "CANARY_SECRET_TOKEN"),
            )
            connection.commit()
            connection.close()

            snapshot = read_devin_usage(database)

        self.assertEqual(snapshot.windows[0].used, 2)
        self.assertNotIn("CANARY", json.dumps(snapshot.to_dict()))
