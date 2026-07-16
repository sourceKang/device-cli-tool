from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CliCommandOutput:
    command: str
    output: str


@dataclass(frozen=True)
class CliCommandVerification:
    command: str
    output: str
    expected_tokens: tuple[str, ...]
    missing_tokens: tuple[str, ...]
    validation_errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.missing_tokens and not self.validation_errors


@dataclass(frozen=True)
class CliVerifyResult:
    verifications: tuple[CliCommandVerification, ...]

    @property
    def passed(self) -> bool:
        return all(verification.passed for verification in self.verifications)

    @property
    def missing_by_command(self) -> dict[str, list[str]]:
        return {
            verification.command: list(verification.missing_tokens)
            for verification in self.verifications
            if verification.missing_tokens
        }

    @property
    def validation_errors_by_command(self) -> dict[str, list[str]]:
        return {
            verification.command: list(verification.validation_errors)
            for verification in self.verifications
            if verification.validation_errors
        }

    @property
    def output_by_command(self) -> dict[str, str]:
        return {verification.command: verification.output for verification in self.verifications}


def tuple_of_strings(values: Sequence[object]) -> tuple[str, ...]:
    return tuple(str(value) for value in values if value not in (None, ""))
