from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cli_tool.models import CliCommandOutput
from cli_tool.transport.serial import SerialCliSession, SerialCliTransport
from cli_tool.transport.ssh import SshCliClient, SshCliSession, SshSessionPool
from cli_tool.transport.ssh_adapter import SshCliTransport
from cli_tool.workflows.verify import run_and_verify


class FakeTransport:
    def __init__(self, outputs: list[CliCommandOutput]) -> None:
        self.outputs = outputs
        self.commands: list[str] = []
        self.owner: str | None = None

    def run_commands(self, commands: list[str] | tuple[str, ...], *, owner: str | None = None) -> list[CliCommandOutput]:
        self.commands = list(commands)
        self.owner = owner
        return self.outputs


def test_run_and_verify_uses_transport_outputs():
    transport = FakeTransport([CliCommandOutput(command="show version", output="Model: NeoX-06\nVersion: V1.0")])

    result = run_and_verify(
        transport,
        ["show version"],
        {"show version": ["model", "version"]},
        owner="unit-test",
    )

    assert result.passed
    assert transport.commands == ["show version"]
    assert transport.owner == "unit-test"


def test_run_and_verify_reports_missing_tokens_from_transport_output():
    transport = FakeTransport([CliCommandOutput(command="show vlan 100", output="VLAN Name: REST_API")])

    result = run_and_verify(
        transport,
        ["show vlan 100"],
        {"show vlan 100": ["VLAN Name", "VID: 100"]},
    )

    assert not result.passed
    assert result.missing_by_command == {"show vlan 100": ["VID: 100"]}


@dataclass(frozen=True)
class PoolResult:
    command: str
    output: str


class FakePool:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.owner: str | None = None

    def run_commands(self, commands: list[str], *, owner: str) -> tuple[list[PoolResult], object]:
        self.commands = commands
        self.owner = owner
        return [PoolResult(command=command, output=f"output for {command}") for command in commands], object()


def test_ssh_cli_transport_wraps_existing_session_pool(monkeypatch):
    fake_pool = FakePool()
    captured = {}

    def fake_ssh_session_pool(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake_pool

    monkeypatch.setattr("cli_tool.transport.ssh.ssh_session_pool", fake_ssh_session_pool)

    transport = SshCliTransport(
        node_key="node3",
        host="192.0.2.10",
        username="admin",
        password="secret",
        max_sessions=1,
        reuse_sessions=False,
    )
    outputs = transport.run_commands(["show version"], owner="adapter-test")

    assert captured["args"] == ("node3", "192.0.2.10", "admin", "secret")
    assert captured["kwargs"]["max_sessions"] == 1
    assert captured["kwargs"]["reuse_sessions"] is False
    assert fake_pool.commands == ["show version"]
    assert fake_pool.owner == "adapter-test"
    assert outputs == [CliCommandOutput(command="show version", output="output for show version")]


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, value: str) -> None:
        self.sent.append(value)


def test_ssh_session_continues_cli_pager(monkeypatch):
    fake_channel = FakeChannel()
    chunks = iter(
        [
            "header\n-- more --, next page: Space, continue: c, quit: ESC",
            "tail\nNXC400#",
        ]
    )

    def fake_read_available(channel, **kwargs):
        assert channel is fake_channel
        return next(chunks)

    monkeypatch.setattr(SshCliClient, "_read_available", staticmethod(fake_read_available))

    session = SshCliSession("192.0.2.10", "admin", "secret")
    session.channel = fake_channel

    output, _elapsed, confirmed = session.run_command("show lcman status")

    assert fake_channel.sent == ["show lcman status\n", "c"]
    assert output == "header\n-- more --, next page: Space, continue: c, quit: ESC\ntail\nNXC400#"
    assert confirmed is False


def test_ssh_session_pool_acquire_does_not_suppress_errors():
    pool = SshSessionPool.__new__(SshSessionPool)
    pooled = SimpleNamespace(timing=SimpleNamespace(error="RuntimeError: boom"))
    released = []

    pool._acquire = lambda owner: pooled
    pool.release = lambda released_pooled: released.append(released_pooled)

    with pytest.raises(RuntimeError, match="boom"):
        with pool.acquire("unit-test"):
            raise RuntimeError("boom")

    assert released == []


def test_serial_cli_transport_runs_commands_through_session(monkeypatch):
    captured = {}

    class FakeSerialSession:
        def __init__(self, **kwargs) -> None:
            captured["kwargs"] = kwargs
            self.closed = False

        def connect(self) -> None:
            captured["connected"] = True

        def run_commands(self, commands: list[str]) -> list[CliCommandOutput]:
            captured["commands"] = commands
            return [CliCommandOutput(command=command, output=f"output for {command}") for command in commands]

        def close(self, *, logout: bool = True) -> None:
            captured["logout"] = logout
            self.closed = True

    monkeypatch.setattr("cli_tool.transport.serial.SerialCliSession", FakeSerialSession)

    transport = SerialCliTransport(
        port="COM5",
        username="admin",
        password="secret",
        baudrate=115200,
        timeout=10,
    )

    outputs = transport.run_commands(["show version"], owner="serial-test")

    assert captured["kwargs"] == {
        "port": "COM5",
        "username": "admin",
        "password": "secret",
        "baudrate": 115200,
        "timeout": 10,
    }
    assert captured["connected"] is True
    assert captured["commands"] == ["show version"]
    assert captured["logout"] is True
    assert outputs == [CliCommandOutput(command="show version", output="output for show version")]


class FakeSerialConnection:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, value: bytes) -> None:
        self.writes.append(value.decode("utf-8"))


def test_serial_session_continues_cli_pager(monkeypatch):
    fake_connection = FakeSerialConnection()
    chunks = iter(
        [
            "header\n-- more --, next page: Space, continue: c, quit: ESC",
            "tail\nMSC#",
        ]
    )

    session = SerialCliSession("COM5", "admin", "secret")
    session.connection = fake_connection
    monkeypatch.setattr(session, "_read_available", lambda **kwargs: next(chunks))

    result = session.run_command("show system-information")

    assert fake_connection.writes == ["show system-information\r\n", "c"]
    assert result.command == "show system-information"
    assert result.output == "header\n-- more --, next page: Space, continue: c, quit: ESC\ntail\nMSC#"

