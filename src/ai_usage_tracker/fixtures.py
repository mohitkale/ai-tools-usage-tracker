from __future__ import annotations

from datetime import UTC, datetime
import json

from .model import DataSource, ProviderSnapshot
from .providers.claude import parse_status_payload
from .providers.codex import parse_rate_limits_result


FIXTURE_TIME = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)


def claude_snapshot() -> ProviderSnapshot:
    payload = {
        "rate_limits": {
            "five_hour": {
                "used_percentage": 37.5,
                "resets_at": 1784689200,
            },
            "seven_day": {
                "used_percentage": 61.0,
                "resets_at": 1785110400,
            },
        },
        # This sensitive-looking field verifies that the parser ignores fields
        # outside the normalized quota allowlist.
        "session_id": "CANARY_SECRET_FIXTURE_SESSION",
    }
    return parse_status_payload(
        json.dumps(payload).encode("utf-8"),
        source=DataSource.FIXTURE,
        collected_at=FIXTURE_TIME,
    )


def codex_snapshot() -> ProviderSnapshot:
    result = {
        "rateLimits": {
            "primary": {
                "usedPercent": 18,
                "windowDurationMins": 300,
                "resetsAt": 1784689200,
            },
            "secondary": {
                "usedPercent": 42,
                "windowDurationMins": 10080,
                "resetsAt": 1785110400,
            },
            "planType": "CANARY_SECRET_FIXTURE_PLAN",
        },
        "rateLimitResetCredits": {
            "availableCount": 1,
            "credits": [{"id": "CANARY_SECRET_FIXTURE_CREDIT"}],
        },
    }
    return parse_rate_limits_result(
        result,
        source=DataSource.FIXTURE,
        collected_at=FIXTURE_TIME,
    )


def all_snapshots() -> tuple[ProviderSnapshot, ...]:
    return (claude_snapshot(), codex_snapshot())

