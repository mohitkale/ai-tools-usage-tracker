from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .discovery import discover_all
from .fixtures import all_snapshots
from .manifest import load_manifest
from .providers.claude import MAX_STATUS_PAYLOAD_BYTES, parse_status_payload
from .security import redact


def _print_json(value: Any, pretty: bool) -> None:
    safe_value = redact(value)
    if pretty:
        print(json.dumps(safe_value, indent=2, sort_keys=True))
    else:
        print(json.dumps(safe_value, separators=(",", ":"), sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-usage-probe",
        description="Credential-safe AI usage collector probe",
    )
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "discover",
        help="detect command-line tools using PATH metadata only",
    )
    subparsers.add_parser(
        "fixture",
        help="emit synthetic UI-ready quota data without filesystem or network access",
    )
    subparsers.add_parser(
        "claude-status",
        help="parse official Claude status-line JSON from stdin",
    )
    subparsers.add_parser(
        "permissions",
        help="show the reviewed permission declaration for every provider",
    )
    return parser


def _read_bounded_stdin(limit: int) -> bytes:
    payload = sys.stdin.buffer.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("stdin payload exceeds the size limit")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        providers = load_manifest()
        if args.command == "discover":
            _print_json(
                {"schema_version": 1, "providers": [r.to_dict() for r in discover_all(providers)]},
                args.pretty,
            )
        elif args.command == "fixture":
            snapshots = [snapshot.to_dict() for snapshot in all_snapshots()]
            _print_json({"schema_version": 1, "snapshots": snapshots}, args.pretty)
        elif args.command == "claude-status":
            snapshot = parse_status_payload(_read_bounded_stdin(MAX_STATUS_PAYLOAD_BYTES))
            _print_json(snapshot.to_dict(), args.pretty)
        elif args.command == "permissions":
            _print_json(
                {
                    "schema_version": 1,
                    "providers": [
                        {
                            "id": provider.id,
                            "enabled_by_default": provider.enabled_by_default,
                            "permissions": sorted(provider.permissions),
                            "credential_access": provider.credential_access,
                            "network_hosts": list(provider.network_hosts),
                        }
                        for provider in providers
                    ],
                },
                args.pretty,
            )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print_json(
            {
                "schema_version": 1,
                "status": "error",
                "error_code": "probe_failed",
                "message": str(exc),
            },
            args.pretty,
        )
        return 2

