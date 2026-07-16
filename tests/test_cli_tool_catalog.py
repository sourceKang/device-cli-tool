from __future__ import annotations

import pytest

from cli_tool.catalog.models import CommandCatalog, CommandSpec
from cli_tool.devices.base import build_driver
from cli_tool.devices.registry import DeviceRegistry


def test_command_spec_builds_single_command_from_params():
    spec = CommandSpec(
        command_id="show_vlan",
        readonly=True,
        command="show vlan {vid}",
        expected_tokens=("VLAN Name", "{vid}"),
    )

    assert spec.build_commands({"vid": 100}) == ["show vlan 100"]
    assert spec.build_expected_tokens({"vid": 100}) == ["VLAN Name", "100"]


def test_command_spec_builds_command_sequence_from_params():
    spec = CommandSpec(
        command_id="set_name",
        readonly=False,
        mode="config",
        commands=("interface {interface}", "name {name}", "exit"),
    )

    assert spec.build_commands({"interface": "ge 1-1", "name": "qa_test"}) == [
        "interface ge 1-1",
        "name qa_test",
        "exit",
    ]


def test_command_spec_reports_missing_template_parameter():
    spec = CommandSpec(command_id="show_vlan", readonly=True, command="show vlan {vid}")

    with pytest.raises(KeyError, match="vid"):
        spec.build_commands({})


def test_command_catalog_rejects_duplicate_command_ids():
    spec = CommandSpec(command_id="show_version", readonly=True, command="show version")

    with pytest.raises(ValueError, match="unique"):
        CommandCatalog([spec, spec])


def test_device_driver_delegates_to_catalog():
    driver = build_driver(
        family="synthetic",
        model="lab",
        specs=[
            CommandSpec(
                command_id="show_version",
                readonly=True,
                command="show version",
                expected_tokens=("Version",),
            )
        ],
    )

    assert driver.build_commands("show_version") == ["show version"]
    assert driver.expected_tokens("show_version") == ["Version"]
    assert driver.command_spec("show_version").readonly is True


def test_device_registry_resolves_model_specific_and_family_default_drivers():
    default_driver = build_driver(
        family="neox",
        model="generic",
        specs=[CommandSpec(command_id="show_version", readonly=True, command="show version")],
    )
    model_driver = build_driver(
        family="neox",
        model="neox-06",
        specs=[CommandSpec(command_id="show_card", readonly=True, command="show card")],
    )
    registry = DeviceRegistry()
    registry.register(default_driver, default_for_family=True)
    registry.register(model_driver)

    assert registry.resolve("NeoX") is default_driver
    assert registry.resolve("NeoX", "NeoX-06") is model_driver
