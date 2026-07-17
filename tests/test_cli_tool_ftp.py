from __future__ import annotations

import ftplib
import hashlib
import ssl

import pytest

from cli_tool.transfer.ftp import (
    FtpCapabilityError,
    FtpReadOnlyClient,
    _parse_mlst_response,
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
        def mlsd(self, path, facts):
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
