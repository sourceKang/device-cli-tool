from __future__ import annotations

import re
import time
from dataclasses import dataclass

from cli_tool.models import CliCommandOutput


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
        banner = self._read_available(first_wait=0.5, idle_wait=0.3, max_wait=self.timeout)
        self._login_if_prompted(banner)

    def run_commands(self, commands: list[str]) -> list[CliCommandOutput]:
        self.connect()
        return [
            CliCommandOutput(command=result.command, output=result.output)
            for result in [self.run_command(command) for command in commands]
        ]

    def run_command(self, command: str) -> SerialCommandResult:
        if self.connection is None:
            raise RuntimeError("serial connection is not available")
        self._write_line(command)
        output = self._read_command_output()
        if YES_NO_CONFIRM_PROMPT.search(output):
            self._write_line("y")
            output = "\n".join([output, self._read_command_output()])
        return SerialCommandResult(command=command, output=output)

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

    def _login_if_prompted(self, output: str) -> None:
        if LOGIN_PROMPT.search(output):
            self._write_line(self.username)
            output = self._read_available(first_wait=0.2, idle_wait=0.3, max_wait=self.timeout)
        if PASSWORD_PROMPT.search(output):
            self._write_line(self.password)
            self._read_available(first_wait=0.2, idle_wait=0.4, max_wait=self.timeout)

    def _read_command_output(self, *, max_pager_continuations: int = 5) -> str:
        output_parts = [self._read_available(first_wait=0.5, idle_wait=0.4, max_wait=self.timeout)]
        for _ in range(max_pager_continuations):
            if not PAGER_PROMPT.search(output_parts[-1]):
                return "\n".join(output_parts)
            self._write_raw("c")
            output_parts.append(self._read_available(first_wait=0.2, idle_wait=0.4, max_wait=self.timeout))
        raise RuntimeError("CLI pager did not finish after sending continue")

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
