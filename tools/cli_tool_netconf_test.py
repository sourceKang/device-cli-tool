from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from cli_tool.reporting.redaction import redact
from cli_tool.transport.netconf import (
    ALLOWED_DATASTORES,
    DEFAULT_CONFIRM_TIMEOUT,
    MUTATION_TARGET,
    NetconfCapabilityError,
    NetconfClient,
)
from cli_tool.workflows.netconf_edit import (
    assert_dry_run_target_confirmation,
    assert_live_mutation_authorized,
    build_netconf_edit_plan,
)


DEFAULT_PASSWORD_ENV = "CLI_TOOL_NETCONF_PASSWORD"
DEFAULT_PORT = 830
DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_OUTPUT_CHARS = 20000
DEFAULT_REPORT_DIR = "reports/cli-tool-netconf"
MIN_CONFIRM_TIMEOUT = 5.0
MAX_CONFIRM_TIMEOUT = 600.0
OPERATIONS = ("connect", "get", "get-config", "edit-config")


def main(argv: list[str] | None = None) -> int:
    try:
        exit_code, payload = run(argv)
    except (PermissionError, ValueError) as error:
        print(json.dumps({"result": "REFUSED", "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="device-cli-netconf-test",
        description=(
            "Test a NETCONF connection and run read-only get/get-config. "
            "edit-config prints a dry-run plan unless --run-mutating is explicit, in which "
            "case it executes candidate + validate + confirmed-commit, never running directly."
        ),
    )
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--host", required=True, help="NETCONF host or IP.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--username", help="Required for connect/get/get-config and edit-config --run-mutating.")
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--host-key-fingerprint",
        help=(
            "Expected NETCONF SSH host key fingerprint (SHA256:... or bare base64, as "
            "ssh-keygen -lf prints it). Required for any operation that connects, unless "
            "--allow-unknown-host-key is explicit."
        ),
    )
    parser.add_argument(
        "--allow-unknown-host-key",
        action="store_true",
        help="Explicitly allow an unpinned NETCONF SSH host key. Never use in CI.",
    )
    parser.add_argument(
        "--datastore",
        choices=sorted(ALLOWED_DATASTORES),
        default=None,
        help="get-config source datastore (default running). Not valid for edit-config.",
    )
    parser.add_argument(
        "--filter-file",
        help="File containing a subtree or XPath filter for get/get-config.",
    )
    parser.add_argument(
        "--edit-config-file",
        help="File containing the edit-config <config> payload.",
    )
    parser.add_argument("--node-key", help="Identifier recorded in the report; required for edit-config.")
    parser.add_argument("--confirm-node", help="Must exactly match --node-key. Required for edit-config.")
    parser.add_argument("--confirm-host", help="Must exactly match --host. Required for edit-config.")
    parser.add_argument(
        "--run-mutating",
        action="store_true",
        help=(
            "Execute edit-config live: candidate + validate + confirmed-commit. Without this "
            "flag edit-config only prints a dry-run plan and never connects."
        ),
    )
    parser.add_argument(
        "--finalize-commit",
        action="store_true",
        help=(
            "Send the confirming commit so the change becomes permanent. Without this, the "
            "confirmed commit is left unconfirmed and the device auto-reverts running once "
            "--confirm-timeout-seconds elapses or the session closes. edit-config --run-mutating only."
        ),
    )
    parser.add_argument(
        "--confirm-timeout-seconds",
        type=float,
        default=None,
        help=f"Confirmed-commit window in seconds (default {DEFAULT_CONFIRM_TIMEOUT:g}). edit-config --run-mutating only.",
    )
    parser.add_argument(
        "--verify-filter-file",
        help="File containing a <filter> to <get> right after commit, before any rollback. edit-config --run-mutating only.",
    )
    parser.add_argument("--include-output", action="store_true")
    parser.add_argument("--max-output-chars", type=int, default=DEFAULT_MAX_OUTPUT_CHARS)
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> tuple[int, dict[str, object]]:
    args = parse_args(argv)
    _validate_args(args)
    if args.operation == "edit-config":
        return _run_edit_config(args)
    return _run_readonly(args)


def _validate_args(args: argparse.Namespace) -> None:
    if args.port < 1 or args.port > 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than 0")
    if args.max_output_chars < 1:
        raise SystemExit("--max-output-chars must be greater than 0")
    if args.operation != "edit-config" and (
        args.confirm_node
        or args.confirm_host
        or args.run_mutating
        or args.edit_config_file
        or args.finalize_commit
        or args.confirm_timeout_seconds is not None
        or args.verify_filter_file
    ):
        raise SystemExit(
            "--confirm-node/--confirm-host/--run-mutating/--edit-config-file/--finalize-commit/"
            "--confirm-timeout-seconds/--verify-filter-file are only valid for edit-config"
        )
    if args.operation == "edit-config" and args.datastore is not None:
        raise SystemExit(
            "--datastore is not valid for edit-config; live execution always targets candidate"
        )
    if args.operation not in {"get", "get-config"} and args.filter_file:
        raise SystemExit("--filter-file is only valid for get/get-config")
    if args.operation == "edit-config":
        if not (args.node_key and args.confirm_node and args.confirm_host and args.edit_config_file):
            raise SystemExit(
                "edit-config requires --node-key, --confirm-node, --confirm-host and --edit-config-file"
            )
        if not args.run_mutating and (
            args.finalize_commit or args.confirm_timeout_seconds is not None or args.verify_filter_file
        ):
            raise SystemExit(
                "--finalize-commit/--confirm-timeout-seconds/--verify-filter-file require --run-mutating"
            )
        if args.run_mutating and not args.username:
            raise SystemExit("--run-mutating requires --username to connect")
        if args.confirm_timeout_seconds is not None and not (
            MIN_CONFIRM_TIMEOUT <= args.confirm_timeout_seconds <= MAX_CONFIRM_TIMEOUT
        ):
            raise SystemExit(
                f"--confirm-timeout-seconds must be between {MIN_CONFIRM_TIMEOUT:g} and {MAX_CONFIRM_TIMEOUT:g}"
            )
    elif not args.username:
        raise SystemExit("--username is required")


def _run_readonly(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    password = os.environ.get(args.password_env)
    if not password:
        raise SystemExit(f"missing NETCONF password env var: {args.password_env}")
    filter_xml = Path(args.filter_file).read_text(encoding="utf-8") if args.filter_file else None
    datastore = args.datastore or "running"

    client = NetconfClient(
        host=args.host,
        username=args.username,
        password=password,
        port=args.port,
        timeout=args.timeout,
        allow_unknown_host_key=args.allow_unknown_host_key,
        expected_host_key_fingerprint=args.host_key_fingerprint,
    )

    started = time.monotonic()
    outcome: dict[str, object] = {}
    error: Exception | None = None
    try:
        if args.operation == "connect":
            result = client.connect_test()
            outcome = {
                "session_id": result.session_id,
                "server_capabilities": list(result.server_capabilities),
                "elapsed_seconds": round(result.elapsed_seconds, 3),
            }
        elif args.operation == "get":
            outcome = _operation_outcome(client.get(filter_xml=filter_xml), args)
        else:
            outcome = _operation_outcome(
                client.get_config(datastore=datastore, filter_xml=filter_xml), args
            )
    except Exception as caught:
        error = caught

    report = _build_report(
        args,
        outcome=outcome,
        error=error,
        duration_seconds=round(time.monotonic() - started, 3),
        datastore=datastore if args.operation == "get-config" else None,
    )
    redacted = redact(report)
    _write_report(redacted, args)
    return (0 if error is None else 1), redacted


def _run_edit_config(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    config_xml = Path(args.edit_config_file).read_text(encoding="utf-8")
    plan = build_netconf_edit_plan(node_key=args.node_key, host=args.host, config_xml=config_xml)

    if not args.run_mutating:
        assert_dry_run_target_confirmation(
            plan, confirmed_node=args.confirm_node, confirmed_host=args.confirm_host
        )
        payload = plan.as_dict()
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        payload["requested_mode"] = "DRY_RUN"
        payload["result"] = "DRY_RUN_READY"
        redacted = redact(payload)
        _write_report(redacted, args)
        return 0, redacted

    assert_live_mutation_authorized(
        node_key=args.node_key,
        host=args.host,
        confirmed_node=args.confirm_node,
        confirmed_host=args.confirm_host,
        run_mutating=args.run_mutating,
    )
    return _execute_live_edit_config(args, config_xml)


def _execute_live_edit_config(args: argparse.Namespace, config_xml: str) -> tuple[int, dict[str, object]]:
    password = os.environ.get(args.password_env)
    if not password:
        raise SystemExit(f"missing NETCONF password env var: {args.password_env}")
    verify_filter_xml = (
        Path(args.verify_filter_file).read_text(encoding="utf-8") if args.verify_filter_file else None
    )
    confirm_timeout = (
        args.confirm_timeout_seconds if args.confirm_timeout_seconds is not None else DEFAULT_CONFIRM_TIMEOUT
    )

    client = NetconfClient(
        host=args.host,
        username=args.username,
        password=password,
        port=args.port,
        timeout=args.timeout,
        allow_unknown_host_key=args.allow_unknown_host_key,
        expected_host_key_fingerprint=args.host_key_fingerprint,
    )

    started = time.monotonic()
    mutation: dict[str, object] = {}
    error: Exception | None = None
    try:
        result = client.execute_confirmed_commit(
            config_xml=config_xml,
            verify_filter_xml=verify_filter_xml,
            confirm_timeout=confirm_timeout,
            finalize=args.finalize_commit,
        )
        mutation = {
            "target": MUTATION_TARGET,
            "confirm_timeout_seconds": result.confirm_timeout_seconds,
            "finalized": result.finalized,
            "steps": [_mutation_step_payload(step, args) for step in result.steps],
        }
    except Exception as caught:
        error = caught

    missing_capabilities = list(error.missing) if isinstance(error, NetconfCapabilityError) else None
    if error is None:
        result_value = "EXECUTED_FINALIZED" if args.finalize_commit else "EXECUTED_TRANSIENT"
    elif missing_capabilities:
        result_value = "REFUSED_MISSING_CAPABILITY"
    else:
        result_value = "FAILED"

    report = {
        "schema_version": 1,
        "tool": "cli_tool_netconf_test",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 3),
        "passed": error is None,
        "operation": "edit-config",
        "requested_mode": "LIVE",
        "result": result_value,
        "security": "encrypted_ssh",
        "host": args.host,
        "port": args.port,
        "username": args.username,
        "node_key": args.node_key,
        "host_key_verification": _host_key_verification_label(args),
        "mutation": mutation,
        "error_type": type(error).__name__ if error else None,
        "error_message": _safe_error_message(error),
        "missing_capabilities": missing_capabilities,
    }
    redacted = redact(report)
    _write_report(redacted, args)
    return (0 if error is None else 1), redacted


def _mutation_step_payload(step, args: argparse.Namespace) -> dict[str, object]:
    payload: dict[str, object] = {"step": step.step, "elapsed_seconds": round(step.elapsed_seconds, 3)}
    if args.include_output:
        payload["xml"] = _truncate(step.xml, args.max_output_chars)
    return payload


def _operation_outcome(result, args: argparse.Namespace) -> dict[str, object]:
    outcome: dict[str, object] = {"elapsed_seconds": round(result.elapsed_seconds, 3)}
    if args.include_output:
        outcome["xml_output"] = _truncate(result.xml, args.max_output_chars)
    return outcome


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"...<truncated {len(value) - max_chars} chars>"


def _host_key_verification_label(args: argparse.Namespace) -> str:
    if args.host_key_fingerprint:
        return "pinned_fingerprint"
    if args.allow_unknown_host_key:
        return "allow_unknown_with_warning"
    return "unspecified"


def _build_report(
    args: argparse.Namespace,
    *,
    outcome: dict[str, object],
    error: Exception | None,
    duration_seconds: float,
    datastore: str | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "tool": "cli_tool_netconf_test",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": duration_seconds,
        "passed": error is None,
        "operation": args.operation,
        "security": "encrypted_ssh",
        "host": args.host,
        "port": args.port,
        "username": args.username,
        "host_key_verification": _host_key_verification_label(args),
        "datastore": datastore,
        "outcome": outcome,
        "error_type": type(error).__name__ if error else None,
        "error_message": _safe_error_message(error),
    }


def _safe_error_message(error: Exception | None) -> str | None:
    if error is None:
        return None
    return "operation failed; inspect error_type and trusted local diagnostics"


def _write_report(redacted_payload: dict[str, object], args: argparse.Namespace) -> Path | None:
    if args.no_report:
        return None
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = report_dir / f"cli_tool_netconf_test_{timestamp}.json"
    path.write_text(json.dumps(redacted_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _package_version() -> str:
    try:
        return version("device-cli-tool")
    except PackageNotFoundError:
        return "development"


if __name__ == "__main__":
    raise SystemExit(main())
