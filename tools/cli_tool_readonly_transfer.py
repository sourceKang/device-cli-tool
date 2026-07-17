from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from cli_tool.reporting.redaction import redact
from cli_tool.reporting.transfer import (
    download_payload,
    file_info_payload,
    file_list_payload,
    identifier_reference,
)
from cli_tool.transfer.sftp import DEFAULT_MAX_DOWNLOAD_BYTES, SftpReadOnlyClient


DEFAULT_PASSWORD_ENV = "CLI_TOOL_SFTP_PASSWORD"
DEFAULT_REPORT_DIR = "reports/cli-tool-transfer"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_transfer(args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="device-cli-transfer",
        description="Run one read-only SFTP operation with fail-closed safeguards.",
    )
    parser.add_argument("operation", choices=["list", "stat", "exists", "download"])
    parser.add_argument("--host", required=True, help="SFTP host or IP.")
    parser.add_argument("--port", type=int, default=22, help="SFTP port.")
    parser.add_argument("--username", required=True, help="SFTP username.")
    parser.add_argument(
        "--password-env",
        default=DEFAULT_PASSWORD_ENV,
        help="Environment variable containing the SFTP password.",
    )
    parser.add_argument(
        "--known-hosts",
        help="Additional OpenSSH known_hosts file. System host keys are always loaded.",
    )
    parser.add_argument(
        "--allow-unknown-host-key",
        action="store_true",
        help="Explicitly allow an unknown host key with a warning. Never use in CI.",
    )
    parser.add_argument("--remote-root", default="/", help="Allowed absolute remote root.")
    parser.add_argument("--remote-path", default=".", help="Path inside --remote-root.")
    parser.add_argument("--local-path", help="Local destination required by download.")
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help="Maximum allowed download size.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow download to atomically replace an existing local file.",
    )
    parser.add_argument(
        "--include-paths",
        action="store_true",
        help="Include remote/local paths and target identifiers in stdout and report.",
    )
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    return parser.parse_args(argv)


def run_transfer(args: argparse.Namespace) -> int:
    _validate_args(args)
    password = os.environ.get(args.password_env)
    if not password:
        raise SystemExit(f"missing SFTP password env var: {args.password_env}")

    started = time.monotonic()
    outcome: dict[str, object] = {}
    error: Exception | None = None
    try:
        with SftpReadOnlyClient(
            host=args.host,
            port=args.port,
            username=args.username,
            password=password,
            timeout=15.0,
            known_hosts_path=args.known_hosts,
            allow_unknown_host_key=args.allow_unknown_host_key,
            remote_root=args.remote_root,
            max_download_bytes=args.max_download_bytes,
        ) as client:
            outcome = _run_operation(client, args)
    except Exception as caught:
        error = caught

    report = _build_report(
        args,
        outcome=outcome,
        error=error,
        duration_seconds=round(time.monotonic() - started, 3),
    )
    report_path = None if args.no_report else _write_report(report, Path(args.report_dir))
    report_path_value = (
        identifier_reference(str(report_path), include_value=args.include_paths)
        if report_path
        else None
    )
    print(
        json.dumps(
            redact({**report, "report_path": report_path_value}),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if error is None else 1


def _run_operation(client: SftpReadOnlyClient, args: argparse.Namespace) -> dict[str, object]:
    if args.operation == "list":
        items = client.list_files(args.remote_path)
        return {
            "count": len(items),
            "files": file_list_payload(items, include_paths=args.include_paths),
        }
    if args.operation == "stat":
        info = client.stat(args.remote_path)
        return {"file": file_info_payload(info, include_paths=args.include_paths)}
    if args.operation == "exists":
        return {"exists": client.exists(args.remote_path)}
    result = client.download(args.remote_path, args.local_path, overwrite=args.overwrite)
    return {"download": download_payload(result, include_paths=args.include_paths)}


def _validate_args(args: argparse.Namespace) -> None:
    if not args.password_env.strip():
        raise SystemExit("--password-env must be a non-empty environment variable name")
    if args.port < 1 or args.port > 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.max_download_bytes < 1:
        raise SystemExit("--max-download-bytes must be greater than 0")
    if args.operation == "download" and not args.local_path:
        raise SystemExit("--local-path is required for download")
    if args.operation != "download" and args.local_path:
        raise SystemExit("--local-path is only valid for download")
    if args.operation != "download" and args.overwrite:
        raise SystemExit("--overwrite is only valid for download")


def _build_report(
    args: argparse.Namespace,
    *,
    outcome: dict[str, object],
    error: Exception | None,
    duration_seconds: float,
) -> dict[str, object]:
    include = args.include_paths
    return {
        "tool": "cli_tool_readonly_transfer",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": duration_seconds,
        "passed": error is None,
        "protocol": "sftp",
        "operation": args.operation,
        "target": identifier_reference(args.host, include_value=include),
        "username": identifier_reference(args.username, include_value=include),
        "remote_root": identifier_reference(args.remote_root, include_value=include),
        "remote_path": identifier_reference(args.remote_path, include_value=include),
        "host_key_verification": (
            "allow_unknown_with_warning" if args.allow_unknown_host_key else "strict"
        ),
        "custom_known_hosts": bool(args.known_hosts),
        "max_download_bytes": args.max_download_bytes,
        "outcome": outcome,
        "error_type": type(error).__name__ if error else None,
        "error_message": _safe_error_message(error),
    }


def _safe_error_message(error: Exception | None) -> str | None:
    if error is None:
        return None
    return "operation failed; inspect error_type and trusted local diagnostics"


def _write_report(report: dict[str, object], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = report_dir / f"cli_tool_readonly_transfer_{timestamp}.json"
    path.write_text(json.dumps(redact(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _package_version() -> str:
    try:
        return version("device-cli-tool")
    except PackageNotFoundError:
        return "development"


if __name__ == "__main__":
    raise SystemExit(main())
