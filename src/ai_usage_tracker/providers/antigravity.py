from __future__ import annotations

import base64
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterator
from urllib.parse import quote

from ..model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus, utc_now
from ..security import absolute_environment_path


MODEL_CREDITS_KEY = "antigravityUnifiedStateSync.modelCredits"
AVAILABLE_CREDITS_KEY = "availableCreditsSentinelKey"
MAX_STATE_CHARACTERS = 32 * 1024
MAX_PROTOBUF_FIELDS = 64


class AntigravityProbeError(RuntimeError):
    """A deliberately non-sensitive Antigravity local-state failure."""


def default_antigravity_database() -> Path:
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Antigravity"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )
    if os.name == "nt":
        base = absolute_environment_path("APPDATA")
        if base is None:
            base = Path.home() / "AppData" / "Roaming"
        return base / "Antigravity" / "User" / "globalStorage" / "state.vscdb"
    base = absolute_environment_path("XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / "Antigravity" / "User" / "globalStorage" / "state.vscdb"


def _read_model_credits(database: Path) -> str:
    if database.is_symlink():
        raise AntigravityProbeError("Antigravity's local state database is a symlink")
    try:
        resolved = database.expanduser().resolve(strict=True)
    except OSError as exc:
        raise AntigravityProbeError("Antigravity's local usage cache was not found") from exc
    if not resolved.is_file():
        raise AntigravityProbeError("Antigravity's local usage cache was not found")
    uri = f"file:{quote(str(resolved), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
        try:
            connection.execute("PRAGMA query_only = ON")
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1", (MODEL_CREDITS_KEY,)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise AntigravityProbeError(
            "Antigravity's local usage cache could not be read safely"
        ) from exc
    if row is None or not isinstance(row[0], str):
        raise AntigravityProbeError("Antigravity has not cached model credits yet")
    if len(row[0]) > MAX_STATE_CHARACTERS:
        raise AntigravityProbeError("Antigravity's model-credit cache is too large")
    return row[0]


def _varint(payload: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(payload):
            raise ValueError("truncated Antigravity protobuf")
        byte = payload[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
    raise ValueError("oversized Antigravity protobuf varint")


def _fields(payload: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    position = 0
    count = 0
    while position < len(payload):
        count += 1
        if count > MAX_PROTOBUF_FIELDS:
            raise ValueError("Antigravity protobuf has too many fields")
        key, position = _varint(payload, position)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ValueError("invalid Antigravity protobuf field")
        if wire == 0:
            value, position = _varint(payload, position)
        elif wire == 2:
            size, position = _varint(payload, position)
            end = position + size
            if end > len(payload):
                raise ValueError("truncated Antigravity protobuf field")
            value = payload[position:end]
            position = end
        else:
            raise ValueError("unsupported Antigravity protobuf field")
        yield number, wire, value


def _decode_base64(value: str | bytes, field: str) -> bytes:
    try:
        raw = value.encode("ascii") if isinstance(value, str) else value
        return base64.b64decode(raw, validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"Antigravity {field} is invalid") from exc


def parse_model_credits(
    encoded: str,
    *,
    collected_at: datetime | None = None,
) -> ProviderSnapshot:
    if not isinstance(encoded, str) or len(encoded) > MAX_STATE_CHARACTERS:
        raise ValueError("Antigravity model-credit payload is invalid")
    outer = _decode_base64(encoded, "model-credit payload")
    available: float | None = None
    for number, wire, raw_entry in _fields(outer):
        if number != 1 or wire != 2 or not isinstance(raw_entry, bytes):
            continue
        key: str | None = None
        wrapped: bytes | None = None
        for entry_number, entry_wire, entry_value in _fields(raw_entry):
            if entry_number == 1 and entry_wire == 2 and isinstance(entry_value, bytes):
                try:
                    key = entry_value.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError("Antigravity model-credit key is invalid") from exc
            elif entry_number == 2 and entry_wire == 2 and isinstance(entry_value, bytes):
                wrapped = entry_value
        if key != AVAILABLE_CREDITS_KEY or wrapped is None:
            continue
        encoded_value = next(
            (
                value
                for field_number, field_wire, value in _fields(wrapped)
                if field_number == 1 and field_wire == 2 and isinstance(value, bytes)
            ),
            None,
        )
        if encoded_value is None:
            raise ValueError("Antigravity available credits are invalid")
        value_payload = _decode_base64(encoded_value, "available credits")
        value = next(
            (
                item
                for field_number, field_wire, item in _fields(value_payload)
                if field_number == 2 and field_wire == 0 and isinstance(item, int)
            ),
            None,
        )
        if value is None:
            raise ValueError("Antigravity available credits are invalid")
        available = float(value)

    now = collected_at or utc_now()
    if available is None:
        return ProviderSnapshot(
            provider_id="antigravity",
            display_name="Antigravity",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.PRIVATE_LOCAL_STATE,
            collected_at=now,
            message="Antigravity has not cached available AI credits.",
        )
    return ProviderSnapshot(
        provider_id="antigravity",
        display_name="Antigravity",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.PRIVATE_LOCAL_STATE,
        collected_at=now,
        windows=(
            QuotaWindow(
                id="model_credits",
                label="AI credits",
                unit="credits",
                remaining=available,
            ),
        ),
    )


def read_antigravity_usage(database: Path | None = None) -> ProviderSnapshot:
    source = database or default_antigravity_database()
    encoded = _read_model_credits(source)
    collected_at = datetime.fromtimestamp(source.expanduser().resolve().stat().st_mtime, UTC)
    return parse_model_credits(encoded, collected_at=collected_at)
