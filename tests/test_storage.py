from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
import unittest

from ai_usage_tracker.model import DataSource, ProviderSnapshot, SnapshotStatus
from ai_usage_tracker.storage import SnapshotStore


class SnapshotStoreTests(unittest.TestCase):
    def test_round_trip_stores_only_redacted_normalized_document(self) -> None:
        snapshot = ProviderSnapshot(
            provider_id="claude",
            display_name="Claude Code",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.OFFICIAL_LOCAL_PAYLOAD,
            collected_at=datetime(2026, 7, 22, tzinfo=UTC),
            message="Bearer CANARY_SECRET_STORAGE_TOKEN",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            target = store.save(snapshot)
            document = store.load("claude")
            raw = target.read_text(encoding="utf-8")

            self.assertIsNotNone(document)
            self.assertNotIn("CANARY_SECRET", raw)
            self.assertIn("[REDACTED]", raw)
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)

    def test_rejects_provider_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            with self.assertRaisesRegex(ValueError, "provider identifier"):
                store.load("../auth")

    def test_rejects_symlinked_snapshot(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink creation is permission-dependent on Windows")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            snapshots.mkdir()
            sensitive = root / "sensitive.json"
            sensitive.write_text(json.dumps({"token": "CANARY_SECRET_SYMLINK"}))
            (snapshots / "claude.json").symlink_to(sensitive)

            with self.assertRaisesRegex(ValueError, "symlinked"):
                SnapshotStore(root).load("claude")

            self.assertIn("CANARY_SECRET_SYMLINK", sensitive.read_text())

    def test_rejects_symlinked_snapshot_directory(self) -> None:
        if os.name == "nt":
            self.skipTest("symlink creation is permission-dependent on Windows")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (root / "snapshots").symlink_to(elsewhere, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "snapshot directory"):
                SnapshotStore(root).load("claude")

    def test_rejects_non_regular_snapshot(self) -> None:
        if os.name == "nt" or not hasattr(os, "mkfifo"):
            self.skipTest("FIFO behavior is POSIX-specific")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / "snapshots"
            snapshots.mkdir()
            os.mkfifo(snapshots / "claude.json")

            with self.assertRaisesRegex(ValueError, "regular file"):
                SnapshotStore(root).load("claude")


if __name__ == "__main__":
    unittest.main()
