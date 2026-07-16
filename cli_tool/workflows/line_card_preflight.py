from __future__ import annotations

from collections.abc import Mapping

from cli_tool.devices.base import DeviceDriver
from cli_tool.parsers.line_card_status import LineCardSnapshot, parse_line_card_status
from cli_tool.transport.base import CliTransport
from cli_tool.workflows.driver_verify import run_driver_verify


DEFAULT_LINE_CARD_COMMAND_ID = "show_line_card_status"


def run_line_card_preflight(
    driver: DeviceDriver,
    transport: CliTransport,
    *,
    command_id: str = DEFAULT_LINE_CARD_COMMAND_ID,
    params: Mapping[str, object] | None = None,
    owner: str | None = None,
) -> LineCardSnapshot:
    """Run a catalog-approved line-card command and return a validated snapshot."""
    result = run_driver_verify(
        driver,
        transport,
        command_id,
        params,
        owner=owner,
    )
    if not result.passed:
        failures = {
            "missing_tokens": result.missing_by_command,
            "validation_errors": result.validation_errors_by_command,
        }
        raise RuntimeError(f"line-card preflight verification failed: {failures}")
    if len(result.output_by_command) != 1:
        raise RuntimeError("line-card preflight requires exactly one CLI command output")
    return parse_line_card_status(next(iter(result.output_by_command.values())))
