from __future__ import annotations

import calendar
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Sequence

from ..model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus, utc_now


GITHUB_API_VERSION = "2026-03-10"
MAX_GH_OUTPUT_BYTES = 2 * 1024 * 1024
GH_TIMEOUT_SECONDS = 20
GH_LOGIN_TIMEOUT_SECONDS = 10 * 60
_LOGIN_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")


class GitHubCopilotProbeError(RuntimeError):
    """A deliberately non-sensitive GitHub Copilot probe failure."""


_SAFE_ERROR_GUIDANCE = {
    "GitHub CLI was not found": "Install GitHub CLI and sign in, then retry.",
    "GitHub CLI is not signed in": (
        "GitHub CLI is not signed in. Use Sign in to open GitHub's official "
        "browser flow."
    ),
    "GitHub CLI sign-in did not complete": (
        "GitHub CLI sign-in did not complete. Try Sign in again or run "
        "`gh auth login --hostname github.com`."
    ),
    "GitHub CLI could not be resolved": (
        "The GitHub CLI installation could not be used. Reinstall it, then retry."
    ),
    "GitHub CLI is not executable": (
        "The GitHub CLI installation could not be used. Reinstall it, then retry."
    ),
    "GitHub CLI usage request failed": (
        "GitHub CLI could not complete the request. Check connectivity and retry."
    ),
    "GitHub rejected the usage request; re-authenticate gh with Plan read access": (
        "GitHub rejected the request. Re-authenticate GitHub CLI, then retry."
    ),
    "GitHub usage response exceeded the size limit": (
        "GitHub returned an unexpectedly large response. Update the app before retrying."
    ),
    "GitHub returned an invalid account identifier": (
        "GitHub CLI returned an invalid account response. Sign in again, then retry."
    ),
}

_SAFE_ERROR_STATUSES = {
    "GitHub CLI was not found": "CLI required",
    "GitHub CLI could not be resolved": "CLI required",
    "GitHub CLI is not executable": "CLI required",
    "GitHub CLI is not signed in": "Sign-in required",
    "GitHub CLI sign-in did not complete": "Sign-in required",
}


def safe_error_guidance(error: GitHubCopilotProbeError) -> str:
    """Map only exact reviewed failures to UI text; never render exception content."""
    return _SAFE_ERROR_GUIDANCE.get(
        str(error),
        "GitHub Copilot usage could not be refreshed. Verify GitHub CLI sign-in and retry.",
    )


def safe_error_status(error: GitHubCopilotProbeError) -> str:
    """Return a short allowlisted badge; unknown failures remain generic."""
    return _SAFE_ERROR_STATUSES.get(str(error), "Needs attention")


def resolve_gh_executable(explicit: str | None = None) -> Path:
    candidates = [explicit] if explicit else [
        shutil.which("gh"),
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
    ]
    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles")
        local_app_data = os.environ.get("LOCALAPPDATA")
        candidates.extend(
            [
                str(Path(program_files) / "GitHub CLI" / "gh.exe")
                if program_files
                else None,
                str(Path(local_app_data) / "Programs" / "GitHub CLI" / "gh.exe")
                if local_app_data
                else None,
            ]
        )
    candidate = next((value for value in candidates if value and Path(value).exists()), None)
    if not candidate:
        raise GitHubCopilotProbeError("GitHub CLI was not found")
    try:
        resolved = Path(candidate).expanduser().resolve(strict=True)
    except OSError as exc:
        raise GitHubCopilotProbeError("GitHub CLI could not be resolved") from exc
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise GitHubCopilotProbeError("GitHub CLI is not executable")
    return resolved


def _minimal_environment() -> dict[str, str]:
    allowed = (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "SystemRoot",
        "WINDIR",
        "TMPDIR",
        "TEMP",
        "TMP",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "GH_HOST": "github.com",
            "GH_PROMPT_DISABLED": "1",
            "NO_COLOR": "1",
            "PATH": os.defpath,
        }
    )
    return environment


def _run_gh(executable: Path, arguments: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(
            (str(executable), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_minimal_environment(),
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubCopilotProbeError("GitHub CLI usage request failed") from exc
    if result.returncode != 0:
        raise GitHubCopilotProbeError(
            "GitHub rejected the usage request; re-authenticate gh with Plan read access"
        )
    if len(result.stdout) > MAX_GH_OUTPUT_BYTES:
        raise GitHubCopilotProbeError("GitHub usage response exceeded the size limit")
    return result.stdout


def _require_gh_authentication(executable: Path) -> None:
    try:
        result = subprocess.run(
            (str(executable), "auth", "status", "--hostname", "github.com"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_minimal_environment(),
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubCopilotProbeError("GitHub CLI usage request failed") from exc
    if result.returncode != 0:
        raise GitHubCopilotProbeError("GitHub CLI is not signed in")


def login_github_cli(executable: Path | None = None) -> None:
    """Run GitHub CLI's official browser flow without receiving its token."""
    gh = executable or resolve_gh_executable()
    try:
        result = subprocess.run(
            (
                str(gh),
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--web",
                "--clipboard",
                "--git-protocol",
                "https",
                "--scopes",
                "user",
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_minimal_environment(),
            timeout=GH_LOGIN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubCopilotProbeError("GitHub CLI sign-in did not complete") from exc
    if result.returncode != 0:
        raise GitHubCopilotProbeError("GitHub CLI sign-in did not complete")


def _read_login(executable: Path) -> str:
    payload = _run_gh(
        executable,
        (
            "api",
            "--method",
            "GET",
            "--header",
            f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
            "user",
            "--jq",
            ".login",
        ),
    )
    try:
        login = payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise GitHubCopilotProbeError("GitHub returned an invalid account identifier") from exc
    if not _LOGIN_PATTERN.fullmatch(login) or login.endswith("-"):
        raise GitHubCopilotProbeError("GitHub returned an invalid account identifier")
    return login


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if number < 0:
        raise ValueError(f"{field} cannot be negative")
    return number


def parse_premium_request_usage(
    payload: bytes,
    *,
    collected_at: datetime | None = None,
) -> ProviderSnapshot:
    if len(payload) > MAX_GH_OUTPUT_BYTES:
        raise ValueError("GitHub usage payload exceeds the size limit")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub usage payload is invalid") from exc
    if not isinstance(document, dict):
        raise ValueError("GitHub usage payload must be an object")
    items = document.get("usageItems")
    if not isinstance(items, list) or len(items) > 10_000:
        raise ValueError("GitHub usage items are invalid")

    total_requests = 0.0
    matching_items = 0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("GitHub usage item is invalid")
        product = item.get("product")
        if not isinstance(product, str) or product.casefold() != "copilot":
            continue
        if item.get("unitType") != "requests":
            continue
        total_requests += _number(item.get("grossQuantity"), "grossQuantity")
        matching_items += 1

    now = collected_at or utc_now()
    if not matching_items:
        return ProviderSnapshot(
            provider_id="github_copilot",
            display_name="GitHub Copilot",
            status=SnapshotStatus.NO_DATA,
            source=DataSource.OFFICIAL_LOCAL_PROCESS,
            collected_at=now,
            message="GitHub returned no Copilot premium-request usage for this month.",
        )

    last_day = calendar.monthrange(now.year, now.month)[1]
    resets_at = datetime(
        now.year + (1 if now.month == 12 else 0),
        1 if now.month == 12 else now.month + 1,
        1,
        tzinfo=UTC,
    )
    return ProviderSnapshot(
        provider_id="github_copilot",
        display_name="GitHub Copilot",
        status=SnapshotStatus.AVAILABLE,
        source=DataSource.OFFICIAL_LOCAL_PROCESS,
        collected_at=now,
        windows=(
            QuotaWindow(
                id="monthly_premium_requests",
                label="Premium requests this month",
                unit="requests",
                used=total_requests,
                window_seconds=last_day * 24 * 60 * 60,
                resets_at=resets_at,
            ),
        ),
    )


def read_copilot_usage(
    executable: Path | None = None,
    *,
    collected_at: datetime | None = None,
) -> ProviderSnapshot:
    gh = executable or resolve_gh_executable()
    _require_gh_authentication(gh)
    now = collected_at or utc_now()
    login = _read_login(gh)
    try:
        payload = _run_gh(
            gh,
            (
                "api",
                "--method",
                "GET",
                "--header",
                "Accept: application/vnd.github+json",
                "--header",
                f"X-GitHub-Api-Version: {GITHUB_API_VERSION}",
                f"users/{login}/settings/billing/premium_request/usage",
                "--field",
                f"year={now.year}",
                "--field",
                f"month={now.month}",
            ),
        )
    finally:
        # Drop the account identifier before parsing or serializing provider data.
        del login
    return parse_premium_request_usage(payload, collected_at=now)
