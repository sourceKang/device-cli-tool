from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Callable


DEFAULT_PORT = 830
ALLOWED_DATASTORES = {"running", "candidate", "startup"}
MUTATION_TARGET = "candidate"
DEFAULT_CONFIRM_TIMEOUT = 30.0
CANDIDATE_CAPABILITIES = ("urn:ietf:params:netconf:capability:candidate:1.0",)
CONFIRMED_COMMIT_CAPABILITIES = (
    "urn:ietf:params:netconf:capability:confirmed-commit:1.0",
    "urn:ietf:params:netconf:capability:confirmed-commit:1.1",
)
REQUIRED_MUTATION_CAPABILITIES = {
    ":candidate": CANDIDATE_CAPABILITIES,
    ":confirmed-commit": CONFIRMED_COMMIT_CAPABILITIES,
}
# <persist>/<persist-id> only exist in :confirmed-commit:1.1 (RFC 6241 section 8.4).
REQUIRED_PERSIST_CAPABILITIES = {
    ":candidate": CANDIDATE_CAPABILITIES,
    ":confirmed-commit:1.1": ("urn:ietf:params:netconf:capability:confirmed-commit:1.1",),
}


class NetconfHostKeyError(Exception):
    """The NETCONF server host key was not verified against a trusted pin."""


class NetconfCapabilityError(Exception):
    """The NETCONF server lacks a capability the safe mutation path depends on."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            "NETCONF server does not advertise required capabilities for edit-config: "
            + ", ".join(missing)
            + "; refusing to mutate (no fallback to writing running directly)"
        )


@dataclass(frozen=True)
class NetconfOperationResult:
    operation: str
    xml: str
    elapsed_seconds: float


@dataclass(frozen=True)
class NetconfConnectResult:
    session_id: str | None
    server_capabilities: tuple[str, ...]
    host_key_fingerprint: str
    elapsed_seconds: float


@dataclass(frozen=True)
class NetconfMutationStepResult:
    step: str
    xml: str
    elapsed_seconds: float


@dataclass(frozen=True)
class NetconfMutationResult:
    steps: tuple[NetconfMutationStepResult, ...]
    finalized: bool
    confirm_timeout_seconds: float
    persist: str | None = None


@dataclass(frozen=True)
class NetconfClient:
    host: str
    username: str
    password: str
    port: int = DEFAULT_PORT
    timeout: float = 15.0
    allow_unknown_host_key: bool = False
    expected_host_key_fingerprint: str | None = None

    def connect_test(self) -> NetconfConnectResult:
        started = time.monotonic()
        session = self._connect()
        try:
            capabilities = tuple(sorted(str(capability) for capability in session.server_capabilities))
            session_id = session.session_id
            return NetconfConnectResult(
                session_id=str(session_id) if session_id is not None else None,
                server_capabilities=capabilities,
                host_key_fingerprint=f"SHA256:{server_key_fingerprint(session)}",
                elapsed_seconds=time.monotonic() - started,
            )
        finally:
            _close_session(session)

    def get(self, *, filter_xml: str | None = None) -> NetconfOperationResult:
        return self._run("get", lambda session: session.get(filter=filter_xml))

    def get_config(self, *, datastore: str = "running", filter_xml: str | None = None) -> NetconfOperationResult:
        _validate_datastore(datastore)
        return self._run(
            "get-config",
            lambda session: session.get_config(source=datastore, filter=filter_xml),
        )

    def edit_config(self, *, datastore: str, config_xml: str) -> NetconfOperationResult:
        # A single, unguarded edit-config with no lock/validate/commit safety net. The CLI
        # tool never calls this directly — live mutation goes through
        # execute_confirmed_commit(), which always targets candidate and defaults to an
        # auto-reverting confirmed commit. Kept for completeness and for tests that need a
        # bare edit-config without the full transaction.
        _validate_datastore(datastore)
        return self._run(
            "edit-config",
            lambda session: session.edit_config(target=datastore, config=config_xml),
        )

    def execute_confirmed_commit(
        self,
        *,
        config_xml: str,
        verify_filter_xml: str | None = None,
        confirm_timeout: float = DEFAULT_CONFIRM_TIMEOUT,
        finalize: bool = False,
        persist: str | None = None,
    ) -> NetconfMutationResult:
        """Merge config_xml into the candidate datastore behind lock/validate/confirmed-commit.

        Always targets "candidate", never "running" — there is no way to request a direct
        running edit through this method. Without finalize=True, the confirming commit is
        never sent, so the device reverts running to its pre-commit state once the confirm
        timeout elapses or this session closes (RFC 6241 confirmed-commit semantics). This
        makes the rollback path a structural guarantee rather than something callers must
        remember to trigger.

        Without persist, "this session closes" is the end of this call, so the change is
        only visible to the optional verify read inside the call and confirm_timeout has
        no practical effect. With persist=<token> (requires :confirmed-commit:1.1), the
        pending commit survives the session close: running keeps the change until
        confirm_timeout elapses, confirm_persisted_commit(token) makes it permanent, or
        cancel_persisted_commit(token) reverts it early. That window is what other
        interfaces (CLI/SNMP/Web) need to observe the change.

        Raises NetconfCapabilityError before any lock/edit RPC if the server does not
        advertise :candidate and :confirmed-commit (1.0 or 1.1; 1.1 when persist is set).
        """
        timeout_text = confirm_timeout_text(confirm_timeout)
        if persist is not None:
            _validate_persist_token(persist)
        session = self._connect()
        steps: list[NetconfMutationStepResult] = []
        committed = False
        try:
            if persist is None:
                missing = missing_mutation_capabilities(session.server_capabilities)
            else:
                missing = missing_persist_capabilities(session.server_capabilities)
            if missing:
                raise NetconfCapabilityError(missing)
            steps.append(_time_step("lock", lambda: session.lock(target=MUTATION_TARGET)))
            try:
                steps.append(
                    _time_step(
                        "edit-config",
                        lambda: session.edit_config(target=MUTATION_TARGET, config=config_xml),
                    )
                )
                steps.append(_time_step("validate", lambda: session.validate(source=MUTATION_TARGET)))
                steps.append(
                    _time_step(
                        "commit",
                        lambda: session.commit(**_confirmed_commit_kwargs(timeout_text, persist)),
                    )
                )
                committed = True
                if verify_filter_xml is not None:
                    steps.append(_time_step("verify", lambda: session.get(filter=verify_filter_xml)))
                if finalize:
                    if persist is None:
                        steps.append(_time_step("finalize", lambda: session.commit()))
                    else:
                        steps.append(_time_step("finalize", lambda: session.commit(persist_id=persist)))
            finally:
                if not committed:
                    try:
                        steps.append(_time_step("discard-changes", lambda: session.discard_changes()))
                    except Exception:
                        pass
                try:
                    steps.append(_time_step("unlock", lambda: session.unlock(target=MUTATION_TARGET)))
                except Exception:
                    pass
        finally:
            _close_session(session)
        return NetconfMutationResult(
            steps=tuple(steps),
            finalized=finalize,
            confirm_timeout_seconds=confirm_timeout,
            persist=persist,
        )

    def confirm_persisted_commit(self, *, persist_id: str) -> NetconfOperationResult:
        """Send the confirming <commit><persist-id/></commit> for a persisted confirmed commit."""
        _validate_persist_token(persist_id)
        return self._run_persist_step(
            "confirm-commit", lambda session: session.commit(persist_id=persist_id)
        )

    def cancel_persisted_commit(self, *, persist_id: str) -> NetconfOperationResult:
        """Revert a persisted confirmed commit now with <cancel-commit><persist-id/></cancel-commit>."""
        _validate_persist_token(persist_id)
        return self._run_persist_step(
            "cancel-commit", lambda session: session.cancel_commit(persist_id=persist_id)
        )

    def _run_persist_step(self, operation: str, call: Callable[[object], object]) -> NetconfOperationResult:
        def guarded(session: object) -> object:
            missing = missing_persist_capabilities(session.server_capabilities)
            if missing:
                raise NetconfCapabilityError(missing)
            return call(session)

        return self._run(operation, guarded)

    def _run(self, operation: str, call: Callable[[object], object]) -> NetconfOperationResult:
        started = time.monotonic()
        session = self._connect()
        try:
            reply = call(session)
            return NetconfOperationResult(
                operation=operation,
                xml=_reply_xml(reply),
                elapsed_seconds=time.monotonic() - started,
            )
        finally:
            _close_session(session)

    def _connect(self):
        if not self.host:
            raise ValueError("host is required")
        if not self.username:
            raise ValueError("username is required")
        if not self.password:
            raise ValueError("password is required")
        if self.port < 1 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if self.expected_host_key_fingerprint is None and not self.allow_unknown_host_key:
            raise NetconfHostKeyError(
                "NETCONF host-key verification requires expected_host_key_fingerprint; "
                "set allow_unknown_host_key=True only for an isolated lab"
            )

        manager = _import_ncclient_manager()
        # hostkey_verify is False because ncclient's own verification overwrites paramiko's
        # preferred host-key algorithms with whatever type known_hosts recorded. For an
        # RSA host key that pins negotiation to "ssh-rsa", which paramiko 5.x no longer
        # implements, so the handshake fails before verification can even run. We let
        # paramiko negotiate rsa-sha2-* normally and verify the key ourselves, between the
        # key exchange and user authentication, so the password is never sent to a server
        # whose host key does not match the pin.
        session = _open_ncclient_session(
            manager,
            verify_server_key=self._verify_server_key,
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=self.timeout,
            hostkey_verify=False,
            allow_agent=False,
            look_for_keys=False,
        )
        try:
            # Defense in depth: re-check the key of the established session.
            self._verify_host_key(session)
        except Exception:
            _close_session(session)
            raise
        return session

    def _verify_host_key(self, session: object) -> None:
        if self.expected_host_key_fingerprint is None:
            return
        self._check_fingerprint(server_key_fingerprint(session))

    def _verify_server_key(self, server_key: object) -> None:
        """Pre-auth hook: called with paramiko's remote server key before any credential is sent."""
        if self.expected_host_key_fingerprint is None:
            return
        self._check_fingerprint(key_fingerprint(server_key))

    def _check_fingerprint(self, actual: str) -> None:
        expected = normalize_fingerprint(self.expected_host_key_fingerprint or "")
        if actual != expected:
            raise NetconfHostKeyError(
                "NETCONF server host key does not match the pinned fingerprint; "
                f"expected SHA256:{expected}, actual SHA256:{actual}"
            )


def normalize_fingerprint(value: str) -> str:
    """Return the bare base64 body of a SHA256 fingerprint, with or without the prefix."""
    text = value.strip()
    if text.lower().startswith("sha256:"):
        text = text[len("sha256:") :]
    text = text.strip().rstrip("=")
    if not text:
        raise ValueError("host key fingerprint is empty")
    return text


def server_key_fingerprint(session: object) -> str:
    """SHA256 fingerprint of the connected server's host key, as ssh-keygen -lf prints it."""
    transport = getattr(getattr(session, "_session", None), "_transport", None)
    get_remote_server_key = getattr(transport, "get_remote_server_key", None)
    if not callable(get_remote_server_key):
        raise NetconfHostKeyError(
            "cannot read the NETCONF server host key from this ncclient session; "
            "ncclient internals changed and host-key verification cannot be trusted"
        )
    return key_fingerprint(get_remote_server_key())


def key_fingerprint(server_key: object) -> str:
    """SHA256 fingerprint (bare base64, no padding) of a paramiko public key object."""
    digest = hashlib.sha256(server_key.asbytes()).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def confirm_timeout_text(confirm_timeout: float) -> str:
    """The <confirm-timeout> value as ncclient needs it: a str of whole seconds >= 1.

    ncclient assigns the value straight to an lxml element's .text, which only accepts
    str/bytes; an int raises TypeError at the commit step on a real session.
    """
    if isinstance(confirm_timeout, bool) or not isinstance(confirm_timeout, (int, float)):
        raise ValueError("confirm_timeout must be a number of seconds")
    if confirm_timeout < 1:
        raise ValueError("confirm_timeout must be at least 1 second")
    if confirm_timeout != int(confirm_timeout):
        raise ValueError("confirm_timeout must be a whole number of seconds")
    return str(int(confirm_timeout))


def missing_mutation_capabilities(server_capabilities: object) -> tuple[str, ...]:
    """Names of the capabilities execute_confirmed_commit() needs that the server lacks."""
    return _missing_capabilities(server_capabilities, REQUIRED_MUTATION_CAPABILITIES)


def missing_persist_capabilities(server_capabilities: object) -> tuple[str, ...]:
    """Names of the capabilities a persisted confirmed commit needs that the server lacks."""
    return _missing_capabilities(server_capabilities, REQUIRED_PERSIST_CAPABILITIES)


def _missing_capabilities(server_capabilities: object, required: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    advertised = {str(capability).split("?", 1)[0].strip() for capability in server_capabilities}
    return tuple(name for name, uris in required.items() if not advertised.intersection(uris))


def _confirmed_commit_kwargs(timeout_text: str, persist: str | None) -> dict[str, object]:
    kwargs: dict[str, object] = {"confirmed": True, "timeout": timeout_text}
    if persist is not None:
        kwargs["persist"] = persist
    return kwargs


def _validate_persist_token(token: str) -> None:
    if not isinstance(token, str) or not token.strip():
        raise ValueError("persist token must be a non-empty string")


def _open_ncclient_session(manager, *, verify_server_key: Callable[[object], None], **connect_kwargs):
    """ncclient manager.connect_ssh(), with verify_server_key run before user authentication.

    ncclient's SSHSession.connect() does the key exchange, then its own (disabled here)
    known_hosts check, then _auth(). Hooking _auth() is the only point where the server
    key is known but no credential has been sent yet.
    """
    device_handler = manager.make_device_handler(None, [])
    device_handler.add_additional_ssh_connect_params(connect_kwargs)
    device_handler.add_additional_netconf_params({})
    session = _make_pinned_ssh_session(device_handler, verify_server_key)
    try:
        session.connect(**connect_kwargs)
    except Exception:
        if session.transport:
            session.close()
        raise
    return manager.Manager(session, device_handler, timeout=connect_kwargs.get("timeout", 30))


def _make_pinned_ssh_session(device_handler: object, verify_server_key: Callable[[object], None]):
    from ncclient import transport

    class PinnedSSHSession(transport.SSHSession):
        def _auth(self, *args, **kwargs):
            verify_server_key(self._transport.get_remote_server_key())
            return super()._auth(*args, **kwargs)

    return PinnedSSHSession(device_handler)


def _validate_datastore(datastore: str) -> None:
    if datastore not in ALLOWED_DATASTORES:
        raise ValueError(f"unsupported datastore: {datastore!r}")


def _import_ncclient_manager():
    try:
        from ncclient import manager
    except ImportError as error:
        raise RuntimeError(
            'ncclient is required for NETCONF; install with pip install "device-cli-tool[netconf]"'
        ) from error
    return manager


def _reply_xml(reply: object) -> str:
    xml = getattr(reply, "xml", None)
    if isinstance(xml, str) and xml:
        return xml
    return str(reply)


def _close_session(session: object) -> None:
    try:
        session.close_session()
    except Exception:
        pass


def _time_step(step: str, call: Callable[[], object]) -> NetconfMutationStepResult:
    started = time.monotonic()
    reply = call()
    return NetconfMutationStepResult(
        step=step, xml=_reply_xml(reply), elapsed_seconds=time.monotonic() - started
    )
