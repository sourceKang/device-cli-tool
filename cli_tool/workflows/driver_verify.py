from __future__ import annotations

from collections.abc import Mapping

from cli_tool.devices.base import DeviceDriver
from cli_tool.models import CliVerifyResult
from cli_tool.transport.base import CliTransport
from cli_tool.workflows.verify import run_and_verify


def run_driver_verify(
    driver: DeviceDriver,
    transport: CliTransport,
    command_id: str,
    params: Mapping[str, object] | None = None,
    *,
    owner: str | None = None,
    case_sensitive: bool = False,
) -> CliVerifyResult:
    spec = driver.command_spec(command_id)
    if not spec.readonly:
        raise ValueError(f"{command_id} is not readonly; use a protected configure workflow")

    commands = driver.build_commands(command_id, params)
    expected_tokens = driver.expected_tokens(command_id, params)
    return run_and_verify(
        transport,
        commands,
        {command: expected_tokens for command in commands},
        owner=owner,
        case_sensitive=case_sensitive,
    )

