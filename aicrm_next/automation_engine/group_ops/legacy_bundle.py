from __future__ import annotations

from typing import Any

from aicrm_next.shared.errors import ApplicationError, ContractError

from .domain import clean_text, normalize_message_content
from .dto import GroupOpsWebhookReceiveRequest
from .durable_effects_repository import plan_trusted_group_ops_bundle
from .external_effects import external_effect_response_defaults, parse_external_effect_scheduled_at
from .repo import GroupOpsRepository


class GroupOpsBundleConflictError(ApplicationError):
    status_code = 409


def receive_trusted_group_bundle(
    *,
    repo: GroupOpsRepository,
    plan: dict[str, Any],
    request: GroupOpsWebhookReceiveRequest,
    idempotency_key: str = "",
) -> tuple[dict[str, Any], int]:
    if clean_text(request.send_mode) not in {"queued"}:
        raise ContractError("send_mode v1 only supports queued")
    request_idempotency = clean_text(idempotency_key or request.idempotency_key)
    if not request_idempotency:
        raise ContractError("idempotency_key is required")
    parse_external_effect_scheduled_at(request.scheduled_at)
    duplicate = repo.find_webhook_event(int(plan["id"]), request_idempotency)
    if duplicate:
        duplicate = dict(duplicate)
        duplicate["status"] = "duplicate"
        chat_ids = _bound_chat_ids(repo, plan)
        duplicate_content = dict(duplicate.get("normalized_content_payload") or {})
        duplicate_content.update(
            {
                "channel": "wecom_customer_group",
                "chat_ids": chat_ids,
                "sender": clean_text(plan.get("owner_userid")),
            }
        )
        planned = _plan_bundle(
            plan=plan,
            event_id=int(duplicate["id"]),
            request_idempotency=request_idempotency,
            chat_ids=chat_ids,
            content_payload=duplicate_content,
            scheduled_at=duplicate.get("scheduled_at"),
            request=request,
        )
        return (
            {
                **external_effect_response_defaults(),
                "status": "duplicate",
                "event": duplicate,
                "execution_id": clean_text(planned.get("execution_id")),
                "status_url": clean_text(planned.get("status_url")),
                "external_effect_job_ids": [int(item) for item in planned.get("job_ids") or []],
                "broadcast_job_ids": [],
                "legacy_broadcast_job_ids": [],
            },
            200,
        )

    content = request.content or {}
    attachments = content.get("attachments") if isinstance(content.get("attachments"), list) else []
    normalized_content = normalize_message_content(
        text=content.get("text") or "",
        attachments=attachments,
        sender=clean_text(plan.get("owner_userid")),
    )
    chat_ids = _bound_chat_ids(repo, plan)
    if not chat_ids:
        raise GroupOpsBundleConflictError("webhook plan has no bound groups")
    event = repo.create_webhook_event(
        int(plan["id"]),
        {
            "idempotency_key": request_idempotency,
            "request_payload": request.model_dump(),
            "normalized_content_payload": normalized_content,
            "scheduled_at": request.scheduled_at or "",
            "status": "accepted",
        },
    )
    queue_content_payload = {
        **normalized_content,
        "channel": "wecom_customer_group",
        "chat_ids": chat_ids,
        "sender": clean_text(plan.get("owner_userid")),
    }
    planned = _plan_bundle(
        plan=plan,
        event_id=int(event["id"]),
        request_idempotency=request_idempotency,
        chat_ids=chat_ids,
        content_payload=queue_content_payload,
        scheduled_at=request.scheduled_at,
        request=request,
    )
    queued = repo.update_webhook_event(int(event["id"]), {"status": "queued", "broadcast_job_ids": []})
    return (
        {
            "status": "queued",
            "event": queued,
            "execution_id": clean_text(planned.get("execution_id")),
            "status_url": clean_text(planned.get("status_url")),
            "broadcast_job_ids": [],
            "legacy_broadcast_job_ids": [],
            "external_effect_job_ids": [int(item) for item in planned.get("job_ids") or []],
            "outbound_mode": "external_effect",
            "external_effect_send_mode": "wecom_group",
            "legacy_outbound_disabled": True,
            "external_effect_required": True,
            "real_external_call_executed": False,
            "wecom_send_executed": False,
            "real_wecom_call_executed": False,
            "real_group_notice_executed": False,
            "real_mention_all_executed": False,
        },
        202,
    )


def _bound_chat_ids(repo: GroupOpsRepository, plan: dict[str, Any]) -> list[str]:
    return [clean_text(item.get("chat_id")) for item in repo.list_bound_groups(int(plan["id"])) if clean_text(item.get("chat_id"))]


def _plan_bundle(
    *,
    plan: dict[str, Any],
    event_id: int,
    request_idempotency: str,
    chat_ids: list[str],
    content_payload: dict[str, Any],
    scheduled_at: Any,
    request: GroupOpsWebhookReceiveRequest,
) -> dict[str, Any]:
    return plan_trusted_group_ops_bundle(
        plan=plan,
        event_id=event_id,
        request_idempotency=request_idempotency,
        chat_ids=chat_ids,
        content_payload=content_payload,
        scheduled_at=scheduled_at,
        test_loopback=bool(request.external_effect_test_loopback),
        test_receiver_base_url=clean_text(request.test_receiver_base_url),
        test_receiver_response_status=int(request.test_receiver_response_status or 200),
    )


__all__ = ["GroupOpsBundleConflictError", "receive_trusted_group_bundle"]
