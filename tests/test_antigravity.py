from __future__ import annotations

import base64
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ai_usage_tracker.model import DataSource
from ai_usage_tracker.providers.antigravity import (
    MODEL_CREDITS_KEY,
    parse_model_credits,
    read_antigravity_usage,
)


def _varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _field(number: int, wire: int, value: bytes | int) -> bytes:
    prefix = _varint(number << 3 | wire)
    if wire == 0:
        return prefix + _varint(int(value))
    assert isinstance(value, bytes)
    return prefix + _varint(len(value)) + value


def _credits_payload(value: int) -> str:
    numeric = _field(2, 0, value)
    wrapped = _field(1, 2, base64.b64encode(numeric))
    entry = _field(1, 2, b"availableCreditsSentinelKey") + _field(2, 2, wrapped)
    ignored = _field(1, 2, b"oauthToken") + _field(2, 2, b"CANARY_SECRET")
    return base64.b64encode(_field(1, 2, entry) + _field(1, 2, ignored)).decode()


class AntigravityUsageTests(unittest.TestCase):
    def test_extracts_only_available_model_credits(self) -> None:
        snapshot = parse_model_credits(
            _credits_payload(1000),
            collected_at=datetime(2026, 7, 23, tzinfo=UTC),
        )
        self.assertEqual(snapshot.windows[0].remaining, 1000)
        self.assertEqual(snapshot.source, DataSource.PRIVATE_LOCAL_STATE)
        self.assertNotIn("CANARY", json.dumps(snapshot.to_dict()))

    def test_reads_only_exact_model_credit_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.vscdb"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value TEXT)")
            connection.execute(
                "INSERT INTO ItemTable VALUES (?, ?)",
                (MODEL_CREDITS_KEY, _credits_payload(250)),
            )
            connection.execute(
                "INSERT INTO ItemTable VALUES (?, ?)",
                ("antigravityUnifiedStateSync.oauthToken", "CANARY_SECRET_TOKEN"),
            )
            connection.commit()
            connection.close()

            snapshot = read_antigravity_usage(database)

        self.assertEqual(snapshot.windows[0].remaining, 250)
