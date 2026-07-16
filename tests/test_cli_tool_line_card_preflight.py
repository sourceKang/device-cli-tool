from __future__ import annotations

import json

import pytest

from cli_tool.catalog.models import CommandSpec
from cli_tool.devices.base import build_driver
from cli_tool.models import CliCommandOutput
from cli_tool.parsers.line_card_status import LineCardStatusParseError, parse_line_card_status
from cli_tool.workflows.line_card_preflight import run_line_card_preflight


VALID_OUTPUT = """Slot   Card Type        Status      FW Version
----   ---------        ------      ----------
1      MSC7000          Active      V1.2.3
2      VLC1424          Standby     V4.5.6
"""


class FakeTransport:
    def __init__(self, output: str) -> None:
        self.output = output
        self.commands: list[str] = []

    def run_commands(self, commands, *, owner=None):
        self.commands = list(commands)
        return [CliCommandOutput(command=command, output=self.output) for command in commands]


def test_parse_line_card_status_returns_json_safe_snapshot():
    snapshot = parse_line_card_status(VALID_OUTPUT)

    assert snapshot.cards[0].slot == "1"
    assert snapshot.cards[0].card_type == "MSC7000"
    assert snapshot.cards[1].status == "Standby"
    assert snapshot.cards[1].fw_version == "V4.5.6"
    assert json.loads(snapshot.to_json())["command"] == "show lc st"


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("", "output is empty"),
        ("Slot  Card Type  Status\n1     MSC7000    Active", "FW Version"),
        (
            "Slot   Card Type   Status   FW Version\n1      MSC7000              V1.2.3",
            "Status",
        ),
        ("Slot   Card Type   Status   FW Version\n----   ---------   ------   ----------", "no line-card rows"),
    ],
)
def test_parse_line_card_status_fails_closed(output, message):
    with pytest.raises(LineCardStatusParseError, match=message):
        parse_line_card_status(output)


def test_run_line_card_preflight_uses_catalog_and_parser():
    driver = build_driver(
        family="synthetic",
        model="lab",
        specs=[
            CommandSpec(
                command_id="show_line_card_status",
                readonly=True,
                command="show lc st",
                expected_tokens=("Card Type", "Status", "FW Version"),
            )
        ],
    )
    transport = FakeTransport(VALID_OUTPUT)

    snapshot = run_line_card_preflight(driver, transport, owner="preflight:node1")

    assert transport.commands == ["show lc st"]
    assert len(snapshot.cards) == 2


def test_run_line_card_preflight_rejects_empty_output_before_parsing():
    driver = build_driver(
        family="synthetic",
        model="lab",
        specs=[
            CommandSpec(
                command_id="show_line_card_status",
                readonly=True,
                command="show lc st",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="CLI output is empty"):
        run_line_card_preflight(driver, FakeTransport(""))
