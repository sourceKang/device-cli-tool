"""Read-only file-transfer capabilities."""

from cli_tool.transfer.models import DownloadResult, RemoteFileInfo
from cli_tool.transfer.paths import UnsafeRemotePathError, resolve_remote_path
from cli_tool.transfer.sftp import SftpReadOnlyClient, TransferLimitError

__all__ = [
    "DownloadResult",
    "RemoteFileInfo",
    "SftpReadOnlyClient",
    "TransferLimitError",
    "UnsafeRemotePathError",
    "resolve_remote_path",
]
