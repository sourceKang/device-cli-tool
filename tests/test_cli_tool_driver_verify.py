from __future__ import annotations

import pytest

from cli_tool.catalog.models import CommandSpec
from cli_tool.devices.base import build_driver
from cli_tool.models import CliCommandOutput
from cli_tool.workflows.driver_verify import run_driver_verify


class FakeTransport:
    def __init__(self, output_by_command: dict[str, str]) -> None:
        self.output_by_command = output_by_command
        self.commands: list[str] = []
        self.owner: str | None = None

    def run_commands(self, commands: list[str] | tuple[str, ...], *, owner: str | None = None) -> list[CliCommandOutput]:
        self.commands = list(commands)
        self.owner = owner
        return [
            CliCommandOutput(command=command, output=self.output_by_command.get(command, ""))
            for command in self.commands
        ]


def test_run_driver_verify_builds_commands_and_expected_tokens_from_driver():
    driver = build_driver(
        family="synthetic",
        model="lab",
        specs=[
            CommandSpec(
                command_id="show_vlan",
                readonly=True,
                command="show vlan {vid}",
                expected_tokens=("VLAN Name", "{vid}"),
            )
        ],
    )
    transport = FakeTransport({"show vlan 100": "VID: 100\nVLAN Name: REST_API"})

    result = run_driver_verify(
        driver,
        transport,
        "show_vlan",
        {"vid": 100},
        owner="case-001",
    )

    assert result.passed
    assert transport.commands == ["show vlan 100"]
    assert transport.owner == "case-001"


def test_run_driver_verify_reports_missing_tokens():
    driver = build_driver(
        family="synthetic",
        model="lab",
        specs=[
            CommandSpec(
                command_id="show_vlan",
                readonly=True,
                command="show vlan {vid}",
                expected_tokens=("VLAN Name", "{vid}"),
            )
        ],
    )
    transport = FakeTransport({"show vlan 200": "VLAN Name: REST_API"})

    result = run_driver_verify(driver, transport, "show_vlan", {"vid": 200})

    assert not result.passed
    assert result.missing_by_command == {"show vlan 200": ["200"]}


def test_run_driver_verify_rejects_non_readonly_command():
    driver = build_driver(
        family="synthetic",
        model="lab",
        specs=[
            CommandSpec(
                command_id="set_vlan",
                readonly=False,
                mode="config",
                commands=("vlan {vid}", "name {name}"),
            )
        ],
    )
    transport = FakeTransport({})

    with pytest.raises(ValueError, match="not readonly"):
        run_driver_verify(driver, transport, "set_vlan", {"vid": 100, "name": "qa"})


def test_run_driver_verify_supports_command_sequences():
    driver = build_driver(
        family="synthetic",
        model="lab",
        specs=[
            CommandSpec(
                command_id="show_interface_detail",
                readonly=True,
                commands=("show interface {interface}", "show running-config interface {interface}"),
                expected_tokens=("{interface}", "enable"),
            )
        ],
    )
    transport = FakeTransport(
        {
            "show interface ge 1-1": "ge 1-1\nadmin: enable",
            "show running-config interface ge 1-1": "interface ge 1-1\nenable",
        }
    )

    result = run_driver_verify(driver, transport, "show_interface_detail", {"interface": "ge 1-1"})

    assert result.passed
    assert transport.commands == ["show interface ge 1-1", "show running-config interface ge 1-1"]
