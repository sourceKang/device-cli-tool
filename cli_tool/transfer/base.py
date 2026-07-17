from __future__ import annotations

from pathlib import Path
from typing import Protocol

from cli_tool.transfer.models import DownloadResult, RemoteFileInfo


class ReadOnlyFileTransfer(Protocol):
    def list_files(self, remote_path: str = ".") -> tuple[RemoteFileInfo, ...]:
        ...

    def stat(self, remote_path: str) -> RemoteFileInfo:
        ...

    def exists(self, remote_path: str) -> bool:
        ...

    def download(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        overwrite: bool = False,
    ) -> DownloadResult:
        ...
