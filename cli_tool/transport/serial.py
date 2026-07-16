from __future__ import annotations

import re
import time
from dataclasses import dataclass

from cli_tool.models import CliCommandOutput
from cli_tool.transport.readiness import (
    detect_prompt,
    ends_with_prompt,
    normalize_stream_text,
    strip_command_envelope,
)


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LOGIN_PROMPT = re.compile(r"(?:login|username|user\s+name)\s*:", re.IGNORECASE)
PASSWORD_PROMPT = re.compile(r"password\s*:", re.IGNORECASE)
LOGOUT_CONFIRM_PROMPT = re.compile(r"logout\s+system\s+now\s*\(y/n\)\?", re.IGNORECASE)
YES_NO_CONFIRM_PROMPT = re.compile(r"(?:please\s+)?confirm\s*\[y/n\]|confirm\s*\(y/n\)", re.IGNORECASE)
PAGER_PROMPT = re.compile(
    r"--\s*more\s*--.*?(?:next\s+page|continue\s*:\s*c|quit\s*:\s*esc)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SerialCliTransport:
    port: str
    username: str
    password: str
    baudrate: int = 115200
    timeout: float = 15.0

    def run_commands(self, commands: list[str] | tuple[str, ...], *, owner: str | None = None) -> list[CliCommandOutput]:
        session = SerialCliSession(
            port=self.port,
            username=self.username,
            password=self.password,
            baudrate=self.baudrate,
            timeout=self.timeout,
        )
        try:
            session.connect()
            return session.run_commands(list(commands))
        finally:
            session.close(logout=True)


@dataclass(frozen=True)
class SerialCommandResult:
    command: str
    output: str


class SerialCliSession:
    def __init__(
        self,
        port: str,
        username: str,
        password: str,
        *,
        baudrate: int = 115200,
        timeout: float = 15.0,
    ) -> None:
        self.port = port
        self.username = username
        self.password = password
        self.baudrate = baudrate
        self.timeout = timeout
        self.connection = None
        self.prompt: str | None = None

    def connect(self) -> None:
        if self.connection is not None and bool(getattr(self.connection, "is_open", True)):
            return
        try:
            import serial
        except ImportError as error:
            raise RuntimeError("pyserial is required for serial CLI verification") from error

        self.connection = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=self.timeout,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self._write_line("")
        _banner, ready, prompt = self._read_until_ready(
            prompt=None,
            stop_patterns=(("login", LOGIN_PROMPT), ("password", PASSWORD_PROMPT)),
        )
        if ready == "login":
            self._write_line(self.username)
            _login_output, ready, prompt = self._read_until_ready(
                prompt=None,
                stop_patterns=(("password", PASSWORD_PROMPT),),
            )
        if ready == "password":
            self._write_line(self.password)
            _password_output, ready, prompt = self._read_until_ready(prompt=None)
        if ready != "prompt" or prompt is None:
            raise RuntimeError("serial CLI prompt was not established")
        self.prompt = prompt

    def run_commands(self, commands: list[str]) -> list[CliCommandOutput]:
        self.connect()
        return [
            CliCommandOutput(command=result.command, output=result.output)
            for result in [self.run_command(command) for command in commands]
        ]

    def run_command(self, command: str) -> SerialCommandResult:
        if self.connection is None:
            raise RuntimeError("serial connection is not available")
        if self.prompt is None:
            raise RuntimeError("serial CLI prompt was not established")
        self._write_line(command)
        output, ready, _prompt = self._read_until_ready(
            prompt=self.prompt,
            stop_patterns=(("confirmation", YES_NO_CONFIRM_PROMPT),),
        )
        if ready == "confirmation":
            self._write_line("y")
            confirmed_output, _ready, _prompt = self._read_until_ready(prompt=self.prompt)
            output = "\n".join([output, confirmed_output])
        return SerialCommandResult(
            command=command,
            output=strip_command_envelope(output, command, self.prompt),
        )

    def close(self, *, logout: bool = True) -> None:
        connection = self.connection
        if connection is None:
            return
        try:
            if logout and bool(getattr(connection, "is_open", True)):
                self._write_line("exit")
                output = self._read_available(first_wait=0.2, idle_wait=0.2, max_wait=2)
                if LOGOUT_CONFIRM_PROMPT.search(output):
                    self._write_line("y")
                    self._read_available(first_wait=0.2, idle_wait=0.2, max_wait=2)
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
            self.connection = None

    def _read_until_ready(
        self,
        *,
        prompt: str | None,
        stop_patterns: tuple[tuple[str, re.Pattern[str]], ...] = (),
        max_pager_continuations: int = 5,
    ) -> tuple[str, str, str | None]:
        if self.connection is None:
            raise RuntimeError("serial connection is not available")

        chunks: list[bytes] = []
        deadline = time.monotonic() + self.timeout
        handled_pagers = 0
        while time.monotonic() < deadline:
            waiting = int(getattr(self.connection, "in_waiting", 0))
            if waiting > 0:
                chunks.append(self.connection.read(waiting))
            elif not bool(getattr(self.connection, "is_open", True)):
                raise ConnectionError("serial connection closed before CLI output became ready")

            output = normalize_stream_text(b"".join(chunks).decode("utf-8", errors="replace"))
            pager_count = len(PAGER_PROMPT.findall(output))
            while handled_pagers < pager_count:
                if handled_pagers >= max_pager_continuations:
                    raise RuntimeError("CLI pager did not finish after sending continue")
                self._write_raw("c")
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

            time.sleep(0.01)

        expected = "CLI prompt" if prompt is None else f"CLI prompt {prompt!r}"
        raise TimeoutError(f"timed out after {self.timeout:g}s waiting for {expected}")

    def _read_available(self, *, first_wait: float, idle_wait: float, max_wait: float) -> str:
        if self.connection is None:
            raise RuntimeError("serial connection is not available")
        time.sleep(first_wait)
        chunks: list[bytes] = []
        deadline = time.monotonic() + max_wait
        idle_deadline = time.monotonic() + idle_wait
        while time.monotonic() < deadline and time.monotonic() < idle_deadline:
            waiting = int(getattr(self.connection, "in_waiting", 0))
            if waiting > 0:
                chunks.append(self.connection.read(waiting))
                idle_deadline = time.monotonic() + idle_wait
            else:
                time.sleep(0.05)
        return normalize_cli_output(b"".join(chunks).decode("utf-8", errors="replace"))

    def _write_line(self, value: str) -> None:
        self._write_raw(value + "\r\n")

    def _write_raw(self, value: str) -> None:
        if self.connection is None:
            raise RuntimeError("serial connection is not available")
        self.connection.write(value.encode("utf-8"))


def normalize_cli_output(output: str) -> str:
    output = ANSI_ESCAPE.sub("", output)
    output = output.replace("\r", "\n")
    lines = []
    for line in output.splitlines():
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)
