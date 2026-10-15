from __future__ import annotations

import inspect

import pytest

from cli_tool.transport import netconf as netconf_module


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
