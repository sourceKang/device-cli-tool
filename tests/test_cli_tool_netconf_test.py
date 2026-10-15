from __future__ import annotations

from dataclasses import dataclass

import pytest

import tools.cli_tool_netconf_test as netconf_test_tool
from cli_tool.transport.netconf import NetconfCapabilityError
from tools.cli_tool_netconf_test import run


@dataclass
class FakeConnectResult:
    session_id: str
    server_capabilities: tuple
    elapsed_seconds: float


@dataclass
class FakeOperationResult:
    operation: str
    xml: str
    elapsed_seconds: float


@dataclass
class FakeMutationStepResult:
    step: str
    xml: str
    elapsed_seconds: float


@dataclass
class FakeMutationResult:
    steps: tuple
    finalized: bool
    confirm_timeout_seconds: float


class FakeNetconfClient:
    created_with: list = []
    execute_calls: list = []

    def __init__(self, **kwargs):
        FakeNetconfClient.created_with.append(kwargs)

    def connect_test(self):
        return FakeConnectResult("42", ("urn:ietf:params:netconf:base:1.1",), 0.01)

    def get(self, *, filter_xml=None):
        return FakeOperationResult(
            "get", "<rpc-reply><password>hunter2</password></rpc-reply>", 0.02
        )

    def get_config(self, *, datastore="running", filter_xml=None):
        return FakeOperationResult("get-config", f"<rpc-reply>{datastore}</rpc-reply>", 0.03)

    def execute_confirmed_commit(self, *, config_xml, verify_filter_xml=None, confirm_timeout=30.0, finalize=False):
        FakeNetconfClient.execute_calls.append(
            {
                "config_xml": config_xml,
                "verify_filter_xml": verify_filter_xml,
                "confirm_timeout": confirm_timeout,
                "finalize": finalize,
            }
        )
        steps = [
            FakeMutationStepResult("lock", "<rpc-reply><ok/></rpc-reply>", 0.01),
            FakeMutationStepResult("edit-config", "<rpc-reply><ok/></rpc-reply>", 0.02),
            FakeMutationStepResult("validate", "<rpc-reply><ok/></rpc-reply>", 0.01),
            FakeMutationStepResult("commit", "<rpc-reply><ok/></rpc-reply>", 0.03),
        ]
        if verify_filter_xml is not None:
            steps.append(
                FakeMutationStepResult(
                    "verify", "<rpc-reply><data><highthreshold>200</highthreshold></data></rpc-reply>", 0.02
                )
            )
        if finalize:
            steps.append(FakeMutationStepResult("finalize", "<rpc-reply><ok/></rpc-reply>", 0.01))
        steps.append(FakeMutationStepResult("unlock", "<rpc-reply><ok/></rpc-reply>", 0.01))
        return FakeMutationResult(steps=tuple(steps), finalized=finalize, confirm_timeout_seconds=confirm_timeout)


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeNetconfClient.created_with = []
    FakeNetconfClient.execute_calls = []
    yield


def test_connect_reports_capabilities_and_defaults_to_unspecified_host_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")

    exit_code, payload = run(
        ["connect", "--host", "192.0.2.10", "--username", "admin", "--report-dir", str(tmp_path)]
    )

    assert exit_code == 0
    assert payload["passed"] is True
    # session_id is treated as sensitive by cli_tool.reporting.redaction's SENSITIVE_KEYS,
    # matching the existing convention for SshTiming.session_id.
    assert payload["outcome"]["session_id"] == "***REDACTED***"
    assert payload["outcome"]["server_capabilities"] == ["urn:ietf:params:netconf:base:1.1"]
    assert payload["host_key_verification"] == "unspecified"
    assert FakeNetconfClient.created_with[0]["allow_unknown_host_key"] is False
    assert FakeNetconfClient.created_with[0]["expected_host_key_fingerprint"] is None


def test_connect_forwards_pinned_host_key_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")

    exit_code, payload = run(
        [
            "connect",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--host-key-fingerprint",
            "SHA256:abc123",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert payload["host_key_verification"] == "pinned_fingerprint"
    assert FakeNetconfClient.created_with[0]["expected_host_key_fingerprint"] == "SHA256:abc123"


def test_get_redacts_included_xml_output(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")

    exit_code, payload = run(
        [
            "get",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--include-output",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert "hunter2" not in payload["outcome"]["xml_output"]
    assert "***REDACTED***" in payload["outcome"]["xml_output"]


def test_get_config_uses_requested_datastore(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")

    exit_code, payload = run(
        [
            "get-config",
            "--host",
            "192.0.2.10",
            "--username",
            "admin",
            "--datastore",
            "candidate",
            "--include-output",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert payload["datastore"] == "candidate"
    assert "candidate" in payload["outcome"]["xml_output"]


def test_missing_password_env_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.delenv("CLI_TOOL_NETCONF_PASSWORD", raising=False)

    with pytest.raises(SystemExit, match="missing NETCONF password"):
        run(["connect", "--host", "192.0.2.10", "--username", "admin", "--report-dir", str(tmp_path)])

    assert FakeNetconfClient.created_with == []


def test_edit_config_dry_run_never_connects(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config><password>hunter2</password></config>", encoding="utf-8")

    exit_code, payload = run(
        [
            "edit-config",
            "--host",
            "192.0.2.3",
            "--node-key",
            "NODE1",
            "--confirm-node",
            "NODE1",
            "--confirm-host",
            "192.0.2.3",
            "--edit-config-file",
            str(config_file),
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert payload["result"] == "DRY_RUN_READY"
    assert payload["live_execution"]["status"] == "AVAILABLE_VIA_RUN_MUTATING"
    assert payload["target"]["datastore"] == "candidate"
    assert "hunter2" not in payload["requested_config_xml"]
    assert FakeNetconfClient.created_with == []
    assert FakeNetconfClient.execute_calls == []


def test_edit_config_datastore_flag_is_rejected(tmp_path):
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config/>", encoding="utf-8")

    with pytest.raises(SystemExit, match="--datastore is not valid for edit-config"):
        run(
            [
                "edit-config",
                "--host",
                "192.0.2.3",
                "--node-key",
                "NODE1",
                "--confirm-node",
                "NODE1",
                "--confirm-host",
                "192.0.2.3",
                "--edit-config-file",
                str(config_file),
                "--datastore",
                "running",
                "--report-dir",
                str(tmp_path),
            ]
        )


def test_edit_config_run_mutating_requires_username(tmp_path):
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config/>", encoding="utf-8")

    with pytest.raises(SystemExit, match="--run-mutating requires --username"):
        run(
            [
                "edit-config",
                "--host",
                "192.0.2.3",
                "--node-key",
                "NODE1",
                "--confirm-node",
                "NODE1",
                "--confirm-host",
                "192.0.2.3",
                "--edit-config-file",
                str(config_file),
                "--run-mutating",
                "--report-dir",
                str(tmp_path),
            ]
        )


def test_mutation_only_flags_require_run_mutating(tmp_path):
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config/>", encoding="utf-8")

    with pytest.raises(SystemExit, match="require --run-mutating"):
        run(
            [
                "edit-config",
                "--host",
                "192.0.2.3",
                "--node-key",
                "NODE1",
                "--confirm-node",
                "NODE1",
                "--confirm-host",
                "192.0.2.3",
                "--edit-config-file",
                str(config_file),
                "--finalize-commit",
                "--report-dir",
                str(tmp_path),
            ]
        )


def test_edit_config_run_mutating_executes_transient_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config><hwmon/></config>", encoding="utf-8")

    exit_code, payload = run(
        [
            "edit-config",
            "--host",
            "192.0.2.3",
            "--username",
            "admin",
            "--node-key",
            "NODE1",
            "--confirm-node",
            "NODE1",
            "--confirm-host",
            "192.0.2.3",
            "--edit-config-file",
            str(config_file),
            "--run-mutating",
            "--confirm-timeout-seconds",
            "15",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert payload["result"] == "EXECUTED_TRANSIENT"
    assert payload["mutation"]["target"] == "candidate"
    assert payload["mutation"]["finalized"] is False
    assert [s["step"] for s in payload["mutation"]["steps"]] == [
        "lock",
        "edit-config",
        "validate",
        "commit",
        "unlock",
    ]
    assert FakeNetconfClient.execute_calls[0]["finalize"] is False
    assert FakeNetconfClient.execute_calls[0]["confirm_timeout"] == 15.0
    assert FakeNetconfClient.execute_calls[0]["config_xml"] == "<config><hwmon/></config>"


def test_edit_config_run_mutating_finalizes_when_requested(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config/>", encoding="utf-8")
    verify_file = tmp_path / "verify.xml"
    verify_file.write_text("<filter/>", encoding="utf-8")

    exit_code, payload = run(
        [
            "edit-config",
            "--host",
            "192.0.2.3",
            "--username",
            "admin",
            "--node-key",
            "NODE1",
            "--confirm-node",
            "NODE1",
            "--confirm-host",
            "192.0.2.3",
            "--edit-config-file",
            str(config_file),
            "--run-mutating",
            "--finalize-commit",
            "--verify-filter-file",
            str(verify_file),
            "--include-output",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert payload["result"] == "EXECUTED_FINALIZED"
    assert payload["mutation"]["finalized"] is True
    assert [s["step"] for s in payload["mutation"]["steps"]] == [
        "lock",
        "edit-config",
        "validate",
        "commit",
        "verify",
        "finalize",
        "unlock",
    ]
    assert FakeNetconfClient.execute_calls[0]["finalize"] is True
    assert FakeNetconfClient.execute_calls[0]["verify_filter_xml"] == "<filter/>"


def test_edit_config_run_mutating_reports_missing_capabilities(monkeypatch, tmp_path):
    class NoCandidateClient(FakeNetconfClient):
        def execute_confirmed_commit(self, **kwargs):
            raise NetconfCapabilityError((":candidate",))

    monkeypatch.setattr(netconf_test_tool, "NetconfClient", NoCandidateClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config/>", encoding="utf-8")

    exit_code, payload = run(
        [
            "edit-config",
            "--host",
            "192.0.2.3",
            "--username",
            "admin",
            "--node-key",
            "NODE1",
            "--confirm-node",
            "NODE1",
            "--confirm-host",
            "192.0.2.3",
            "--edit-config-file",
            str(config_file),
            "--run-mutating",
            "--report-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert payload["passed"] is False
    assert payload["result"] == "REFUSED_MISSING_CAPABILITY"
    assert payload["error_type"] == "NetconfCapabilityError"
    assert payload["missing_capabilities"] == [":candidate"]
    assert payload["mutation"] == {}


def test_edit_config_requires_exact_target_confirmation(tmp_path):
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config/>", encoding="utf-8")

    with pytest.raises(PermissionError, match="node"):
        run(
            [
                "edit-config",
                "--host",
                "192.0.2.3",
                "--node-key",
                "NODE1",
                "--confirm-node",
                "WRONG",
                "--confirm-host",
                "192.0.2.3",
                "--edit-config-file",
                str(config_file),
                "--report-dir",
                str(tmp_path),
            ]
        )


def test_edit_config_run_mutating_requires_exact_target_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(netconf_test_tool, "NetconfClient", FakeNetconfClient)
    monkeypatch.setenv("CLI_TOOL_NETCONF_PASSWORD", "secret")
    config_file = tmp_path / "edit.xml"
    config_file.write_text("<config/>", encoding="utf-8")

    with pytest.raises(PermissionError, match="node"):
        run(
            [
                "edit-config",
                "--host",
                "192.0.2.3",
                "--username",
                "admin",
                "--node-key",
                "NODE1",
                "--confirm-node",
                "WRONG",
                "--confirm-host",
                "192.0.2.3",
                "--edit-config-file",
                str(config_file),
                "--run-mutating",
                "--report-dir",
                str(tmp_path),
            ]
        )

    assert FakeNetconfClient.execute_calls == []
