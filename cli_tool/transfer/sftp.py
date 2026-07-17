from __future__ import annotations

import errno
import hashlib
import os
import stat as stat_module
from pathlib import Path
from posixpath import join as posix_join
from uuid import uuid4

from cli_tool.transfer.models import DownloadResult, RemoteFileInfo
from cli_tool.transfer.paths import resolve_remote_path
from cli_tool.transport.ssh_host_keys import configure_ssh_host_keys


DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 64 * 1024


class TransferLimitError(RuntimeError):
    """Raised when a remote file exceeds the configured download limit."""


class SftpReadOnlyClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 22,
        timeout: float = 15.0,
        known_hosts_path: str | Path | None = None,
        allow_unknown_host_key: bool = False,
        remote_root: str = "/",
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
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
        if max_download_bytes < 1:
            raise ValueError("max_download_bytes must be greater than 0")
        if chunk_size < 1:
            raise ValueError("chunk_size must be greater than 0")

        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.known_hosts_path = known_hosts_path
        self.allow_unknown_host_key = allow_unknown_host_key
        self.remote_root = resolve_remote_path(remote_root, ".")
        self.max_download_bytes = max_download_bytes
        self.chunk_size = chunk_size
        self._client = None
        self._sftp = None

    def __enter__(self) -> SftpReadOnlyClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def connect(self) -> None:
        if self._sftp is not None:
            return
        try:
            import paramiko
        except ImportError as error:
            raise RuntimeError("paramiko is required for SFTP transfer") from error

        client = paramiko.SSHClient()
        configure_ssh_host_keys(
            client,
            paramiko,
            known_hosts_path=self.known_hosts_path,
            allow_unknown_host_key=self.allow_unknown_host_key,
        )
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                look_for_keys=False,
                allow_agent=False,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
            )
            self._sftp = client.open_sftp()
            self._client = client
        except Exception:
            client.close()
            raise

    def close(self) -> None:
        sftp = self._sftp
        client = self._client
        try:
            if sftp is not None:
                sftp.close()
        finally:
            self._sftp = None
            try:
                if client is not None:
                    client.close()
            finally:
                self._client = None

    def list_files(self, remote_path: str = ".") -> tuple[RemoteFileInfo, ...]:
        directory = self._resolve(remote_path)
        attributes = self._require_sftp().listdir_attr(directory)
        results = [
            self._to_file_info(self._resolve(posix_join(directory, attribute.filename)), attribute)
            for attribute in attributes
        ]
        return tuple(sorted(results, key=lambda item: item.path))

    def stat(self, remote_path: str) -> RemoteFileInfo:
        resolved = self._resolve(remote_path)
        attributes = self._require_sftp().lstat(resolved)
        return self._to_file_info(resolved, attributes)

    def exists(self, remote_path: str) -> bool:
        try:
            self.stat(remote_path)
        except OSError as error:
            if _is_not_found(error):
                return False
            raise
        return True

    def download(
        self,
        remote_path: str,
        local_path: str | Path,
        *,
        overwrite: bool = False,
    ) -> DownloadResult:
        info = self.stat(remote_path)
        if not info.is_regular_file or info.is_symlink:
            raise ValueError("SFTP download only supports regular non-symlink files")
        if info.size > self.max_download_bytes:
            raise TransferLimitError(
                f"remote file size {info.size} exceeds max_download_bytes={self.max_download_bytes}"
            )

        destination = Path(local_path)
        if destination.exists() and not overwrite:
            raise FileExistsError(f"local destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
        digest = hashlib.sha256()
        downloaded = 0

        try:
            with self._require_sftp().open(info.path, "rb") as source, temporary.open("xb") as target:
                while True:
                    chunk = source.read(self.chunk_size)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > self.max_download_bytes:
                        raise TransferLimitError(
                            f"download exceeded max_download_bytes={self.max_download_bytes}"
                        )
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())

            if downloaded != info.size:
                raise IOError(
                    f"SFTP download size mismatch: expected {info.size}, received {downloaded}"
                )
            _commit_local_file(temporary, destination, overwrite=overwrite)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return DownloadResult(
            remote_path=info.path,
            local_path=str(destination),
            size=downloaded,
            sha256=digest.hexdigest(),
        )

    def _resolve(self, remote_path: str) -> str:
        return resolve_remote_path(self.remote_root, remote_path)

    def _require_sftp(self):
        self.connect()
        if self._sftp is None:
            raise RuntimeError("SFTP session is not available")
        return self._sftp

    @staticmethod
    def _to_file_info(path: str, attributes) -> RemoteFileInfo:
        mode = int(getattr(attributes, "st_mode", 0) or 0)
        return RemoteFileInfo(
            path=path,
            size=int(getattr(attributes, "st_size", 0) or 0),
            modified_time=_optional_float(getattr(attributes, "st_mtime", None)),
            is_directory=stat_module.S_ISDIR(mode),
            is_regular_file=stat_module.S_ISREG(mode),
            is_symlink=stat_module.S_ISLNK(mode),
        )


def _commit_local_file(temporary: Path, destination: Path, *, overwrite: bool) -> None:
    if overwrite:
        os.replace(temporary, destination)
        return
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise FileExistsError(f"local destination already exists: {destination}") from None
    except OSError as error:
        raise OSError(
            "atomic no-overwrite commit requires hard-link support in the destination directory"
        ) from error
    temporary.unlink()


def _optional_float(value) -> float | None:
    return None if value is None else float(value)


def _is_not_found(error: OSError) -> bool:
    return getattr(error, "errno", None) == errno.ENOENT or (error.args and error.args[0] == errno.ENOENT)
