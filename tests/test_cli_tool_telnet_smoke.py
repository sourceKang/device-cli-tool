from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from tools import cli_tool_readonly_smoke


@dataclass(frozen=True)
class FakeDriver:
    family: str = "generic"
    model: str = "generic"


@dataclass(frozen=True)
class FakeResult:
    passed: bool = True
    missing_by_command: dict[str, list[str]] = None
    output_by_command: dict[str, str] = None

    def __post_init__(self):
        object.__setattr__(self, "missing_by_command", self.missing_by_command or {})
        object.__setattr__(
            self,
            "output_by_command",
            self.output_by_command or {"show version": "Version: synthetic"},
        )


def test_smoke_rejects_telnet_without_explicit_insecure_flag(monkeypatch):
    monkeypatch.setenv("CLI_TOOL_SSH_PASSWORD", "secret")
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--transport",
            "telnet",
            "--node-key",
            "node1",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
        ]
    )

    with pytest.raises(SystemExit, match="plaintext"):
        cli_tool_readonly_smoke.run_smoke(args)


def test_smoke_builds_explicit_telnet_transport(monkeypatch, capsys):
    monkeypatch.setenv("CLI_TOOL_SSH_PASSWORD", "secret")
    captured: dict[str, object] = {}

    class FakeTelnetTransport:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(cli_tool_readonly_smoke, "TelnetCliTransport", FakeTelnetTransport)
    monkeypatch.setattr(cli_tool_readonly_smoke, "load_driver_source", lambda path: FakeDriver())
    monkeypatch.setattr(
        cli_tool_readonly_smoke,
        "run_driver_verify",
        lambda *args, **kwargs: FakeResult(),
    )
    args = cli_tool_readonly_smoke.parse_args(
        [
            "--transport",
            "telnet",
            "--allow-insecure-telnet",
            "--node-key",
            "node1",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--telnet-port",
            "2323",
            "--telnet-timeout",
            "9",
            "--no-report",
        ]
    )

    assert cli_tool_readonly_smoke.run_smoke(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert captured == {
        "host": "192.0.2.10",
        "port": 2323,
        "username": "admin",
        "password": "secret",
        "timeout": 9.0,
        "allow_insecure_telnet": True,
    }
    assert payload["transport"] == "telnet"
    assert payload["security"] == "insecure_plaintext"
    assert payload["telnet_port"] == 2323
    assert payload["telnet_timeout"] == 9.0
    assert payload["host_key_verification"] is None


def test_target_yaml_cannot_enable_insecure_telnet(monkeypatch, tmp_path):
    monkeypatch.setenv("CLI_TOOL_SSH_PASSWORD", "secret")
    target_config = tmp_path / "targets.yaml"
    target_config.write_text(
        """
version: 1
targets:
  legacy:
    transport: telnet
    allow_insecure_telnet: true
    host: 192.0.2.10
    username: admin
""".strip(),
        encoding="utf-8",
    )
    args = cli_tool_readonly_smoke.parse_args(
        ["--target-config", str(target_config), "--target", "legacy"]
    )

    with pytest.raises(SystemExit, match="--allow-insecure-telnet"):
        cli_tool_readonly_smoke.run_smoke(args)
