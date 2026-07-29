from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .application import ExecuteUserOpsBatchSendCommand, PreviewUserOpsBatchSendCommand
from .dto import BatchSendRequest


class OpsEnrollmentAudienceE2EGateway:
    def __init__(
        self,
        *,
        preview_command: Callable[[BatchSendRequest], dict[str, Any]] | None = None,
        execute_command: Callable[[BatchSendRequest], dict[str, Any]] | None = None,
    ) -> None:
        self._preview_command = preview_command or PreviewUserOpsBatchSendCommand()
        self._execute_command = execute_command or ExecuteUserOpsBatchSendCommand()

    def preview(self, *, package_id: int, content: str) -> dict[str, Any]:
        return self._preview_command(_batch_request(package_id=package_id, content=content, confirm=False))

    def execute(self, *, package_id: int, content: str, confirm: bool) -> dict[str, Any]:
        return self._execute_command(_batch_request(package_id=package_id, content=content, confirm=confirm))


def _batch_request(*, package_id: int, content: str, confirm: bool) -> BatchSendRequest:
    return BatchSendRequest(
        target_source="ai_audience_package",
        target_source_id=int(package_id),
        selection_mode="all_filtered",
        content=content,
        images=[],
        attachments=[],
        include_do_not_disturb=False,
        confirm=bool(confirm),
        operator="prod-e2e",
    )
