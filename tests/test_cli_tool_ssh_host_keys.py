from __future__ import annotations

from types import SimpleNamespace

import pytest

from cli_tool.transport.ssh_host_keys import configure_ssh_host_keys


class FakeClient:
    def __init__(self) -> None:
        self.system_loaded = False
        self.loaded_paths: list[str] = []
        self.policy = None

    def load_system_host_keys(self) -> None:
        self.system_loaded = True

    def load_host_keys(self, path: str) -> None:
        self.loaded_paths.append(path)

    def set_missing_host_key_policy(self, policy) -> None:
        self.policy = policy


class RejectPolicy:
    pass


class WarningPolicy:
    pass


PARAMIKO = SimpleNamespace(RejectPolicy=RejectPolicy, WarningPolicy=WarningPolicy)


def test_host_key_policy_is_strict_by_default():
    client = FakeClient()

    configure_ssh_host_keys(client, PARAMIKO)

    assert client.system_loaded is True
    assert client.loaded_paths == []
    assert isinstance(client.policy, RejectPolicy)


def test_host_key_policy_loads_explicit_known_hosts(tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("example ssh-ed25519 AAAA", encoding="utf-8")
    client = FakeClient()

    configure_ssh_host_keys(client, PARAMIKO, known_hosts_path=known_hosts)

    assert client.loaded_paths == [str(known_hosts)]
    assert isinstance(client.policy, RejectPolicy)


def test_host_key_policy_rejects_missing_known_hosts(tmp_path):
    with pytest.raises(FileNotFoundError, match="known_hosts"):
        configure_ssh_host_keys(
            FakeClient(),
            PARAMIKO,
            known_hosts_path=tmp_path / "missing",
        )


def test_unknown_host_key_requires_explicit_opt_in():
    client = FakeClient()

    configure_ssh_host_keys(client, PARAMIKO, allow_unknown_host_key=True)

    assert isinstance(client.policy, WarningPolicy)
