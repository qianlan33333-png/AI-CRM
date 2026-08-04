from __future__ import annotations

from typing import Any, Protocol


class OperationCycleActionPlanEvidencePort(Protocol):
    def verify_prepare_result(
        self,
        *,
        strategy_key: str,
        run_key: str,
        strategy_version: int,
        context_hash: str,
        result: dict[str, Any],
    ) -> dict[str, Any]: ...

    def get_plan_state(self, plan_id: str) -> dict[str, Any]: ...


class UnconfiguredOperationCycleActionPlanEvidencePort:
    def verify_prepare_result(self, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("operation-cycle action plan evidence port is not configured")

    def get_plan_state(self, plan_id: str) -> dict[str, Any]:
        del plan_id
        raise RuntimeError("operation-cycle action plan evidence port is not configured")


__all__ = [
    "OperationCycleActionPlanEvidencePort",
    "UnconfiguredOperationCycleActionPlanEvidencePort",
]
