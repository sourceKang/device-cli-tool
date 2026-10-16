from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest

from cli_tool.transport import netconf as netconf_module
from cli_tool.transport.netconf import NetconfCapabilityError, NetconfClient, NetconfHostKeyError


RAW_HOST_KEY = b"fake-host-key-material-for-tests"
BASE_CAPABILITY = "urn:ietf:params:netconf:base:1.1"
CANDIDATE_CAPABILITY = "urn:ietf:params:netconf:capability:candidate:1.0"
CONFIRMED_COMMIT_1_0 = "urn:ietf:params:netconf:capability:confirmed-commit:1.0"
CONFIRMED_COMMIT_1_1 = "urn:ietf:params:netconf:capability:confirmed-commit:1.1"
MUTATION_CAPABILITIES = (BASE_CAPABILITY, CANDIDATE_CAPABILITY, CONFIRMED_COMMIT_1_1)


def _fingerprint(raw_key: bytes) -> str:
    digest = hashlib.sha256(raw_key).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


EXPECTED_FINGERPRINT = _fingerprint(RAW_HOST_KEY)


class FakeServerKey:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def asbytes(self) -> bytes:
        return self._raw


class FakeReply:
    def __init__(self, xml: str) -> None:
        self.xml = xml


class FakeSession:
    def __init__(
        self, *, capabilities=(), session_id="4", raw_key: bytes = RAW_HOST_KEY, fail_on: str | None = None
    ) -> None:
        self.server_capabilities = capabilities
        self.session_id = session_id
        self.closed = False
        self.calls: list[tuple] = []
        self.fail_on = fail_on
        self.raw_key = raw_key
        self.connect_events: list = []
        transport =SimpleNamespace(get_remote_server_key=lambda: FakeServerKey(raw_key))
        self._session = SimpleNamespace(_transport=transport)

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RuntimeError(f"fake {name} failure")

    def get(self, filter=None):
        self.calls.append(("get", filter))
        self._maybe_fail("get")
        return FakeReply("<rpc-reply><data>get-ok</data></rpc-reply>")

    def get_config(self, source=None, filter=None):
        self.calls.append(("get-config", source, filter))
        self._maybe_fail("get-config")
        return FakeReply(f"<rpc-reply><data>{source}-config</data></rpc-reply>")

    def edit_config(self, target=None, config=None):
        self.calls.append(("edit-config", target, config))
        self._maybe_fail("edit-config")
        return FakeReply("<rpc-reply><ok/></rpc-reply>")

    def lock(self, target=None):
        self.calls.append(("lock", target))
        self._maybe_fail("lock")
        return FakeReply("<rpc-reply><ok/></rpc-reply>")

    def unlock(self, target=None):
        self.calls.append(("unlock", target))
        self._maybe_fail("unlock")
        return FakeReply("<rpc-reply><ok/></rpc-reply>")

    def validate(self, source=None):
        self.calls.append(("validate", source))
        self._maybe_fail("validate")
        return FakeReply("<rpc-reply><ok/></rpc-reply>")

    def commit(self, confirmed=False, timeout=None, persist=None, persist_id=None):
        if persist is None and persist_id is None:
            self.calls.append(("commit", confirmed, timeout))
        else:
            self.calls.append(("commit", confirmed, timeout, persist, persist_id))
        self._maybe_fail("commit")
        return FakeReply("<rpc-reply><ok/></rpc-reply>")

    def cancel_commit(self, persist_id=None):
        self.calls.append(("cancel-commit", persist_id))
        self._maybe_fail("cancel-commit")
        return FakeReply("<rpc-reply><ok/></rpc-reply>")

    def discard_changes(self):
        self.calls.append(("discard-changes",))
        self._maybe_fail("discard-changes")
        return FakeReply("<rpc-reply><ok/></rpc-reply>")

    def close_session(self):
        self.closed = True


def _fake_manager(session: FakeSession):
    return SimpleNamespace(name="fake-manager")


def _fake_open_ncclient_session(session: FakeSession):
    """Mimics ncclient's order: key exchange -> pre-auth host-key hook -> password auth."""

    def open_session(manager, *, verify_server_key, **kwargs):
        session.connect_kwargs = kwargs
        session.connect_events = ["kex"]
        verify_server_key(FakeServerKey(session.raw_key))
        session.connect_events.append(("auth-password", kwargs["password"]))
        return session

    return open_session


def _patch_manager(monkeypatch, session: FakeSession) -> None:
    monkeypatch.setattr(netconf_module, "_import_ncclient_manager", lambda: _fake_manager(session))
    monkeypatch.setattr(netconf_module, "_open_ncclient_session", _fake_open_ncclient_session(session))


def test_connect_test_verifies_pinned_fingerprint_and_closes_session(monkeypatch):
    session = FakeSession(
        capabilities=["urn:ietf:params:netconf:base:1.1", "urn:ietf:params:netconf:base:1.0"]
    )
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=f"SHA256:{EXPECTED_FINGERPRINT}",
    )

    result = client.connect_test()

    assert result.session_id == "4"
    assert result.host_key_fingerprint == f"SHA256:{EXPECTED_FINGERPRINT}"
    assert result.server_capabilities == (
        "urn:ietf:params:netconf:base:1.0",
        "urn:ietf:params:netconf:base:1.1",
    )
    assert session.closed is True
    # host-key verification must not weaken paramiko's own negotiation
    assert session.connect_kwargs["hostkey_verify"] is False


def test_connect_test_accepts_fingerprint_without_prefix(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=EXPECTED_FINGERPRINT,
    )

    client.connect_test()  # must not raise

    assert session.closed is True


def test_mismatched_fingerprint_is_refused_and_no_rpc_is_sent(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint="SHA256:" + "A" * 43,
    )

    with pytest.raises(NetconfHostKeyError, match="does not match"):
        client.get()

    assert session.calls == []
    # refused between key exchange and authentication: the password never left the client
    assert session.connect_events == ["kex"]


def test_matching_fingerprint_is_checked_before_password_auth(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)

    _client().get()

    assert session.connect_events == ["kex", ("auth-password", "secret")]


def test_pre_auth_hook_with_allow_unknown_host_key_does_not_block_auth(monkeypatch):
    session = FakeSession(raw_key=b"some-other-key")
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10", username="admin", password="secret", allow_unknown_host_key=True
    )

    client.get()

    assert session.connect_events == ["kex", ("auth-password", "secret")]


def test_missing_fingerprint_without_opt_in_is_refused_before_connecting(monkeypatch):
    session = FakeSession()
    manager_module = _fake_manager(session)
    calls = []
    monkeypatch.setattr(
        netconf_module,
        "_import_ncclient_manager",
        lambda: calls.append("called") or manager_module,
    )
    client = NetconfClient(host="192.0.2.10", username="admin", password="secret")

    with pytest.raises(NetconfHostKeyError, match="requires expected_host_key_fingerprint"):
        client.connect_test()

    assert calls == []  # never even attempted to reach ncclient/paramiko
    assert session.calls == []


def test_allow_unknown_host_key_skips_pinning(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10", username="admin", password="secret", allow_unknown_host_key=True
    )

    result = client.get()

    assert result.operation == "get"
    assert session.calls == [("get", None)]


def test_get_sends_filter_and_returns_xml(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=EXPECTED_FINGERPRINT,
    )

    result = client.get(filter_xml="<filter/>")

    assert result.operation == "get"
    assert "get-ok" in result.xml
    assert session.calls == [("get", "<filter/>")]
    assert session.closed is True


def test_get_config_uses_requested_datastore(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=EXPECTED_FINGERPRINT,
    )

    result = client.get_config(datastore="candidate")

    assert "candidate-config" in result.xml
    assert session.calls == [("get-config", "candidate", None)]


def test_get_config_rejects_unknown_datastore(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=EXPECTED_FINGERPRINT,
    )

    with pytest.raises(ValueError, match="datastore"):
        client.get_config(datastore="bogus")
    assert session.calls == []


def test_edit_config_is_implemented_for_the_transport_but_not_wired_into_cli(monkeypatch):
    session = FakeSession()
    _patch_manager(monkeypatch, session)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=EXPECTED_FINGERPRINT,
    )

    result = client.edit_config(datastore="candidate", config_xml="<config/>")

    assert result.operation == "edit-config"
    assert session.calls == [("edit-config", "candidate", "<config/>")]


def test_missing_ncclient_raises_friendly_runtime_error(monkeypatch):
    def raise_import_error():
        raise RuntimeError(
            'ncclient is required for NETCONF; install with pip install "device-cli-tool[netconf]"'
        )

    monkeypatch.setattr(netconf_module, "_import_ncclient_manager", raise_import_error)
    client = NetconfClient(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=EXPECTED_FINGERPRINT,
    )

    with pytest.raises(RuntimeError, match="ncclient is required"):
        client.connect_test()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "", "username": "admin", "password": "secret"},
        {"host": "192.0.2.10", "username": "", "password": "secret"},
        {"host": "192.0.2.10", "username": "admin", "password": ""},
        {"host": "192.0.2.10", "username": "admin", "password": "secret", "port": 0},
        {"host": "192.0.2.10", "username": "admin", "password": "secret", "timeout": 0},
    ],
)
def test_rejects_unsafe_connection_parameters(kwargs, monkeypatch):
    _patch_manager(monkeypatch, FakeSession())
    client = NetconfClient(allow_unknown_host_key=True, **kwargs)

    with pytest.raises(ValueError):
        client.connect_test()


def _client(**overrides) -> NetconfClient:
    defaults = dict(
        host="192.0.2.10",
        username="admin",
        password="secret",
        expected_host_key_fingerprint=EXPECTED_FINGERPRINT,
    )
    defaults.update(overrides)
    return NetconfClient(**defaults)


def test_execute_confirmed_commit_runs_lock_edit_validate_commit_unlock_without_finalizing(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    result = _client().execute_confirmed_commit(config_xml="<config/>", confirm_timeout=15)

    assert [call[0] for call in session.calls] == ["lock", "edit-config", "validate", "commit", "unlock"]
    assert session.calls[0] == ("lock", "candidate")
    assert session.calls[1] == ("edit-config", "candidate", "<config/>")
    assert session.calls[2] == ("validate", "candidate")
    assert session.calls[3] == ("commit", True, "15")
    assert session.calls[4] == ("unlock", "candidate")
    assert result.finalized is False
    assert [s.step for s in result.steps] == ["lock", "edit-config", "validate", "commit", "unlock"]
    assert session.closed is True


def test_execute_confirmed_commit_reads_back_before_unlock_when_verify_filter_given(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    result = _client().execute_confirmed_commit(config_xml="<config/>", verify_filter_xml="<filter/>")

    assert [call[0] for call in session.calls] == [
        "lock",
        "edit-config",
        "validate",
        "commit",
        "get",
        "unlock",
    ]
    assert session.calls[4] == ("get", "<filter/>")
    assert [s.step for s in result.steps] == [
        "lock",
        "edit-config",
        "validate",
        "commit",
        "verify",
        "unlock",
    ]


def test_execute_confirmed_commit_finalizes_with_a_second_bare_commit_when_requested(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    result = _client().execute_confirmed_commit(config_xml="<config/>", finalize=True)

    commit_calls = [call for call in session.calls if call[0] == "commit"]
    assert commit_calls == [("commit", True, "30"), ("commit", False, None)]
    assert result.finalized is True


def test_execute_confirmed_commit_never_targets_running(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    _client().execute_confirmed_commit(config_xml="<config/>")

    targeted = {call[1] for call in session.calls if call[0] in {"lock", "edit-config", "validate", "unlock"}}
    assert targeted == {"candidate"}


def test_execute_confirmed_commit_discards_and_unlocks_when_validate_fails(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES, fail_on="validate")
    _patch_manager(monkeypatch, session)

    with pytest.raises(RuntimeError, match="fake validate failure"):
        _client().execute_confirmed_commit(config_xml="<config/>")

    assert [call[0] for call in session.calls] == ["lock", "edit-config", "validate", "discard-changes", "unlock"]
    assert session.closed is True


def test_execute_confirmed_commit_does_not_discard_after_a_successful_commit(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES, fail_on="get")
    _patch_manager(monkeypatch, session)

    with pytest.raises(RuntimeError, match="fake get failure"):
        _client().execute_confirmed_commit(config_xml="<config/>", verify_filter_xml="<filter/>")

    call_names = [call[0] for call in session.calls]
    assert call_names == ["lock", "edit-config", "validate", "commit", "get", "unlock"]
    assert "discard-changes" not in call_names


@pytest.mark.parametrize("confirm_timeout", [0, -5, 0.5, 0.999, 7.5, True, "30"])
def test_execute_confirmed_commit_rejects_timeouts_it_would_have_to_truncate(confirm_timeout, monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    with pytest.raises(ValueError, match="confirm_timeout"):
        _client().execute_confirmed_commit(config_xml="<config/>", confirm_timeout=confirm_timeout)

    assert session.calls == []
    assert session.connect_events == []  # refused before connecting


@pytest.mark.parametrize(("confirm_timeout", "expected"), [(1, "1"), (30.0, "30"), (600, "600")])
def test_execute_confirmed_commit_passes_confirm_timeout_as_str(confirm_timeout, expected, monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    _client().execute_confirmed_commit(config_xml="<config/>", confirm_timeout=confirm_timeout)

    commit = next(call for call in session.calls if call[0] == "commit")
    assert commit == ("commit", True, expected)
    assert isinstance(commit[2], str)  # ncclient assigns it to lxml .text, which rejects int


def test_execute_confirmed_commit_with_persist_sends_token_and_reports_it(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    result = _client().execute_confirmed_commit(config_xml="<config/>", confirm_timeout=120, persist="tok-1")

    commit_calls = [call for call in session.calls if call[0] == "commit"]
    assert commit_calls == [("commit", True, "120", "tok-1", None)]
    assert result.persist == "tok-1"
    assert result.finalized is False


def test_execute_confirmed_commit_with_persist_finalizes_with_persist_id(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    _client().execute_confirmed_commit(config_xml="<config/>", persist="tok-1", finalize=True)

    commit_calls = [call for call in session.calls if call[0] == "commit"]
    assert commit_calls == [("commit", True, "30", "tok-1", None), ("commit", False, None, None, "tok-1")]


def test_execute_confirmed_commit_with_persist_requires_confirmed_commit_1_1(monkeypatch):
    session = FakeSession(capabilities=(BASE_CAPABILITY, CANDIDATE_CAPABILITY, CONFIRMED_COMMIT_1_0))
    _patch_manager(monkeypatch, session)

    with pytest.raises(NetconfCapabilityError) as caught:
        _client().execute_confirmed_commit(config_xml="<config/>", persist="tok-1")

    assert caught.value.missing == (":confirmed-commit:1.1",)
    assert session.calls == []


@pytest.mark.parametrize("persist", ["", "   "])
def test_execute_confirmed_commit_rejects_empty_persist_token(persist, monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    with pytest.raises(ValueError, match="persist"):
        _client().execute_confirmed_commit(config_xml="<config/>", persist=persist)

    assert session.connect_events == []


def test_confirm_and_cancel_persisted_commit_send_persist_id(monkeypatch):
    session = FakeSession(capabilities=MUTATION_CAPABILITIES)
    _patch_manager(monkeypatch, session)

    confirmed = _client().confirm_persisted_commit(persist_id="tok-1")
    cancelled = _client().cancel_persisted_commit(persist_id="tok-1")

    assert confirmed.operation == "confirm-commit"
    assert cancelled.operation == "cancel-commit"
    assert session.calls == [("commit", False, None, None, "tok-1"), ("cancel-commit", "tok-1")]


def test_cancel_persisted_commit_refuses_without_confirmed_commit_1_1(monkeypatch):
    session = FakeSession(capabilities=(BASE_CAPABILITY, CANDIDATE_CAPABILITY, CONFIRMED_COMMIT_1_0))
    _patch_manager(monkeypatch, session)

    with pytest.raises(NetconfCapabilityError):
        _client().cancel_persisted_commit(persist_id="tok-1")

    assert session.calls == []
    assert session.closed is True


@pytest.mark.parametrize(
    "capabilities",
    [
        (BASE_CAPABILITY, CANDIDATE_CAPABILITY, CONFIRMED_COMMIT_1_0),
        (BASE_CAPABILITY, CANDIDATE_CAPABILITY, CONFIRMED_COMMIT_1_1),
        (BASE_CAPABILITY, CANDIDATE_CAPABILITY + "?ignored=1", CONFIRMED_COMMIT_1_1),
    ],
)
def test_execute_confirmed_commit_proceeds_when_candidate_and_confirmed_commit_are_advertised(
    capabilities, monkeypatch
):
    session = FakeSession(capabilities=capabilities)
    _patch_manager(monkeypatch, session)

    _client().execute_confirmed_commit(config_xml="<config/>")

    assert [call[0] for call in session.calls] == ["lock", "edit-config", "validate", "commit", "unlock"]


@pytest.mark.parametrize(
    ("capabilities", "missing"),
    [
        ((BASE_CAPABILITY, CONFIRMED_COMMIT_1_1), (":candidate",)),
        ((BASE_CAPABILITY, CANDIDATE_CAPABILITY), (":confirmed-commit",)),
        ((BASE_CAPABILITY,), (":candidate", ":confirmed-commit")),
        ((), (":candidate", ":confirmed-commit")),
        # A writable-running server must not be treated as a substitute for candidate.
        (
            (BASE_CAPABILITY, "urn:ietf:params:netconf:capability:writable-running:1.0", CONFIRMED_COMMIT_1_1),
            (":candidate",),
        ),
    ],
)
def test_execute_confirmed_commit_refuses_before_any_rpc_when_capabilities_are_missing(
    capabilities, missing, monkeypatch
):
    session = FakeSession(capabilities=capabilities)
    _patch_manager(monkeypatch, session)

    with pytest.raises(NetconfCapabilityError) as caught:
        _client().execute_confirmed_commit(config_xml="<config/>", finalize=True)

    assert caught.value.missing == missing
    for name in missing:
        assert name in str(caught.value)
    assert session.calls == []  # no lock, no edit-config, nothing touched running
    assert session.closed is True
