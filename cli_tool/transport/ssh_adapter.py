from __future__ import annotations

from dataclasses import dataclass

from cli_tool.models import CliCommandOutput


@dataclass(frozen=True)
class SshCliTransport:
    node_key: str
    host: str
    username: str
    password: str
    max_sessions: int | None = None
    acquire_timeout_seconds: float | None = None
    ssh_timeout: float = 15
    connect_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    reuse_sessions: bool | None = None

    def run_commands(self, commands: list[str] | tuple[str, ...], *, owner: str | None = None) -> list[CliCommandOutput]:
        from cli_tool.transport.ssh import ssh_session_pool

        command_list = list(commands)
        pool = ssh_session_pool(
            self.node_key,
            self.host,
            self.username,
            self.password,
            max_sessions=self.max_sessions,
            acquire_timeout_seconds=self.acquire_timeout_seconds,
            ssh_timeout=self.ssh_timeout,
            connect_attempts=self.connect_attempts,
            retry_backoff_seconds=self.retry_backoff_seconds,
            reuse_sessions=self.reuse_sessions,
        )
        results, _timing = pool.run_commands(
            command_list,
            owner=owner or f"{self.node_key}:{command_list[0] if command_list else 'no_commands'}",
        )
        return [CliCommandOutput(command=result.command, output=result.output) for result in results]

