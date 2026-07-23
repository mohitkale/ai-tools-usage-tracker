from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from urllib.parse import quote

from ..model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus, utc_now
from ..security import absolute_environment_path


DEVIN_PLAN_KEY = "windsurf.settings.cachedPlanInfo"
MAX_PLAN_BYTES = 128 * 1024


class DevinProbeError(RuntimeError):
    """A deliberately non-sensitive Devin local-state failure."""


def default_devin_database() -> Path:
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Devin"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )
    if os.name == "nt":
        base = absolute_environment_path("APPDATA")
        if base is None:
            base = Path.home() / "AppData" / "Roaming"
        return base / "Devin" / "User" / "globalStorage" / "state.vscdb"
    base = absolute_environment_path("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / "Devin" / "User" / "globalStorage" / "state.vscdb"


def _read_cached_plan(database: Path) -> bytes:
    if database.is_symlink():
        raise DevinProbeError("Devin's local state database is a symlink")
    try:
        resolved = database.expanduser().resolve(strict=True)
    except OSError as exc:
        raise DevinProbeError("Devin's local usage cache was not found") from exc
    if not resolved.is_file():
        raise DevinProbeError("Devin's local usage cache was not found")
    uri = f"file:{quote(str(resolved), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
        try:
            connection.execute("PRAGMA query_only = ON")
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1", (DEVIN_PLAN_KEY,)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise DevinProbeError("Devin's local usage cache could not be read safely") from exc
    if row is None or not isinstance(row[0], str):
        raise DevinProbeError("Devin has not cached plan usage yet")
    payload = row[0].encode("utf-8")
    if len(payload) > MAX_PLAN_BYTES:
        raise DevinProbeError("Devin's local usage cache exceeded the size limit")
    return payload


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if result < 0:
        raise ValueError(f"{field} cannot be negative")
    return result


def _optional_number(value: Any, field: str) -> float | None:
    return None if value is None else _number(value, field)


def _reset(value: Any, field: str) -> datetime | None:
    timestamp = _optional_number(value, field)
    if not timestamp:
        return None
    if timestamp > 100_000_000_000:
        timestamp /= 1000
    try:
        result = datetime.fromtimestamp(timestamp, UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    return result if 2000 <= result.year <= 2100 else None


def parse_cached_plan(
    payload: bytes,
    *,
    collected_at: datetime | None = None,
) -> ProviderSnapshot:
    if len(payload) > MAX_PLAN_BYTES:
        raise ValueError("Devin plan payload exceeds the size limit")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Devin plan payload is invalid") from exc
    if not isinstance(document, dict):
        raise ValueError("Devin plan payload must be an object")

    now = collected_at or utc_now()
    windows: list[QuotaWindow] = []
    quota = document.get("quotaUsage")
    if quota is not None:
        if not isinstance(quota, dict):
            raise ValueError("Devin quota usage is invalid")
        for window_id, label, remaining_key, reset_key, seconds in (
            ("daily", "Daily quota", "dailyRemainingPercent", "dailyResetAtUnix", 86400),
            (
                "weekly",
                "Weekly quota",
                "weeklyRemainingPercent",
                "weeklyResetAtUnix",
                7 * 86400,
            ),
        ):
            remaining = _optional_number(quota.get(remaining_key), remaining_key)
            if remaining is None:
                continue
            resets_at = _reset(quota.get(reset_key), reset_key)
            if resets_at is not None and resets_at <= utc_now():
                resets_at = None
            windows.append(
                QuotaWindow(
                    id=window_id,
                    label=label,
                    unit="percent",
                    remaining=min(remaining, 100),
                    used_percent=max(0, 100 - min(remaining, 100)),
                    window_seconds=seconds,
                    resets_at=resets_at,
                )
            )

    usage = document.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            raise ValueError("Devin included usage is invalid")
        for window_id, label, used_key, limit_key in (
            ("messages", "Included messages", "usedMessages", "messages"),
            ("flow_actions", "Included flow actions", "usedFlowActions", "flowActions"),
            ("flex_credits", "Flex credits", "usedFlexCredits", "flexCredits"),
        ):
            used = _optional_number(usage.get(used_key), used_key)
            limit = _optional_number(usage.get(limit_key), limit_key)
            if used is None or limit is None or limit <= 0:
                continue
            windows.append(
                QuotaWindow(
                    id=window_id,
                    label=label,
                    unit="count",
                    used=used,
                    limit=limit,
                    remaining=max(limit - used, 0),
                    used_percent=min(used / limit * 100, 100),
                )
            )

    if not windows:
        return ProviderSnapshot(
            provider_id="devin",
            display_name="Devin",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.PRIVATE_LOCAL_STATE,
            collected_at=now,
            message="Devin has not cached supported plan usage.",
        )
    return ProviderSnapshot(
        provider_id="devin",
        display_name="Devin",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.PRIVATE_LOCAL_STATE,
        collected_at=now,
        windows=tuple(windows),
    )


def read_devin_usage(database: Path | None = None) -> ProviderSnapshot:
    source = database or default_devin_database()
    payload = _read_cached_plan(source)
    collected_at = datetime.fromtimestamp(source.expanduser().resolve().stat().st_mtime, UTC)
    return parse_cached_plan(payload, collected_at=collected_at)
