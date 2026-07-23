from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

from ..model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus, utc_now


NANO_AI_CREDITS_PER_CREDIT = 1_000_000_000


class GitHubCopilotProbeError(RuntimeError):
    """A deliberately non-sensitive GitHub Copilot probe failure."""


def default_copilot_cli_database() -> Path:
    configured = os.environ.get("COPILOT_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".copilot"
    return base / "session-store.db"


def _read_local_credit_totals(database: Path) -> tuple[int, int, str | None]:
    """Read only aggregate numeric usage from Copilot CLI's local event store."""
    if database.is_symlink():
        raise GitHubCopilotProbeError("Copilot CLI local state is a symlink")
    try:
        resolved = database.expanduser().resolve(strict=True)
    except OSError as exc:
        raise GitHubCopilotProbeError(
            "Copilot CLI has not recorded local usage yet"
        ) from exc
    if not resolved.is_file():
        raise GitHubCopilotProbeError("Copilot CLI local usage is unavailable")

    uri = f"file:{quote(str(resolved), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
        try:
            connection.execute("PRAGMA query_only = ON")
            row = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(total_nano_aiu), 0), "
                "MAX(created_at) FROM assistant_usage_events"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise GitHubCopilotProbeError(
            "Copilot CLI local usage could not be read safely"
        ) from exc

    if (
        row is None
        or len(row) != 3
        or isinstance(row[0], bool)
        or not isinstance(row[0], int)
        or isinstance(row[1], bool)
        or not isinstance(row[1], int)
        or row[0] < 0
        or row[1] < 0
        or (row[2] is not None and not isinstance(row[2], str))
    ):
        raise GitHubCopilotProbeError("Copilot CLI local usage is invalid")
    return row[0], row[1], row[2]


def read_copilot_cli_usage(database: Path | None = None) -> ProviderSnapshot:
    """Return local Copilot CLI AI-credit usage without reading credentials."""
    source = database or default_copilot_cli_database()
    if not source.exists() and not source.is_symlink():
        return ProviderSnapshot(
            provider_id="github_copilot",
            display_name="GitHub Copilot",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.OFFICIAL_LOCAL_PAYLOAD,
            collected_at=utc_now(),
            message="Copilot CLI has not recorded local AI-credit usage yet.",
        )

    count, total_nano_credits, latest = _read_local_credit_totals(source)
    collected_at = utc_now()
    if latest:
        try:
            parsed = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GitHubCopilotProbeError(
                "Copilot CLI local usage timestamp is invalid"
            ) from exc
        if parsed.tzinfo is None:
            raise GitHubCopilotProbeError(
                "Copilot CLI local usage timestamp is invalid"
            )
        collected_at = parsed.astimezone(UTC)

    if count == 0:
        return ProviderSnapshot(
            provider_id="github_copilot",
            display_name="GitHub Copilot",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.OFFICIAL_LOCAL_PAYLOAD,
            collected_at=collected_at,
            message="Copilot CLI has not recorded local AI-credit usage yet.",
        )

    return ProviderSnapshot(
        provider_id="github_copilot",
        display_name="GitHub Copilot",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.OFFICIAL_LOCAL_PAYLOAD,
        collected_at=collected_at,
        windows=(
            QuotaWindow(
                id="local_ai_credits",
                label="Local AI credits",
                unit="ai_credits",
                used=total_nano_credits / NANO_AI_CREDITS_PER_CREDIT,
            ),
        ),
    )


_SAFE_ERROR_GUIDANCE = {
    "Copilot CLI local state is a symlink": (
        "The Copilot CLI usage database was blocked because it is a symlink."
    ),
    "Copilot CLI has not recorded local usage yet": (
        "Send a prompt in Copilot CLI, then refresh."
    ),
    "Copilot CLI local usage is unavailable": (
        "The Copilot CLI usage database is not a regular file."
    ),
    "Copilot CLI local usage could not be read safely": (
        "The Copilot CLI usage database could not be opened read-only."
    ),
    "Copilot CLI local usage is invalid": (
        "The Copilot CLI usage database contains an unsupported value."
    ),
    "Copilot CLI local usage timestamp is invalid": (
        "The Copilot CLI usage database contains an unsupported timestamp."
    ),
}


def safe_error_guidance(error: GitHubCopilotProbeError) -> str:
    """Map only exact reviewed failures to UI text; never render exception content."""
    return _SAFE_ERROR_GUIDANCE.get(
        str(error),
        "GitHub Copilot local usage could not be refreshed safely.",
    )


def safe_error_status(error: GitHubCopilotProbeError) -> str:
    """Return a short allowlisted badge; unknown failures remain generic."""
    if str(error) == "Copilot CLI has not recorded local usage yet":
        return "Waiting for data"
    return "Local data unavailable"
