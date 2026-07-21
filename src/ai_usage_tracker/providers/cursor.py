from __future__ import annotations

from datetime import UTC, datetime
import http.client
import json
import os
from pathlib import Path
import sqlite3
import ssl
import struct
import sys
from typing import Any, Iterator
from urllib.parse import quote

from ..model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus, utc_now


CURSOR_HOST = "api2.cursor.sh"
CURSOR_USAGE_PATH = "/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
CURSOR_TOKEN_KEY = "cursorAuth/accessToken"
MAX_TOKEN_CHARACTERS = 8192
MAX_RESPONSE_BYTES = 512 * 1024
MAX_PROTOBUF_FIELDS = 256
DEFAULT_TIMEOUT_SECONDS = 10.0


class CursorProbeError(RuntimeError):
    """A deliberately non-sensitive Cursor probe failure."""


def default_cursor_database() -> Path:
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = (
        Path(config_home)
        if config_home and Path(config_home).is_absolute()
        else Path.home() / ".config"
    )
    return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _read_cursor_access_token(database: Path) -> str:
    resolved = database.expanduser().resolve()
    if not resolved.is_file():
        raise CursorProbeError("Cursor's local state database was not found")
    database_uri = f"file:{quote(str(resolved), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(
            database_uri,
            uri=True,
            timeout=1,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1",
                (CURSOR_TOKEN_KEY,),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise CursorProbeError("Cursor's local state could not be read safely") from exc
    if row is None or not isinstance(row[0], str):
        raise CursorProbeError("Cursor is not signed in or its session is unavailable")
    token = row[0]
    if token.startswith('"'):
        try:
            decoded = json.loads(token)
        except json.JSONDecodeError as exc:
            raise CursorProbeError("Cursor's session has an unsupported format") from exc
        if not isinstance(decoded, str):
            raise CursorProbeError("Cursor's session has an unsupported format")
        token = decoded
    if not token or len(token) > MAX_TOKEN_CHARACTERS:
        raise CursorProbeError("Cursor's session has an unsupported format")
    if any(character in token for character in "\r\n\0"):
        raise CursorProbeError("Cursor's session has an unsupported format")
    return token


def _request_current_period_usage(
    token: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("Cursor timeout must be between 0 and 60 seconds")
    connection = http.client.HTTPSConnection(
        CURSOR_HOST,
        port=443,
        timeout=timeout_seconds,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "POST",
            CURSOR_USAGE_PATH,
            body=b"",
            headers={
                "Accept": "application/proto",
                "Authorization": f"Bearer {token}",
                "Connect-Protocol-Version": "1",
                "Content-Type": "application/proto",
                "User-Agent": "ai-usage-tracker/0.1.0",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise CursorProbeError(
                "Cursor rejected its usage request; the local session may be expired"
            )
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].lower()
        if content_type not in {"application/proto", "application/connect+proto"}:
            raise CursorProbeError("Cursor returned an unsupported usage response")
        if response.getheader("Content-Encoding", "identity").lower() not in {
            "",
            "identity",
        }:
            raise CursorProbeError("Cursor returned an unsupported encoded response")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise CursorProbeError("Cursor's usage endpoint could not be reached") from exc
    finally:
        connection.close()
    if len(payload) > MAX_RESPONSE_BYTES:
        raise CursorProbeError("Cursor's usage response exceeded the size limit")
    return payload


def _read_varint(payload: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(payload):
            raise ValueError("truncated protobuf varint")
        byte = payload[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
    raise ValueError("oversized protobuf varint")


def _protobuf_fields(payload: bytes) -> Iterator[tuple[int, int, int | bytes | float]]:
    position = 0
    field_count = 0
    while position < len(payload):
        field_count += 1
        if field_count > MAX_PROTOBUF_FIELDS:
            raise ValueError("protobuf field count exceeded the safety limit")
        key, position = _read_varint(payload, position)
        field_number, wire_type = key >> 3, key & 7
        if field_number == 0:
            raise ValueError("invalid protobuf field number")
        if wire_type == 0:
            value, position = _read_varint(payload, position)
        elif wire_type == 1:
            end = position + 8
            if end > len(payload):
                raise ValueError("truncated protobuf fixed64")
            value = struct.unpack("<d", payload[position:end])[0]
            position = end
        elif wire_type == 2:
            size, position = _read_varint(payload, position)
            end = position + size
            if end > len(payload):
                raise ValueError("truncated protobuf field")
            value = payload[position:end]
            position = end
        elif wire_type == 5:
            end = position + 4
            if end > len(payload):
                raise ValueError("truncated protobuf fixed32")
            value = payload[position:end]
            position = end
        else:
            raise ValueError("unsupported protobuf wire type")
        yield field_number, wire_type, value


def _parse_plan_usage(payload: bytes) -> dict[str, int | float | bool]:
    integer_fields = {
        1: "total_spend",
        2: "included_spend",
        3: "bonus_spend",
        4: "remaining",
        5: "limit",
        8: "auto_spend",
        9: "api_spend",
        10: "auto_limit",
        11: "api_limit",
    }
    double_fields = {
        12: "auto_percent_used",
        13: "api_percent_used",
        14: "total_percent_used",
    }
    result: dict[str, int | float | bool] = {}
    for number, wire_type, value in _protobuf_fields(payload):
        if number in integer_fields and wire_type == 0 and isinstance(value, int):
            result[integer_fields[number]] = value
        elif number == 6 and wire_type == 0 and isinstance(value, int):
            result["remaining_bonus"] = bool(value)
        elif number in double_fields and wire_type == 1 and isinstance(value, float):
            result[double_fields[number]] = value
    return result


def _parse_spend_limit_usage(payload: bytes) -> dict[str, int]:
    integer_fields = {
        1: "total_spend",
        2: "pooled_limit",
        3: "pooled_used",
        4: "pooled_remaining",
        5: "individual_limit",
        6: "individual_used",
        7: "individual_remaining",
        9: "overall_limit",
        10: "overall_used",
        11: "overall_remaining",
    }
    result: dict[str, int] = {}
    for number, wire_type, value in _protobuf_fields(payload):
        if number in integer_fields and wire_type == 0 and isinstance(value, int):
            result[integer_fields[number]] = value
    return result


def parse_current_period_usage(payload: bytes) -> dict[str, Any]:
    """Decode only non-sensitive quota fields from Cursor's protobuf response."""
    if not isinstance(payload, bytes):
        raise ValueError("Cursor usage payload must be bytes")
    document: dict[str, Any] = {}
    for number, wire_type, value in _protobuf_fields(payload):
        if number == 1 and wire_type == 0 and isinstance(value, int):
            document["billing_cycle_start"] = value
        elif number == 2 and wire_type == 0 and isinstance(value, int):
            document["billing_cycle_end"] = value
        elif number == 3 and wire_type == 2 and isinstance(value, bytes):
            document["plan_usage"] = _parse_plan_usage(value)
        elif number == 4 and wire_type == 2 and isinstance(value, bytes):
            document["spend_limit_usage"] = _parse_spend_limit_usage(value)
        elif number == 6 and wire_type == 0 and isinstance(value, int):
            document["enabled"] = bool(value)
    return document


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    number = float(value)
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _number(value, field)


def _epoch_timestamp(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    epoch = _number(value, field)
    if epoch >= 100_000_000_000:
        epoch /= 1000
    try:
        parsed = datetime.fromtimestamp(epoch, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"{field} is outside the supported timestamp range") from exc
    if not 2000 <= parsed.year <= 2100:
        raise ValueError(f"{field} is outside the supported timestamp range")
    return parsed


def _percentage(used: float | None, limit: float | None, fallback: Any) -> float | None:
    if used is not None and limit is not None and limit > 0:
        return min(used / limit * 100, 100)
    percent = _optional_number(fallback, "usage percentage")
    if percent is None:
        return None
    return min(percent, 100)


def parse_usage_summary(
    document: Any,
    *,
    collected_at: datetime | None = None,
) -> ProviderSnapshot:
    if not isinstance(document, dict):
        raise ValueError("Cursor usage response must be an object")
    now = collected_at or utc_now()
    if document.get("enabled") is False:
        return ProviderSnapshot(
            provider_id="cursor",
            display_name="Cursor",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.PRIVATE_PROVIDER_API,
            collected_at=now,
            message="Cursor's usage display is disabled for this account.",
        )

    starts_at = _epoch_timestamp(document.get("billing_cycle_start"), "billing cycle start")
    resets_at = _epoch_timestamp(document.get("billing_cycle_end"), "billing cycle end")
    window_seconds = None
    if starts_at is not None and resets_at is not None:
        duration = int((resets_at - starts_at).total_seconds())
        if duration > 0:
            window_seconds = duration

    windows: list[QuotaWindow] = []
    plan = document.get("plan_usage")
    if isinstance(plan, dict):
        used = _optional_number(plan.get("included_spend"), "plan included spend")
        limit = _optional_number(plan.get("limit"), "plan limit")
        remaining = _optional_number(plan.get("remaining"), "plan remaining")
        if remaining is None and used is not None and limit is not None:
            remaining = max(limit - used, 0)
        percent = _percentage(used, limit, plan.get("total_percent_used"))
        if any(value is not None for value in (used, limit, remaining, percent)):
            windows.append(
                QuotaWindow(
                    id="billing_cycle",
                    label="Included usage",
                    unit="currency_cents",
                    used=used,
                    limit=limit,
                    remaining=remaining,
                    used_percent=percent,
                    window_seconds=window_seconds,
                    resets_at=resets_at,
                )
            )

    spend = document.get("spend_limit_usage")
    if isinstance(spend, dict):
        used = _optional_number(spend.get("individual_used"), "spend-limit used")
        limit = _optional_number(spend.get("individual_limit"), "spend-limit limit")
        remaining = _optional_number(
            spend.get("individual_remaining"), "spend-limit remaining"
        )
        percent = _percentage(used, limit, None)
        if any(value is not None for value in (used, limit, remaining, percent)):
            windows.append(
                QuotaWindow(
                    id="spend_limit",
                    label="On-demand spend limit",
                    unit="currency_cents",
                    used=used,
                    limit=limit,
                    remaining=remaining,
                    used_percent=percent,
                    window_seconds=window_seconds,
                    resets_at=resets_at,
                )
            )

    if not windows:
        return ProviderSnapshot(
            provider_id="cursor",
            display_name="Cursor",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.PRIVATE_PROVIDER_API,
            collected_at=now,
            message="Cursor returned no supported usage measurements.",
        )
    return ProviderSnapshot(
        provider_id="cursor",
        display_name="Cursor",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.PRIVATE_PROVIDER_API,
        collected_at=now,
        windows=tuple(windows),
    )


def read_cursor_usage(database: Path | None = None) -> ProviderSnapshot:
    token = _read_cursor_access_token(database or default_cursor_database())
    try:
        payload = _request_current_period_usage(token)
    finally:
        # Python cannot guarantee zeroization of immutable strings, but dropping
        # the only application reference minimizes its lifetime.
        del token
    try:
        document = parse_current_period_usage(payload)
    except ValueError as exc:
        raise CursorProbeError("Cursor returned an invalid usage response") from exc
    return parse_usage_summary(document)
