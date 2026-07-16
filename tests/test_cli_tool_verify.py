from __future__ import annotations

from cli_tool.models import CliCommandOutput
from cli_tool.parsers.text_tokens import missing_tokens
from cli_tool.workflows.verify import verify_cli_outputs, verify_output_tokens


def test_missing_tokens_matches_case_insensitively_by_default():
    missing = missing_tokens("VLAN Name: REST_API\nState: Active", ["vlan name", "active", "missing"])

    assert missing == ["missing"]


def test_missing_tokens_can_match_case_sensitively():
    missing = missing_tokens("State: Active", ["state: active", "State: Active"], case_sensitive=True)

    assert missing == ["state: active"]


def test_verify_output_tokens_returns_passed_result_when_all_tokens_exist():
    result = verify_output_tokens(
        "show vlan 100",
        "VLAN Name: REST_API\nVID: 100",
        ["VLAN Name", "100"],
    )

    assert result.passed
    assert result.command == "show vlan 100"
    assert result.expected_tokens == ("VLAN Name", "100")
    assert result.missing_tokens == ()


def test_verify_cli_outputs_reports_missing_tokens_by_command():
    result = verify_cli_outputs(
        {
            "show version": "Version: V1.0",
            "show vlan 100": "VLAN Name: REST_API",
        },
        {
            "show version": ["Version"],
            "show vlan 100": ["VLAN Name", "VID: 100"],
        },
    )

    assert not result.passed
    assert result.missing_by_command == {"show vlan 100": ["VID: 100"]}


def test_verify_cli_outputs_accepts_command_output_objects():
    result = verify_cli_outputs(
        [CliCommandOutput(command="show version", output="Model: NeoX-06")],
        {"show version": ["model", "neox-06"]},
    )

    assert result.passed
    assert result.output_by_command == {"show version": "Model: NeoX-06"}


def test_verify_cli_outputs_marks_missing_command_output_as_failed():
    result = verify_cli_outputs({}, {"show vlan 100": ["VLAN Name"]})

    assert not result.passed
    assert result.missing_by_command == {"show vlan 100": ["VLAN Name"]}
