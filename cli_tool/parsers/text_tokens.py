from __future__ import annotations

from collections.abc import Sequence


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def missing_tokens(output: str, expected_tokens: Sequence[str], *, case_sensitive: bool = False) -> list[str]:
    normalized_output = normalize_text(output)
    if not case_sensitive:
        normalized_output = normalized_output.casefold()

    missing = []
    for token in expected_tokens:
        if token in (None, ""):
            continue
        expected = normalize_text(str(token))
        haystack = normalized_output if case_sensitive else normalized_output
        needle = expected if case_sensitive else expected.casefold()
        if needle not in haystack:
            missing.append(str(token))
    return missing

