from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

from .model import ProviderSnapshot
from .security import redact


MAX_SNAPSHOT_BYTES = 256 * 1024
PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SNAPSHOT_KEYS = {
    "schema_version",
    "provider_id",
    "display_name",
    "status",
    "source",
    "collected_at",
    "windows",
    "error_code",
    "message",
}


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AI Usage Tracker"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "AI Usage Tracker"
        return Path.home() / "AppData" / "Local" / "AI Usage Tracker"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data and Path(xdg_data).is_absolute():
        return Path(xdg_data) / "ai-usage-tracker"
    return Path.home() / ".local" / "share" / "ai-usage-tracker"


def _validate_provider_id(provider_id: str) -> None:
    if not PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("invalid provider identifier")


def _validate_document(document: Any, provider_id: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("snapshot must be an object")
    if set(document) != SNAPSHOT_KEYS:
        raise ValueError("snapshot contains unexpected fields")
    if document.get("schema_version") != 1:
        raise ValueError("unsupported snapshot schema")
    if document.get("provider_id") != provider_id:
        raise ValueError("snapshot provider does not match its filename")
    if not isinstance(document.get("windows"), list):
        raise ValueError("snapshot windows must be an array")
    return document


class SnapshotStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_data_dir()) / "snapshots"

    def _path(self, provider_id: str) -> Path:
        _validate_provider_id(provider_id)
        return self.root / f"{provider_id}.json"

    def save(self, snapshot: ProviderSnapshot) -> Path:
        target = self._path(snapshot.provider_id)
        if self.root.is_symlink():
            raise ValueError("refusing to use a symlinked snapshot directory")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.root, 0o700)
        if target.is_symlink():
            raise ValueError("refusing to replace a symlinked snapshot")

        document = _validate_document(redact(snapshot.to_dict()), snapshot.provider_id)
        encoded = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise ValueError("normalized snapshot exceeds the size limit")

        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{snapshot.provider_id}.",
                suffix=".tmp",
                dir=self.root,
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary.name, 0o600)
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, target)
            os.chmod(target, 0o600)
            temporary_name = None
            return target
        finally:
            if temporary_name is not None:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass

    def load(self, provider_id: str) -> dict[str, Any] | None:
        target = self._path(provider_id)
        if self.root.is_symlink():
            raise ValueError("refusing to use a symlinked snapshot directory")
        if target.is_symlink():
            raise ValueError("refusing to read a symlinked snapshot")
        if not target.exists():
            return None
        size = target.stat().st_size
        if size > MAX_SNAPSHOT_BYTES:
            raise ValueError("normalized snapshot exceeds the size limit")
        document = json.loads(target.read_bytes())
        return _validate_document(document, provider_id)
