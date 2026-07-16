from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    readonly: bool
    mode: str = "exec"
    command: str | None = None
    commands: tuple[str, ...] = ()
    expected_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.command and self.commands:
            raise ValueError(f"{self.command_id} cannot define both command and commands")
        if not self.command and not self.commands:
            raise ValueError(f"{self.command_id} must define command or commands")
        if not self.command_id:
            raise ValueError("command_id is required")

    def build_commands(self, params: Mapping[str, object] | None = None) -> list[str]:
        params = params or {}
        templates = self.commands or (self.command or "",)
        return [_format_template(template, params) for template in templates]

    def build_expected_tokens(self, params: Mapping[str, object] | None = None) -> list[str]:
        params = params or {}
        return [_format_template(token, params) for token in self.expected_tokens]


class CommandCatalog:
    def __init__(self, specs: Sequence[CommandSpec]) -> None:
        self._specs = {spec.command_id: spec for spec in specs}
        if len(self._specs) != len(specs):
            raise ValueError("command_id values must be unique")

    def get(self, command_id: str) -> CommandSpec:
        try:
            return self._specs[command_id]
        except KeyError as error:
            raise KeyError(f"unknown command_id: {command_id}") from error

    def build_commands(self, command_id: str, params: Mapping[str, object] | None = None) -> list[str]:
        return self.get(command_id).build_commands(params)

    def build_expected_tokens(self, command_id: str, params: Mapping[str, object] | None = None) -> list[str]:
        return self.get(command_id).build_expected_tokens(params)


def _format_template(template: str, params: Mapping[str, object]) -> str:
    try:
        return template.format_map(_StrictFormatParams(params))
    except KeyError as error:
        missing = error.args[0]
        raise KeyError(f"missing command template parameter: {missing}") from error


class _StrictFormatParams(dict[str, object]):
    def __init__(self, params: Mapping[str, object]) -> None:
        super().__init__((str(key), value) for key, value in params.items())

    def __missing__(self, key: str) -> object:
        raise KeyError(key)

