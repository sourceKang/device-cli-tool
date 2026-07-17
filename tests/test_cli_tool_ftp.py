from __future__ import annotations

import ftplib
import hashlib
import ssl

import pytest

from cli_tool.transfer.ftp import (
    FtpCapabilityError,
    FtpReadOnlyClient,
    _parse_mlst_response,
    _parse_unix_list_line,
)
from cli_tool.transfer.sftp import TransferLimitError


class FakeFtp:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {"/safe/file.bin": b"payload"}
        self.connected = None
        self.login_args = None
        self.passive = None
        self.protected = False
        self.commands: list[str] = []
        self.closed = False
        self.modify = "20260717010000"

    def connect(self, host, port, timeout):
        self.connected = (host, port, timeout)

    def login(self, username, password):
        self.login_args = (username, password)

    def prot_p(self):
        self.protected = True

    def set_pasv(self, passive):
        self.passive = passive

    def mlsd(self, path, facts):
        return iter(
            [
                ("folder", {"type": "dir", "modify": self.modify}),
                (
                    "file.bin",
                    {
                        "type": "file",
                        "size": str(len(self.files["/safe/file.bin"])),
                        "modify": self.modify,
                    },
                ),
            ]
        )

    def sendcmd(self, command):
        self.commands.append(command)
        if command.startswith("MLST "):
            path = command[5:]
            if path not in self.files:
                raise ftplib.error_perm("550 not found")
            return (
                "250-Listing\n "
                f"type=file;size={len(self.files[path])};modify={self.modify}; {path}\n"
                "250 End"
            )
        if command.startswith("MDTM "):
            return f"213 {self.modify}"
        raise AssertionError(command)

    def size(self, path):
        return len(self.files[path])

    def retrbinary(self, command, callback, blocksize):
        self.commands.append(command)
        path = command[5:]
        content = self.files[path]
        for index in range(0, len(content), blocksize):
            callback(content[index : index + blocksize])

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


def _client(fake: FakeFtp, *, protocol: str = "ftps", **kwargs) -> FtpReadOnlyClient:
    client = FtpReadOnlyClient(
        "192.0.2.10",
        "admin",
        "secret",
        protocol=protocol,
        remote_root="/safe",
        allow_insecure_ftp=protocol == "ftp",
        **kwargs,
    )
    client._ftp = fake
    return client


def test_plaintext_ftp_requires_explicit_opt_in():
    with pytest.raises(PermissionError, match="plaintext"):
        FtpReadOnlyClient(
            "192.0.2.10",
            "admin",
            "secret",
            protocol="ftp",
        )


@pytest.mark.parametrize(
    ("protocol", "expected_port"),
    [("ftp", 21), ("ftps", 21), ("ftps-implicit", 990)],
)
def test_ftp_protocol_default_ports(protocol, expected_port):
    client = FtpReadOnlyClient(
        "192.0.2.10",
        "admin",
        "secret",
        protocol=protocol,
        allow_insecure_ftp=protocol == "ftp",
    )

    assert client.port == expected_port


def test_ftps_connect_protects_data_channel(monkeypatch):
    fake = FakeFtp()
    captured = {}

    def factory(*, timeout, context):
        captured["context"] = context
        return fake

    monkeypatch.setattr("cli_tool.transfer.ftp.ftplib.FTP_TLS", factory)
    client = FtpReadOnlyClient(
        "example.test",
        "admin",
        "secret",
        protocol="ftps",
        timeout=7,
    )

    client.connect()

    assert fake.connected == ("example.test", 21, 7)
    assert fake.login_args == ("admin", "secret")
    assert fake.protected is True
    assert fake.passive is True
    assert captured["context"].verify_mode == ssl.CERT_REQUIRED
    assert captured["context"].check_hostname is True


def test_plain_ftp_connect_records_no_tls(monkeypatch):
    fake = FakeFtp()
    monkeypatch.setattr("cli_tool.transfer.ftp.ftplib.FTP", lambda **kwargs: fake)
    client = FtpReadOnlyClient(
        "192.0.2.10",
        "admin",
        "secret",
        protocol="ftp",
        allow_insecure_ftp=True,
    )

    client.connect()

    assert fake.protected is False
    assert fake.passive is True


def test_ftp_list_stat_exists_and_download(tmp_path):
    fake = FakeFtp()
    client = _client(fake)
    destination = tmp_path / "file.bin"

    items = client.list_files(".")
    info = client.stat("file.bin")
    result = client.download("file.bin", destination)

    assert [item.path for item in items] == ["/safe/file.bin", "/safe/folder"]
    assert info.size == len(b"payload")
    assert info.is_regular_file is True
    assert client.exists("file.bin") is True
    assert client.exists("missing.bin") is False
    assert destination.read_bytes() == b"payload"
    assert result.sha256 == hashlib.sha256(b"payload").hexdigest()


def test_ftp_rejects_command_line_breaks_in_remote_path():
    with pytest.raises(ValueError, match="line breaks"):
        _client(FakeFtp()).stat("file.bin\r\nDELE /safe/file.bin")


def test_ftp_list_requires_mlsd():
    class NoMlsd(FakeFtp):
        def mlsd(self, path, facts=None):
            raise ftplib.error_perm("500 unsupported")

    with pytest.raises(FtpCapabilityError, match="MLSD"):
        _client(NoMlsd()).list_files(".")


def test_ftp_download_enforces_size_limit(tmp_path):
    fake = FakeFtp({"/safe/file.bin": b"12345"})
    client = _client(fake, max_download_bytes=4)

    with pytest.raises(TransferLimitError):
        client.download("file.bin", tmp_path / "file.bin")

    assert list(tmp_path.glob("*.part")) == []


def test_ftp_download_rejects_metadata_change(tmp_path):
    class ChangingFtp(FakeFtp):
        calls = 0

        def sendcmd(self, command):
            if command.startswith("MLST "):
                self.calls += 1
                if self.calls > 1:
                    self.modify = "20260717010001"
            return super().sendcmd(command)

    destination = tmp_path / "changed.bin"
    client = _client(ChangingFtp())

    with pytest.raises(IOError, match="metadata changed"):
        client.download("file.bin", destination)

    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_unverified_tls_is_explicit():
    client = FtpReadOnlyClient(
        "example.test",
        "admin",
        "secret",
        protocol="ftps",
        allow_unverified_tls=True,
    )

    context = client._new_client().context

    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_mlst_parser_accepts_facts_prefixed_by_reply_code():
    facts = _parse_mlst_response(
        "250-type=file;size=7;modify=20260717010000; /safe/file.bin\n250 End"
    )

    assert facts == {
        "type": "file",
        "size": "7",
        "modify": "20260717010000",
    }

class FakeLegacyFtp(FakeFtp):
    directory_lines = (
        "-rw-r--r-- 1 admin admin 7 Jul 17 14:00 file.bin",
        "drwxr-xr-x 2 admin admin 0 Jan 02 2025 folder",
        "lrwxrwxrwx 1 admin admin 8 Jul 17 14:01 link -> file.bin",
    )

    def mlsd(self, path, facts=None):
        raise ftplib.error_perm("500 MLSD unsupported")

    def sendcmd(self, command):
        self.commands.append(command)
        if command.startswith("MLST "):
            raise ftplib.error_perm("500 MLST unsupported")
        if command.startswith("MDTM "):
            return f"213 {self.modify}"
        raise AssertionError(command)

    def retrlines(self, command, callback):
        self.commands.append(command)
        requested = command[5:]
        if requested == "/safe":
            lines = self.directory_lines
        else:
            name = requested.rsplit("/", 1)[-1]
            lines = tuple(line for line in self.directory_lines if _listed_name(line) == name)
            if not lines:
                raise ftplib.error_perm("550 not found")
        for line in lines:
            callback(line)


def _listed_name(line: str) -> str:
    name = line.split(maxsplit=8)[-1]
    return name.split(" -> ", 1)[0]


def test_legacy_listing_requires_double_opt_in():
    with pytest.raises(ValueError, match="provided together"):
        FtpReadOnlyClient(
            "192.0.2.10",
            "admin",
            "secret",
            protocol="ftp",
            allow_insecure_ftp=True,
            allow_legacy_listing=True,
        )

    with pytest.raises(ValueError, match="provided together"):
        FtpReadOnlyClient(
            "192.0.2.10",
            "admin",
            "secret",
            protocol="ftp",
            allow_insecure_ftp=True,
            legacy_list_format="unix",
        )


def test_legacy_unix_list_stat_exists_and_download(tmp_path):
    fake = FakeLegacyFtp()
    client = _client(
        fake,
        allow_legacy_listing=True,
        legacy_list_format="unix",
    )
    destination = tmp_path / "file.bin"

    items = client.list_files(".")
    info = client.stat("file.bin")
    result = client.download("file.bin", destination)

    assert [item.path for item in items] == [
        "/safe/file.bin",
        "/safe/folder",
        "/safe/link",
    ]
    assert items[0].is_regular_file is True
    assert items[1].is_directory is True
    assert items[2].is_symlink is True
    assert all(item.modified_time is None for item in items)
    assert client.last_listing_method == "legacy_unix"
    assert info.modified_time is not None
    assert client.exists("missing.bin") is False
    assert destination.read_bytes() == b"payload"
    assert result.sha256 == hashlib.sha256(b"payload").hexdigest()


def test_legacy_download_rejects_symlink(tmp_path):
    client = _client(
        FakeLegacyFtp(),
        allow_legacy_listing=True,
        legacy_list_format="unix",
    )

    with pytest.raises(ValueError, match="non-symlink"):
        client.download("link", tmp_path / "link")


def test_mlsd_without_opts_is_preferred_over_legacy_list():
    class NoOptsMlsd(FakeFtp):
        def mlsd(self, path, facts=None):
            if facts is not None:
                raise ftplib.error_perm("501 OPTS unsupported")
            return super().mlsd(path, facts=[])

        def retrlines(self, command, callback):
            raise AssertionError("legacy LIST must not run when MLSD works")

    client = _client(
        NoOptsMlsd(),
        allow_legacy_listing=True,
        legacy_list_format="unix",
    )

    assert len(client.list_files(".")) == 2
    assert client.last_listing_method == "mlsd"


def test_legacy_fallback_does_not_hide_permission_errors():
    class PermissionDenied(FakeFtp):
        def mlsd(self, path, facts=None):
            raise ftplib.error_perm("550 permission denied")

        def retrlines(self, command, callback):
            raise AssertionError("legacy LIST must not run after permission denial")

    client = _client(
        PermissionDenied(),
        allow_legacy_listing=True,
        legacy_list_format="unix",
    )

    with pytest.raises(ftplib.error_perm, match="permission denied"):
        client.list_files(".")


@pytest.mark.parametrize(
    "line",
    [
        "total 1",
        "prw-r--r-- 1 admin admin 0 Jul 17 14:00 pipe",
        "-rw-r--r-- 1 admin admin 1 Jul 32 14:00 file",
        "-rw-r--r-- 1 admin admin 1 Jul 17 25:00 file",
        "-rw-r--r-- 1 admin admin 1 Jul 17 14:00 ../file",
        "lrwxrwxrwx 1 admin admin 1 Jul 17 14:00 link",
        "-rw-r--r-- 1 admin admin 1 Jul 17 14:00 trailing ",
    ],
)
def test_legacy_unix_parser_fails_closed(line):
    with pytest.raises(FtpCapabilityError):
        _parse_unix_list_line(line)


def test_legacy_unix_parser_accepts_spaces_and_symlinks():
    regular = _parse_unix_list_line(
        "-rw-r--r-- 1 admin admin 7 Jul 17 14:00 file with spaces.bin"
    )
    symlink = _parse_unix_list_line(
        "lrwxrwxrwx 1 admin admin 7 Jul 17 14:00 link name -> target name"
    )

    assert regular == ("file with spaces.bin", 7, False, True, False)
    assert symlink == ("link name", 7, False, False, True)

def test_stat_does_not_infer_regular_file_from_size_without_mlst():
    class NoMlst(FakeFtp):
        def sendcmd(self, command):
            if command.startswith("MLST "):
                raise ftplib.error_perm("500 MLST unsupported")
            return super().sendcmd(command)

    with pytest.raises(FtpCapabilityError, match="MLST"):
        _client(NoMlst()).stat("file.bin")


def test_mlsd_regular_file_requires_size_fact():
    class MissingSize(FakeFtp):
        def mlsd(self, path, facts=None):
            return iter([("file.bin", {"type": "file", "modify": self.modify})])

    with pytest.raises(FtpCapabilityError, match="missing size"):
        _client(MissingSize()).list_files(".")
