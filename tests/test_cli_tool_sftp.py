from __future__ import annotations

import errno
import hashlib
import io
import stat
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cli_tool.transfer.paths import UnsafeRemotePathError, resolve_remote_path
from cli_tool.transfer.sftp import SftpReadOnlyClient, TransferLimitError


@dataclass
class FakeAttributes:
    filename: str
    st_mode: int
    st_size: int = 0
    st_mtime: float = 0.0


class FakeSftp:
    def __init__(self, files: dict[str, bytes], *, directories: dict[str, list[str]] | None = None) -> None:
        self.files = files
        self.directories = directories or {}
        self.closed = False

    def listdir_attr(self, path: str):
        return [
            FakeAttributes(name, stat.S_IFREG | 0o644, len(self.files[f"{path}/{name}"]), 123.0)
            for name in self.directories.get(path, [])
        ]

    def lstat(self, path: str):
        if path not in self.files:
            raise OSError(errno.ENOENT, "missing")
        return FakeAttributes(path.rsplit("/", 1)[-1], stat.S_IFREG | 0o644, len(self.files[path]), 123.0)

    def open(self, path: str, mode: str):
        assert mode == "rb"
        return io.BytesIO(self.files[path])

    def close(self) -> None:
        self.closed = True


def _client(fake_sftp: FakeSftp, **kwargs) -> SftpReadOnlyClient:
    client = SftpReadOnlyClient(
        "192.0.2.10",
        "admin",
        "secret",
        remote_root="/safe",
        **kwargs,
    )
    client._sftp = fake_sftp
    return client


@pytest.mark.parametrize(
    ("root", "path"),
    [
        ("/safe", "../secret"),
        ("/safe", "/outside/file"),
        ("relative", "file"),
        ("/safe", "folder\\file"),
        ("/safe", "bad\x00name"),
    ],
)
def test_remote_path_resolution_rejects_unsafe_paths(root, path):
    with pytest.raises(UnsafeRemotePathError):
        resolve_remote_path(root, path)


def test_sftp_list_stat_and_exists_are_root_bounded():
    fake = FakeSftp(
        {"/safe/a.txt": b"alpha", "/safe/b.txt": b"beta"},
        directories={"/safe": ["b.txt", "a.txt"]},
    )
    client = _client(fake)

    items = client.list_files(".")
    info = client.stat("a.txt")

    assert [item.path for item in items] == ["/safe/a.txt", "/safe/b.txt"]
    assert info.size == 5
    assert info.is_regular_file is True
    assert client.exists("a.txt") is True
    assert client.exists("missing.txt") is False


def test_sftp_download_is_atomic_and_returns_checksum(tmp_path):
    content = b"safe read-only payload"
    client = _client(FakeSftp({"/safe/image.bin": content}), chunk_size=4)
    destination = tmp_path / "downloads" / "image.bin"

    result = client.download("image.bin", destination)

    assert destination.read_bytes() == content
    assert result.size == len(content)
    assert result.sha256 == hashlib.sha256(content).hexdigest()
    assert list(destination.parent.glob("*.part")) == []


def test_sftp_download_rejects_existing_destination(tmp_path):
    destination = tmp_path / "existing.bin"
    destination.write_bytes(b"keep")
    client = _client(FakeSftp({"/safe/file.bin": b"replace"}))

    with pytest.raises(FileExistsError):
        client.download("file.bin", destination)

    assert destination.read_bytes() == b"keep"


def test_sftp_download_enforces_size_limit_before_transfer(tmp_path):
    client = _client(
        FakeSftp({"/safe/large.bin": b"12345"}),
        max_download_bytes=4,
    )

    with pytest.raises(TransferLimitError):
        client.download("large.bin", tmp_path / "large.bin")

    assert list(tmp_path.glob("*.part")) == []


def test_sftp_download_rejects_symlink(tmp_path):
    fake = FakeSftp({"/safe/link": b"target"})
    client = _client(fake)
    fake.lstat = lambda path: FakeAttributes("link", stat.S_IFLNK | 0o777, 6, 123.0)

    with pytest.raises(ValueError, match="non-symlink"):
        client.download("link", tmp_path / "link")


def test_sftp_download_rejects_remote_metadata_change(tmp_path):
    class ChangingMetadataSftp(FakeSftp):
        calls = 0

        def lstat(self, path: str):
            attributes = super().lstat(path)
            self.calls += 1
            if self.calls > 1:
                attributes.st_mtime += 1
            return attributes

    destination = tmp_path / "changed.bin"
    client = _client(ChangingMetadataSftp({"/safe/changed.bin": b"payload"}))

    with pytest.raises(IOError, match="metadata changed"):
        client.download("changed.bin", destination)

    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_sftp_private_key_auth_is_forwarded_to_paramiko(monkeypatch, tmp_path):
    private_key = tmp_path / "id_test"
    private_key.write_text("placeholder", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeSftpSession:
        def close(self) -> None:
            pass

    class FakeSshClient:
        def load_system_host_keys(self) -> None:
            pass

        def load_host_keys(self, path: str) -> None:
            pass

        def set_missing_host_key_policy(self, policy) -> None:
            pass

        def connect(self, **kwargs) -> None:
            captured.update(kwargs)

        def open_sftp(self):
            return FakeSftpSession()

        def close(self) -> None:
            pass

    fake_paramiko = SimpleNamespace(
        SSHClient=FakeSshClient,
        RejectPolicy=lambda: object(),
        WarningPolicy=lambda: object(),
    )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    client = SftpReadOnlyClient(
        "192.0.2.10",
        "admin",
        private_key_path=private_key,
        private_key_passphrase="key-secret",
        timeout=7,
    )
    client.connect()
    client.close()

    assert captured["key_filename"] == str(private_key)
    assert captured["passphrase"] == "key-secret"
    assert "password" not in captured
    assert captured["timeout"] == 7
    assert captured["look_for_keys"] is False
    assert captured["allow_agent"] is False


def test_sftp_requires_exactly_one_auth_method(tmp_path):
    private_key = tmp_path / "id_test"
    private_key.write_text("placeholder", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one"):
        SftpReadOnlyClient("192.0.2.10", "admin")

    with pytest.raises(ValueError, match="exactly one"):
        SftpReadOnlyClient(
            "192.0.2.10",
            "admin",
            "secret",
            private_key_path=private_key,
        )
