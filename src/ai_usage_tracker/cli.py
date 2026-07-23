from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .discovery import discover_all
from .fixtures import all_snapshots
from .manifest import load_manifest
from .providers.claude import MAX_STATUS_PAYLOAD_BYTES, parse_status_payload
from .providers.codex import CodexProbeError, read_rate_limits, resolve_codex_executable
from .providers.cursor import CursorProbeError, read_cursor_usage
from .security import redact
from .security import redact_text
from .storage import SnapshotStore
from .verification import verify_ui_readiness


def _print_json(value: Any, pretty: bool) -> None:
    safe_value = redact(value)
    if pretty:
        print(json.dumps(safe_value, indent=2, sort_keys=True))
    else:
        print(json.dumps(safe_value, separators=(",", ":"), sort_keys=True))


def _safe_error_message(exc: BaseException) -> str:
    if isinstance(exc, OSError):
        return "A local filesystem or process operation failed."
    if isinstance(exc, json.JSONDecodeError):
        return "A local JSON document was invalid."
    return redact_text(str(exc))[:500]


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
        "verify-fixtures",
        help="check whether fixture snapshots satisfy the future UI contract",
    )
    subparsers.add_parser(
        "claude-status",
        help="parse official Claude status-line JSON from stdin",
    )
    claude_capture = subparsers.add_parser(
        "claude-capture",
        help="normalize Claude status-line JSON and store only the latest snapshot",
    )
    claude_capture.add_argument(
        "--data-dir",
        help="override the app data directory; intended for testing and portable installs",
    )
    snapshot = subparsers.add_parser(
        "snapshot",
        help="read a previously normalized local snapshot",
    )
    snapshot.add_argument("--provider", required=True, choices=("claude", "codex"))
    snapshot.add_argument(
        "--data-dir",
        help="override the app data directory; intended for testing and portable installs",
    )
    codex_live = subparsers.add_parser(
        "codex-live",
        help="read rate limits through the official local Codex app-server",
    )
    codex_live.add_argument(
        "--allow-official-process",
        action="store_true",
        help="confirm that Codex may use its own authentication and provider connection",
    )
    codex_live.add_argument(
        "--executable",
        help="explicit Codex executable path; otherwise resolve codex from PATH",
    )
    cursor_live = subparsers.add_parser(
        "cursor-live",
        help="read individual usage through Cursor's private read-only interface",
    )
    cursor_live.add_argument(
        "--allow-private-cursor-session",
        action="store_true",
        help="allow one exact Cursor access-token read and one request to api2.cursor.sh",
    )
    cursor_live.add_argument(
        "--database",
        help="override the Cursor state database path",
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
        elif args.command == "verify-fixtures":
            verification = verify_ui_readiness(all_snapshots())
            _print_json(verification, args.pretty)
            if not verification["ui_ready"]:
                return 1
        elif args.command == "claude-status":
            snapshot = parse_status_payload(_read_bounded_stdin(MAX_STATUS_PAYLOAD_BYTES))
            _print_json(snapshot.to_dict(), args.pretty)
        elif args.command == "claude-capture":
            snapshot = parse_status_payload(_read_bounded_stdin(MAX_STATUS_PAYLOAD_BYTES))
            store = SnapshotStore(Path(args.data_dir) if args.data_dir else None)
            store.save(snapshot)
            percentages = [
                f"{window.label}: {window.used_percent:.0f}%"
                for window in snapshot.windows
                if window.used_percent is not None
            ]
            print("Claude | " + " | ".join(percentages) if percentages else "Claude usage pending")
        elif args.command == "snapshot":
            store = SnapshotStore(Path(args.data_dir) if args.data_dir else None)
            document = store.load(args.provider)
            if document is None:
                _print_json(
                    {
                        "schema_version": 1,
                        "provider_id": args.provider,
                        "status": "no_data",
                    },
                    args.pretty,
                )
            else:
                _print_json(document, args.pretty)
        elif args.command == "codex-live":
            if not args.allow_official_process:
                raise ValueError(
                    "codex-live requires --allow-official-process; no process was started"
                )
            executable = resolve_codex_executable(args.executable)
            snapshot = read_rate_limits(executable)
            _print_json(snapshot.to_dict(), args.pretty)
        elif args.command == "cursor-live":
            if not args.allow_private_cursor_session:
                raise ValueError(
                    "cursor-live requires --allow-private-cursor-session; no credential was read"
                )
            database = Path(args.database) if args.database else None
            snapshot = read_cursor_usage(database)
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
                            "executables": list(provider.executables),
                            "paths": list(provider.paths),
                        }
                        for provider in providers
                    ],
                },
                args.pretty,
            )
        return 0
    except (
        CodexProbeError,
        CursorProbeError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _print_json(
            {
                "schema_version": 1,
                "status": "error",
                "error_code": "probe_failed",
                "message": _safe_error_message(exc),
            },
            args.pretty,
        )
        return 2
