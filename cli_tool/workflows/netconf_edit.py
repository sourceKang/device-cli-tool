from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cli_tool.safety.policy import (
    MutationAuthorization,
    validate_ip_literal,
    validate_node_key,
)
from cli_tool.transport.netconf import MUTATION_TARGET


LIVE_EXECUTION_NOTE = (
    "Live execution requires --run-mutating together with exact --confirm-node/--confirm-host. "
    "It always targets the candidate datastore behind lock/validate/confirmed-commit, never "
    "running directly, and defaults to a non-finalized (auto-reverting) commit unless "
    "--finalize-commit is explicit."
)


@dataclass(frozen=True)
class NetconfEditPlan:
    node_key: str
    host: str
    config_xml: str
    live_execution_status: str
    live_execution_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workflow": "netconf_edit_config_requested",
            "mode": "DRY_RUN",
            "target": {
                "node_key": self.node_key,
                "host": self.host,
                "datastore": MUTATION_TARGET,
            },
            "requested_config_xml": self.config_xml,
            "live_execution": {
                "status": self.live_execution_status,
                "note": self.live_execution_note,
                "requires_run_mutating": True,
                "requires_exact_node_host_confirmation": True,
                "always_targets_candidate": True,
                "finalizes_only_if_explicit": True,
            },
        }


def build_netconf_edit_plan(
    *,
    node_key: str,
    host: str,
    config_xml: str,
) -> NetconfEditPlan:
    validate_node_key(node_key)
    normalized_host = validate_ip_literal(host)
    if not config_xml.strip():
        raise ValueError("config_xml is required")
    return NetconfEditPlan(
        node_key=node_key,
        host=normalized_host,
        config_xml=config_xml,
        live_execution_status="AVAILABLE_VIA_RUN_MUTATING",
        live_execution_note=LIVE_EXECUTION_NOTE,
    )


def assert_dry_run_target_confirmation(
    plan: NetconfEditPlan,
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


def assert_live_mutation_authorized(
    *,
    node_key: str,
    host: str,
    confirmed_node: str,
    confirmed_host: str,
    run_mutating: bool,
) -> None:
    """Gate for actually executing execute_confirmed_commit() against a device.

    Reuses the same MutationAuthorization primitive xpon_remediation.py established:
    exact node/host confirmation plus an explicit --run-mutating. There is no further
    content allowlist here — the config_xml payload is arbitrary YANG data, so the safety
    boundary is procedural (candidate + validate + confirmed-commit), not content-based.
    """
    validate_node_key(node_key)
    normalized_host = validate_ip_literal(host)
    authorization = MutationAuthorization(
        expected_node=node_key,
        expected_host=normalized_host,
        confirmed_node=confirmed_node,
        confirmed_host=confirmed_host,
        run_mutating=run_mutating,
    )
    authorization.validate()
