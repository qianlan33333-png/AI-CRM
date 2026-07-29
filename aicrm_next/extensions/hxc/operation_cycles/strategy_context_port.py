from __future__ import annotations

from typing import Any, Protocol


class OperationCycleExecutionContextPort(Protocol):
    def get_execution_context(self, strategy_key: str) -> dict[str, Any] | None: ...


class DefaultOperationCycleExecutionContextPort:
    def get_execution_context(self, strategy_key: str) -> dict[str, Any] | None:
        from .strategy_context import execution_context_contract

        return execution_context_contract(strategy_key)


def build_operation_cycle_execution_context_port() -> OperationCycleExecutionContextPort:
    return DefaultOperationCycleExecutionContextPort()


__all__ = [
    "DefaultOperationCycleExecutionContextPort",
    "OperationCycleExecutionContextPort",
    "build_operation_cycle_execution_context_port",
]
