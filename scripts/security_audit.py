#!/usr/bin/env python3
"""Fail closed on common repository security and publishing mistakes."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from ai_usage_tracker.manifest import load_manifest  # noqa: E402


MAX_SCANNED_FILE_BYTES = 2 * 1024 * 1024
FORBIDDEN_BASENAMES = {
    ".env",
    "auth.json",
    "credentials.json",
    "secrets.json",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
}
SECRET_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github_token": re.compile(rb"\b(?:ghp_|github_pat_)[A-Za-z0-9_-]{20,}\b"),
    "openai_or_anthropic_key": re.compile(
        rb"\b(?:sk-ant-|sk-proj-)[A-Za-z0-9_-]{20,}\b"
    ),
    "aws_access_key": re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    "google_api_key": re.compile(rb"\bAIza[A-Za-z0-9_-]{20,}\b"),
}
FORBIDDEN_CODE = {
    "dynamic_eval": re.compile(rb"(?m)^\s*(?:eval|exec)\s*\("),
    "shell_execution": re.compile(rb"shell\s*=\s*True"),
}


def _git(*arguments: str) -> bytes:
    result = subprocess.run(
        ("git", *arguments),
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("git repository inspection failed")
    return result.stdout


def _repository_files() -> tuple[Path, ...]:
    payload = _git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    paths: list[Path] = []
    for encoded in payload.split(b"\0"):
        if not encoded:
            continue
        relative = encoded.decode("utf-8", errors="strict")
        path = PROJECT_ROOT / relative
        if path.is_file() and not path.is_symlink():
            paths.append(path)
    return tuple(paths)


def _contains_test_canary(match: bytes) -> bool:
    return b"CANARY" in match.upper()


def _scan_payload(
    payload: bytes,
    *,
    location: str,
    findings: list[dict[str, str]],
) -> None:
    for finding_type, pattern in SECRET_PATTERNS.items():
        for match in pattern.finditer(payload):
            if not _contains_test_canary(match.group(0)):
                findings.append({"type": finding_type, "location": location})
                break
    for finding_type, pattern in FORBIDDEN_CODE.items():
        if pattern.search(payload):
            findings.append({"type": finding_type, "location": location})


def _scan_files(findings: list[dict[str, str]]) -> int:
    scanned = 0
    for path in _repository_files():
        relative = path.relative_to(PROJECT_ROOT)
        lower_name = path.name.casefold()
        if lower_name in FORBIDDEN_BASENAMES or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append(
                {"type": "sensitive_filename", "location": relative.as_posix()}
            )
            continue
        if path.stat().st_size > MAX_SCANNED_FILE_BYTES:
            findings.append(
                {"type": "oversized_repository_file", "location": relative.as_posix()}
            )
            continue
        payload = path.read_bytes()
        if b"\0" in payload:
            continue
        _scan_payload(payload, location=relative.as_posix(), findings=findings)
        scanned += 1
    return scanned


def _scan_history(findings: list[dict[str, str]]) -> None:
    history = _git("log", "-p", "--all", "--no-ext-diff", "--no-textconv")
    for finding_type, pattern in SECRET_PATTERNS.items():
        unsafe = next(
            (
                match.group(0)
                for match in pattern.finditer(history)
                if not _contains_test_canary(match.group(0))
            ),
            None,
        )
        if unsafe is not None:
            findings.append({"type": finding_type, "location": "git-history"})


def _check_manifest(findings: list[dict[str, str]]) -> None:
    providers = load_manifest()
    for provider in providers:
        if provider.enabled_by_default:
            findings.append(
                {"type": "provider_enabled_by_default", "location": provider.id}
            )
        if provider.network_hosts and "provider_network" not in provider.permissions:
            findings.append(
                {"type": "undeclared_provider_network", "location": provider.id}
            )
        if "private_state" in provider.permissions and provider.stability == "stable":
            findings.append(
                {"type": "private_adapter_marked_stable", "location": provider.id}
            )
        if (
            {"local_payload", "private_state"} & provider.permissions
            and not provider.paths
        ):
            findings.append(
                {"type": "undeclared_local_path", "location": provider.id}
            )


def _check_dependencies(findings: list[dict[str, str]]) -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text("utf-8"))
    dependencies = project.get("project", {}).get("dependencies")
    if dependencies != []:
        findings.append(
            {"type": "runtime_dependencies_present", "location": "pyproject.toml"}
        )


def main() -> int:
    findings: list[dict[str, str]] = []
    scanned_files = _scan_files(findings)
    _scan_history(findings)
    _check_manifest(findings)
    _check_dependencies(findings)
    result = {
        "schema_version": 1,
        "status": "pass" if not findings else "fail",
        "scanned_files": scanned_files,
        "findings": findings,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
