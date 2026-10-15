from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cli_tool.safety.policy import (
    MutationAuthorization,
    validate_device_family,
    validate_ip_literal,
    validate_node_key,
    validate_xpon_mutation_commands,
    validate_xpon_target,
)


SHOW_UNREGISTERED = "show in remote xont unreg"
SHOW_SUMMARY = "show in remote xont summary"


@dataclass(frozen=True)
class XponRemediationPlan:
    device_family: str
    node_key: str
    host: str
    slot: int
    port: int
    precheck_commands: tuple[str, ...]
    mutation_commands: tuple[str, ...]
    postcheck_commands: tuple[str, ...]
    live_execution_status: str
    live_execution_blocker: str

    @property
    def interface(self) -> str:
        return f"xpon {self.slot}-{self.port}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workflow": "xpon_register_method_a_requested",
            "mode": "DRY_RUN",
            "target": {
                "node_key": self.node_key,
                "device_family": self.device_family,
                "host": self.host,
                "slot": self.slot,
                "port": self.port,
                "interface": self.interface,
            },
            "precheck_commands": list(self.precheck_commands),
            "mutation_commands": list(self.mutation_commands),
            "postcheck_commands": list(self.postcheck_commands),
            "live_execution": {
                "status": self.live_execution_status,
                "blocker": self.live_execution_blocker,
                "requires_run_mutating": True,
                "requires_exact_node_host_confirmation": True,
                "requires_original_state_for_rollback": True,
            },
        }


def build_register_method_a_plan(
    *,
    device_family: str,
    node_key: str,
    host: str,
    slot: int,
    port: int,
) -> XponRemediationPlan:
    validate_device_family(device_family)
    validate_node_key(node_key)
    normalized_host = validate_ip_literal(host)
    validate_xpon_target(slot=slot, port=port)
    mutation_commands = validate_xpon_mutation_commands(
        (
            "configure",
            f"interface xpon {slot}-{port}",
            "inactive",
            "register-method A",
            "no inactive",
            "exit",
        ),
        slot=slot,
        port=port,
    )
    blocker = (
        "The read-only command/parser for the original register-method and inactive state "
        "has not been verified on the target family; rollback cannot yet be guaranteed."
    )
    return XponRemediationPlan(
        device_family=device_family,
        node_key=node_key,
        host=normalized_host,
        slot=slot,
        port=port,
        precheck_commands=(SHOW_UNREGISTERED, SHOW_SUMMARY),
        mutation_commands=mutation_commands,
        postcheck_commands=(SHOW_UNREGISTERED, SHOW_SUMMARY),
        live_execution_status="LOCKED",
        live_execution_blocker=blocker,
    )


def assert_dry_run_target_confirmation(
    plan: XponRemediationPlan,
    *,
    confirmed_node: str,
    confirmed_host: str,
) -> None:
    authorization = MutationAuthorization(
        expected_node=plan.node_key,
        expected_host=plan.host,
        confirmed_node=confirmed_node,
        confirmed_host=confirmed_host,
        run_mutating=True,
    )
    authorization.validate()


def assert_live_execution_unlocked(plan: XponRemediationPlan, authorization: MutationAuthorization) -> None:
    authorization.validate()
    validate_xpon_mutation_commands(plan.mutation_commands, slot=plan.slot, port=plan.port)
    if plan.live_execution_status != "UNLOCKED":
        raise PermissionError(f"live execution is locked: {plan.live_execution_blocker}")
