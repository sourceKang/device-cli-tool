from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from cli_tool.parsers.text_tokens import normalize_text


SHOW_LINE_CARD_STATUS_COMMAND = "show lc st"
REQUIRED_LINE_CARD_FIELDS = ("Card Type", "Status", "FW Version")

_HEADER_PATTERNS = {
    "card_type": re.compile(r"\bCard\s+Type\b", re.IGNORECASE),
    "status": re.compile(r"\bStatus\b", re.IGNORECASE),
    "fw_version": re.compile(r"\bFW\s+Version\b", re.IGNORECASE),
}
_SEPARATOR_LINE = re.compile(r"^[\s=+|:-]+$")


class LineCardStatusParseError(ValueError):
    """Raised when line-card output cannot be trusted as a complete snapshot."""


@dataclass(frozen=True)
class LineCardRecord:
    slot: str
    card_type: str
    status: str
    fw_version: str


@dataclass(frozen=True)
class LineCardSnapshot:
    cards: tuple[LineCardRecord, ...]
    command: str = SHOW_LINE_CARD_STATUS_COMMAND
    schema_version: int = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "command": self.command,
            "cards": [asdict(card) for card in self.cards],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent)


def parse_line_card_status(output: str) -> LineCardSnapshot:
    """Parse a fixed-width ``show lc st`` table into a validated snapshot.

    The parser intentionally fails closed. Unknown layouts, empty output, missing
    required headers, missing values, and zero data rows are errors rather than an
    empty successful snapshot.
    """
    if not output.strip():
        raise LineCardStatusParseError("show lc st output is empty")

    lines = normalize_text(output).splitlines()
    header_index, positions = _find_header(lines)
    missing_headers = [
        label
        for key, label in zip(_HEADER_PATTERNS, REQUIRED_LINE_CARD_FIELDS, strict=True)
        if key not in positions
    ]
    if missing_headers:
        raise LineCardStatusParseError(
            "show lc st output is missing required field(s): " + ", ".join(missing_headers)
        )
    if header_index is None:
        raise LineCardStatusParseError("show lc st table header was not found")

    card_start = positions["card_type"]
    status_start = positions["status"]
    fw_start = positions["fw_version"]
    if not card_start < status_start < fw_start:
        raise LineCardStatusParseError(
            "unsupported show lc st column order; expected Card Type, Status, FW Version"
        )

    cards: list[LineCardRecord] = []
    for line_number, line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if not line.strip() or _SEPARATOR_LINE.fullmatch(line):
            continue
        record = LineCardRecord(
            slot=line[:card_start].strip(),
            card_type=line[card_start:status_start].strip(),
            status=line[status_start:fw_start].strip(),
            fw_version=line[fw_start:].strip(),
        )
        missing_values = [
            label
            for label, value in (
                ("Card Type", record.card_type),
                ("Status", record.status),
                ("FW Version", record.fw_version),
            )
            if not value
        ]
        if missing_values:
            raise LineCardStatusParseError(
                f"show lc st row {line_number} is missing required value(s): "
                + ", ".join(missing_values)
            )
        cards.append(record)

    if not cards:
        raise LineCardStatusParseError("show lc st output contains no line-card rows")
    return LineCardSnapshot(cards=tuple(cards))


def _find_header(lines: list[str]) -> tuple[int | None, dict[str, int]]:
    best_index: int | None = None
    best_positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        positions = {
            key: match.start()
            for key, pattern in _HEADER_PATTERNS.items()
            if (match := pattern.search(line)) is not None
        }
        if len(positions) > len(best_positions):
            best_index = index
            best_positions = positions
        if len(positions) == len(_HEADER_PATTERNS):
            return index, positions
    return best_index, best_positions
