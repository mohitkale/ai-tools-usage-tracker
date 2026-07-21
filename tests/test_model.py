from __future__ import annotations

from datetime import UTC, datetime
import unittest

from ai_usage_tracker.model import (
    DataSource,
    ProviderSnapshot,
    QuotaWindow,
    SnapshotStatus,
)


class ModelTests(unittest.TestCase):
    def test_available_snapshot_requires_usage_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            ProviderSnapshot(
                provider_id="test",
                display_name="Test",
                status=SnapshotStatus.AVAILABLE,
                source=DataSource.FIXTURE,
                collected_at=datetime.now(UTC),
            )

    def test_serialized_model_contains_only_normalized_fields(self) -> None:
        snapshot = ProviderSnapshot(
            provider_id="test",
            display_name="Test",
            status=SnapshotStatus.AVAILABLE,
            source=DataSource.FIXTURE,
            collected_at=datetime(2026, 7, 22, tzinfo=UTC),
            windows=(
                QuotaWindow(
                    id="daily",
                    label="Daily",
                    unit="requests",
                    used=2,
                    limit=10,
                    remaining=8,
                    used_percent=20,
                    window_seconds=86400,
                ),
            ),
        )
        self.assertEqual(
            set(snapshot.to_dict()),
            {
                "schema_version",
                "provider_id",
                "display_name",
                "status",
                "source",
                "collected_at",
                "windows",
                "error_code",
                "message",
            },
        )


if __name__ == "__main__":
    unittest.main()

