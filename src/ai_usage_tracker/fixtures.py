from __future__ import annotations

from datetime import UTC, datetime
import json

from .model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus
from .providers.claude import parse_status_payload
from .providers.codex import parse_rate_limits_result


FIXTURE_TIME = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def claude_snapshot(
    collected_at: datetime = FIXTURE_TIME,
) -> ProviderSnapshot:
    payload = {
        "rate_limits": {
            "five_hour": {
                "used_percentage": 37.5,
                "resets_at": int(
                    datetime(2026, 7, 24, 18, 30, tzinfo=UTC).timestamp()
                ),
            },
            "seven_day": {
                "used_percentage": 61.0,
                "resets_at": int(
                    datetime(2026, 7, 28, 0, 0, tzinfo=UTC).timestamp()
                ),
            },
        },
        # This sensitive-looking field verifies that the parser ignores fields
        # outside the normalized quota allowlist.
        "session_id": "CANARY_SECRET_FIXTURE_SESSION",
    }
    return parse_status_payload(
        json.dumps(payload).encode("utf-8"),
        source=DataSource.FIXTURE,
        collected_at=collected_at,
    )


def codex_snapshot(
    collected_at: datetime = FIXTURE_TIME,
) -> ProviderSnapshot:
    result = {
        "rateLimits": {
            "primary": {
                "usedPercent": 18,
                "windowDurationMins": 300,
                "resetsAt": int(
                    datetime(2026, 7, 24, 18, 30, tzinfo=UTC).timestamp()
                ),
            },
            "secondary": {
                "usedPercent": 42,
                "windowDurationMins": 10080,
                "resetsAt": int(
                    datetime(2026, 7, 30, 0, 0, tzinfo=UTC).timestamp()
                ),
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
        collected_at=collected_at,
    )


def cursor_snapshot(
    collected_at: datetime = FIXTURE_TIME,
) -> ProviderSnapshot:
    reset = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    return ProviderSnapshot(
        provider_id="cursor",
        display_name="Cursor",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.FIXTURE,
        collected_at=collected_at,
        windows=(
            QuotaWindow(
                id="total_spend",
                label="Total spend",
                unit="currency_cents",
                used=1284,
                limit=2000,
                remaining=716,
                used_percent=64.2,
                resets_at=reset,
            ),
            QuotaWindow(
                id="auto_usage",
                label="Auto usage",
                unit="percent",
                used_percent=31,
                resets_at=reset,
            ),
            QuotaWindow(
                id="api_usage",
                label="API usage",
                unit="percent",
                used_percent=76,
                resets_at=reset,
            ),
        ),
    )


def devin_snapshot(
    collected_at: datetime = FIXTURE_TIME,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id="devin",
        display_name="Devin",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.FIXTURE,
        collected_at=collected_at,
        windows=(
            QuotaWindow(
                id="daily",
                label="Daily quota",
                unit="acu",
                used=18,
                limit=40,
                remaining=22,
                used_percent=45,
                resets_at=datetime(2026, 7, 25, 0, 0, tzinfo=UTC),
            ),
            QuotaWindow(
                id="weekly",
                label="Weekly quota",
                unit="acu",
                used=62,
                limit=120,
                remaining=58,
                used_percent=51.7,
                resets_at=datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
            ),
        ),
    )


def antigravity_snapshot(
    collected_at: datetime = FIXTURE_TIME,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id="antigravity",
        display_name="Antigravity",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.FIXTURE,
        collected_at=collected_at,
        windows=(
            QuotaWindow(
                id="model_credits",
                label="AI credits",
                unit="credits",
                remaining=1380,
            ),
        ),
    )


def github_copilot_snapshot(
    collected_at: datetime = FIXTURE_TIME,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider_id="github_copilot",
        display_name="GitHub Copilot",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.FIXTURE,
        collected_at=collected_at,
        windows=(
            QuotaWindow(
                id="local_ai_credits",
                label="Local AI credits",
                unit="ai_credits",
                used=184.35,
            ),
        ),
    )


def all_snapshots(
    collected_at: datetime = FIXTURE_TIME,
) -> tuple[ProviderSnapshot, ...]:
    return (
        cursor_snapshot(collected_at),
        claude_snapshot(collected_at),
        codex_snapshot(collected_at),
        devin_snapshot(collected_at),
        antigravity_snapshot(collected_at),
        github_copilot_snapshot(collected_at),
    )
