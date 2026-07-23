from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import time
from typing import Any, BinaryIO

from ..model import DataSource, ProviderSnapshot, QuotaWindow, SnapshotStatus, utc_now
from ..security import absolute_environment_path


MAX_PROTOCOL_LINE_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0


class CodexProbeError(RuntimeError):
    """A deliberately non-sensitive Codex probe failure."""


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _integer(value: Any, field: str) -> int:
    number = _number(value, field)
    if not number.is_integer():
        raise ValueError(f"{field} must be an integer")
    return int(number)


def _reset_time(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    timestamp = _integer(value, field)
    if timestamp < 0:
        raise ValueError(f"{field} cannot be negative")
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _window_label(duration_minutes: int | None, fallback: str) -> str:
    labels = {
        300: "5 hour",
        1440: "24 hour",
        10080: "7 day",
    }
    if duration_minutes is None:
        return fallback
    return labels.get(duration_minutes, f"{duration_minutes} minute")


def parse_rate_limits_result(
    result: Any,
    *,
    source: DataSource = DataSource.OFFICIAL_LOCAL_PROCESS,
    collected_at: datetime | None = None,
) -> ProviderSnapshot:
    if not isinstance(result, dict):
        raise ValueError("Codex rate-limit result must be an object")
    rate_limits = result.get("rateLimits")
    now = collected_at or utc_now()
    if rate_limits is None:
        return ProviderSnapshot(
            provider_id="codex",
            display_name="Codex",
            status=SnapshotStatus.NO_DATA,
            source=source,
            collected_at=now,
            message="Codex returned no rate-limit snapshot.",
        )
    if not isinstance(rate_limits, dict):
        raise ValueError("Codex rateLimits must be an object")

    windows: list[QuotaWindow] = []
    for window_id, fallback_label in (
        ("primary", "Primary window"),
        ("secondary", "Secondary window"),
    ):
        raw_window = rate_limits.get(window_id)
        if raw_window is None:
            continue
        if not isinstance(raw_window, dict):
            raise ValueError(f"Codex {window_id} window must be an object")
        duration_value = raw_window.get("windowDurationMins")
        duration_minutes = (
            None
            if duration_value is None
            else _integer(duration_value, f"rateLimits.{window_id}.windowDurationMins")
        )
        if duration_minutes is not None and duration_minutes <= 0:
            raise ValueError("Codex window duration must be positive")
        windows.append(
            QuotaWindow(
                id=window_id,
                label=_window_label(duration_minutes, fallback_label),
                unit="percent",
                used_percent=_number(
                    raw_window.get("usedPercent"),
                    f"rateLimits.{window_id}.usedPercent",
                ),
                window_seconds=(
                    duration_minutes * 60 if duration_minutes is not None else None
                ),
                resets_at=_reset_time(
                    raw_window.get("resetsAt"),
                    f"rateLimits.{window_id}.resetsAt",
                ),
            )
        )

    if not windows:
        return ProviderSnapshot(
            provider_id="codex",
            display_name="Codex",
            status=SnapshotStatus.NO_DATA,
            source=source,
            collected_at=now,
            message="Codex returned no supported quota windows.",
        )
    return ProviderSnapshot(
        provider_id="codex",
        display_name="Codex",
        status=SnapshotStatus.AVAILABLE,
        source=source,
        collected_at=now,
        windows=tuple(windows),
    )


def _minimal_environment() -> dict[str, str]:
    # Authentication environment variables are intentionally excluded. Codex
    # may use its own OS-vault or file-backed login without exposing it here.
    allowed = {
        "APPDATA",
        "CODEX_HOME",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["NO_COLOR"] = "1"
    return environment


class _LineReader:
    def __init__(self, stream: BinaryIO) -> None:
        self._queue: Queue[bytes | BaseException | None] = Queue()
        self._thread = Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: BinaryIO) -> None:
        try:
            while True:
                line = stream.readline(MAX_PROTOCOL_LINE_BYTES + 1)
                if not line:
                    self._queue.put(None)
                    return
                if len(line) > MAX_PROTOCOL_LINE_BYTES:
                    self._queue.put(CodexProbeError("Codex protocol line exceeded the size limit"))
                    return
                self._queue.put(line)
        except BaseException as exc:  # pragma: no cover - defensive thread boundary
            self._queue.put(exc)

    def next(self, timeout: float) -> bytes | None:
        try:
            item = self._queue.get(timeout=timeout)
        except Empty as exc:
            raise CodexProbeError("Codex app-server timed out") from exc
        if isinstance(item, BaseException):
            raise CodexProbeError("Codex app-server output could not be read") from item
        return item


def _send(stream: BinaryIO, message: dict[str, Any]) -> None:
    encoded = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    stream.write(encoded)
    stream.flush()


def _wait_for_response(
    reader: _LineReader, request_id: int, deadline: float
) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexProbeError("Codex app-server timed out")
        line = reader.next(remaining)
        if line is None:
            raise CodexProbeError("Codex app-server exited before responding")
        try:
            message = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Ignore non-protocol diagnostics without retaining or returning them.
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            # Notifications can contain local paths and opaque identifiers. They
            # are deliberately discarded here rather than logged.
            continue
        if "error" in message:
            raise CodexProbeError("Codex app-server rejected the rate-limit request")
        result = message.get("result")
        if not isinstance(result, dict):
            raise CodexProbeError("Codex app-server returned an invalid response")
        return result


def _automatic_codex_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []

    install_dir = absolute_environment_path("CODEX_INSTALL_DIR")
    if install_dir:
        executable_name = "codex.exe" if os.name == "nt" else "codex"
        candidates.append(install_dir / executable_name)

    home = Path.home()
    if sys.platform == "darwin":
        candidates.extend(
            (
                Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
                home / "Applications" / "ChatGPT.app" / "Contents" / "Resources" / "codex",
                home / ".local" / "bin" / "codex",
            )
        )
    elif os.name == "nt":
        local_app_data = absolute_environment_path("LOCALAPPDATA")
        if local_app_data:
            candidates.append(
                local_app_data
                / "Programs"
                / "OpenAI"
                / "Codex"
                / "bin"
                / "codex.exe"
            )
    else:
        candidates.append(home / ".local" / "bin" / "codex")

    discovered = shutil.which("codex")
    if discovered:
        candidates.append(Path(discovered))
    return tuple(candidates)


def _usable_executable(candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        return None
    return resolved


def resolve_codex_executable(explicit_path: str | None = None) -> Path:
    if explicit_path:
        resolved = _usable_executable(Path(explicit_path).expanduser())
        if resolved is None:
            raise CodexProbeError("The explicit Codex executable is not usable")
        return resolved

    for candidate in _automatic_codex_candidates():
        resolved = _usable_executable(candidate)
        if resolved is not None:
            return resolved
    raise CodexProbeError(
        "Codex executable was not found in PATH or a standard install location; "
        "pass --executable with the trusted Codex binary path"
    )


def read_rate_limits(
    executable: Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProviderSnapshot:
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise ValueError("Codex timeout must be between 0 and 60 seconds")
    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryDirectory(prefix="ai-usage-codex-") as private_cwd:
        process = subprocess.Popen(
            [
                str(executable),
                "app-server",
                "--listen",
                "stdio://",
                "-c",
                "analytics.enabled=false",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=private_cwd,
            env=_minimal_environment(),
            shell=False,
        )
        try:
            if process.stdin is None or process.stdout is None:  # pragma: no cover
                raise CodexProbeError("Codex app-server pipes were unavailable")
            reader = _LineReader(process.stdout)
            _send(
                process.stdin,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "ai-usage-tracker",
                            "title": "AI Usage Tracker",
                            "version": "0.1.0",
                        },
                        "capabilities": {
                            "experimentalApi": True,
                            "optOutNotificationMethods": [],
                        },
                    },
                },
            )
            _wait_for_response(reader, 1, deadline)
            _send(process.stdin, {"method": "initialized"})
            _send(
                process.stdin,
                {"id": 2, "method": "account/rateLimits/read", "params": None},
            )
            result = _wait_for_response(reader, 2, deadline)
            return parse_rate_limits_result(result)
        finally:
            if process.stdin is not None:
                process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
