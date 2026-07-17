from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from cli_tool.catalog.loader import load_driver_source
from cli_tool.reporting.redaction import redact, redact_text
from cli_tool.transport.serial import SerialCliTransport
from cli_tool.transport.ssh_adapter import SshCliTransport
from cli_tool.transport.telnet import TelnetCliTransport
from cli_tool.workflows.driver_verify import run_driver_verify


DEFAULT_MAX_OUTPUT_CHARS = 20_000
DEFAULT_TRANSPORT = "ssh"
DEFAULT_CATALOG = "builtin:generic"
DEFAULT_COMMAND_ID = "show_version"
DEFAULT_PASSWORD_ENV = "CLI_TOOL_SSH_PASSWORD"
DEFAULT_SSH_TIMEOUT = 15.0
DEFAULT_SSH_CONNECT_ATTEMPTS = 3
DEFAULT_SSH_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_SERIAL_BAUDRATE = 115200
DEFAULT_SERIAL_TIMEOUT = 15.0
DEFAULT_TELNET_PORT = 23
DEFAULT_TELNET_TIMEOUT = 15.0
DEFAULT_REPORT_DIR = "reports/cli-tool"


@dataclass(frozen=True)
class SmokeTarget:
    node_key: str
    host: str
    username: str
    password: str
    auth_source: str
    transport: str = "ssh"
    serial_port: str | None = None


@dataclass(frozen=True)
class SmokeRunConfig:
    target: SmokeTarget
    catalog: Path
    command_id: str
    ssh_timeout: float
    ssh_connect_attempts: int
    ssh_retry_backoff_seconds: float
    known_hosts_path: Path | None
    allow_unknown_host_key: bool
    telnet_port: int
    telnet_timeout: float
    allow_insecure_telnet: bool
    baudrate: int
    serial_timeout: float
    report_dir: Path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_smoke(args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="device-cli-smoke",
        description="Run one read-only CLI catalog command through cli_tool transport.",
    )
    parser.add_argument(
        "--transport",
        choices=["ssh", "serial", "telnet"],
        help="CLI transport to use; overrides node_target.cli.",
    )
    parser.add_argument("--catalog", help="Built-in catalog name or YAML path; overrides node_target.cli.")
    parser.add_argument("--command-id", help="Read-only command id from the catalog; overrides node_target.cli.default_command.")
    parser.add_argument(
        "--env-node",
        help="Resolve node key, host, username and password from project config_loader using readwrite credentials.",
    )
    parser.add_argument("--target-config", help="Portable target YAML for projects without config_loader.")
    parser.add_argument("--target", help="Target key under --target-config targets.")
    parser.add_argument("--auth-profile", default="default", help="Auth profile used with --env-node.")
    parser.add_argument("--node-key", help="Logical node key used for SSH session pooling.")
    parser.add_argument("--host", help="Device SSH host or IP.")
    parser.add_argument("--username", help="SSH username.")
    parser.add_argument(
        "--password-env",
        help="Environment variable containing the CLI password; overrides config_loader credentials.",
    )
    parser.add_argument("--param", action="append", default=[], help="Command template parameter as key=value.")
    parser.add_argument("--owner", default="cli-tool-readonly-smoke", help="Owner label for SSH timing diagnostics.")
    parser.add_argument("--ssh-timeout", type=float, help="SSH connect/auth timeout in seconds.")
    parser.add_argument("--ssh-connect-attempts", type=int, help="Maximum SSH connection attempts.")
    parser.add_argument(
        "--ssh-retry-backoff-seconds",
        type=float,
        help="Initial SSH retry backoff in seconds; doubles after each failed attempt.",
    )
    parser.add_argument(
        "--known-hosts",
        help="Additional OpenSSH known_hosts file. System host keys are always loaded.",
    )
    parser.add_argument(
        "--allow-unknown-host-key",
        action="store_true",
        help=(
            "Explicitly allow an unknown SSH host key with a warning. "
            "This cannot be enabled from target YAML and should not be used in CI."
        ),
    )
    parser.add_argument("--serial-port", help="Serial console port, for example COM5.")
    parser.add_argument("--baudrate", type=int, help="Serial console baudrate.")
    parser.add_argument("--serial-timeout", type=float, help="Serial read/login timeout in seconds.")
    parser.add_argument("--telnet-port", type=int, help="Telnet port; defaults to 23.")
    parser.add_argument("--telnet-timeout", type=float, help="Telnet connect/login/command timeout.")
    parser.add_argument(
        "--allow-insecure-telnet",
        action="store_true",
        help=(
            "Allow plaintext Telnet for an isolated lab. "
            "This cannot be enabled from target YAML."
        ),
    )
    parser.add_argument(
        "--include-output",
        action="store_true",
        help="Include command output in stdout JSON and report. Do not use if output may contain sensitive values.",
    )
    parser.add_argument(
        "--max-output-chars",
        type=int,
        default=DEFAULT_MAX_OUTPUT_CHARS,
        help="Maximum redacted characters to keep per command when --include-output is used.",
    )
    parser.add_argument(
        "--report-dir",
        help="Directory for the redacted JSON smoke report; overrides node_target.cli.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Do not write a JSON smoke report.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_package_version()}")
    return parser.parse_args(argv)


def _package_version() -> str:
    try:
        return version("device-cli-tool")
    except PackageNotFoundError:
        return "development"


def run_smoke(args: argparse.Namespace) -> int:
    config = _resolve_smoke_config(args)
    target = config.target
    driver = load_driver_source(str(config.catalog))
    transport = _build_transport(config)
    started = time.monotonic()
    result = run_driver_verify(
        driver,
        transport,
        config.command_id,
        _parse_params(args.param),
        owner=args.owner,
    )
    duration_seconds = round(time.monotonic() - started, 3)
    report = _build_report(
        args=args,
        config=config,
        target=target,
        driver_family=driver.family,
        driver_model=driver.model,
        result=result,
        duration_seconds=duration_seconds,
    )
    report_path = None if args.no_report else _write_report(report, config.report_dir)
    stdout_payload = {**report, "report_path": str(report_path) if report_path else None}
    if not args.include_output:
        stdout_payload["output_by_command"] = {}
    print(json.dumps(redact(stdout_payload), ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


def _resolve_smoke_config(args: argparse.Namespace) -> SmokeRunConfig:
    env_config, cli_config = _resolve_config_source(args)

    transport = _string_setting(args.transport, cli_config, "transport", DEFAULT_TRANSPORT).lower()
    if transport not in {"ssh", "serial", "telnet"}:
        raise SystemExit("node_target.cli.transport must be 'ssh', 'serial' or 'telnet'")
    if transport == "telnet" and not args.allow_insecure_telnet:
        raise SystemExit("Telnet is plaintext; pass --allow-insecure-telnet for an isolated lab")

    catalog = Path(_string_setting(args.catalog, cli_config, "catalog", DEFAULT_CATALOG))
    command_id = _string_setting(args.command_id, cli_config, "default_command", DEFAULT_COMMAND_ID)
    timeout = cli_config.get("timeout")
    ssh_timeout = _positive_float(
        args.ssh_timeout if args.ssh_timeout is not None else cli_config.get("ssh_timeout", timeout),
        "ssh_timeout",
        DEFAULT_SSH_TIMEOUT,
    )
    ssh_connect_attempts = _positive_int(
        args.ssh_connect_attempts
        if args.ssh_connect_attempts is not None
        else cli_config.get("ssh_connect_attempts"),
        "ssh_connect_attempts",
        DEFAULT_SSH_CONNECT_ATTEMPTS,
    )
    ssh_retry_backoff_seconds = _non_negative_float(
        args.ssh_retry_backoff_seconds
        if args.ssh_retry_backoff_seconds is not None
        else cli_config.get("ssh_retry_backoff_seconds"),
        "ssh_retry_backoff_seconds",
        DEFAULT_SSH_RETRY_BACKOFF_SECONDS,
    )
    known_hosts_value = args.known_hosts or _optional_string(cli_config, "known_hosts")
    known_hosts_path = Path(known_hosts_value) if known_hosts_value else None
    allow_unknown_host_key = bool(args.allow_unknown_host_key)
    telnet_port = _positive_int(
        args.telnet_port if args.telnet_port is not None else cli_config.get("telnet_port"),
        "telnet_port",
        DEFAULT_TELNET_PORT,
    )
    if telnet_port > 65535:
        raise SystemExit("node_target.cli.telnet_port must be between 1 and 65535")
    telnet_timeout = _positive_float(
        args.telnet_timeout if args.telnet_timeout is not None else cli_config.get("telnet_timeout", timeout),
        "telnet_timeout",
        DEFAULT_TELNET_TIMEOUT,
    )
    serial_timeout = _positive_float(
        args.serial_timeout if args.serial_timeout is not None else cli_config.get("serial_timeout", timeout),
        "serial_timeout",
        DEFAULT_SERIAL_TIMEOUT,
    )
    baudrate = _positive_int(
        args.baudrate if args.baudrate is not None else cli_config.get("baudrate"),
        "baudrate",
        DEFAULT_SERIAL_BAUDRATE,
    )
    report_dir = Path(_string_setting(args.report_dir, cli_config, "report_dir", DEFAULT_REPORT_DIR))
    target = _resolve_target(args, env_config, cli_config, transport)
    return SmokeRunConfig(
        target=target,
        catalog=catalog,
        command_id=command_id,
        ssh_timeout=ssh_timeout,
        ssh_connect_attempts=ssh_connect_attempts,
        ssh_retry_backoff_seconds=ssh_retry_backoff_seconds,
        known_hosts_path=known_hosts_path,
        allow_unknown_host_key=allow_unknown_host_key,
        telnet_port=telnet_port,
        telnet_timeout=telnet_timeout,
        allow_insecure_telnet=bool(args.allow_insecure_telnet),
        baudrate=baudrate,
        serial_timeout=serial_timeout,
        report_dir=report_dir,
    )


def _resolve_target(args: argparse.Namespace, env_config, cli_config: dict[str, object], transport: str) -> SmokeTarget:
    username = args.username or _optional_string(cli_config, "username")
    if username is None and env_config is not None:
        username = env_config.readwrite.username
    password, auth_source = _resolve_password(args, cli_config, env_config, username)

    if transport == "serial":
        return _resolve_serial_target(args, env_config, cli_config, username, password, auth_source)

    node_key = args.node_key or _optional_string(cli_config, "node_key")
    if node_key is None and env_config is not None:
        node_key = env_config.dut.node_key
    host = args.host or _optional_string(cli_config, "host")
    if host is None and env_config is not None:
        host = env_config.dut.device_ip
    missing = [name for name, value in (("node_key", node_key), ("host", host), ("username", username)) if not value]
    if missing:
        raise SystemExit(
            f"missing required arguments with --transport {transport}: "
            f"{', '.join('--' + name.replace('_', '-') for name in missing)}"
        )
    return SmokeTarget(
        node_key=node_key,
        host=host,
        username=username,
        password=password,
        auth_source=auth_source,
        transport=transport,
    )


def _resolve_serial_target(args, env_config, cli_config, username, password, auth_source) -> SmokeTarget:
    serial_port = args.serial_port or _optional_string(cli_config, "serial_port")
    node_key = args.node_key or _optional_string(cli_config, "node_key")
    if node_key is None:
        node_key = env_config.dut.node_key if env_config is not None else serial_port
    missing = [name for name, value in (("serial_port", serial_port), ("username", username)) if not value]
    if missing:
        raise SystemExit(
            f"missing required arguments with --transport serial: "
            f"{', '.join('--' + name.replace('_', '-') for name in missing)}"
        )
    return SmokeTarget(
        node_key=node_key,
        host=serial_port,
        username=username,
        password=password,
        auth_source=auth_source,
        transport="serial",
        serial_port=serial_port,
    )


def _build_transport(config: SmokeRunConfig):
    target = config.target
    if target.transport == "serial":
        return SerialCliTransport(
            port=target.serial_port or target.host,
            username=target.username,
            password=target.password,
            baudrate=config.baudrate,
            timeout=config.serial_timeout,
        )
    if target.transport == "telnet":
        return TelnetCliTransport(
            host=target.host,
            port=config.telnet_port,
            username=target.username,
            password=target.password,
            timeout=config.telnet_timeout,
            allow_insecure_telnet=config.allow_insecure_telnet,
        )
    return SshCliTransport(
        node_key=target.node_key,
        host=target.host,
        username=target.username,
        password=target.password,
        max_sessions=1,
        ssh_timeout=config.ssh_timeout,
        connect_attempts=config.ssh_connect_attempts,
        retry_backoff_seconds=config.ssh_retry_backoff_seconds,
        reuse_sessions=False,
        known_hosts_path=config.known_hosts_path,
        allow_unknown_host_key=config.allow_unknown_host_key,
    )


def _node_cli_config(env_config) -> dict[str, object]:
    if env_config is None:
        return {}
    node_target = getattr(env_config, "node_target", {})
    if not isinstance(node_target, dict):
        raise SystemExit("config_loader EnvironmentConfig.node_target must be a mapping")
    cli_config = node_target.get("cli", {})
    if cli_config is None:
        return {}
    if not isinstance(cli_config, dict):
        raise SystemExit("node_target.cli must be a mapping")
    return cli_config


def _resolve_config_source(args) -> tuple[object | None, dict[str, object]]:
    if args.env_node and (args.target_config or args.target):
        raise SystemExit("--env-node cannot be combined with --target-config or --target")
    if bool(args.target_config) != bool(args.target):
        raise SystemExit("--target-config and --target must be provided together")
    if args.target_config:
        return None, _load_portable_target(Path(args.target_config), args.target)
    if args.env_node:
        env_config = _load_environment_for_node(args.env_node, args.auth_profile)
        return env_config, _node_cli_config(env_config)
    return None, {}


def _load_portable_target(path: Path, target_key: str) -> dict[str, object]:
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("PyYAML is required to load portable target config") from error
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SystemExit(f"cannot read target config {path}: {error}") from error
    if not isinstance(data, dict) or data.get("version") != 1:
        raise SystemExit(f"{path}: target config must declare version: 1")
    targets = data.get("targets")
    if not isinstance(targets, dict):
        raise SystemExit(f"{path}: targets must be a mapping")
    target = targets.get(target_key)
    if not isinstance(target, dict):
        raise SystemExit(f"{path}: target {target_key!r} is not defined")
    resolved = dict(target)
    resolved.setdefault("node_key", target_key)
    return resolved


def _resolve_password(args, cli_config: dict[str, object], env_config, username: str | None) -> tuple[str, str]:
    password_env = args.password_env or _optional_string(cli_config, "password_env")
    if password_env:
        password = os.environ.get(password_env)
        if not password:
            raise SystemExit(f"missing CLI password env var: {password_env}")
        return password, f"env:{password_env}"
    if env_config is not None:
        if username != env_config.readwrite.username:
            raise SystemExit("overriding node_target.cli.username requires --password-env or cli.password_env")
        return env_config.readwrite.password, f"config_loader:{env_config.auth_profile}:readwrite"
    password = os.environ.get(DEFAULT_PASSWORD_ENV)
    if not password:
        raise SystemExit(f"missing CLI password env var: {DEFAULT_PASSWORD_ENV}")
    return password, f"env:{DEFAULT_PASSWORD_ENV}"


def _string_setting(argument_value, cli_config: dict[str, object], key: str, default: str) -> str:
    value = argument_value if argument_value is not None else cli_config.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"node_target.cli.{key} must be a non-empty string")
    return value.strip()


def _optional_string(cli_config: dict[str, object], key: str) -> str | None:
    value = cli_config.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"node_target.cli.{key} must be a non-empty string")
    return value.strip()


def _positive_float(value, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SystemExit(f"node_target.cli.{name} must be a positive number")
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"node_target.cli.{name} must be a positive number") from error
    if resolved <= 0:
        raise SystemExit(f"node_target.cli.{name} must be a positive number")
    return resolved


def _non_negative_float(value, name: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SystemExit(f"node_target.cli.{name} must be a non-negative number")
    try:
        resolved = float(value)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"node_target.cli.{name} must be a non-negative number") from error
    if resolved < 0:
        raise SystemExit(f"node_target.cli.{name} must be a non-negative number")
    return resolved


def _positive_int(value, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SystemExit(f"node_target.cli.{name} must be a positive integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"node_target.cli.{name} must be a positive integer") from error
    if resolved <= 0 or str(resolved) != str(value).strip():
        raise SystemExit(f"node_target.cli.{name} must be a positive integer")
    return resolved



def _load_environment_for_node(node: str, auth_profile: str):
    try:
        from config_loader import load_environment
        from config_loader.settings import ConfigError
    except ImportError as error:
        raise SystemExit("--env-node requires a compatible config_loader package from the consuming project") from error

    try:
        return load_environment(node=node, auth_profile=auth_profile)
    except ConfigError:
        upper_node = node.upper()
        if upper_node == node:
            raise
        return load_environment(node=upper_node, auth_profile=auth_profile)

def _build_report(
    *,
    args: argparse.Namespace,
    config: SmokeRunConfig,
    target: SmokeTarget,
    driver_family: str,
    driver_model: str,
    result,
    duration_seconds: float,
) -> dict[str, object]:
    return {
        "tool": "cli_tool_readonly_smoke",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": duration_seconds,
        "passed": result.passed,
        "catalog": str(config.catalog),
        "transport": target.transport,
        "security": (
            "insecure_plaintext"
            if target.transport == "telnet"
            else "encrypted"
            if target.transport == "ssh"
            else "local_serial"
        ),
        "family": driver_family,
        "model": driver_model,
        "command_id": config.command_id,
        "node_key": target.node_key,
        "host": target.host,
        "serial_port": target.serial_port,
        "telnet_port": config.telnet_port if target.transport == "telnet" else None,
        "telnet_timeout": config.telnet_timeout if target.transport == "telnet" else None,
        "username": target.username,
        "auth_source": target.auth_source,
        "owner": args.owner,
        "ssh_connect_attempts": config.ssh_connect_attempts if target.transport == "ssh" else None,
        "ssh_retry_backoff_seconds": config.ssh_retry_backoff_seconds if target.transport == "ssh" else None,
        "host_key_verification": (
            "allow_unknown_with_warning" if config.allow_unknown_host_key else "strict"
        )
        if target.transport == "ssh"
        else None,
        "custom_known_hosts": bool(config.known_hosts_path) if target.transport == "ssh" else None,
        "missing_by_command": result.missing_by_command,
        "validation_errors_by_command": getattr(result, "validation_errors_by_command", {}),
        "output_by_command": _prepare_output_by_command(
            result.output_by_command,
            include_output=args.include_output,
            max_output_chars=args.max_output_chars,
        ),
    }


def _write_report(report: dict[str, object], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"cli_tool_readonly_smoke_{timestamp}.json"
    path.write_text(json.dumps(redact(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _prepare_output_by_command(
    output_by_command: dict[str, str],
    *,
    include_output: bool,
    max_output_chars: int,
) -> dict[str, str]:
    if not include_output:
        return {}
    if max_output_chars < 1:
        raise SystemExit("--max-output-chars must be greater than 0")

    prepared = {}
    for command, output in output_by_command.items():
        redacted_output = redact_text(output)
        if len(redacted_output) > max_output_chars:
            prepared[command] = (
                redacted_output[:max_output_chars]
                + f"\n... [truncated after {max_output_chars} redacted characters]"
            )
        else:
            prepared[command] = redacted_output
    return prepared


def _parse_params(values: list[str]) -> dict[str, str]:
    params = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"invalid --param value, expected key=value: {value}")
        key, param_value = value.split("=", 1)
        key = key.strip()
        if not key:
            raise SystemExit(f"invalid --param value, empty key: {value}")
        params[key] = param_value
    return params


if __name__ == "__main__":
    raise SystemExit(main())






