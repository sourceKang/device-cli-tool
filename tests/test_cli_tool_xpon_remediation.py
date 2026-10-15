from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cli_tool.safety.policy import MutationAuthorization, validate_xpon_mutation_commands
from cli_tool.workflows.xpon_remediation import (
    assert_dry_run_target_confirmation,
    assert_live_execution_unlocked,
    build_register_method_a_plan,
)
from tools.cli_tool_xpon_plan import run


def test_xpon_plan_converts_slot_port_into_exact_protected_sequence():
    plan = build_register_method_a_plan(
        device_family="IES4204",
        node_key="NODE3",
        host="192.0.2.3",
        slot=3,
        port=16,
    )

    assert plan.device_family == "IES4204"
    assert plan.interface == "xpon 3-16"
    assert plan.precheck_commands == (
        "show in remote xont unreg",
        "show in remote xont summary",
    )
    assert plan.mutation_commands == (
        "configure",
        "interface xpon 3-16",
        "inactive",
        "register-method A",
        "no inactive",
        "exit",
    )
    assert plan.postcheck_commands == plan.precheck_commands
    assert plan.live_execution_status == "LOCKED"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"device_family": "IES4204", "node_key": "NODE3;delete", "host": "192.0.2.3", "slot": 3, "port": 16},
        {"device_family": "IES4204", "node_key": "NODE3", "host": "device.example", "slot": 3, "port": 16},
        {"device_family": "IES4204", "node_key": "NODE3", "host": "192.0.2.3", "slot": True, "port": 16},
        {"device_family": "IES4204", "node_key": "NODE3", "host": "192.0.2.3", "slot": 3, "port": 17},
    ],
)
def test_xpon_plan_rejects_unsafe_target_values(kwargs):
    with pytest.raises(ValueError):
        build_register_method_a_plan(**kwargs)


def test_xpon_mutation_allowlist_rejects_added_or_changed_commands():
    with pytest.raises(PermissionError, match="exactly match"):
        validate_xpon_mutation_commands(
            (
                "configure",
                "interface xpon 3-16",
                "inactive",
                "register-method B",
                "no inactive",
                "exit",
            ),
            slot=3,
            port=16,
        )


def test_target_confirmation_must_match_node_and_host_exactly():
    plan = build_register_method_a_plan(device_family="IES4204", node_key="NODE3", host="192.0.2.3", slot=3, port=16)

    assert_dry_run_target_confirmation(plan, confirmed_node="NODE3", confirmed_host="192.0.2.3")
    with pytest.raises(PermissionError, match="node"):
        assert_dry_run_target_confirmation(plan, confirmed_node="NODE1", confirmed_host="192.0.2.3")
    with pytest.raises(PermissionError, match="host"):
        assert_dry_run_target_confirmation(plan, confirmed_node="NODE3", confirmed_host="192.0.2.4")


def test_live_execution_requires_flag_and_stays_locked_without_verified_rollback():
    plan = build_register_method_a_plan(device_family="IES4204", node_key="NODE3", host="192.0.2.3", slot=3, port=16)
    authorization = MutationAuthorization("NODE3", "192.0.2.3", "NODE3", "192.0.2.3", False)
    with pytest.raises(PermissionError, match="--run-mutating"):
        assert_live_execution_unlocked(plan, authorization)

    authorization = replace(authorization, run_mutating=True)
    with pytest.raises(PermissionError, match="live execution is locked"):
        assert_live_execution_unlocked(plan, authorization)


def test_dry_run_cli_writes_browser_report_without_credentials(tmp_path):
    exit_code, payload = run(
        [
            "--device-family",
            "IES4204",
            "--node-key",
            "NODE3",
            "--host",
            "192.0.2.3",
            "--confirm-node",
            "NODE3",
            "--confirm-host",
            "192.0.2.3",
            "--slot",
            "3",
            "--port",
            "16",
            "--report-dir",
            str(tmp_path),
        ]
    )

    latest_json = tmp_path / "latest.json"
    latest_html = tmp_path / "latest.html"
    raw = latest_json.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert exit_code == 0
    assert payload["result"] == "DRY_RUN_READY"
    assert report["live_execution"]["status"] == "LOCKED"
    assert latest_html.is_file()
    assert "未連線、未修改設備" in latest_html.read_text(encoding="utf-8")
    assert "password" not in raw.casefold()
    assert "token" not in raw.casefold()


def test_run_mutating_is_refused_and_only_writes_locked_audit_report(tmp_path):
    exit_code, payload = run(
        [
            "--device-family",
            "IES4204",
            "--node-key",
            "NODE3",
            "--host",
            "192.0.2.3",
            "--confirm-node",
            "NODE3",
            "--confirm-host",
            "192.0.2.3",
            "--slot",
            "3",
            "--port",
            "16",
            "--report-dir",
            str(tmp_path),
            "--run-mutating",
        ]
    )

    assert exit_code == 2
    assert payload["result"] == "REFUSED_LIVE_EXECUTION_LOCKED"
    assert (tmp_path / "latest.html").is_file()
