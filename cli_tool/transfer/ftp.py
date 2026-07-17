from __future__ import annotations

import errno
import ftplib
import hashlib
import os
import re
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from cli_tool.transfer.models import DownloadResult, RemoteFileInfo
from cli_tool.transfer.paths import UnsafeRemotePathError, resolve_remote_path
from cli_tool.transfer.sftp import DEFAULT_CHUNK_SIZE, DEFAULT_MAX_DOWNLOAD_BYTES, TransferLimitError


FTP_PROTOCOLS = {"ftp", "ftps", "ftps-implicit"}
DEFAULT_PORTS = {"ftp": 21, "ftps": 21, "ftps-implicit": 990}
LEGACY_LIST_FORMATS = {"unix"}
_UNIX_MONTHS = {
    name: index
    for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}

_UNIX_LIST_PATTERN = re.compile(
    r"^(?P<mode>[bcdlps-][rwxStTs-]{9}[.+@]?)\s+"
    r"(?P<links>\d+)\s+"
    r"(?P<owner>\S+)\s+"
    r"(?P<group>\S+)\s+"
    r"(?P<size>\d+)\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<when>\d{2}:\d{2}|\d{4})\s+"
    r"(?P<name>.+)$"
)


class FtpCapabilityError(RuntimeError):
    """Raised when a server cannot provide fail-closed structured metadata."""


class ImplicitFtpTls(ftplib.FTP_TLS):
    """FTP_TLS variant that wraps the control socket before reading the greeting."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address
        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            source_address=self.source_address,
        )
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


class FtpReadOnlyClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        protocol: str = "ftps",
        port: int | None = None,
        timeout: float = 15.0,
        remote_root: str = "/",
        passive: bool = True,
        ca_file: str | Path | None = None,
        allow_unverified_tls: bool = False,
        allow_insecure_ftp: bool = False,
        allow_legacy_listing: bool = False,
        legacy_list_format: str | None = None,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        normalized_protocol = protocol.lower()
        if normalized_protocol not in FTP_PROTOCOLS:
            raise ValueError(f"unsupported FTP protocol: {protocol}")
        if not host:
            raise ValueError("host is required")
        if not username:
            raise ValueError("username is required")
        if not password:
            raise ValueError("password is required")
        resolved_port = DEFAULT_PORTS[normalized_protocol] if port is None else port
        if resolved_port < 1 or resolved_port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if max_download_bytes < 1:
            raise ValueError("max_download_bytes must be greater than 0")
        if chunk_size < 1:
            raise ValueError("chunk_size must be greater than 0")
        if normalized_protocol == "ftp" and not allow_insecure_ftp:
            raise PermissionError(
                "FTP is plaintext; set allow_insecure_ftp=True only for an isolated lab"
            )
        if normalized_protocol == "ftp" and (ca_file or allow_unverified_tls):
            raise ValueError("TLS options are not valid for plaintext FTP")
        normalized_legacy_format = legacy_list_format.lower() if legacy_list_format else None
        if allow_legacy_listing != (normalized_legacy_format is not None):
            raise ValueError(
                "allow_legacy_listing and legacy_list_format must be provided together"
            )
        if (
            normalized_legacy_format is not None
            and normalized_legacy_format not in LEGACY_LIST_FORMATS
        ):
            raise ValueError(f"unsupported legacy LIST format: {legacy_list_format}")

        self.host = host
        self.username = username
        self.password = password
        self.protocol = normalized_protocol
        self.port = resolved_port
        self.timeout = timeout
        self.remote_root = resolve_remote_path(remote_root, ".")
        self.passive = passive
        self.ca_file = Path(ca_file).expanduser() if ca_file else None
        self.allow_unverified_tls = allow_unverified_tls
        self.allow_insecure_ftp = allow_insecure_ftp
        self.allow_legacy_listing = allow_legacy_listing
        self.legacy_list_format = normalized_legacy_format
        self.max_download_bytes = max_download_bytes
        self.chunk_size = chunk_size
        self.last_listing_method: str | None = None
        self.last_metadata_method: str | None = None
        self._ftp = None

    def __enter__(self) -> FtpReadOnlyClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def connect(self) -> None:
        if self._ftp is not None:
            return
        client = self._new_client()
        try:
            client.connect(self.host, self.port, timeout=self.timeout)
            client.login(self.username, self.password)
            if self.protocol != "ftp":
                client.prot_p()
            client.set_pasv(self.passive)
            self._ftp = client
        except Exception:
            client.close()
            raise

    def close(self) -> None:
        client = self._ftp
        self._ftp = None
        if client is None:
            return
        try:
            client.quit()
        except Exception:
            client.close()

    def list_files(self, remote_path: str = ".") -> tuple[RemoteFileInfo, ...]:
        directory = self._resolve(remote_path)
        client = self._require_ftp()
        try:
            entries = list(client.mlsd(directory, facts=["type", "size", "modify"]))
        except AttributeError as error:
            return self._legacy_list_files(directory, error)
        except ftplib.error_perm as error:
            if not _is_unsupported_command(error):
                raise
            try:
                entries = list(client.mlsd(directory))
            except AttributeError as retry_error:
                return self._legacy_list_files(directory, retry_error)
            except ftplib.error_perm as retry_error:
                if not _is_unsupported_command(retry_error):
                    raise
                return self._legacy_list_files(directory, retry_error)

        self.last_listing_method = "mlsd"
        results = [
            _file_info_from_facts(self._resolve(_join_remote(directory, name)), facts)
            for name, facts in entries
            if name not in {".", ".."}
        ]
        return tuple(sorted(results, key=lambda item: item.path))

    def _legacy_list_files(
        self,
        directory: str,
        unsupported_error: Exception,
    ) -> tuple[RemoteFileInfo, ...]:
        if not self.allow_legacy_listing or self.legacy_list_format != "unix":
            raise FtpCapabilityError(
                "FTP server must support MLSD unless strict legacy listing is explicitly enabled"
            ) from unsupported_error

        lines: list[str] = []
        self._require_ftp().retrlines(f"LIST {directory}", lines.append)
        results: list[RemoteFileInfo] = []
        for line in lines:
            name, size, is_directory, is_regular_file, is_symlink = _parse_unix_list_line(line)
            if name in {".", ".."}:
                continue
            results.append(
                RemoteFileInfo(
                    path=self._resolve(_join_remote(directory, name)),
                    size=size,
                    modified_time=None,
                    is_directory=is_directory,
                    is_regular_file=is_regular_file,
                    is_symlink=is_symlink,
                )
            )
        self.last_listing_method = "legacy_unix"
        return tuple(sorted(results, key=lambda item: item.path))

    def stat(self, remote_path: str) -> RemoteFileInfo:
        resolved = self._resolve(remote_path)
        client = self._require_ftp()
        try:
            response = client.sendcmd(f"MLST {resolved}")
            facts = _parse_mlst_response(response)
            info = _file_info_from_facts(resolved, facts)
            self.last_metadata_method = "mlst"
            return info
        except ftplib.error_perm as error:
            if _is_unsupported_command(error):
                return self._legacy_stat(resolved, error)
            if _is_not_found(error):
                raise FileNotFoundError(resolved) from error
            raise

    def _legacy_stat(
        self,
        resolved: str,
        unsupported_error: Exception,
    ) -> RemoteFileInfo:
        if not self.allow_legacy_listing or self.legacy_list_format != "unix":
            raise FtpCapabilityError(
                "FTP server must support MLST unless strict legacy listing is explicitly enabled"
            ) from unsupported_error

        lines: list[str] = []
        try:
            self._require_ftp().retrlines(f"LIST {resolved}", lines.append)
        except ftplib.error_perm as error:
            if _is_not_found(error):
                raise FileNotFoundError(resolved) from error
            raise
        if len(lines) != 1:
            raise FtpCapabilityError(
                "legacy UNIX LIST stat must return exactly one structured entry"
            )

        name, size, is_directory, is_regular_file, is_symlink = _parse_unix_list_line(lines[0])
        if name != resolved.rsplit("/", 1)[-1]:
            raise FtpCapabilityError("legacy UNIX LIST stat returned a different entry name")

        modified_time = None
        if is_regular_file:
            try:
                modified_time = _parse_modify_time(
                    self._require_ftp().sendcmd(f"MDTM {resolved}").split()[-1]
                )
            except ftplib.error_perm:
                pass
        self.last_metadata_method = "legacy_unix"
        return RemoteFileInfo(
            path=resolved,
            size=size,
            modified_time=modified_time,
            is_directory=is_directory,
            is_regular_file=is_regular_file,
            is_symlink=is_symlink,
        )

    def exists(self, remote_path: str) -> bool:
        try:
            self.stat(remote_path)
        except FileNotFoundError:
            return False
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
            raise ValueError("FTP download only supports regular non-symlink files")
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
            with temporary.open("xb") as target:
                def receive(chunk: bytes) -> None:
                    nonlocal downloaded
                    downloaded += len(chunk)
                    if downloaded > self.max_download_bytes:
                        raise TransferLimitError(
                            f"download exceeded max_download_bytes={self.max_download_bytes}"
                        )
                    target.write(chunk)
                    digest.update(chunk)

                self._require_ftp().retrbinary(
                    f"RETR {info.path}",
                    receive,
                    blocksize=self.chunk_size,
                )
                target.flush()
                os.fsync(target.fileno())

            if downloaded != info.size:
                raise IOError(f"FTP download size mismatch: expected {info.size}, received {downloaded}")
            final_info = self.stat(info.path)
            if _file_identity(final_info) != _file_identity(info):
                raise IOError("remote file metadata changed during FTP download")
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

    def _new_client(self):
        if self.protocol == "ftp":
            return ftplib.FTP(timeout=self.timeout)
        context = _tls_context(
            ca_file=self.ca_file,
            allow_unverified_tls=self.allow_unverified_tls,
        )
        client_class = ImplicitFtpTls if self.protocol == "ftps-implicit" else ftplib.FTP_TLS
        return client_class(timeout=self.timeout, context=context)

    def _resolve(self, remote_path: str) -> str:
        if "\r" in remote_path or "\n" in remote_path:
            raise UnsafeRemotePathError("FTP remote path cannot contain line breaks")
        return resolve_remote_path(self.remote_root, remote_path)

    def _require_ftp(self):
        self.connect()
        if self._ftp is None:
            raise RuntimeError("FTP session is not available")
        return self._ftp


def _tls_context(*, ca_file: Path | None, allow_unverified_tls: bool) -> ssl.SSLContext:
    if allow_unverified_tls:
        return ssl._create_unverified_context()
    if ca_file is not None and not ca_file.is_file():
        raise FileNotFoundError(f"CA file does not exist: {ca_file}")
    context = ssl.create_default_context()
    if ca_file is not None:
        context.load_verify_locations(cafile=str(ca_file))
    return context


def _parse_mlst_response(response: str) -> dict[str, str]:
    for raw_line in response.splitlines():
        line = raw_line.strip()
        if line[:3].isdigit() and len(line) > 4 and line[3] in {"-", " "}:
            line = line[4:].lstrip()
        if "=" not in line or ";" not in line:
            continue
        facts_text = line.split(None, 1)[0]
        facts = _parse_facts(facts_text)
        if "type" in facts:
            return facts
    raise FtpCapabilityError("FTP MLST response did not contain structured facts")


def _parse_facts(value: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    for item in value.split(";"):
        if "=" not in item:
            continue
        key, fact_value = item.split("=", 1)
        facts[key.strip().lower()] = fact_value.strip()
    return facts


def _file_info_from_facts(path: str, facts: dict[str, str]) -> RemoteFileInfo:
    normalized = {str(key).lower(): str(value) for key, value in facts.items()}
    entry_type = normalized.get("type", "").lower()
    is_directory = entry_type in {"dir", "cdir", "pdir"}
    is_symlink = "slink" in entry_type or entry_type in {"link", "symlink"}
    is_regular = entry_type == "file"
    if not (is_directory or is_regular or is_symlink):
        raise FtpCapabilityError(f"unsupported FTP MLST type: {entry_type or '<missing>'}")
    if is_regular and "size" not in normalized:
        raise FtpCapabilityError("FTP MLST file metadata is missing size")
    size_text = normalized.get("size", "0")
    try:
        size = int(size_text)
    except ValueError as error:
        raise FtpCapabilityError(f"invalid FTP size fact: {size_text}") from error
    return RemoteFileInfo(
        path=path,
        size=size,
        modified_time=_parse_modify_time(normalized.get("modify")),
        is_directory=is_directory,
        is_regular_file=is_regular,
        is_symlink=is_symlink,
    )


def _parse_modify_time(value: str | None) -> float | None:
    if not value:
        return None
    normalized = value.split(".", 1)[0]
    try:
        parsed = datetime.strptime(normalized, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise FtpCapabilityError(f"invalid FTP modify fact: {value}") from error
    return parsed.timestamp()


def _parse_unix_list_line(line: str) -> tuple[str, int, bool, bool, bool]:
    if not line or any(character in line for character in ("\x00", "\r", "\n")):
        raise FtpCapabilityError("legacy UNIX LIST line contains invalid control characters")
    match = _UNIX_LIST_PATTERN.fullmatch(line)
    if match is None:
        raise FtpCapabilityError("legacy UNIX LIST line does not match the strict format")

    entry_type = match.group("mode")[0]
    if entry_type not in {"-", "d", "l"}:
        raise FtpCapabilityError(f"unsupported legacy UNIX LIST entry type: {entry_type}")
    _validate_unix_list_date(match.group("month"), match.group("day"), match.group("when"))

    name = match.group("name")
    if entry_type == "l":
        if " -> " not in name:
            raise FtpCapabilityError("legacy UNIX LIST symlink is missing its target")
        name, target = name.split(" -> ", 1)
        if not target or any(character in target for character in ("\x00", "\r", "\n")):
            raise FtpCapabilityError("legacy UNIX LIST symlink target is invalid")
    _validate_legacy_name(name)

    size = int(match.group("size"))
    return name, size, entry_type == "d", entry_type == "-", entry_type == "l"


def _validate_legacy_name(name: str) -> None:
    if not name or name != name.strip():
        raise FtpCapabilityError("legacy UNIX LIST entry name has unsafe surrounding whitespace")
    if any(character in name for character in ("/", "\\", "\x00", "\r", "\n")):
        raise FtpCapabilityError("legacy UNIX LIST entry name is not a safe basename")


def _validate_unix_list_date(month: str, day: str, when: str) -> None:
    month_number = _UNIX_MONTHS[month]
    day_number = int(day)
    try:
        if ":" in when:
            hour, minute = (int(part) for part in when.split(":", 1))
            datetime(2000, month_number, day_number, hour, minute)
        else:
            datetime(int(when), month_number, day_number)
    except ValueError as error:
        raise FtpCapabilityError("legacy UNIX LIST timestamp is invalid") from error


def _join_remote(directory: str, name: str) -> str:
    return f"/{name}" if directory == "/" else f"{directory.rstrip('/')}/{name}"


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


def _file_identity(info: RemoteFileInfo) -> tuple[object, ...]:
    return (
        info.size,
        info.modified_time,
        info.is_directory,
        info.is_regular_file,
        info.is_symlink,
    )


def _is_not_found(error: ftplib.error_perm) -> bool:
    message = str(error).lstrip()
    return message.startswith("550") or getattr(error, "errno", None) == errno.ENOENT


def _is_unsupported_command(error: ftplib.error_perm) -> bool:
    message = str(error).lstrip()
    return message.startswith(("500", "501", "502", "504"))
