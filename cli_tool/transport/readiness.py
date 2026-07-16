from __future__ import annotations

import re


ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEFAULT_PROMPT_PATTERN = re.compile(
    r"(?m)^(?P<prompt>[^\r\n]{1,200}[#>$%])\s*$"
)


def normalize_stream_text(output: str) -> str:
    """Normalize terminal control characters without changing column alignment."""
    return ANSI_ESCAPE.sub("", output).replace("\r\n", "\n").replace("\r", "\n")


def detect_prompt(output: str) -> str | None:
    """Return the final CLI prompt when the stream currently ends at a prompt."""
    normalized = normalize_stream_text(output)
    matches = list(DEFAULT_PROMPT_PATTERN.finditer(normalized))
    if not matches:
        return None
    match = matches[-1]
    if normalized[match.end() :].strip():
        return None
    return match.group("prompt").strip()


def ends_with_prompt(output: str, prompt: str) -> bool:
    normalized = normalize_stream_text(output)
    return re.search(rf"(?:^|\n){re.escape(prompt)}[ \t]*\Z", normalized) is not None


def strip_command_envelope(output: str, command: str, prompt: str) -> str:
    """Remove command echo and the terminal prompt from a completed response."""
    lines = normalize_stream_text(output).splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if lines and lines[-1].strip() == prompt:
        lines.pop()

    if lines:
        first = lines[0].strip()
        if first == command:
            lines.pop(0)
        elif first == f"{prompt}{command}" or first == f"{prompt} {command}":
            lines.pop(0)

    return "\n".join(line.rstrip() for line in lines).strip()
