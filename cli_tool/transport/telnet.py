from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass

from cli_tool.models import CliCommandOutput
from cli_tool.transport.readiness import (
    detect_prompt,
    ends_with_prompt,
    normalize_stream_text,
    strip_command_envelope,
)


IAC = 255
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250
SE = 240

LOGIN_PROMPT = re.compile(r"(?:login|username|user\s+name)\s*[:>]", re.IGNORECASE)
PASSWORD_PROMPT = re.compile(r"password\s*[:>]", re.IGNORECASE)
YES_NO_CONFIRM_PROMPT = re.compile(
    r"(?:please\s+)?confirm\s*\[y/n\]|confirm\s*\(y/n\)",
    re.IGNORECASE,
)
PAGER_PROMPT = re.compile(
    r"--\s*more\s*--.*?(?:next\s+page|continue\s*:\s*c|quit\s*:\s*esc)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TelnetCliTransport:
    host: str
    username: str
    password: str
    port: int = 23
    timeout: float = 15.0
    allow_insecure_telnet: bool = False

    def run_commands(
        self,
        commands: list[str] | tuple[str, ...],
        *,
        owner: str | None = None,
    ) -> list[CliCommandOutput]:
        if not self.allow_insecure_telnet:
            raise PermissionError(
                "Telnet is plaintext; set allow_insecure_telnet=True only for an isolated lab"
            )
        session = TelnetCliSession(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=self.timeout,
        )
        try:
            session.connect()
            return session.run_commands(list(commands))
        finally:
            session.close(logout=True)


class TelnetCliSession:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 23,
        timeout: float = 15.0,
    ) -> None:
        if not host:
            raise ValueError("host is required")
        if not username:
            raise ValueError("username is required")
        if not password:
            raise ValueError("password is required")
        if port < 1 or port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.connection = None
        self.prompt: str | None = None
        self._protocol_buffer = bytearray()

    def connect(self) -> None:
        if self.connection is not None:
            return
        connection = socket.create_connection((self.host, self.port), timeout=self.timeout)
        connection.settimeout(min(self.timeout, 0.2))
        self.connection = connection
        try:
            _banner, ready, prompt = self._read_until_ready(
                prompt=None,
                stop_patterns=(("login", LOGIN_PROMPT), ("password", PASSWORD_PROMPT)),
            )
            if ready == "login":
                self._write_line(self.username)
                _login, ready, prompt = self._read_until_ready(
                    prompt=None,
                    stop_patterns=(("password", PASSWORD_PROMPT),),
                )
            if ready == "password":
                self._write_line(self.password)
                _password, ready, prompt = self._read_until_ready(prompt=None)
            if ready != "prompt" or prompt is None:
                raise RuntimeError("Telnet CLI prompt was not established")
            self.prompt = prompt
        except Exception:
            self.close(logout=False)
            raise

    def run_commands(self, commands: list[str]) -> list[CliCommandOutput]:
        self.connect()
        return [self.run_command(command) for command in commands]

    def run_command(self, command: str) -> CliCommandOutput:
        if self.connection is None:
            raise RuntimeError("Telnet connection is not available")
        if self.prompt is None:
            raise RuntimeError("Telnet CLI prompt was not established")
        self._write_line(command)
        output, ready, _prompt = self._read_until_ready(
            prompt=self.prompt,
            stop_patterns=(("confirmation", YES_NO_CONFIRM_PROMPT),),
        )
        if ready == "confirmation":
            self._write_line("y")
            confirmed, _ready, _prompt = self._read_until_ready(prompt=self.prompt)
            output = "\n".join([output, confirmed])
        return CliCommandOutput(
            command=command,
            output=strip_command_envelope(output, command, self.prompt),
        )

    def close(self, *, logout: bool = True) -> None:
        connection = self.connection
        if connection is None:
            return
        try:
            if logout:
                try:
                    self._write_line("exit")
                except OSError:
                    pass
        finally:
            connection.close()
            self.connection = None
            self.prompt = None
            self._protocol_buffer.clear()

    def _read_until_ready(
        self,
        *,
        prompt: str | None,
        stop_patterns: tuple[tuple[str, re.Pattern[str]], ...] = (),
        max_pager_continuations: int = 5,
    ) -> tuple[str, str, str | None]:
        if self.connection is None:
            raise RuntimeError("Telnet connection is not available")

        chunks: list[bytes] = []
        deadline = time.monotonic() + self.timeout
        handled_pagers = 0
        while time.monotonic() < deadline:
            try:
                raw = self.connection.recv(65535)
            except socket.timeout:
                raw = None
            if raw == b"":
                raise ConnectionError("Telnet connection closed before CLI output became ready")
            if raw:
                visible, responses = self._consume_protocol(raw)
                if responses:
                    self.connection.sendall(responses)
                if visible:
                    chunks.append(visible)

            output = normalize_stream_text(b"".join(chunks).decode("utf-8", errors="replace"))
            pager_count = len(PAGER_PROMPT.findall(output))
            while handled_pagers < pager_count:
                if handled_pagers >= max_pager_continuations:
                    raise RuntimeError("CLI pager did not finish after sending continue")
                self._write_raw(b"c")
                chunks.append(b"\n")
                handled_pagers += 1

            if prompt is not None and ends_with_prompt(output, prompt):
                return output, "prompt", prompt
            if prompt is None:
                detected = detect_prompt(output)
                if detected is not None:
                    return output, "prompt", detected
            for name, pattern in stop_patterns:
                if pattern.search(output):
                    return output, name, prompt

        expected = "CLI prompt" if prompt is None else f"CLI prompt {prompt!r}"
        raise TimeoutError(f"timed out after {self.timeout:g}s waiting for {expected}")

    def _consume_protocol(self, data: bytes) -> tuple[bytes, bytes]:
        self._protocol_buffer.extend(data)
        visible, responses, remaining = consume_telnet_bytes(bytes(self._protocol_buffer))
        self._protocol_buffer = bytearray(remaining)
        return visible, responses

    def _write_line(self, value: str) -> None:
        self._write_raw((value + "\r\n").encode("utf-8"))

    def _write_raw(self, value: bytes) -> None:
        if self.connection is None:
            raise RuntimeError("Telnet connection is not available")
        self.connection.sendall(value)


def consume_telnet_bytes(data: bytes) -> tuple[bytes, bytes, bytes]:
    """Strip RFC 854 negotiation and refuse all optional features."""
    visible = bytearray()
    responses = bytearray()
    index = 0

    while index < len(data):
        if data[index] != IAC:
            visible.append(data[index])
            index += 1
            continue
        if index + 1 >= len(data):
            break

        command = data[index + 1]
        if command == IAC:
            visible.append(IAC)
            index += 2
            continue
        if command in {DO, DONT, WILL, WONT}:
            if index + 2 >= len(data):
                break
            option = data[index + 2]
            if command == DO:
                responses.extend((IAC, WONT, option))
            elif command == WILL:
                responses.extend((IAC, DONT, option))
            index += 3
            continue
        if command == SB:
            end = _find_subnegotiation_end(data, index + 2)
            if end is None:
                break
            index = end
            continue
        index += 2

    return bytes(visible), bytes(responses), data[index:]


def _find_subnegotiation_end(data: bytes, start: int) -> int | None:
    index = start
    while index + 1 < len(data):
        if data[index] == IAC and data[index + 1] == SE:
            return index + 2
        index += 1
    return None
