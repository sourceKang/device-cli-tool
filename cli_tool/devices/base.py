from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cli_tool.catalog.models import CommandCatalog, CommandSpec


@dataclass(frozen=True)
class DeviceDriver:
    family: str
    model: str
    catalog: CommandCatalog

    def build_commands(self, command_id: str, params: Mapping[str, object] | None = None) -> list[str]:
        return self.catalog.build_commands(command_id, params)

    def expected_tokens(self, command_id: str, params: Mapping[str, object] | None = None) -> list[str]:
        return self.catalog.build_expected_tokens(command_id, params)

    def command_spec(self, command_id: str) -> CommandSpec:
        return self.catalog.get(command_id)

    def normalize_output(self, command: str, output: str) -> str:
        return output

    def enter_config_mode(self) -> list[str]:
        return ["configure"]

    def exit_config_mode(self) -> list[str]:
        return ["exit"]


def build_driver(family: str, model: str, specs: Sequence[CommandSpec]) -> DeviceDriver:
    return DeviceDriver(family=family, model=model, catalog=CommandCatalog(specs))

