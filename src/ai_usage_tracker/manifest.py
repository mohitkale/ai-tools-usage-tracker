from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


ALLOWED_PERMISSIONS = {
    "local_metadata",
    "local_process",
    "local_payload",
    "delegated_provider_network",
    "provider_network",
    "credential_reference",
    "private_state",
}


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    id: str
    display_name: str
    stability: str
    enabled_by_default: bool
    permissions: frozenset[str]
    credential_access: str
    network_hosts: tuple[str, ...]
    executables: tuple[str, ...]
    executable_role: str
    notes: str


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "providers.json"


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"manifest field {field!r} must be a non-empty string")
    return value


def load_manifest(path: Path | None = None) -> tuple[ProviderManifest, ...]:
    manifest_path = path or default_manifest_path()
    raw = manifest_path.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError("provider manifest exceeds the size limit")
    document = json.loads(raw)
    if document.get("schema_version") != 1:
        raise ValueError("unsupported provider manifest schema")

    providers: list[ProviderManifest] = []
    seen_ids: set[str] = set()
    for item in document.get("providers", []):
        provider_id = _required_string(item.get("id"), "id")
        if provider_id in seen_ids:
            raise ValueError(f"duplicate provider id: {provider_id}")
        seen_ids.add(provider_id)

        permissions = frozenset(item.get("permissions", []))
        unknown = permissions - ALLOWED_PERMISSIONS
        if unknown:
            raise ValueError(f"unknown permissions for {provider_id}: {sorted(unknown)}")

        network_hosts = tuple(item.get("network_hosts", []))
        if any("/" in host or ":" in host or not host for host in network_hosts):
            raise ValueError(f"invalid network host declaration for {provider_id}")
        if network_hosts and "provider_network" not in permissions:
            raise ValueError(f"{provider_id} declares hosts without network permission")

        discovery = item.get("discovery", {})
        executable_role = discovery.get("executable_role", "provider")
        if executable_role not in {"provider", "host"}:
            raise ValueError(f"invalid executable role for {provider_id}")

        providers.append(
            ProviderManifest(
                id=provider_id,
                display_name=_required_string(item.get("display_name"), "display_name"),
                stability=_required_string(item.get("stability"), "stability"),
                enabled_by_default=item.get("enabled_by_default") is True,
                permissions=permissions,
                credential_access=_required_string(
                    item.get("credential_access"), "credential_access"
                ),
                network_hosts=network_hosts,
                executables=tuple(discovery.get("executables", [])),
                executable_role=executable_role,
                notes=_required_string(item.get("notes"), "notes"),
            )
        )
    return tuple(providers)
