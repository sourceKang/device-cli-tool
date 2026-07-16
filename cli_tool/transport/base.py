from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from cli_tool.models import CliCommandOutput


class CliTransport(Protocol):
    def run_commands(self, commands: Sequence[str], *, owner: str | None = None) -> list[CliCommandOutput]:
        ...

