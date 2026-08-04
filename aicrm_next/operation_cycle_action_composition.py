from __future__ import annotations

from .extensions.growth.cloud_orchestrator.operation_cycle_action_port import (
    build_operation_cycle_action_plan_evidence_port,
)
from .extensions.hxc.operation_cycles.action_service import (
    OperationCycleActionDependencies,
    configure_operation_cycle_action_dependencies as _configure,
)


def configure_operation_cycle_action_dependencies() -> None:
    _configure(
        OperationCycleActionDependencies(
            plan_evidence_port=build_operation_cycle_action_plan_evidence_port(),
        )
    )


__all__ = ["configure_operation_cycle_action_dependencies"]
