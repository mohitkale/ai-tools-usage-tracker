from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

from ..model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus, utc_now


MAX_STATUS_PAYLOAD_BYTES = 1024 * 1024


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _reset_time(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    timestamp = _number(value, field)
    if timestamp < 0:
        raise ValueError(f"{field} cannot be negative")
    return datetime.fromtimestamp(timestamp, tz=UTC)


def parse_status_payload(
    payload: bytes,
    *,
    source: DataSource = DataSource.OFFICIAL_LOCAL_PAYLOAD,
    collected_at: datetime | None = None,
) -> ProviderSnapshot:
    if len(payload) > MAX_STATUS_PAYLOAD_BYTES:
        raise ValueError("Claude status payload exceeds the size limit")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Claude status payload is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("Claude status payload must be a JSON object")

    rate_limits = document.get("rate_limits")
    now = collected_at or utc_now()
    if rate_limits is not None and not isinstance(rate_limits, dict):
        raise ValueError("Claude rate_limits must be an object")

    definitions = (
        ("five_hour", "5 hour", 5 * 60 * 60),
        ("seven_day", "7 day", 7 * 24 * 60 * 60),
    )
    windows: list[QuotaWindow] = []
    for window_id, label, seconds in definitions:
        raw_window = rate_limits.get(window_id) if isinstance(rate_limits, dict) else None
        if raw_window is None:
            continue
        if not isinstance(raw_window, dict):
            raise ValueError(f"Claude {window_id} rate limit must be an object")
        percentage = _number(
            raw_window.get("used_percentage"),
            f"rate_limits.{window_id}.used_percentage",
        )
        windows.append(
            QuotaWindow(
                id=window_id,
                label=label,
                unit="percent",
                used_percent=percentage,
                window_seconds=seconds,
                resets_at=_reset_time(
                    raw_window.get("resets_at"),
                    f"rate_limits.{window_id}.resets_at",
                ),
            )
        )

    if not windows:
        context_window = document.get("context_window")
        if context_window is not None and not isinstance(context_window, dict):
            raise ValueError("Claude context_window must be an object")
        context_percentage = (
            context_window.get("used_percentage")
            if isinstance(context_window, dict)
            else None
        )
        if context_percentage is not None:
            windows.append(
                QuotaWindow(
                    id="session_context",
                    label="Session context",
                    unit="percent",
                    used_percent=_number(
                        context_percentage,
                        "context_window.used_percentage",
                    ),
                )
            )

    if not windows:
        return ProviderSnapshot(
            provider_id="claude",
            display_name="Claude Code",
            status=SnapshotStatus.NO_DATA,
            source=source,
            collected_at=now,
            message="Claude supplied no subscription limits or session-context usage.",
        )
    return ProviderSnapshot(
        provider_id="claude",
        display_name="Claude Code",
        status=SnapshotStatus.AVAILABLE,
        source=source,
        collected_at=now,
        windows=tuple(windows),
    )
