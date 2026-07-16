from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LOGOUT_CONFIRM_PROMPT = re.compile(r"logout\s+system\s+now\s*\(y/n\)\?", re.IGNORECASE)
YES_NO_CONFIRM_PROMPT = re.compile(r"(?:please\s+)?confirm\s*\[y/n\]|confirm\s*\(y/n\)", re.IGNORECASE)
PAGER_PROMPT = re.compile(
    r"--\s*more\s*--.*?(?:next\s+page|continue\s*:\s*c|quit\s*:\s*esc)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CliCommandResult:
    command: str
    output: str


@dataclass
class SshTiming:
    node_key: str
    host: str
    owner: str
    session_id: str
    reused_session: bool
    token_path: str
    wait_seconds: float
    connect_seconds: float = 0.0
    banner_read_seconds: float = 0.0
    command_seconds: list[dict[str, float | str | bool]] = field(default_factory=list)
    logout_seconds: float = 0.0
    closed: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "node_key": self.node_key,
            "host": self.host,
            "owner": self.owner,
            "session_id": self.session_id,
            "reused_session": self.reused_session,
            "token_path": self.token_path,
            "wait_seconds": round(self.wait_seconds, 3),
            "connect_seconds": round(self.connect_seconds, 3),
            "banner_read_seconds": round(self.banner_read_seconds, 3),
            "command_seconds": [
                {
                    "command": item["command"],
                    "elapsed_seconds": round(float(item["elapsed_seconds"]), 3),
                    "confirmed": bool(item.get("confirmed", False)),
                }
                for item in self.command_seconds
            ],
            "logout_seconds": round(self.logout_seconds, 3),
            "closed": self.closed,
            "error": self.error,
        }


class SshCliClient:
    def __init__(self, host: str, username: str, password: str, *, timeout: float = 15) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout

    def run_commands(self, commands: list[str]) -> list[CliCommandResult]:
        session = SshCliSession(self.host, self.username, self.password, timeout=self.timeout)
        try:
            session.connect()
            return session.run_commands(commands)
        finally:
            session.close(logout=True)

    @staticmethod
    def _logout(channel) -> float:
        started = time.monotonic()
        channel.send("exit\n")
        output = SshCliClient._read_available(channel, first_wait=0.2, idle_wait=0.2, max_wait=2)
        if LOGOUT_CONFIRM_PROMPT.search(output):
            channel.send("y\n")
            SshCliClient._read_available(channel, first_wait=0.2, idle_wait=0.2, max_wait=2)
        return time.monotonic() - started

    @staticmethod
    def _read_available(channel, *, first_wait: float = 0.8, idle_wait: float = 0.4, max_wait: float = 8) -> str:
        time.sleep(first_wait)
        chunks: list[str] = []
        deadline = time.time() + max_wait
        idle_deadline = time.time() + idle_wait
        while time.time() < deadline and time.time() < idle_deadline:
            if channel.recv_ready():
                chunks.append(channel.recv(65535).decode("utf-8", errors="replace"))
                idle_deadline = time.time() + idle_wait
            else:
                time.sleep(0.1)
        return normalize_cli_output("".join(chunks))

    @staticmethod
    def _read_command_output(channel, *, max_pager_continuations: int = 5) -> str:
        output_parts = [SshCliClient._read_available(channel)]
        for _ in range(max_pager_continuations):
            if not PAGER_PROMPT.search(output_parts[-1]):
                return "\n".join(output_parts)
            channel.send("c")
            output_parts.append(SshCliClient._read_available(channel, first_wait=0.2, idle_wait=0.4, max_wait=8))
        raise RuntimeError("CLI pager did not finish after sending continue")


class SshCliSession:
    def __init__(self, host: str, username: str, password: str, *, timeout: float = 15) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout
        self.client = None
        self.channel = None
        self.connect_seconds = 0.0
        self.banner_read_seconds = 0.0
        self.session_id = f"ssh-{os.getpid()}-{id(self):x}"

    def connect(self) -> None:
        if self.is_healthy():
            return
        try:
            import paramiko
        except ImportError as error:
            raise RuntimeError("paramiko is required for SSH CLI verification") from error

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        started = time.monotonic()
        self.client.connect(
            hostname=self.host,
            username=self.username,
            password=self.password,
            look_for_keys=False,
            allow_agent=False,
            timeout=self.timeout,
            banner_timeout=self.timeout,
            auth_timeout=self.timeout,
        )
        self.connect_seconds += time.monotonic() - started
        self.channel = self.client.invoke_shell(width=200, height=120)
        self.channel.settimeout(3)
        started = time.monotonic()
        SshCliClient._read_available(self.channel)
        self.banner_read_seconds += time.monotonic() - started

    def is_healthy(self) -> bool:
        if self.client is None or self.channel is None:
            return False
        if bool(getattr(self.channel, "closed", False)):
            return False
        transport = self.client.get_transport()
        if transport is None:
            return False
        is_active = getattr(transport, "is_active", None)
        return bool(is_active()) if callable(is_active) else True

    def run_commands(self, commands: list[str]) -> list[CliCommandResult]:
        self.connect()
        if self.channel is None:
            raise RuntimeError("SSH channel is not available")
        results = []
        for command in commands:
            output, _elapsed, _confirmed = self.run_command(command)
            results.append(CliCommandResult(command=command, output=output))
        return results

    def run_command(self, command: str) -> tuple[str, float, bool]:
        if self.channel is None:
            raise RuntimeError("SSH channel is not available")
        started = time.monotonic()
        confirmed = False
        self.channel.send(command + "\n")
        output = SshCliClient._read_command_output(self.channel)
        if YES_NO_CONFIRM_PROMPT.search(output):
            confirmed = True
            self.channel.send("y\n")
            output = "\n".join(
                [
                    output,
                    SshCliClient._read_command_output(
                        self.channel,
                    ),
                ]
            )
        return output, time.monotonic() - started, confirmed

    def close(self, *, logout: bool = True) -> float:
        logout_seconds = 0.0
        channel = self.channel
        if channel is not None:
            try:
                if logout and not bool(getattr(channel, "closed", False)):
                    logout_seconds = SshCliClient._logout(channel)
            finally:
                channel.close()
                self.channel = None
        if self.client is not None:
            transport = self.client.get_transport()
            if transport is not None:
                transport.close()
            self.client.close()
            self.client = None
        return logout_seconds


class FileTokenSemaphore:
    def __init__(self, name: str, max_tokens: int, directory: Path, *, timeout_seconds: float) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self.name = safe_token_name(name)
        self.max_tokens = max_tokens
        self.directory = directory
        self.timeout_seconds = timeout_seconds

    def acquire(self, owner: str) -> tuple[Path, float]:
        self.directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        while True:
            self._remove_stale_tokens()
            for index in range(self.max_tokens):
                path = self.directory / f"{self.name}.{index}.token"
                try:
                    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    continue
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(
                        {
                            "owner": owner,
                            "pid": os.getpid(),
                            "created_at": time.time(),
                            "node": self.name,
                        },
                        file,
                    )
                return path, time.monotonic() - started
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"waiting for {self.name} SSH token timed out after {self.timeout_seconds:.0f}s "
                    f"(max_tokens={self.max_tokens}, owner={owner})"
                )
            time.sleep(0.2)

    def release(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _remove_stale_tokens(self) -> None:
        for path in self.directory.glob(f"{self.name}.*.token"):
            if self._is_stale(path):
                self.release(path)

    @staticmethod
    def _is_stale(path: Path) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        pid = data.get("pid")
        if not isinstance(pid, int) or pid == os.getpid():
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False


class PooledSshSession:
    def __init__(self, session: SshCliSession, token_path: Path, timing: SshTiming, pool: SshSessionPool) -> None:
        self.session = session
        self.token_path = token_path
        self.timing = timing
        self.pool = pool

    def run_commands(self, commands: list[str]) -> list[CliCommandResult]:
        results = []
        try:
            for command in commands:
                output, elapsed, confirmed = self.session.run_command(command)
                self.timing.command_seconds.append(
                    {"command": command, "elapsed_seconds": elapsed, "confirmed": confirmed}
                )
                results.append(CliCommandResult(command=command, output=output))
            return results
        except Exception as error:
            self.timing.error = f"{type(error).__name__}: {error}"
            self.pool.discard(self)
            raise


class SshSessionPool:
    def __init__(
        self,
        node_key: str,
        host: str,
        username: str,
        password: str,
        *,
        max_sessions: int = 2,
        acquire_timeout_seconds: float = 60,
        ssh_timeout: float = 15,
        token_directory: Path | None = None,
        reuse_sessions: bool = True,
    ) -> None:
        self.node_key = node_key.lower()
        self.host = host
        self.username = username
        self.password = password
        self.max_sessions = max_sessions
        self.ssh_timeout = ssh_timeout
        self.reuse_sessions = reuse_sessions
        self.token_directory = token_directory or default_token_directory()
        self.semaphore = FileTokenSemaphore(
            f"{self.node_key}_{self.host}",
            max_sessions,
            self.token_directory,
            timeout_seconds=acquire_timeout_seconds,
        )
        self._idle: list[tuple[SshCliSession, Path]] = []
        self._lock = threading.Lock()
        atexit.register(self.close_all)

    @contextmanager
    def acquire(self, owner: str) -> Iterator[PooledSshSession]:
        pooled = self._acquire(owner)
        try:
            yield pooled
        finally:
            if not pooled.timing.error:
                self.release(pooled)

    def run_commands(self, commands: list[str], *, owner: str) -> tuple[list[CliCommandResult], SshTiming]:
        with self.acquire(owner) as pooled:
            results = pooled.run_commands(commands)
            return results, pooled.timing

    def _acquire(self, owner: str) -> PooledSshSession:
        with self._lock:
            while self._idle:
                session, token_path = self._idle.pop()
                if session.is_healthy():
                    timing = SshTiming(
                        node_key=self.node_key,
                        host=self.host,
                        owner=owner,
                        session_id=session.session_id,
                        reused_session=True,
                        token_path=str(token_path),
                        wait_seconds=0.0,
                    )
                    return PooledSshSession(session, token_path, timing, self)
                session.close(logout=False)
                self.semaphore.release(token_path)

        token_path, wait_seconds = self.semaphore.acquire(owner)
        session = SshCliSession(self.host, self.username, self.password, timeout=self.ssh_timeout)
        timing = SshTiming(
            node_key=self.node_key,
            host=self.host,
            owner=owner,
            session_id=session.session_id,
            reused_session=False,
            token_path=str(token_path),
            wait_seconds=wait_seconds,
        )
        try:
            session.connect()
            timing.connect_seconds = session.connect_seconds
            timing.banner_read_seconds = session.banner_read_seconds
        except Exception:
            self.semaphore.release(token_path)
            session.close(logout=False)
            raise
        return PooledSshSession(session, token_path, timing, self)

    def release(self, pooled: PooledSshSession) -> None:
        if self.reuse_sessions and pooled.session.is_healthy():
            with self._lock:
                self._idle.append((pooled.session, pooled.token_path))
            return
        self.discard(pooled, logout=True)

    def discard(self, pooled: PooledSshSession, *, logout: bool = False) -> None:
        logout_seconds = pooled.session.close(logout=logout)
        pooled.timing.logout_seconds += logout_seconds
        pooled.timing.closed = True
        self.semaphore.release(pooled.token_path)

    def close_all(self) -> None:
        with self._lock:
            sessions = self._idle
            self._idle = []
        for session, token_path in sessions:
            session.close(logout=True)
            self.semaphore.release(token_path)


_POOLS: dict[tuple[str, str, str, int, bool], SshSessionPool] = {}
_POOLS_LOCK = threading.Lock()


def ssh_session_pool(
    node_key: str,
    host: str,
    username: str,
    password: str,
    *,
    max_sessions: int | None = None,
    acquire_timeout_seconds: float | None = None,
    ssh_timeout: float = 15,
    reuse_sessions: bool | None = None,
) -> SshSessionPool:
    resolved_max_sessions = max_sessions or int(os.environ.get("NEOX_SSH_POOL_SIZE", "2"))
    resolved_reuse_sessions = reuse_sessions if reuse_sessions is not None else env_bool("NEOX_SSH_POOL_REUSE", True)
    pool_key = (node_key.lower(), host, username, resolved_max_sessions, resolved_reuse_sessions)
    with _POOLS_LOCK:
        pool = _POOLS.get(pool_key)
        if pool is None:
            pool = SshSessionPool(
                node_key,
                host,
                username,
                password,
                max_sessions=resolved_max_sessions,
                acquire_timeout_seconds=acquire_timeout_seconds
                or float(os.environ.get("NEOX_SSH_POOL_ACQUIRE_TIMEOUT", "60")),
                ssh_timeout=ssh_timeout,
                reuse_sessions=resolved_reuse_sessions,
            )
            _POOLS[pool_key] = pool
        return pool



def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
def close_ssh_session_pools() -> None:
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close_all()


def default_token_directory() -> Path:
    configured = os.environ.get("NEOX_SSH_POOL_TOKEN_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / ".pytest_cache" / "ssh-session-pool"


def safe_token_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def normalize_cli_output(output: str) -> str:
    output = ANSI_ESCAPE.sub("", output)
    output = output.replace("\r", "\n")
    lines = []
    for line in output.splitlines():
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)
