from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cli_tool.models import CliCommandOutput
from cli_tool.transport.serial import SerialCliSession, SerialCliTransport
from cli_tool.transport.readiness import detect_prompt
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


def test_prompt_detection_uses_final_prompt_after_banner_text():
    assert detect_prompt("old-banner#\nWelcome\nNXC400#") == "NXC400#"


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
    assert captured["kwargs"]["connect_attempts"] == 3
    assert captured["kwargs"]["retry_backoff_seconds"] == 1.0
    assert captured["kwargs"]["reuse_sessions"] is False
    assert fake_pool.commands == ["show version"]
    assert fake_pool.owner == "adapter-test"
    assert outputs == [CliCommandOutput(command="show version", output="output for show version")]


class FakeChannel:
    def __init__(self, chunks: list[str] | None = None) -> None:
        self.sent: list[str] = []
        self.chunks = [chunk.encode() for chunk in (chunks or [])]
        self.closed = False

    def send(self, value: str) -> None:
        self.sent.append(value)

    def recv_ready(self) -> bool:
        return bool(self.chunks)

    def recv(self, size: int) -> bytes:
        return self.chunks.pop(0)


def test_ssh_session_continues_cli_pager(monkeypatch):
    fake_channel = FakeChannel(
        chunks=[
            "header\n-- more --, next page: Space, continue: c, quit: ESC",
            "tail\nNXC400#",
        ]
    )

    session = SshCliSession("192.0.2.10", "admin", "secret")
    session.channel = fake_channel
    session.prompt = "NXC400#"

    output, _elapsed, confirmed = session.run_command("show lcman status")

    assert fake_channel.sent == ["show lcman status\n", "c"]
    assert output == "header\n-- more --, next page: Space, continue: c, quit: ESC\ntail"
    assert confirmed is False


def test_ssh_session_waits_for_prompt_instead_of_idle_gap(monkeypatch):
    class DelayedPromptChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__(["show version\nVersion: V1.0\n", "NXC400#"])
            self.polls_without_data = 2

        def recv_ready(self) -> bool:
            if len(self.chunks) == 1 and self.polls_without_data > 0:
                self.polls_without_data -= 1
                return False
            return super().recv_ready()

    monkeypatch.setattr("cli_tool.transport.ssh.time.sleep", lambda _seconds: None)
    channel = DelayedPromptChannel()
    session = SshCliSession("192.0.2.10", "admin", "secret")
    session.channel = channel
    session.prompt = "NXC400#"

    output, _elapsed, _confirmed = session.run_command("show version")

    assert output == "Version: V1.0"
    assert channel.polls_without_data == 0


def test_ssh_session_returns_empty_when_device_only_echoes_command_and_prompt():
    channel = FakeChannel(["show version\nNXC400#"])
    session = SshCliSession("192.0.2.10", "admin", "secret")
    session.channel = channel
    session.prompt = "NXC400#"

    output, _elapsed, _confirmed = session.run_command("show version")

    assert output == ""


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


def test_ssh_session_retries_transient_connect_errors(monkeypatch):
    connect_calls = []
    sleeps = []

    class FakeTransportState:
        def close(self) -> None:
            pass

    class FakeConnectChannel:
        def __init__(self) -> None:
            self.closed = False
            self.chunks: list[bytes] = []

        def settimeout(self, timeout) -> None:
            self.timeout = timeout

        def send(self, value: str) -> None:
            if value == "\n":
                self.chunks.append(b"NXC400#")

        def recv_ready(self) -> bool:
            return bool(self.chunks)

        def recv(self, size: int) -> bytes:
            return self.chunks.pop(0)

        def close(self) -> None:
            self.closed = True

    class FakeSshClient:
        def __init__(self) -> None:
            self.connected = False

        def set_missing_host_key_policy(self, policy) -> None:
            pass

        def connect(self, **kwargs) -> None:
            connect_calls.append(kwargs)
            if len(connect_calls) < 3:
                raise OSError("temporarily unavailable")
            self.connected = True

        def invoke_shell(self, **kwargs):
            return FakeConnectChannel()

        def get_transport(self):
            return FakeTransportState() if self.connected else None

        def close(self) -> None:
            self.connected = False

    fake_paramiko = SimpleNamespace(
        SSHClient=FakeSshClient,
        AutoAddPolicy=lambda: object(),
        SSHException=type("FakeSshException", (Exception,), {}),
        AuthenticationException=type("FakeAuthenticationException", (Exception,), {}),
        BadAuthenticationType=type("FakeBadAuthenticationType", (Exception,), {}),
        PasswordRequiredException=type("FakePasswordRequiredException", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)
    monkeypatch.setattr("cli_tool.transport.ssh.time.sleep", sleeps.append)

    session = SshCliSession(
        "192.0.2.10",
        "admin",
        "secret",
        connect_attempts=3,
        retry_backoff_seconds=0.5,
    )
    session.connect()

    assert len(connect_calls) == 3
    assert sleeps == [0.5, 1.0]
    assert session.connect_attempts_used == 3
    assert session.channel is not None
    assert session.prompt == "NXC400#"


def test_ssh_session_does_not_retry_authentication_errors(monkeypatch):
    connect_calls = []
    sleeps = []

    class FakeAuthenticationException(Exception):
        pass

    class FakeSshClient:
        def set_missing_host_key_policy(self, policy) -> None:
            pass

        def connect(self, **kwargs) -> None:
            connect_calls.append(kwargs)
            raise FakeAuthenticationException("authentication failed")

        def get_transport(self):
            return None

        def close(self) -> None:
            pass

    fake_paramiko = SimpleNamespace(
        SSHClient=FakeSshClient,
        AutoAddPolicy=lambda: object(),
        SSHException=type("FakeSshException", (Exception,), {}),
        AuthenticationException=FakeAuthenticationException,
        BadAuthenticationType=type("FakeBadAuthenticationType", (Exception,), {}),
        PasswordRequiredException=type("FakePasswordRequiredException", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)
    monkeypatch.setattr("cli_tool.transport.ssh.time.sleep", sleeps.append)

    session = SshCliSession("192.0.2.10", "admin", "wrong", connect_attempts=3)

    with pytest.raises(FakeAuthenticationException, match="authentication failed"):
        session.connect()

    assert len(connect_calls) == 1
    assert sleeps == []
    assert session.connect_attempts_used == 1


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
    def __init__(self, chunks: list[str] | None = None) -> None:
        self.writes: list[str] = []
        self.chunks = [chunk.encode() for chunk in (chunks or [])]
        self.is_open = True

    def write(self, value: bytes) -> None:
        self.writes.append(value.decode("utf-8"))

    @property
    def in_waiting(self) -> int:
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size: int) -> bytes:
        return self.chunks.pop(0)


def test_serial_session_continues_cli_pager(monkeypatch):
    fake_connection = FakeSerialConnection(
        chunks=[
            "header\n-- more --, next page: Space, continue: c, quit: ESC",
            "tail\nMSC#",
        ]
    )

    session = SerialCliSession("COM5", "admin", "secret")
    session.connection = fake_connection
    session.prompt = "MSC#"

    result = session.run_command("show system-information")

    assert fake_connection.writes == ["show system-information\r\n", "c"]
    assert result.command == "show system-information"
    assert result.output == "header\n-- more --, next page: Space, continue: c, quit: ESC\ntail"


def test_serial_session_login_establishes_prompt(monkeypatch):
    connection = FakeSerialConnection(["Username:", "Password:", "MSC#"])
    connection.close = lambda: setattr(connection, "is_open", False)
    fake_serial = SimpleNamespace(
        Serial=lambda **kwargs: connection,
        EIGHTBITS=8,
        PARITY_NONE="N",
        STOPBITS_ONE=1,
    )
    monkeypatch.setitem(sys.modules, "serial", fake_serial)

    session = SerialCliSession("COM5", "admin", "secret", timeout=1)
    session.connect()

    assert session.prompt == "MSC#"
    assert connection.writes == ["\r\n", "admin\r\n", "secret\r\n"]

