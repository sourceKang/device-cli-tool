"""Example consuming-project pytest fixture for read-only SFTP."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from cli_tool.transfer import SftpReadOnlyClient


@pytest.fixture(scope="session")
def sftp_client_factory() -> Callable[..., SftpReadOnlyClient]:
    """Build a bounded read-only client without opening a connection."""

    def build(
        *,
        host: str,
        username: str,
        remote_root: str,
        password_env: str = "CLI_TOOL_SFTP_PASSWORD",
        known_hosts: str | Path | None = None,
        port: int = 22,
        timeout: float = 15.0,
        max_download_bytes: int = 50 * 1024 * 1024,
    ) -> SftpReadOnlyClient:
        password = os.environ.get(password_env)
        if not password:
            pytest.fail(f"missing SFTP password environment variable: {password_env}")
        return SftpReadOnlyClient(
            host=host,
            port=port,
            username=username,
            password=password,
            known_hosts_path=known_hosts,
            remote_root=remote_root,
            timeout=timeout,
            max_download_bytes=max_download_bytes,
        )

    return build
