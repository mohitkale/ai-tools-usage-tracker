from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "session_token",
    "token",
}

TOKEN_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:ghp_|github_pat_|sk-ant-|sk-proj-)[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"CANARY_SECRET_[A-Za-z0-9_-]+"),
)


def absolute_environment_path(name: str) -> Path | None:
    """Return an environment-provided directory only when it is absolute."""
    value = os.environ.get(name)
    if not value:
        return None
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else None


def _is_sensitive_key(value: Any) -> bool:
    key = str(value).casefold()
    if key in SENSITIVE_KEYS:
        return True
    canonical = re.sub(r"[^a-z0-9]", "", key)
    return any(
        canonical.endswith(suffix)
        for suffix in (
            "accesstoken",
            "apikey",
            "authorization",
            "cookie",
            "credentials",
            "password",
            "refreshtoken",
            "secret",
            "sessiontoken",
        )
    )


def redact_text(value: str) -> str:
    redacted = value
    for pattern in TOKEN_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                output[key] = "[REDACTED]"
            else:
                output[key] = redact(item)
        return output
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def validate_official_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise ValueError("provider URLs must use HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("provider URLs cannot contain credentials")
    if parsed.port not in (None, 443):
        raise ValueError("provider URLs must use the standard HTTPS port")
    if hostname not in {host.lower() for host in allowed_hosts}:
        raise ValueError("provider hostname is not allowlisted")
    return hostname


def validate_redirect(
    original_url: str, redirect_url: str, allowed_hosts: tuple[str, ...]
) -> None:
    original_host = validate_official_url(original_url, allowed_hosts)
    redirect_host = validate_official_url(redirect_url, allowed_hosts)
    if original_host != redirect_host:
        raise ValueError("cross-host redirects are forbidden")
