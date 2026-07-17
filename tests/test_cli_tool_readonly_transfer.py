from __future__ import annotations

import json

import pytest

from cli_tool.transfer.models import RemoteFileInfo
from tools import cli_tool_readonly_transfer


class FakeClient:
    captured: dict[str, object] = {}

    def __init__(self, **kwargs) -> None:
        self.__class__.captured = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        pass

    def list_files(self, path: str):
        return (
            RemoteFileInfo(
                path="/safe/private.cfg",
                size=12,
                modified_time=123.0,
                is_directory=False,
                is_regular_file=True,
                is_symlink=False,
            ),
        )

    def stat(self, path: str):
        return self.list_files(path)[0]

    def exists(self, path: str):
        return True


def test_transfer_cli_redacts_identifiers_and_paths_by_default(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SFTP_TEST_PASSWORD", "secret")
    monkeypatch.setattr(cli_tool_readonly_transfer, "SftpReadOnlyClient", FakeClient)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "list",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password-env",
            "SFTP_TEST_PASSWORD",
            "--remote-root",
            "/safe",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert cli_tool_readonly_transfer.run_transfer(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == 1
    assert payload["passed"] is True
    assert payload["auth_method"] == "password"
    assert payload["timeout_seconds"] == 15.0
    assert payload["host_key_verification"] == "strict"
    assert payload["target"].startswith("sha256:")
    assert payload["username"].startswith("sha256:")
    assert payload["outcome"]["files"][0]["path"].startswith("sha256:")
    serialized = json.dumps(payload)
    assert "192.0.2.10" not in serialized
    assert "/safe/private.cfg" not in serialized
    assert FakeClient.captured["password"] == "secret"
    assert FakeClient.captured["private_key_path"] is None
    assert FakeClient.captured["timeout"] == 15.0
    assert FakeClient.captured["allow_unknown_host_key"] is False


def test_transfer_cli_can_explicitly_include_paths(monkeypatch, capsys):
    monkeypatch.setenv("CLI_TOOL_SFTP_PASSWORD", "secret")
    monkeypatch.setattr(cli_tool_readonly_transfer, "SftpReadOnlyClient", FakeClient)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "stat",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--remote-root",
            "/safe",
            "--remote-path",
            "private.cfg",
            "--include-paths",
            "--allow-unknown-host-key",
            "--no-report",
        ]
    )

    assert cli_tool_readonly_transfer.run_transfer(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["target"] == "192.0.2.10"
    assert payload["outcome"]["file"]["path"] == "/safe/private.cfg"
    assert payload["host_key_verification"] == "allow_unknown_with_warning"
    assert FakeClient.captured["allow_unknown_host_key"] is True


def test_transfer_cli_supports_private_key_auth(monkeypatch, capsys, tmp_path):
    private_key = tmp_path / "id_test"
    private_key.write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv("KEY_PASSPHRASE", "key-secret")
    monkeypatch.setattr(cli_tool_readonly_transfer, "SftpReadOnlyClient", FakeClient)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--private-key",
            str(private_key),
            "--private-key-passphrase-env",
            "KEY_PASSPHRASE",
            "--timeout",
            "8",
            "--no-report",
        ]
    )

    assert cli_tool_readonly_transfer.run_transfer(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["auth_method"] == "private_key"
    assert payload["timeout_seconds"] == 8.0
    assert FakeClient.captured["password"] is None
    assert FakeClient.captured["private_key_path"] == str(private_key)
    assert FakeClient.captured["private_key_passphrase"] == "key-secret"


def test_transfer_cli_requires_local_path_for_download():
    args = cli_tool_readonly_transfer.parse_args(
        ["download", "--host", "192.0.2.10", "--username", "admin"]
    )

    with pytest.raises(SystemExit, match="--local-path"):
        cli_tool_readonly_transfer.run_transfer(args)


def test_transfer_cli_requires_password_environment_variable(monkeypatch):
    monkeypatch.delenv("MISSING_SFTP_PASSWORD", raising=False)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--password-env",
            "MISSING_SFTP_PASSWORD",
        ]
    )

    with pytest.raises(SystemExit, match="MISSING_SFTP_PASSWORD"):
        cli_tool_readonly_transfer.run_transfer(args)


def test_transfer_cli_requires_private_key_passphrase_environment_variable(monkeypatch, tmp_path):
    private_key = tmp_path / "id_test"
    private_key.write_text("placeholder", encoding="utf-8")
    monkeypatch.delenv("MISSING_KEY_PASSPHRASE", raising=False)
    args = cli_tool_readonly_transfer.parse_args(
        [
            "exists",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--private-key",
            str(private_key),
            "--private-key-passphrase-env",
            "MISSING_KEY_PASSPHRASE",
        ]
    )

    with pytest.raises(SystemExit, match="MISSING_KEY_PASSPHRASE"):
        cli_tool_readonly_transfer.run_transfer(args)
