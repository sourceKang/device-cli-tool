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
from cli_tool.transfer.ftp import DEFAULT_PORTS as FTP_DEFAULT_PORTS
from cli_tool.transfer.ftp import FTP_PROTOCOLS, FtpReadOnlyClient
from cli_tool.transfer.sftp import DEFAULT_MAX_DOWNLOAD_BYTES, SftpReadOnlyClient


DEFAULT_SFTP_PASSWORD_ENV = "CLI_TOOL_SFTP_PASSWORD"
DEFAULT_FTP_PASSWORD_ENV = "CLI_TOOL_FTP_PASSWORD"
DEFAULT_REPORT_DIR = "reports/cli-tool-transfer"
DEFAULT_TIMEOUT = 15.0
PROTOCOLS = ("sftp", "ftps", "ftps-implicit", "ftp")
DEFAULT_PORTS = {"sftp": 22, **FTP_DEFAULT_PORTS}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_transfer(args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="device-cli-transfer",
        description="Run one fail-closed read-only SFTP, FTPS or FTP operation.",
    )
    parser.add_argument("operation", choices=["list", "stat", "exists", "download"])
    parser.add_argument("--protocol", choices=PROTOCOLS, default="sftp")
    parser.add_argument("--host", required=True, help="Transfer host or IP.")
    parser.add_argument("--port", type=int, help="Protocol port; defaults by protocol.")
    parser.add_argument("--username", required=True, help="Transfer username.")
    parser.add_argument(
        "--password-env",
        help=(
            "Environment variable containing the password. Defaults to "
            "CLI_TOOL_SFTP_PASSWORD or CLI_TOOL_FTP_PASSWORD by protocol."
        ),
    )
    parser.add_argument("--private-key", help="SFTP private key file used instead of password.")
    parser.add_argument(
        "--private-key-passphrase-env",
        help="Environment variable containing the SFTP private key passphrase.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Connect, login and transfer timeout in seconds.",
    )
    parser.add_argument(
        "--known-hosts",
        help="SFTP additional OpenSSH known_hosts file.",
    )
    parser.add_argument(
        "--allow-unknown-host-key",
        action="store_true",
        help="Explicitly allow an unknown SFTP host key. Never use in CI.",
    )
    parser.add_argument("--ca-file", help="FTPS CA bundle used in addition to platform trust.")
    parser.add_argument(
        "--allow-unverified-tls",
        action="store_true",
        help="Explicitly disable FTPS certificate verification. Never use in CI.",
    )
    parser.add_argument(
        "--allow-insecure-ftp",
        action="store_true",
        help="Allow plaintext FTP for an isolated lab.",
    )
    parser.add_argument(
        "--active-mode",
        action="store_true",
        help="Use FTP active mode instead of passive mode.",
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
        help="Include paths and target identifiers in stdout and report.",
    )
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    return parser.parse_args(argv)


def run_transfer(args: argparse.Namespace) -> int:
    _validate_args(args)
    port = args.port if args.port is not None else DEFAULT_PORTS[args.protocol]
    password, private_key_passphrase, auth_method = _resolve_auth(args)

    started = time.monotonic()
    outcome: dict[str, object] = {}
    error: Exception | None = None
    try:
        with _build_client(
            args,
            port=port,
            password=password,
            private_key_passphrase=private_key_passphrase,
        ) as client:
            outcome = _run_operation(client, args)
    except Exception as caught:
        error = caught

    report = _build_report(
        args,
        port=port,
        outcome=outcome,
        error=error,
        auth_method=auth_method,
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


def _build_client(
    args: argparse.Namespace,
    *,
    port: int,
    password: str | None,
    private_key_passphrase: str | None,
):
    if args.protocol == "sftp":
        return SftpReadOnlyClient(
            host=args.host,
            port=port,
            username=args.username,
            password=password,
            private_key_path=args.private_key,
            private_key_passphrase=private_key_passphrase,
            timeout=args.timeout,
            known_hosts_path=args.known_hosts,
            allow_unknown_host_key=args.allow_unknown_host_key,
            remote_root=args.remote_root,
            max_download_bytes=args.max_download_bytes,
        )
    return FtpReadOnlyClient(
        host=args.host,
        port=port,
        username=args.username,
        password=password or "",
        protocol=args.protocol,
        timeout=args.timeout,
        remote_root=args.remote_root,
        passive=not args.active_mode,
        ca_file=args.ca_file,
        allow_unverified_tls=args.allow_unverified_tls,
        allow_insecure_ftp=args.allow_insecure_ftp,
        max_download_bytes=args.max_download_bytes,
    )


def _run_operation(client, args: argparse.Namespace) -> dict[str, object]:
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
    if args.password_env is not None and not args.password_env.strip():
        raise SystemExit("--password-env must be a non-empty environment variable name")
    if args.private_key_passphrase_env and not args.private_key:
        raise SystemExit("--private-key-passphrase-env requires --private-key")
    if args.port is not None and not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than 0")
    if args.max_download_bytes < 1:
        raise SystemExit("--max-download-bytes must be greater than 0")
    if args.operation == "download" and not args.local_path:
        raise SystemExit("--local-path is required for download")
    if args.operation != "download" and args.local_path:
        raise SystemExit("--local-path is only valid for download")
    if args.operation != "download" and args.overwrite:
        raise SystemExit("--overwrite is only valid for download")

    if args.protocol != "sftp" and (args.private_key or args.private_key_passphrase_env):
        raise SystemExit("private-key authentication is only valid for SFTP")
    if args.protocol != "sftp" and (args.known_hosts or args.allow_unknown_host_key):
        raise SystemExit("known-host options are only valid for SFTP")
    if args.protocol == "sftp" and (
        args.ca_file or args.allow_unverified_tls or args.allow_insecure_ftp or args.active_mode
    ):
        raise SystemExit("FTP/TLS options are not valid for SFTP")
    if args.protocol == "ftp" and not args.allow_insecure_ftp:
        raise SystemExit("FTP is plaintext; pass --allow-insecure-ftp for an isolated lab")
    if args.protocol != "ftp" and args.allow_insecure_ftp:
        raise SystemExit("--allow-insecure-ftp is only valid with --protocol ftp")
    if args.protocol not in FTP_PROTOCOLS and args.active_mode:
        raise SystemExit("--active-mode is only valid for FTP/FTPS")
    if args.protocol not in {"ftps", "ftps-implicit"} and (
        args.ca_file or args.allow_unverified_tls
    ):
        raise SystemExit("TLS certificate options require FTPS")


def _resolve_auth(args: argparse.Namespace) -> tuple[str | None, str | None, str]:
    if args.private_key:
        passphrase = None
        if args.private_key_passphrase_env:
            passphrase = os.environ.get(args.private_key_passphrase_env)
            if passphrase is None:
                raise SystemExit(
                    f"missing private key passphrase env var: {args.private_key_passphrase_env}"
                )
        return None, passphrase, "private_key"

    default_env = (
        DEFAULT_SFTP_PASSWORD_ENV if args.protocol == "sftp" else DEFAULT_FTP_PASSWORD_ENV
    )
    password_env = args.password_env or default_env
    password = os.environ.get(password_env)
    if not password:
        raise SystemExit(f"missing {args.protocol.upper()} password env var: {password_env}")
    return password, None, "password"


def _build_report(
    args: argparse.Namespace,
    *,
    port: int,
    outcome: dict[str, object],
    error: Exception | None,
    auth_method: str,
    duration_seconds: float,
) -> dict[str, object]:
    include = args.include_paths
    is_sftp = args.protocol == "sftp"
    is_ftps = args.protocol in {"ftps", "ftps-implicit"}
    return {
        "schema_version": 1,
        "tool": "cli_tool_readonly_transfer",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": duration_seconds,
        "passed": error is None,
        "protocol": args.protocol,
        "port": port,
        "security": (
            "encrypted_ssh"
            if is_sftp
            else "encrypted_tls"
            if is_ftps
            else "insecure_plaintext"
        ),
        "operation": args.operation,
        "auth_method": auth_method,
        "timeout_seconds": args.timeout,
        "target": identifier_reference(args.host, include_value=include),
        "username": identifier_reference(args.username, include_value=include),
        "remote_root": identifier_reference(args.remote_root, include_value=include),
        "remote_path": identifier_reference(args.remote_path, include_value=include),
        "host_key_verification": (
            "allow_unknown_with_warning" if args.allow_unknown_host_key else "strict"
        )
        if is_sftp
        else None,
        "custom_known_hosts": bool(args.known_hosts) if is_sftp else None,
        "tls_verification": (
            "unverified_explicit" if args.allow_unverified_tls else "strict"
        )
        if is_ftps
        else None,
        "custom_ca": bool(args.ca_file) if is_ftps else None,
        "data_channel_protection": (
            "private" if is_ftps else "plaintext" if args.protocol == "ftp" else None
        ),
        "passive": (not args.active_mode) if args.protocol in FTP_PROTOCOLS else None,
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
