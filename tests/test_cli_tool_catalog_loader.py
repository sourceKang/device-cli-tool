from __future__ import annotations

import pytest

from cli_tool.catalog.loader import load_catalog, load_driver


def test_load_catalog_builds_command_specs_from_yaml(tmp_path):
    path = tmp_path / "synthetic.yaml"
    path.write_text(
        """
commands:
  show_vlan:
    mode: exec
    command: "show vlan {vid}"
    readonly: true
    expected_tokens:
      - "VLAN Name"
      - "{vid}"
""",
        encoding="utf-8",
    )

    catalog = load_catalog(path)

    assert catalog.build_commands("show_vlan", {"vid": 100}) == ["show vlan 100"]
    assert catalog.build_expected_tokens("show_vlan", {"vid": 100}) == ["VLAN Name", "100"]


def test_load_driver_builds_device_driver_from_yaml(tmp_path):
    path = tmp_path / "driver.yaml"
    path.write_text(
        """
family: neox
model: neox-06
commands:
  show_version:
    command: "show version"
    readonly: true
    expected_tokens:
      - "Version"
""",
        encoding="utf-8",
    )

    driver = load_driver(path)

    assert driver.family == "neox"
    assert driver.model == "neox-06"
    assert driver.build_commands("show_version") == ["show version"]
    assert driver.expected_tokens("show_version") == ["Version"]


def test_load_catalog_supports_command_sequences(tmp_path):
    path = tmp_path / "sequence.yaml"
    path.write_text(
        """
commands:
  show_interface_detail:
    mode: exec
    readonly: true
    commands:
      - "show interface {interface}"
      - "show running-config interface {interface}"
    expected_tokens:
      - "{interface}"
      - "enable"
""",
        encoding="utf-8",
    )

    catalog = load_catalog(path)

    assert catalog.build_commands("show_interface_detail", {"interface": "ge 1-1"}) == [
        "show interface ge 1-1",
        "show running-config interface ge 1-1",
    ]


def test_load_catalog_rejects_missing_readonly(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text(
        """
commands:
  show_version:
    command: "show version"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="readonly"):
        load_catalog(path)


def test_load_driver_requires_family_and_model(tmp_path):
    path = tmp_path / "invalid_driver.yaml"
    path.write_text(
        """
family: neox
commands:
  show_version:
    command: "show version"
    readonly: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model"):
        load_driver(path)
