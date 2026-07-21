from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

from aicrm_next.platform_foundation.auth_platform.context import AuthContext

from .commands import (
    QueueCommandConflict,
    QueueCommandResult,
    QueueCommandTarget,
    QueueRuntimeCommandService,
)


@dataclass(frozen=True)
class ManualQueueCommand:
    actor: str
    reason: str
    expected_version: str
    expected_status: str = ""
    command_id: str = ""
    duplicate_risk_confirmed: bool = False


class QueueCommandPayloadError(ValueError):
    def __init__(self, missing_fields: tuple[str, ...]) -> None:
        self.missing_fields = missing_fields
        super().__init__("manual_queue_command_fields_required")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def authenticated_queue_actor(request: Any) -> str:
    """Return the authenticated principal that owns a manual queue action."""

    context = getattr(getattr(request, "state", None), "auth_context", None)
    if isinstance(context, AuthContext):
        return _text(context.principal_id)
    return ""


def parse_manual_queue_command(
    payload: Mapping[str, Any],
    *,
    authenticated_actor: str = "",
) -> ManualQueueCommand:
    claimed_actor = _text(payload.get("actor"))
    values = {
        "actor": _text(authenticated_actor) or claimed_actor,
        "reason": _text(payload.get("reason")),
        "expected_version": _text(payload.get("expected_version")),
    }
    required_values = {**values, "actor": claimed_actor}
    missing = tuple(key for key, value in required_values.items() if not value)
    if missing:
        raise QueueCommandPayloadError(missing)
    return ManualQueueCommand(
        actor=values["actor"],
        reason=values["reason"],
        expected_version=values["expected_version"],
        expected_status=_text(payload.get("expected_status")),
        command_id=_text(payload.get("command_id")),
        duplicate_risk_confirmed=_bool(
            payload.get(
                "duplicate_risk_confirmed",
                payload.get("confirm_duplicate_risk"),
            )
        ),
    )


def submit_manual_queue_command(
    service: QueueRuntimeCommandService,
    target: QueueCommandTarget,
    command: ManualQueueCommand,
    *,
    source_route: str,
) -> QueueCommandResult:
    if command.expected_status and command.expected_status != target.status:
        raise QueueCommandConflict(
            "queue command expected_status does not match the current durable row"
        )
    return service.request_immediate_execution(
        target.queue_kind,
        target.item_id,
        expected_status=target.status,
        expected_version=command.expected_version,
        actor=command.actor,
        reason=command.reason,
        command_id=command.command_id,
        source_route=source_route,
    )


def accepted_queue_command_payload(
    result: QueueCommandResult,
    command: ManualQueueCommand,
) -> dict[str, Any]:
    target = result.target
    status_url = "/api/admin/executions/" + quote(target.execution_id, safe="")
    return {
        "ok": True,
        "accepted": True,
        "action": result.action,
        "queue_kind": target.queue_kind,
        "item_id": target.item_id,
        "execution_id": target.execution_id,
        "command_id": result.command_id,
        "intent_id": result.intent_id,
        "status_url": status_url,
        "lane": target.lane,
        "status": target.status,
        "expected_version": command.expected_version,
        "accepted_version": target.version_token,
        "actor": command.actor,
        "reason": command.reason,
        "duplicate_risk_confirmed": command.duplicate_risk_confirmed,
        "notification": dict(result.notification_payload),
        "real_external_call_executed": False,
    }


def submit_manual_queue_action(
    service: QueueRuntimeCommandService,
    target: QueueCommandTarget,
    command: ManualQueueCommand,
    *,
    action: str,
    source_route: str,
) -> QueueCommandResult:
    if command.expected_status and command.expected_status != target.status:
        raise QueueCommandConflict(
            "queue command expected_status does not match the current durable row"
        )
    return service.request_manual_action(
        target.queue_kind,
        target.item_id,
        action=action,
        expected_status=target.status,
        expected_version=command.expected_version,
        actor=command.actor,
        reason=command.reason,
        duplicate_risk_confirmed=command.duplicate_risk_confirmed,
        command_id=command.command_id,
        source_route=source_route,
    )


__all__ = [
    "ManualQueueCommand",
    "QueueCommandPayloadError",
    "accepted_queue_command_payload",
    "authenticated_queue_actor",
    "parse_manual_queue_command",
    "submit_manual_queue_command",
    "submit_manual_queue_action",
]
