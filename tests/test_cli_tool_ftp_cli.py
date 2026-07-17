from __future__ import annotations

import json

import pytest

from cli_tool.transfer.models import RemoteFileInfo
from tools import cli_tool_readonly_transfer


class FakeFtpClient:
    captured: dict[str, object] = {}

    def __init__(self, **kwargs):
        self.__class__.captured = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        pass

    def list_files(self, path):
        self.last_listing_method = (
            "legacy_unix" if self.captured.get("allow_legacy_listing") else "mlsd"
        )
        return (
            RemoteFileInfo(
                path="/safe/file.bin",
                size=7,
                modified_time=123.0,
                is_directory=False,
                is_regular_file=True,
                is_symlink=False,
            ),
        )

    def stat(self, path):
        self.last_metadata_method = (
            "legacy_unix" if self.captured.get("allow_legacy_listing") else "mlst"
        )
        return self.list_files(path)[0]

    def exists(self, path):
        self.last_metadata_method = (
            "legacy_unix" if self.captured.get("allow_legacy_listing") else "mlst"
        )
        return True


@pytest.mark.parametrize(
    ("protocol", "expected_port"),
    [("ftps", 21), ("ftps-implicit", 990)],
)
def test_transfer_cli_builds_verified_ftps_by_default(
    monkeypatch,
    capsys,
    protocol,
    expected_port,
):
    monkeypatch.setenv("CLI_TOOL_FTP_PASSWORD", "secret")
    monkeypatch.setattr(cli_tool_readonly_transfer, "FtpReadOnlyClient", FakeFtpClient)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "list",
            "--protocol",
            protocol,
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--remote-root",
            "/safe",
            "--no-report",
        ]
    )

    assert cli_tool_readonly_transfer.run_transfer(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["protocol"] == protocol
    assert payload["port"] == expected_port
    assert payload["security"] == "encrypted_tls"
    assert payload["tls_verification"] == "strict"
    assert payload["data_channel_protection"] == "private"
    assert payload["passive"] is True
    assert FakeFtpClient.captured["password"] == "secret"
    assert FakeFtpClient.captured["allow_unverified_tls"] is False


def test_transfer_cli_rejects_plain_ftp_without_opt_in(monkeypatch):
    monkeypatch.setenv("CLI_TOOL_FTP_PASSWORD", "secret")
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--protocol",
            "ftp",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
        ]
    )

    with pytest.raises(SystemExit, match="plaintext"):
        cli_tool_readonly_transfer.run_transfer(args)


def test_transfer_cli_allows_explicit_plain_ftp(monkeypatch, capsys):
    monkeypatch.setenv("CLI_TOOL_FTP_PASSWORD", "secret")
    monkeypatch.setattr(cli_tool_readonly_transfer, "FtpReadOnlyClient", FakeFtpClient)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--protocol",
            "ftp",
            "--allow-insecure-ftp",
            "--active-mode",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--no-report",
        ]
    )

    assert cli_tool_readonly_transfer.run_transfer(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["security"] == "insecure_plaintext"
    assert payload["data_channel_protection"] == "plaintext"
    assert payload["passive"] is False
    assert FakeFtpClient.captured["allow_insecure_ftp"] is True
    assert FakeFtpClient.captured["passive"] is False


def test_transfer_cli_records_explicit_unverified_tls(monkeypatch, capsys):
    monkeypatch.setenv("CLI_TOOL_FTP_PASSWORD", "secret")
    monkeypatch.setattr(cli_tool_readonly_transfer, "FtpReadOnlyClient", FakeFtpClient)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--protocol",
            "ftps",
            "--allow-unverified-tls",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--no-report",
        ]
    )

    assert cli_tool_readonly_transfer.run_transfer(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["tls_verification"] == "unverified_explicit"
    assert FakeFtpClient.captured["allow_unverified_tls"] is True


def test_transfer_cli_rejects_ftp_options_for_sftp():
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--allow-unverified-tls",
        ]
    )

    with pytest.raises(SystemExit, match="not valid for SFTP"):
        cli_tool_readonly_transfer.run_transfer(args)


def test_transfer_cli_rejects_known_hosts_for_ftps():
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--protocol",
            "ftps",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--known-hosts",
            "known_hosts",
        ]
    )

    with pytest.raises(SystemExit, match="only valid for SFTP"):
        cli_tool_readonly_transfer.run_transfer(args)

def test_transfer_cli_enables_strict_legacy_unix_listing(monkeypatch, capsys):
    monkeypatch.setenv("CLI_TOOL_FTP_PASSWORD", "secret")
    monkeypatch.setattr(cli_tool_readonly_transfer, "FtpReadOnlyClient", FakeFtpClient)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "list",
            "--protocol",
            "ftp",
            "--allow-insecure-ftp",
            "--allow-legacy-listing",
            "--legacy-list-format",
            "unix",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--no-report",
        ]
    )

    assert cli_tool_readonly_transfer.run_transfer(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["legacy_listing"] == "enabled_unix"
    assert payload["outcome"]["listing_method"] == "legacy_unix"
    assert FakeFtpClient.captured["allow_legacy_listing"] is True
    assert FakeFtpClient.captured["legacy_list_format"] == "unix"


def test_transfer_cli_requires_both_legacy_listing_options():
    args = cli_tool_readonly_transfer.parse_args(
        [
            "list",
            "--protocol",
            "ftp",
            "--allow-insecure-ftp",
            "--allow-legacy-listing",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
        ]
    )

    with pytest.raises(SystemExit, match="provided together"):
        cli_tool_readonly_transfer.run_transfer(args)


def test_transfer_cli_rejects_legacy_listing_for_sftp():
    args = cli_tool_readonly_transfer.parse_args(
        [
            "list",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--allow-legacy-listing",
            "--legacy-list-format",
            "unix",
        ]
    )

    with pytest.raises(SystemExit, match="not valid for SFTP"):
        cli_tool_readonly_transfer.run_transfer(args)
