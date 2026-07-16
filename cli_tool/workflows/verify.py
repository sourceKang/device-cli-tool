from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from cli_tool.models import CliCommandOutput, CliCommandVerification, CliVerifyResult, tuple_of_strings
from cli_tool.parsers.text_tokens import missing_tokens
from cli_tool.transport.base import CliTransport


def verify_output_tokens(
    command: str,
    output: str,
    expected_tokens: Sequence[str],
    *,
    case_sensitive: bool = False,
) -> CliCommandVerification:
    expected = tuple_of_strings(expected_tokens)
    missing = tuple(missing_tokens(output, expected, case_sensitive=case_sensitive))
    validation_errors = () if output.strip() else ("CLI output is empty",)
    return CliCommandVerification(
        command=command,
        output=output,
        expected_tokens=expected,
        missing_tokens=missing,
        validation_errors=validation_errors,
    )


def verify_cli_outputs(
    outputs: Mapping[str, str] | Iterable[CliCommandOutput],
    expected_by_command: Mapping[str, Sequence[str]],
    *,
    case_sensitive: bool = False,
) -> CliVerifyResult:
    output_by_command = _coerce_output_by_command(outputs)
    verifications = tuple(
        verify_output_tokens(
            command,
            output_by_command.get(command, ""),
            expected_tokens,
            case_sensitive=case_sensitive,
        )
        for command, expected_tokens in expected_by_command.items()
    )
    return CliVerifyResult(verifications=verifications)


def run_and_verify(
    transport: CliTransport,
    commands: Sequence[str],
    expected_by_command: Mapping[str, Sequence[str]],
    *,
    owner: str | None = None,
    case_sensitive: bool = False,
) -> CliVerifyResult:
    outputs = transport.run_commands(commands, owner=owner)
    return verify_cli_outputs(outputs, expected_by_command, case_sensitive=case_sensitive)


def _coerce_output_by_command(outputs: Mapping[str, str] | Iterable[CliCommandOutput]) -> dict[str, str]:
    if isinstance(outputs, Mapping):
        return {str(command): str(output) for command, output in outputs.items()}
    return {item.command: item.output for item in outputs}
