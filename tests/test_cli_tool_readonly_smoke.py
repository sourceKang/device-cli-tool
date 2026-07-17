from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import cli_tool_readonly_smoke


@dataclass(frozen=True)
class FakeResult:
    passed: bool
    missing_by_command: dict[str, list[str]]
    output_by_command: dict[str, str]


@dataclass(frozen=True)
class FakeDriver:
    family: str = "generic"
    model: str = "generic"


def test_parse_params_accepts_key_value_pairs():
    assert cli_tool_readonly_smoke._parse_params(["vid=100", "name=qa_test"]) == {
        "vid": "100",
        "name": "qa_test",
    }


def test_parse_params_rejects_invalid_values():
    with pytest.raises(SystemExit, match="key=value"):
        cli_tool_readonly_smoke._parse_params(["missing_equals"])


def test_parse_args_reports_installed_version(capsys):
    with pytest.raises(SystemExit) as error:
        cli_tool_readonly_smoke.parse_args(["--version"])

    assert error.value.code == 0
    assert "device-cli-smoke" in capsys.readouterr().out


def test_run_smoke_requires_password_env_without_env_node(monkeypatch):
    monkeypatch.delenv("MISSING_PASSWORD", raising=False)
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--node-key",
            "node3",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password-env",
            "MISSING_PASSWORD",
        ]
    )

    with pytest.raises(SystemExit, match="MISSING_PASSWORD"):
        cli_tool_readonly_smoke.run_smoke(args)


def test_run_smoke_builds_ssh_transport_and_hides_output_by_default(monkeypatch, capsys, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)

    args = cli_tool_readonly_smoke.parse_args(
        [
            "--node-key",
            "node3",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--param",
            "vid=100",
            "--report-dir",
            str(tmp_path),
        ]
    )

    exit_code = cli_tool_readonly_smoke.run_smoke(args)
    stdout_payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["transport"]["password"] == "secret"
    assert captured["transport"]["max_sessions"] == 1
    assert captured["transport"]["connect_attempts"] == 3
    assert captured["transport"]["retry_backoff_seconds"] == 1.0
    assert captured["transport"]["reuse_sessions"] is False
    assert captured["transport"]["known_hosts_path"] is None
    assert captured["transport"]["allow_unknown_host_key"] is False
    assert captured["catalog"] == "builtin:generic"
    assert captured["verify"]["command_id"] == "show_version"
    assert captured["verify"]["params"] == {"vid": "100"}
    assert stdout_payload["output_by_command"] == {}
    assert stdout_payload["report_path"]

    report = _load_single_report(tmp_path)
    assert report["passed"] is True
    assert report["family"] == "generic"
    assert report["model"] == "generic"
    assert report["auth_source"] == "env:CLI_TOOL_SSH_PASSWORD"
    assert report["ssh_connect_attempts"] == 3
    assert report["ssh_retry_backoff_seconds"] == 1.0
    assert report["host_key_verification"] == "strict"
    assert report["custom_known_hosts"] is False
    assert report["output_by_command"] == {}


def test_run_smoke_forwards_explicit_unknown_host_key_opt_in(monkeypatch, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("example ssh-ed25519 AAAA", encoding="utf-8")
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--node-key",
            "node3",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--known-hosts",
            str(known_hosts),
            "--allow-unknown-host-key",
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0
    assert captured["transport"]["known_hosts_path"] == known_hosts
    assert captured["transport"]["allow_unknown_host_key"] is True
    report = _load_single_report(tmp_path / "reports")
    assert report["host_key_verification"] == "allow_unknown_with_warning"
    assert report["custom_known_hosts"] is True


def test_run_smoke_builds_serial_transport(monkeypatch, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)

    args = cli_tool_readonly_smoke.parse_args(
        [
            "--transport",
            "serial",
            "--serial-port",
            "COM5",
            "--username",
            "admin",
            "--baudrate",
            "115200",
            "--serial-timeout",
            "10",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0

    assert captured["serial_transport"] == {
        "port": "COM5",
        "username": "admin",
        "password": "secret",
        "baudrate": 115200,
        "timeout": 10,
    }
    assert captured["verify"]["command_id"] == "show_version"

    report = _load_single_report(tmp_path)
    assert report["transport"] == "serial"
    assert report["serial_port"] == "COM5"
    assert report["host"] == "COM5"
    assert report["auth_source"] == "env:CLI_TOOL_SSH_PASSWORD"


def test_run_smoke_resolves_serial_transport_from_env_node_config(monkeypatch, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)

    def fake_load_environment_for_node(node, auth_profile):
        captured["environment"] = {"node": node, "auth_profile": auth_profile}
        return SimpleNamespace(
            auth_profile="default",
            dut=SimpleNamespace(node_key="NODE1", device_ip="192.0.2.11"),
            readwrite=SimpleNamespace(username="admin", password="profile-password"),
            node_target={
                "cli": {
                    "transport": "serial",
                    "serial_port": "COM5",
                    "baudrate": 9600,
                    "timeout": 9,
                    "catalog": "configs/cli_tool/ies52xx.yaml",
                    "default_command": "show_system_information",
                    "report_dir": str(tmp_path),
                }
            },
        )

    monkeypatch.setattr(cli_tool_readonly_smoke, "_load_environment_for_node", fake_load_environment_for_node)
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--env-node",
            "node1",
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0

    assert captured["serial_transport"] == {
        "port": "COM5",
        "username": "admin",
        "password": "profile-password",
        "baudrate": 9600,
        "timeout": 9.0,
    }
    assert captured["catalog"].endswith("configs\\cli_tool\\ies52xx.yaml") or captured["catalog"].endswith(
        "configs/cli_tool/ies52xx.yaml"
    )
    assert captured["verify"]["command_id"] == "show_system_information"

    report = _load_single_report(tmp_path)
    assert report["transport"] == "serial"
    assert report["serial_port"] == "COM5"
    assert report["auth_source"] == "config_loader:default:readwrite"


def test_env_node_cli_arguments_override_serial_config(monkeypatch, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)
    monkeypatch.setenv("SERIAL_OVERRIDE_PASSWORD", "override-password")

    def fake_load_environment_for_node(node, auth_profile):
        return SimpleNamespace(
            auth_profile="default",
            dut=SimpleNamespace(node_key="NODE1", device_ip="192.0.2.11"),
            readwrite=SimpleNamespace(username="profile-user", password="profile-password"),
            node_target={
                "cli": {
                    "transport": "serial",
                    "serial_port": "COM4",
                    "baudrate": 9600,
                    "username": "config-user",
                    "password_env": "CONFIG_PASSWORD",
                }
            },
        )

    monkeypatch.setattr(cli_tool_readonly_smoke, "_load_environment_for_node", fake_load_environment_for_node)
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--env-node",
            "NODE1",
            "--serial-port",
            "COM5",
            "--baudrate",
            "115200",
            "--username",
            "override-user",
            "--password-env",
            "SERIAL_OVERRIDE_PASSWORD",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0
    assert captured["serial_transport"] == {
        "port": "COM5",
        "username": "override-user",
        "password": "override-password",
        "baudrate": 115200,
        "timeout": 15.0,
    }


def test_run_smoke_loads_portable_target_config(monkeypatch, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)
    monkeypatch.setenv("PORTABLE_SERIAL_PASSWORD", "portable-password")
    target_config = tmp_path / "device_cli_targets.yaml"
    target_config.write_text(
        """
version: 1
targets:
  lab-ies:
    transport: serial
    serial_port: COM5
    baudrate: 115200
    username: portable-user
    password_env: PORTABLE_SERIAL_PASSWORD
    catalog: builtin:ies52xx
    default_command: show_version
""".strip(),
        encoding="utf-8",
    )

    args = cli_tool_readonly_smoke.parse_args(
        [
            "--target-config",
            str(target_config),
            "--target",
            "lab-ies",
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0
    assert captured["catalog"] == "builtin:ies52xx"
    assert captured["serial_transport"] == {
        "port": "COM5",
        "username": "portable-user",
        "password": "portable-password",
        "baudrate": 115200,
        "timeout": 15.0,
    }
    report = _load_single_report(tmp_path / "reports")
    assert report["node_key"] == "lab-ies"
    assert report["auth_source"] == "env:PORTABLE_SERIAL_PASSWORD"


def test_portable_target_config_requires_target_pair():
    args = cli_tool_readonly_smoke.parse_args(["--target-config", "targets.yaml"])

    with pytest.raises(SystemExit, match="provided together"):
        cli_tool_readonly_smoke.run_smoke(args)


def test_run_smoke_can_resolve_env_node_readwrite_default(monkeypatch, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)

    def fake_load_environment_for_node(node, auth_profile):
        captured["environment"] = {"node": node, "auth_profile": auth_profile}
        return SimpleNamespace(
            auth_profile="default",
            dut=SimpleNamespace(node_key="NODE1", device_ip="192.0.2.11"),
            readwrite=SimpleNamespace(username="rw-user", password="rw-password"),
            node_target={
                "cli": {
                    "ssh_connect_attempts": 4,
                    "ssh_retry_backoff_seconds": 0.25,
                }
            },
        )

    monkeypatch.setattr(cli_tool_readonly_smoke, "_load_environment_for_node", fake_load_environment_for_node)

    args = cli_tool_readonly_smoke.parse_args(
        [
            "--env-node",
            "node1",
            "--auth-profile",
            "default",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0

    assert captured["environment"] == {"node": "node1", "auth_profile": "default"}
    assert captured["transport"]["node_key"] == "NODE1"
    assert captured["transport"]["host"] == "192.0.2.11"
    assert captured["transport"]["username"] == "rw-user"
    assert captured["transport"]["password"] == "rw-password"
    assert captured["transport"]["connect_attempts"] == 4
    assert captured["transport"]["retry_backoff_seconds"] == 0.25

    report = _load_single_report(tmp_path)
    assert report["auth_source"] == "config_loader:default:readwrite"
    assert report["node_key"] == "NODE1"
    assert report["host"] == "192.0.2.11"
    assert report["username"] == "rw-user"
    assert report["username"] == "rw-user"


def test_run_smoke_can_include_output_when_explicitly_requested(monkeypatch, tmp_path):
    _install_fake_smoke_dependencies(monkeypatch)
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--node-key",
            "node3",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--include-output",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0

    report = _load_single_report(tmp_path)
    assert report["output_by_command"] == {"show version": "Version: synthetic"}


def test_run_smoke_redacts_and_truncates_included_output(monkeypatch, tmp_path):
    captured = _install_fake_smoke_dependencies(monkeypatch)

    def fake_run_driver_verify(driver, transport, command_id, params, *, owner):
        captured["verify"] = {
            "driver": driver,
            "transport": transport,
            "command_id": command_id,
            "params": params,
            "owner": owner,
        }
        return FakeResult(
            passed=True,
            missing_by_command={},
            output_by_command={
                "show system-information": "\n".join(
                    [
                        "Hostname: NXC400",
                        "MAC address: F8:D4:A3:C9:E1:B0",
                        "Serial number: S230Z10023204",
                        "tail",
                    ]
                ),
            },
        )

    monkeypatch.setattr(cli_tool_readonly_smoke, "run_driver_verify", fake_run_driver_verify)

    args = cli_tool_readonly_smoke.parse_args(
        [
            "--node-key",
            "node3",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--command-id",
            "show_system_information",
            "--include-output",
            "--max-output-chars",
            "60",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0

    report = _load_single_report(tmp_path)
    included_output = report["output_by_command"]["show system-information"]
    assert "NXC400" not in included_output
    assert "F8:D4:A3:C9:E1:B0" not in included_output
    assert "S230Z10023204" not in included_output
    assert "Hostname: ***REDACTED***" in included_output
    assert "... [truncated after 60 redacted characters]" in included_output


def test_run_smoke_can_skip_report(monkeypatch, capsys, tmp_path):
    _install_fake_smoke_dependencies(monkeypatch)
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--node-key",
            "node3",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--report-dir",
            str(tmp_path),
            "--no-report",
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0
    stdout_payload = json.loads(capsys.readouterr().out)

    assert stdout_payload["report_path"] is None
    assert list(tmp_path.glob("*.json")) == []


def test_run_smoke_returns_nonzero_when_verification_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("CLI_TOOL_SSH_PASSWORD", "secret")

    class FakeTransport:
        def __init__(self, **kwargs) -> None:
            pass

    monkeypatch.setattr(cli_tool_readonly_smoke, "load_driver_source", lambda path: FakeDriver())
    monkeypatch.setattr(cli_tool_readonly_smoke, "SshCliTransport", FakeTransport)
    monkeypatch.setattr(
        cli_tool_readonly_smoke,
        "run_driver_verify",
        lambda *args, **kwargs: FakeResult(
            passed=False,
            missing_by_command={"show version": ["Version"]},
            output_by_command={},
        ),
    )

    args = cli_tool_readonly_smoke.parse_args(
        [
            "--node-key",
            "node3",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 1
    report = _load_single_report(tmp_path)
    assert report["missing_by_command"] == {"show version": ["Version"]}


def _install_fake_smoke_dependencies(monkeypatch):
    monkeypatch.setenv("CLI_TOOL_SSH_PASSWORD", "secret")
    captured = {}

    class FakeTransport:
        def __init__(self, **kwargs) -> None:
            captured["transport"] = kwargs

    class FakeSerialTransport:
        def __init__(self, **kwargs) -> None:
            captured["serial_transport"] = kwargs

    def fake_load_driver(path):
        captured["catalog"] = str(path)
        return FakeDriver()

    def fake_run_driver_verify(driver, transport, command_id, params, *, owner):
        captured["verify"] = {
            "driver": driver,
            "transport": transport,
            "command_id": command_id,
            "params": params,
            "owner": owner,
        }
        return FakeResult(
            passed=True,
            missing_by_command={},
            output_by_command={"show version": "Version: synthetic"},
        )

    monkeypatch.setattr(cli_tool_readonly_smoke, "load_driver_source", fake_load_driver)
    monkeypatch.setattr(cli_tool_readonly_smoke, "SshCliTransport", FakeTransport)
    monkeypatch.setattr(cli_tool_readonly_smoke, "SerialCliTransport", FakeSerialTransport)
    monkeypatch.setattr(cli_tool_readonly_smoke, "run_driver_verify", fake_run_driver_verify)
    return captured


def _load_single_report(report_dir: Path) -> dict[str, object]:
    reports = list(report_dir.glob("cli_tool_readonly_smoke_*.json"))
    assert len(reports) == 1
    return json.loads(reports[0].read_text(encoding="utf-8"))




