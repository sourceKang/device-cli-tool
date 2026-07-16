from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

from cli_tool.catalog.models import CommandCatalog, CommandSpec
from cli_tool.devices.base import DeviceDriver, build_driver


BUILTIN_CATALOG_PACKAGE = "cli_tool.catalog.data"
BUILTIN_CATALOG_NAMES = frozenset({"generic", "neox", "ies52xx", "olt140x"})


def load_catalog(path: str | Path) -> CommandCatalog:
    data = _load_yaml_mapping(path)
    return CommandCatalog(_command_specs_from_mapping(_commands_mapping(data, path)))


def load_driver(path: str | Path) -> DeviceDriver:
    data = _load_yaml_mapping(path)
    return _driver_from_mapping(data, path)


def load_driver_source(source: str | Path) -> DeviceDriver:
    if isinstance(source, str) and source.startswith("builtin:"):
        return load_builtin_driver(source.removeprefix("builtin:"))
    return load_driver(source)


def load_builtin_driver(name: str) -> DeviceDriver:
    normalized = name.strip().casefold().removesuffix(".yaml")
    if normalized not in BUILTIN_CATALOG_NAMES:
        available = ", ".join(sorted(BUILTIN_CATALOG_NAMES))
        raise ValueError(f"unknown built-in catalog {name!r}; available: {available}")
    source = f"builtin:{normalized}"
    resource = files(BUILTIN_CATALOG_PACKAGE).joinpath(f"{normalized}.yaml")
    data = _load_yaml_text(resource.read_text(encoding="utf-8"), source)
    return _driver_from_mapping(data, source)


def _driver_from_mapping(data: Mapping[str, Any], source: str | Path) -> DeviceDriver:
    family = _required_string(data, "family", source)
    model = _required_string(data, "model", source)
    return build_driver(
        family=family,
        model=model,
        specs=_command_specs_from_mapping(_commands_mapping(data, source)),
    )


def _load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    resolved = Path(path)
    return _load_yaml_text(resolved.read_text(encoding="utf-8"), resolved)


def _load_yaml_text(content: str, source: str | Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as error:
        raise RuntimeError("PyYAML is required to load CLI command catalogs") from error

    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError(f"{source}: top-level YAML value must be a mapping")
    return data


def _commands_mapping(data: Mapping[str, Any], path: str | Path) -> Mapping[str, Any]:
    commands = data.get("commands")
    if not isinstance(commands, Mapping):
        raise ValueError(f"{Path(path)}: commands must be a mapping")
    return commands


def _command_specs_from_mapping(commands: Mapping[str, Any]) -> list[CommandSpec]:
    specs = []
    for command_id, raw_spec in commands.items():
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("command id must be a non-empty string")
        if not isinstance(raw_spec, Mapping):
            raise ValueError(f"{command_id}: command spec must be a mapping")
        specs.append(_command_spec_from_mapping(command_id, raw_spec))
    return specs


def _command_spec_from_mapping(command_id: str, raw_spec: Mapping[str, Any]) -> CommandSpec:
    readonly = raw_spec.get("readonly")
    if not isinstance(readonly, bool):
        raise ValueError(f"{command_id}: readonly must be true or false")

    command = raw_spec.get("command")
    if command is not None and not isinstance(command, str):
        raise ValueError(f"{command_id}: command must be a string")

    return CommandSpec(
        command_id=command_id,
        readonly=readonly,
        mode=str(raw_spec.get("mode", "exec")),
        command=command,
        commands=_string_tuple(raw_spec.get("commands"), field=f"{command_id}.commands"),
        expected_tokens=_string_tuple(raw_spec.get("expected_tokens"), field=f"{command_id}.expected_tokens"),
    )


def _string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError(f"{field} must be a list of strings")
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must contain only strings")
    return tuple(value)


def _required_string(data: Mapping[str, Any], key: str, path: str | Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{Path(path)}: {key} must be a non-empty string")
    return value
