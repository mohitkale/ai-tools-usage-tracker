from __future__ import annotations

from typing import Any, Iterable

from .model import ProviderSnapshot, SnapshotStatus


def verify_ui_readiness(snapshots: Iterable[ProviderSnapshot]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    all_ready = True
    count = 0
    for snapshot in snapshots:
        count += 1
        serialized = snapshot.to_dict()
        base_fields = all(
            serialized.get(field) is not None
            for field in (
                "provider_id",
                "display_name",
                "status",
                "source",
                "collected_at",
            )
        )
        available = snapshot.status == SnapshotStatus.AVAILABLE
        measures = [
            window.used_percent is not None
            or (window.used is not None and window.limit is not None)
            for window in snapshot.windows
        ]
        windows_ready = (not available and not snapshot.windows) or (
            available and bool(snapshot.windows) and all(measures)
        )
        ready = base_fields and windows_ready
        all_ready = all_ready and ready
        results.append(
            {
                "provider_id": snapshot.provider_id,
                "ready": ready,
                "window_count": len(snapshot.windows),
                "has_percentage": any(
                    window.used_percent is not None for window in snapshot.windows
                ),
                "has_reset_time": any(
                    window.resets_at is not None for window in snapshot.windows
                ),
                "has_window_duration": any(
                    window.window_seconds is not None for window in snapshot.windows
                ),
            }
        )
    return {
        "schema_version": 1,
        "ui_ready": all_ready and count > 0,
        "snapshot_count": count,
        "providers": results,
    }

