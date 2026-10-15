from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Sequence


SAFE_NODE_KEY = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
SAFE_DEVICE_FAMILY = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
SAFE_INTERFACE = re.compile(r"interface xpon ([1-9][0-9]?)-([1-9][0-9]?)\Z")
ALLOWED_FIXED_COMMANDS = {
    "configure",
    "inactive",
    "register-method A",
    "no inactive",
    "exit",
}


@dataclass(frozen=True)
class MutationAuthorization:
    expected_node: str
    expected_host: str
    confirmed_node: str
    confirmed_host: str
    run_mutating: bool

    def validate(self) -> None:
        validate_node_key(self.expected_node)
        validate_ip_literal(self.expected_host)
        if not self.run_mutating:
            raise PermissionError("mutating workflow requires --run-mutating")
        if self.confirmed_node != self.expected_node:
            raise PermissionError("confirmed node does not exactly match the expected node")
        if self.confirmed_host != self.expected_host:
            raise PermissionError("confirmed host does not exactly match the expected host")


def validate_node_key(value: str) -> str:
    if not SAFE_NODE_KEY.fullmatch(value):
        raise ValueError(f"unsafe node key: {value!r}")
    return value


def validate_device_family(value: str) -> str:
    if not SAFE_DEVICE_FAMILY.fullmatch(value):
        raise ValueError(f"unsafe device family: {value!r}")
    return value


def validate_ip_literal(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise ValueError(f"host must be an IP literal; actual={value!r}") from error


def validate_xpon_target(*, slot: int, port: int) -> None:
    limits = {"slot": (slot, 64), "port": (port, 16)}
    for name, (value, maximum) in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"xPON {name} must be an integer from 1 to {maximum}; actual={value!r}")


def validate_xpon_mutation_commands(commands: Sequence[str], *, slot: int, port: int) -> tuple[str, ...]:
    validate_xpon_target(slot=slot, port=port)
    command_tuple = tuple(commands)
    expected = (
        "configure",
        f"interface xpon {slot}-{port}",
        "inactive",
        "register-method A",
        "no inactive",
        "exit",
    )
    if command_tuple != expected:
        raise PermissionError("xPON mutation commands do not exactly match the protected allowlist sequence")
    for command in command_tuple:
        if command in ALLOWED_FIXED_COMMANDS:
            continue
        match = SAFE_INTERFACE.fullmatch(command)
        if match and (int(match.group(1)), int(match.group(2))) == (slot, port):
            continue
        raise PermissionError(f"command is outside the protected xPON allowlist: {command!r}")
    return command_tuple
