from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
from typing import Any, Literal, Sequence


MAX_CLAUDE_SETTINGS_BYTES = 1024 * 1024


class ClaudeSetupError(RuntimeError):
    """A non-sensitive Claude status-line setup failure."""


def default_claude_settings() -> Path:
    return Path.home() / ".claude" / "settings.json"


def widget_capture_argv() -> tuple[str, ...]:
    if getattr(sys, "frozen", False):
        return (str(Path(sys.executable).resolve()), "--claude-capture")
    return (
        str(Path(sys.executable).resolve()),
        "-m",
        "ai_usage_tracker.widget",
        "--claude-capture",
    )


def format_status_command(argv: Sequence[str]) -> str:
    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise ValueError("Claude capture command is invalid")
    if any(any(character in value for character in "\r\n\0") for value in argv):
        raise ValueError("Claude capture command is invalid")
    if os.name == "nt":
        # Claude runs status commands through Git Bash when present and
        # PowerShell otherwise. Calling PowerShell explicitly makes a quoted
        # frozen-app path work consistently in both environments.
        if any('"' in value for value in argv):
            raise ValueError("Claude capture command is invalid")
        powershell_arguments = " ".join(
            f"'{value.replace(chr(39), chr(39) * 2)}'" for value in argv
        )
        return (
            "powershell -NoProfile -NonInteractive -Command "
            f'"& {powershell_arguments}"'
        )
    return shlex.join(argv)


def _read_settings(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ClaudeSetupError("Claude settings is a symlink; refusing to modify it")
    if not path.exists():
        return {}
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ClaudeSetupError("Claude settings is not a regular file")
        if metadata.st_size > MAX_CLAUDE_SETTINGS_BYTES:
            raise ClaudeSetupError("Claude settings exceeds the size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as settings_file:
            payload = settings_file.read(MAX_CLAUDE_SETTINGS_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaudeSetupError("Claude settings is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ClaudeSetupError("Claude settings must contain a JSON object")
    return document


def install_claude_status_line(
    argv: Sequence[str],
    settings_path: Path | None = None,
) -> bool:
    """Install only our status-line command, preserving every unrelated setting.

    Returns True when the file changed. An existing different status line is never
    overwritten because it may be important to the user or contain sensitive logic.
    """
    path = (settings_path or default_claude_settings()).expanduser()
    parent = path.parent
    if parent.is_symlink():
        raise ClaudeSetupError("Claude settings directory is a symlink")
    command = format_status_command(argv)
    document = _read_settings(path)
    expected = {"type": "command", "command": command}
    existing = document.get("statusLine")
    if existing == expected:
        return False
    if existing is not None:
        raise ClaudeSetupError(
            "Claude already has a different status line; it was left unchanged"
        )

    document["statusLine"] = expected
    payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if len(payload) > MAX_CLAUDE_SETTINGS_BYTES:
        raise ClaudeSetupError("Claude settings would exceed the size limit")

    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".ai-usage-status-", suffix=".tmp", dir=parent
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        if path.is_symlink():
            raise ClaudeSetupError("Claude settings became a symlink")
        os.replace(temporary_name, path)
        temporary_name = ""
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return True


def claude_status_line_state(
    argv: Sequence[str],
    settings_path: Path | None = None,
) -> Literal["absent", "installed", "different"]:
    """Report only whether our exact command is configured, never its contents."""
    path = (settings_path or default_claude_settings()).expanduser()
    document = _read_settings(path)
    existing = document.get("statusLine")
    if existing is None:
        return "absent"
    if (
        isinstance(existing, dict)
        and existing.get("type") == "command"
        and existing.get("command") == format_status_command(argv)
    ):
        return "installed"
    return "different"
