from __future__ import annotations

import socket

import pytest

from cli_tool.transport.telnet import (
    DO,
    DONT,
    IAC,
    WILL,
    WONT,
    TelnetCliSession,
    TelnetCliTransport,
    consume_telnet_bytes,
)


class FakeSocket:
    def __init__(self, chunks: list[bytes], *, respond_to_command: bool = True) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []
        self.closed = False
        self.timeout = None
        self.respond_to_command = respond_to_command

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv(self, size: int) -> bytes:
        if self.chunks:
            return self.chunks.pop(0)
        raise socket.timeout()

    def sendall(self, value: bytes) -> None:
        self.sent.append(value)
        if value == b"admin\r\n":
            self.chunks.append(b"Password:")
        elif value == b"secret\r\n":
            self.chunks.append(b"NXC400#")
        elif value == b"show version\r\n" and self.respond_to_command:
            self.chunks.append(b"show version\r\nVersion: V1.0\r\nNXC400#")
        elif value == b"c":
            self.chunks.append(b"tail\r\nNXC400#")

    def close(self) -> None:
        self.closed = True


def test_telnet_negotiation_is_stripped_and_refused():
    visible, responses, remaining = consume_telnet_bytes(
        bytes([IAC, WILL, 1, IAC, DO, 3]) + b"Username:"
    )

    assert visible == b"Username:"
    assert responses == bytes([IAC, DONT, 1, IAC, WONT, 3])
    assert remaining == b""


def test_telnet_negotiation_preserves_fragmented_command():
    visible, responses, remaining = consume_telnet_bytes(b"banner" + bytes([IAC, WILL]))

    assert visible == b"banner"
    assert responses == b""
    assert remaining == bytes([IAC, WILL])


def test_telnet_session_logs_in_and_waits_for_prompt(monkeypatch):
    fake = FakeSocket([bytes([IAC, WILL, 1]) + b"Username:"])
    monkeypatch.setattr(
        "cli_tool.transport.telnet.socket.create_connection",
        lambda address, timeout: fake,
    )
    session = TelnetCliSession("192.0.2.10", "admin", "secret", timeout=1)

    session.connect()
    result = session.run_command("show version")
    session.close()

    assert fake.sent[:3] == [
        bytes([IAC, DONT, 1]),
        b"admin\r\n",
        b"secret\r\n",
    ]
    assert result.output == "Version: V1.0"
    assert fake.sent[-1] == b"exit\r\n"
    assert fake.closed is True


def test_telnet_session_continues_pager():
    fake = FakeSocket(
        [
            (
                b"show version\r\nheader\r\n"
                b"-- more --, next page: Space, continue: c, quit: ESC"
            )
        ],
        respond_to_command=False,
    )
    session = TelnetCliSession("192.0.2.10", "admin", "secret", timeout=1)
    session.connection = fake
    session.prompt = "NXC400#"

    result = session.run_command("show version")

    assert b"c" in fake.sent
    assert result.output.endswith("tail")


def test_telnet_transport_requires_explicit_insecure_opt_in():
    transport = TelnetCliTransport(
        host="192.0.2.10",
        username="admin",
        password="secret",
    )

    with pytest.raises(PermissionError, match="plaintext"):
        transport.run_commands(["show version"])


def test_telnet_empty_command_output_remains_empty():
    fake = FakeSocket([b"show version\r\nNXC400#"])
    session = TelnetCliSession("192.0.2.10", "admin", "secret", timeout=1)
    session.connection = fake
    session.prompt = "NXC400#"

    result = session.run_command("show version")

    assert result.output == ""
