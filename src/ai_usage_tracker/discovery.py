from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from .manifest import ProviderManifest


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    provider_id: str
    display_name: str
    detected: bool
    host_detected: bool
    executable: str | None
    detection_confidence: str
    stability: str
    credential_access: str

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "detected": self.detected,
            "host_detected": self.host_detected,
            "executable": self.executable,
            "detection_confidence": self.detection_confidence,
            "stability": self.stability,
            "credential_access": self.credential_access,
        }


def discover_provider(provider: ProviderManifest) -> DiscoveryResult:
    detected_name: str | None = None
    for executable in provider.executables:
        resolved = shutil.which(executable)
        if resolved:
            # Do not expose or persist the user's installation path.
            detected_name = Path(resolved).name
            break
    is_direct = detected_name is not None and provider.executable_role == "provider"
    is_host = detected_name is not None and provider.executable_role == "host"
    return DiscoveryResult(
        provider_id=provider.id,
        display_name=provider.display_name,
        detected=is_direct,
        host_detected=is_host,
        executable=detected_name,
        detection_confidence=(
            "direct_cli" if is_direct else "host_only" if is_host else "none"
        ),
        stability=provider.stability,
        credential_access=provider.credential_access,
    )


def discover_all(providers: tuple[ProviderManifest, ...]) -> tuple[DiscoveryResult, ...]:
    return tuple(discover_provider(provider) for provider in providers)
