from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ai_usage_tracker.model import DataSource, SnapshotStatus
from ai_usage_tracker.providers.github_copilot import (
    GitHubCopilotProbeError,
    read_copilot_cli_usage,
    safe_error_guidance,
    safe_error_status,
)


class GitHubCopilotLocalUsageTests(unittest.TestCase):
    @staticmethod
    def _create_database(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE assistant_usage_events ("
                "total_nano_aiu INTEGER NOT NULL, "
                "created_at TEXT NOT NULL, "
                "prompt TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO assistant_usage_events VALUES (?, ?, ?)",
                (
                    (
                        600_025_000,
                        "2026-07-23T13:27:59.625Z",
                        "CANARY_SECRET_PROMPT",
                    ),
                    (
                        399_975_000,
                        "2026-07-23T13:29:00.000Z",
                        "CANARY_SECRET_SECOND_PROMPT",
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_reads_only_aggregate_local_ai_credits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "session-store.db"
            self._create_database(database)

            snapshot = read_copilot_cli_usage(database)

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.source, DataSource.OFFICIAL_LOCAL_PAYLOAD)
        self.assertEqual(snapshot.windows[0].used, 1)
        self.assertEqual(
            snapshot.collected_at.isoformat(), "2026-07-23T13:29:00+00:00"
        )
        self.assertNotIn("CANARY", json.dumps(snapshot.to_dict()))

    def test_missing_database_is_waiting_for_local_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = read_copilot_cli_usage(
                Path(directory) / "session-store.db"
            )

        self.assertEqual(snapshot.status, SnapshotStatus.NO_DATA)
        self.assertEqual(snapshot.windows, ())

    def test_symlinked_database_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "real.db"
            link = root / "session-store.db"
            self._create_database(database)
            link.symlink_to(database)

            with self.assertRaisesRegex(
                GitHubCopilotProbeError, "local state is a symlink"
            ):
                read_copilot_cli_usage(link)

    def test_error_guidance_uses_only_reviewed_exact_messages(self) -> None:
        known = safe_error_guidance(
            GitHubCopilotProbeError(
                "Copilot CLI local usage could not be read safely"
            )
        )
        unknown = safe_error_guidance(
            GitHubCopilotProbeError("CANARY_SECRET provider output")
        )

        self.assertIn("read-only", known)
        self.assertNotIn("CANARY", unknown)
        self.assertEqual(
            safe_error_status(
                GitHubCopilotProbeError(
                    "Copilot CLI local usage could not be read safely"
                )
            ),
            "Local data unavailable",
        )


if __name__ == "__main__":
    unittest.main()
