from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from .storage import default_data_dir


SETTINGS_FILENAME = "widget-settings.json"
MAX_SETTINGS_BYTES = 16 * 1024
SUPPORTED_PROVIDERS = frozenset(
    {"antigravity", "claude", "codex", "cursor", "devin", "github_copilot"}
)
ALLOWED_REFRESH_MINUTES = frozenset({2, 5, 10, 15, 30})


@dataclass(frozen=True, slots=True)
class WidgetSettings:
    enabled_providers: frozenset[str] = frozenset()
    refresh_minutes: int = 5
    always_on_top: bool = True

    def __post_init__(self) -> None:
        if not self.enabled_providers <= SUPPORTED_PROVIDERS:
            raise ValueError("settings contain an unsupported provider")
        if self.refresh_minutes not in ALLOWED_REFRESH_MINUTES:
            raise ValueError("settings contain an unsupported refresh interval")
        if not isinstance(self.always_on_top, bool):
            raise ValueError("always_on_top must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "enabled_providers": sorted(self.enabled_providers),
            "refresh_minutes": self.refresh_minutes,
            "always_on_top": self.always_on_top,
        }


def _parse_settings(document: Any) -> WidgetSettings:
    if not isinstance(document, dict):
        raise ValueError("widget settings must be an object")
    expected = {
        "schema_version",
        "enabled_providers",
        "refresh_minutes",
        "always_on_top",
    }
    if set(document) != expected or document.get("schema_version") != 1:
        raise ValueError("widget settings have an unsupported schema")
    providers = document.get("enabled_providers")
    if not isinstance(providers, list) or any(
        not isinstance(provider, str) for provider in providers
    ):
        raise ValueError("enabled_providers must be a list of provider identifiers")
    refresh_minutes = document.get("refresh_minutes")
    if isinstance(refresh_minutes, bool) or not isinstance(refresh_minutes, int):
        raise ValueError("refresh_minutes must be an integer")
    return WidgetSettings(
        enabled_providers=frozenset(providers),
        refresh_minutes=refresh_minutes,
        always_on_top=document.get("always_on_top"),
    )


class WidgetSettingsStore:
    """Stores only non-sensitive UI consent and display preferences."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_data_dir()
        self.path = self.root / SETTINGS_FILENAME

    def load(self) -> WidgetSettings:
        if self.root.is_symlink():
            raise ValueError("refusing to use a symlinked settings directory")
        if self.path.is_symlink():
            raise ValueError("refusing to read symlinked widget settings")
        if not self.path.exists():
            return WidgetSettings()
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        descriptor = os.open(self.path, flags)
        with os.fdopen(descriptor, "rb") as settings_file:
            metadata = os.fstat(settings_file.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("widget settings must be a regular file")
            if metadata.st_size > MAX_SETTINGS_BYTES:
                raise ValueError("widget settings exceed the size limit")
            payload = settings_file.read(MAX_SETTINGS_BYTES + 1)
        if len(payload) > MAX_SETTINGS_BYTES:
            raise ValueError("widget settings exceed the size limit")
        try:
            document = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("widget settings are invalid") from exc
        return _parse_settings(document)

    def save(self, settings: WidgetSettings) -> Path:
        if self.root.is_symlink():
            raise ValueError("refusing to use a symlinked settings directory")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.root, 0o700)
        if self.path.is_symlink():
            raise ValueError("refusing to replace symlinked widget settings")
        payload = json.dumps(
            settings.to_dict(), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if len(payload) > MAX_SETTINGS_BYTES:  # pragma: no cover - fixed schema
            raise ValueError("widget settings exceed the size limit")

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".widget-settings.",
                suffix=".tmp",
                dir=self.root,
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                if os.name != "nt":
                    os.chmod(temporary.name, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
            temporary_name = None
            return self.path
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass
