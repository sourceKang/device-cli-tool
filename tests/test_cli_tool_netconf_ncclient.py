from __future__ import annotations

import base64
import hashlib
import inspect
from types import SimpleNamespace

import pytest

from cli_tool.transport import netconf as netconf_module
from cli_tool.transport.netconf import NetconfClient, NetconfHostKeyError


# Offline compatibility smoke test against the real ncclient package: import and construct
# only, never connect. Skipped when the optional "netconf" extra is not installed.
pytest.importorskip("ncclient")


def test_import_ncclient_manager_returns_real_manager_module():
    manager = netconf_module._import_ncclient_manager()

    assert manager.__name__ == "ncclient.manager"
    assert callable(manager.connect)


def test_ssh_connect_accepts_the_keyword_arguments_netconf_client_passes():
    from ncclient.transport import SSHSession

    parameters = inspect.signature(SSHSession.connect).parameters
    for name in ("host", "port", "username", "password", "timeout", "hostkey_verify", "allow_agent", "look_for_keys"):
        assert name in parameters


def test_ssh_session_still_exposes_the_transport_used_for_host_key_pinning():
    manager = netconf_module._import_ncclient_manager()
    from ncclient.transport import SSHSession

    session = SSHSession(manager.make_device_handler({"name": "default"}))

    assert hasattr(session, "_transport")  # server_key_fingerprint() reads _session._transport
    assert "_session" in inspect.getsource(manager.Manager.__init__)


def test_manager_supports_every_rpc_the_confirmed_commit_path_uses():
    manager = netconf_module._import_ncclient_manager()

    for name in ("lock", "unlock", "edit_config", "validate", "commit", "discard_changes", "get", "get_config"):
        assert name in manager.OPERATIONS
    commit_parameters = inspect.signature(manager.OPERATIONS["commit"].request).parameters
    assert {"confirmed", "timeout"} <= set(commit_parameters)


def test_capability_check_understands_real_ncclient_capabilities_objects():
    from ncclient.capabilities import Capabilities

    full = Capabilities(
        [
            "urn:ietf:params:netconf:base:1.1",
            "urn:ietf:params:netconf:capability:candidate:1.0",
            "urn:ietf:params:netconf:capability:confirmed-commit:1.1",
        ]
    )
    running_only = Capabilities(
        [
            "urn:ietf:params:netconf:base:1.1",
            "urn:ietf:params:netconf:capability:writable-running:1.0",
        ]
    )

    assert netconf_module.missing_mutation_capabilities(full) == ()
    assert netconf_module.missing_mutation_capabilities(running_only) == (":candidate", ":confirmed-commit")


# --- confirmed-commit RPC built by the real ncclient Commit operation (no connection) ---


def _build_commit_xml(**kwargs) -> str:
    """Run ncclient's real Commit.request() with a stub that returns the RPC node unsent."""
    from lxml import etree

    commit_class = netconf_module._import_ncclient_manager().OPERATIONS["commit"]
    stub = SimpleNamespace(_assert=lambda capability: None, _request=lambda node: node)
    node = commit_class.request(stub, **kwargs)
    return etree.tostring(node).decode("utf-8")


def test_real_ncclient_commit_rejects_an_int_confirm_timeout():
    # The v0.7.0 bug: this is what every live confirmed commit hit.
    with pytest.raises(TypeError):
        _build_commit_xml(confirmed=True, timeout=30)


def test_real_ncclient_commit_accepts_the_timeout_netconf_client_sends():
    xml = _build_commit_xml(confirmed=True, timeout=netconf_module.confirm_timeout_text(30.0))

    assert "<nc:confirmed/>" in xml or "<confirmed/>" in xml
    assert "confirm-timeout>30</" in xml


def test_real_ncclient_commit_with_persist_and_persist_id():
    persisted = _build_commit_xml(**netconf_module._confirmed_commit_kwargs("120", "tok-1"))
    confirming = _build_commit_xml(persist_id="tok-1")

    assert "confirm-timeout>120</" in persisted
    assert "persist>tok-1</" in persisted
    assert "persist-id>tok-1</" in confirming
    assert "confirmed" not in confirming


class _RealCommitSession:
    """Fake session whose commit() runs ncclient's real Commit.request() XML builder."""

    server_capabilities = (
        "urn:ietf:params:netconf:base:1.1",
        "urn:ietf:params:netconf:capability:candidate:1.0",
        "urn:ietf:params:netconf:capability:confirmed-commit:1.1",
    )
    session_id = "7"

    def __init__(self) -> None:
        self.commit_xml: list[str] = []
        self._session = SimpleNamespace(
            _transport=SimpleNamespace(get_remote_server_key=lambda: _FakeKey(b"real-commit-key"))
        )

    def commit(self, **kwargs):
        self.commit_xml.append(_build_commit_xml(**kwargs))
        return SimpleNamespace(xml="<rpc-reply><ok/></rpc-reply>")

    def __getattr__(self, name):
        if name in {"lock", "unlock", "edit_config", "validate", "discard_changes", "get"}:
            return lambda **kwargs: SimpleNamespace(xml="<rpc-reply><ok/></rpc-reply>")
        raise AttributeError(name)

    def close_session(self):
        pass


def test_execute_confirmed_commit_builds_a_valid_commit_rpc_with_real_ncclient(monkeypatch):
    session = _RealCommitSession()
    monkeypatch.setattr(
        netconf_module, "_open_ncclient_session", lambda manager, *, verify_server_key, **kwargs: session
    )
    client = NetconfClient(host="192.0.2.10", username="admin", password="secret", allow_unknown_host_key=True)

    result = client.execute_confirmed_commit(config_xml="<config/>", confirm_timeout=45, finalize=True)

    assert [step.step for step in result.steps] == ["lock", "edit-config", "validate", "commit", "finalize", "unlock"]
    assert "confirm-timeout>45</" in session.commit_xml[0]
    assert "confirmed" not in session.commit_xml[1]


# --- host key checked before password authentication (no connection) ---


class _FakeKey:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def asbytes(self) -> bytes:
        return self._raw


def _fingerprint(raw: bytes) -> str:
    return base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")


class _FakeParamikoTransport:
    def __init__(self, raw_key: bytes) -> None:
        self.raw_key = raw_key
        self.password_attempts: list[tuple[str, str]] = []

    def get_remote_server_key(self):
        return _FakeKey(self.raw_key)

    def auth_password(self, username, password):
        self.password_attempts.append((username, password))
        return []


def _pinned_session(client: NetconfClient, raw_key: bytes):
    manager = netconf_module._import_ncclient_manager()
    session = netconf_module._make_pinned_ssh_session(
        manager.make_device_handler(None, []), client._verify_server_key
    )
    session._transport = _FakeParamikoTransport(raw_key)
    return session


def test_pinned_ssh_session_refuses_before_sending_the_password_on_key_mismatch():
    client = NetconfClient(
        host="192.0.2.10", username="admin", password="secret",
        expected_host_key_fingerprint=_fingerprint(b"pinned-key"),
    )
    session = _pinned_session(client, b"attacker-key")

    with pytest.raises(NetconfHostKeyError, match="does not match"):
        session._auth("admin", "secret", [], False, False)

    assert session._transport.password_attempts == []


def test_pinned_ssh_session_authenticates_after_the_key_matches():
    client = NetconfClient(
        host="192.0.2.10", username="admin", password="secret",
        expected_host_key_fingerprint=f"SHA256:{_fingerprint(b'pinned-key')}",
    )
    session = _pinned_session(client, b"pinned-key")

    session._auth("admin", "secret", [], False, False)

    assert session._transport.password_attempts == [("admin", "secret")]


def test_open_ncclient_session_runs_the_hook_inside_real_ssh_connect_order(monkeypatch):
    from ncclient import transport

    events: list[str] = []

    def fake_connect(self, **kwargs):
        # Same order as SSHSession.connect(): key exchange, (disabled) known_hosts, _auth().
        events.append(f"kex hostkey_verify={kwargs['hostkey_verify']}")
        self._transport = _FakeParamikoTransport(b"attacker-key")
        self._auth(kwargs["username"], kwargs["password"], [], kwargs["allow_agent"], kwargs["look_for_keys"])
        events.append("authenticated")

    monkeypatch.setattr(transport.SSHSession, "connect", fake_connect)
    monkeypatch.setattr(transport.SSHSession, "close", lambda self: events.append("closed"))
    client = NetconfClient(
        host="192.0.2.10", username="admin", password="secret",
        expected_host_key_fingerprint=_fingerprint(b"pinned-key"),
    )

    with pytest.raises(NetconfHostKeyError):
        client.connect_test()

    assert events == ["kex hostkey_verify=False", "closed"]


def test_open_ncclient_session_returns_a_real_manager_when_the_key_matches(monkeypatch):
    from ncclient import transport

    def fake_connect(self, **kwargs):
        self._transport = _FakeParamikoTransport(b"pinned-key")
        self._auth(kwargs["username"], kwargs["password"], [], kwargs["allow_agent"], kwargs["look_for_keys"])

    monkeypatch.setattr(transport.SSHSession, "connect", fake_connect)
    client = NetconfClient(
        host="192.0.2.10", username="admin", password="secret",
        expected_host_key_fingerprint=_fingerprint(b"pinned-key"),
    )
    manager = netconf_module._import_ncclient_manager()

    session = netconf_module._open_ncclient_session(
        manager,
        verify_server_key=client._verify_server_key,
        host="192.0.2.10", port=830, username="admin", password="secret", timeout=15.0,
        hostkey_verify=False, allow_agent=False, look_for_keys=False,
    )

    assert isinstance(session, manager.Manager)
    assert session._session._transport.password_attempts == [("admin", "secret")]
    assert netconf_module.server_key_fingerprint(session) == _fingerprint(b"pinned-key")
