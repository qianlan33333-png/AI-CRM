from __future__ import annotations

from typing import Any, Protocol

from .action_dispatcher import GroupOpsActionDispatcher


class DispatchActionInput(dict):
    pass


class GroupOpsActionPort(Protocol):
    def dispatch(self, input_data: dict[str, Any]) -> dict[str, Any]: ...


class DefaultGroupOpsActionPort:
    def __init__(self, dispatcher: GroupOpsActionDispatcher | None = None) -> None:
        self._dispatcher = dispatcher or GroupOpsActionDispatcher()

    def dispatch(self, input_data: dict[str, Any]) -> dict[str, Any]:
        return self._dispatcher.dispatch(input_data)


def build_group_ops_action_port() -> GroupOpsActionPort:
    return DefaultGroupOpsActionPort()
