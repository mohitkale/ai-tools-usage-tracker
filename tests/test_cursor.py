from __future__ import annotations

from contextlib import redirect_stdout
from datetime import UTC, datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from ai_usage_tracker.model import DataSource, SnapshotStatus
from ai_usage_tracker.cli import main
from ai_usage_tracker.providers.cursor import (
    CURSOR_HOST,
    CURSOR_TOKEN_KEY,
    CURSOR_USAGE_PATH,
    _read_cursor_access_token,
    _request_current_period_usage,
    parse_current_period_usage,
    parse_usage_summary,
)


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _field(number: int, wire_type: int, value: bytes | int | float) -> bytes:
    result = _varint(number << 3 | wire_type)
    if wire_type == 0:
        return result + _varint(int(value))
    if wire_type == 1:
        return result + struct.pack("<d", float(value))
    assert isinstance(value, bytes)
    return result + _varint(len(value)) + value


class CursorUsageParserTests(unittest.TestCase):
    def test_decodes_and_normalizes_only_quota_fields(self) -> None:
        plan = b"".join(
            (
                _field(1, 0, 700),
                _field(2, 0, 500),
                _field(3, 0, 200),
                _field(4, 0, 1500),
                _field(5, 0, 2000),
                _field(12, 1, 30.0),
                _field(13, 1, 10.0),
                _field(14, 1, 25.0),
                _field(7, 2, b"CANARY_SECRET_TOOLTIP"),
            )
        )
        spend_limit = b"".join(
            (_field(5, 0, 1000), _field(6, 0, 250), _field(7, 0, 750))
        )
        payload = b"".join(
            (
                _field(1, 0, 1_784_675_600_000),
                _field(2, 0, 1_787_354_400_000),
                _field(3, 2, plan),
                _field(4, 2, spend_limit),
                _field(6, 0, 1),
                _field(7, 2, b"CANARY_SECRET_DISPLAY_MESSAGE"),
            )
        )

        document = parse_current_period_usage(payload)
        snapshot = parse_usage_summary(
            document,
            collected_at=datetime(2026, 7, 22, tzinfo=UTC),
        )

        self.assertEqual(snapshot.status, SnapshotStatus.AVAILABLE)
        self.assertEqual(snapshot.source, DataSource.PRIVATE_PROVIDER_API)
        self.assertEqual(snapshot.windows[0].label, "Total usage")
        self.assertEqual(snapshot.windows[0].used, 700)
        self.assertIsNone(snapshot.windows[0].limit)
        self.assertEqual(snapshot.windows[0].used_percent, 25)
        self.assertEqual(snapshot.windows[1].label, "Auto models")
        self.assertEqual(snapshot.windows[1].used_percent, 30)
        self.assertEqual(snapshot.windows[2].label, "API models")
        self.assertEqual(snapshot.windows[2].used_percent, 10)
        self.assertEqual(snapshot.windows[3].used_percent, 25)
        serialized = json.dumps(snapshot.to_dict())
        self.assertNotIn("CANARY_SECRET", serialized)

    def test_returns_no_data_when_usage_display_is_disabled(self) -> None:
        snapshot = parse_usage_summary(
            {"enabled": False}, collected_at=datetime(2026, 7, 22, tzinfo=UTC)
        )
        self.assertEqual(snapshot.status, SnapshotStatus.NO_DATA)

    def test_does_not_present_included_bucket_as_total_usage(self) -> None:
        snapshot = parse_usage_summary(
            {
                "enabled": True,
                "plan_usage": {
                    "total_spend": 8827,
                    "included_spend": 2000,
                    "limit": 2000,
                    "total_percent_used": 25.5855,
                },
            },
            collected_at=datetime(2026, 7, 22, tzinfo=UTC),
        )
        self.assertEqual(snapshot.windows[0].used, 8827)
        self.assertIsNone(snapshot.windows[0].limit)
        self.assertAlmostEqual(snapshot.windows[0].used_percent, 25.5855)

    def test_rejects_truncated_protobuf(self) -> None:
        with self.assertRaises(ValueError):
            parse_current_period_usage(b"\x1a\x05\x08")


class CursorCredentialTests(unittest.TestCase):
    def test_cli_does_not_read_session_without_explicit_consent(self) -> None:
        output = StringIO()
        with patch("ai_usage_tracker.cli.read_cursor_usage") as reader:
            with redirect_stdout(output):
                exit_code = main(["cursor-live"])

        self.assertEqual(exit_code, 2)
        reader.assert_not_called()
        self.assertIn("no credential was read", output.getvalue())

    def test_reads_only_exact_token_key_from_database_without_modifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.vscdb"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
            connection.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                (CURSOR_TOKEN_KEY, "CANARY_SECRET_CURSOR_ACCESS_TOKEN"),
            )
            connection.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                ("cursorAuth/refreshToken", "CANARY_SECRET_MUST_NOT_BE_READ"),
            )
            connection.commit()
            connection.close()
            before = hashlib.sha256(database.read_bytes()).digest()

            token = _read_cursor_access_token(database)
            after = hashlib.sha256(database.read_bytes()).digest()

            self.assertEqual(token, "CANARY_SECRET_CURSOR_ACCESS_TOKEN")
            self.assertEqual(before, after)

    def test_rejects_a_symlinked_cursor_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "real.vscdb"
            database.write_bytes(b"not opened")
            link = root / "state.vscdb"
            link.symlink_to(database)

            with self.assertRaisesRegex(Exception, "symlink"):
                _read_cursor_access_token(link)

    def test_request_is_pinned_to_cursor_rpc_and_uses_empty_body(self) -> None:
        class FakeResponse:
            status = 200

            @staticmethod
            def getheader(name: str, default: str = "") -> str:
                if name == "Content-Type":
                    return "application/proto"
                return "identity" if name == "Content-Encoding" else default

            @staticmethod
            def read(limit: int) -> bytes:
                return b"\x30\x01"

        class FakeConnection:
            def __init__(self) -> None:
                self.request_args = None

            def request(self, method, path, body, headers):
                self.request_args = (method, path, body, headers)

            @staticmethod
            def getresponse():
                return FakeResponse()

            @staticmethod
            def close():
                return None

        fake_connection = FakeConnection()
        with patch(
            "ai_usage_tracker.providers.cursor.http.client.HTTPSConnection",
            return_value=fake_connection,
        ) as constructor:
            payload = _request_current_period_usage("CANARY_SECRET_CURSOR_TOKEN")

        constructor.assert_called_once()
        self.assertEqual(constructor.call_args.args[0], CURSOR_HOST)
        method, path, body, headers = fake_connection.request_args
        self.assertEqual(method, "POST")
        self.assertEqual(path, CURSOR_USAGE_PATH)
        self.assertEqual(body, b"")
        self.assertEqual(
            headers["Authorization"], "Bearer CANARY_SECRET_CURSOR_TOKEN"
        )
        self.assertEqual(headers["Connect-Protocol-Version"], "1")
        self.assertEqual(payload, b"\x30\x01")


if __name__ == "__main__":
    unittest.main()
