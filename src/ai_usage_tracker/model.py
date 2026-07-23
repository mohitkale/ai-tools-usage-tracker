from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import math
from typing import Any


class SnapshotStatus(StrEnum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class DataSource(StrEnum):
    FIXTURE = "fixture"
    OFFICIAL_LOCAL_PAYLOAD = "official_local_payload"
    OFFICIAL_LOCAL_PROCESS = "official_local_process"
    OFFICIAL_PROVIDER_API = "official_provider_api"
    PRIVATE_LOCAL_STATE = "private_local_state"
    PRIVATE_PROVIDER_API = "private_provider_api"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso8601(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _finite_number(name: str, value: float | None) -> None:
    if value is not None and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    id: str
    label: str
    unit: str
    used: float | None = None
    limit: float | None = None
    remaining: float | None = None
    used_percent: float | None = None
    window_seconds: int | None = None
    resets_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.label or not self.unit:
            raise ValueError("quota id, label, and unit are required")
        for name in ("used", "limit", "remaining", "used_percent"):
            value = getattr(self, name)
            _finite_number(name, value)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.used_percent is not None and self.used_percent > 100:
            raise ValueError("used_percent cannot exceed 100")
        if self.window_seconds is not None and self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        _iso8601(self.resets_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "unit": self.unit,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "used_percent": self.used_percent,
            "window_seconds": self.window_seconds,
            "resets_at": _iso8601(self.resets_at),
        }


@dataclass(frozen=True, slots=True)
class ProviderSnapshot:
    provider_id: str
    display_name: str
    status: SnapshotStatus
    source: DataSource
    collected_at: datetime
    windows: tuple[QuotaWindow, ...] = ()
    error_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if not self.provider_id or not self.display_name:
            raise ValueError("provider id and display name are required")
        _iso8601(self.collected_at)
        if self.status == SnapshotStatus.AVAILABLE and not self.windows:
            raise ValueError("available snapshots require at least one quota window")
        if self.status != SnapshotStatus.AVAILABLE and self.windows:
            raise ValueError("non-available snapshots cannot contain quota windows")
        if self.status == SnapshotStatus.ERROR and not self.error_code:
            raise ValueError("error snapshots require an error code")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "status": self.status.value,
            "source": self.source.value,
            "collected_at": _iso8601(self.collected_at),
            "windows": [window.to_dict() for window in self.windows],
            "error_code": self.error_code,
            "message": self.message,
        }
