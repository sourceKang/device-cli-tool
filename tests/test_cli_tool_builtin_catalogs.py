from __future__ import annotations

from pathlib import Path

import pytest

from cli_tool.catalog.loader import load_builtin_driver, load_driver, load_driver_source
from cli_tool.models import CliCommandOutput
from cli_tool.workflows.driver_verify import run_driver_verify


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "configs" / "cli_tool"


@pytest.mark.parametrize(
    ("catalog_name", "expected_family"),
    [
        ("generic", "generic"),
        ("neox", "neox"),
        ("ies52xx", "ies52xx"),
        ("olt140x", "olt140x"),
    ],
)
def test_packaged_builtin_catalogs_load(catalog_name, expected_family):
    driver = load_builtin_driver(catalog_name)

    assert driver.family == expected_family
    assert driver.build_commands("show_version") == ["show version"]


def test_builtin_catalog_source_syntax_loads_packaged_catalog():
    driver = load_driver_source("builtin:ies52xx")

    assert driver.family == "ies52xx"
    assert driver.build_commands("show_system_information") == ["show system-information"]


class FakeTransport:
    def __init__(self, output_by_command: dict[str, str]) -> None:
        self.output_by_command = output_by_command
        self.commands: list[str] = []

    def run_commands(self, commands: list[str] | tuple[str, ...], *, owner: str | None = None) -> list[CliCommandOutput]:
        self.commands = list(commands)
        return [
            CliCommandOutput(command=command, output=self.output_by_command.get(command, ""))
            for command in self.commands
        ]


@pytest.mark.parametrize(
    ("catalog_name", "family"),
    [
        ("generic.yaml", "generic"),
        ("neox.yaml", "neox"),
        ("ies52xx.yaml", "ies52xx"),
        ("olt140x.yaml", "olt140x"),
    ],
)
def test_builtin_catalog_loads_show_version(catalog_name: str, family: str):
    driver = load_driver(CATALOG_DIR / catalog_name)

    assert driver.family == family
    assert driver.model == "generic"
    assert driver.command_spec("show_version").readonly is True
    assert driver.build_commands("show_version") == ["show version"]


def test_builtin_generic_show_version_can_run_through_driver_workflow():
    driver = load_driver(CATALOG_DIR / "generic.yaml")
    transport = FakeTransport({"show version": "Version: synthetic"})

    result = run_driver_verify(driver, transport, "show_version")

    assert result.passed
    assert transport.commands == ["show version"]


@pytest.mark.parametrize("catalog_name", ["neox.yaml", "ies52xx.yaml"])
def test_builtin_confirmed_catalogs_load_show_system_information(catalog_name: str):
    driver = load_driver(CATALOG_DIR / catalog_name)

    assert driver.command_spec("show_system_information").readonly is True
    assert driver.build_commands("show_system_information") == ["show system-information"]


@pytest.mark.parametrize("catalog_name", ["generic.yaml", "olt140x.yaml"])
def test_builtin_unconfirmed_catalogs_do_not_include_show_system_information(catalog_name: str):
    driver = load_driver(CATALOG_DIR / catalog_name)

    with pytest.raises(KeyError, match="show_system_information"):
        driver.command_spec("show_system_information")


def test_builtin_neox_show_system_information_can_run_through_driver_workflow():
    driver = load_driver(CATALOG_DIR / "neox.yaml")
    transport = FakeTransport({"show system-information": "Hostname: synthetic\nF/W version: V1.02"})

    result = run_driver_verify(driver, transport, "show_system_information")

    assert result.passed
    assert transport.commands == ["show system-information"]
