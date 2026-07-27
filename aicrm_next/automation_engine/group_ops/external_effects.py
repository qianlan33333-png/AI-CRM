from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    GROUP_OPS_MESSAGE_LOOPBACK,
    GROUP_OPS_WEBHOOK_ACTION_LOOPBACK,
    WECOM_MESSAGE_GROUP_SEND,
)
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform.platform_foundation.external_effects.test_receiver import (
    TEST_RECEIVER_PATH_PREFIX,
    canonical_payload_hash,
)
from aicrm_next.platform.platform_foundation.external_effects.models import public_datetime, utcnow
from aicrm_next.platform.shared.errors import ContractError

from .domain import clean_text, mask_sensitive_payload

GROUP_OPS_EFFECT_ACTION_TYPES = {
    "send_group_message",
    "group_notice",
    "webhook_notify",
}
GROUP_OPS_GROUP_EFFECT_ACTION_TYPES = {"send_group_message", "group_notice"}


def group_ops_effect_action_type(action_type: str) -> bool:
    return clean_text(action_type).lower() in GROUP_OPS_EFFECT_ACTION_TYPES


def external_effect_response_defaults() -> dict[str, Any]:
    return {
        "external_effect_job_ids": [],
        "legacy_broadcast_job_ids": [],
        "outbound_mode": "external_effect",
        "legacy_outbound_disabled": True,
        "external_effect_required": True,
        "real_external_call_executed": False,
        "wecom_send_executed": False,
        "real_wecom_call_executed": False,
        "real_group_notice_executed": False,
        "real_mention_all_executed": False,
    }


def content_payload_summary(content_payload: dict[str, Any]) -> dict[str, Any]:
    payload = mask_sensitive_payload(dict(content_payload or {}))
    text = payload.get("text") if isinstance(payload.get("text"), dict) else {}
    attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
    chat_ids = payload.get("chat_ids") if isinstance(payload.get("chat_ids"), list) else []
    return {
        "channel": clean_text(payload.get("channel")),
        "sender_present": bool(clean_text(payload.get("sender"))),
        "chat_count": len(chat_ids),
        "text_length": len(clean_text(text.get("content"))),
        "attachment_count": len(attachments),
        "payload_keys": sorted(str(key) for key in payload.keys())[:20],
    }


def _stable_suffix(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return __import__("hashlib").sha1(raw.encode("utf-8")).hexdigest()[:12]


def parse_external_effect_scheduled_at(value: Any) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("scheduled_at must be an ISO datetime") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _loopback_payload(
    *,
    base_url: str,
    response_status: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    payload_hash = canonical_payload_hash(body)
    return {
        "webhook_url": f"{base_url.rstrip('/')}{TEST_RECEIVER_PATH_PREFIX}",
        "body": body,
        "receiver_response_status": int(response_status or 200),
        "test_receiver_expires_at": public_datetime(utcnow() + timedelta(hours=12)),
        "execution_scope": "test_loopback",
        "is_test": True,
        "expected_payload_hash": payload_hash,
    }


def plan_group_ops_external_effect(
    *,
    effect_type: str = GROUP_OPS_MESSAGE_LOOPBACK,
    plan_id: int,
    target_type: str,
    target_id: str,
    business_id: str = "",
    node_id: int | str = "",
    trigger_event_id: str = "",
    chat_ids: list[str] | None = None,
    content_summary: str = "",
    content_payload: dict[str, Any] | None = None,
    operator_member_id: str = "",
    owner_userid: str = "",
    webhook_key: str = "",
    source_module: str = "automation_engine.group_ops",
    source_route: str = "",
    source_event_id: str = "",
    source_command_id: str = "",
    idempotency_key: str = "",
    test_loopback: bool = False,
    test_receiver_base_url: str = "",
    test_receiver_response_status: int = 200,
    scheduled_at: Any = None,
) -> dict[str, Any]:
    if not test_loopback and effect_type == GROUP_OPS_MESSAGE_LOOPBACK:
        effect_type = WECOM_MESSAGE_GROUP_SEND
    execution_mode = "execute"
    status = "queued"
    parsed_scheduled_at = parse_external_effect_scheduled_at(scheduled_at)

    chat_ids = [clean_text(item) for item in list(chat_ids or []) if clean_text(item)]
    content_payload = dict(content_payload or {})
    content_payload_redacted = mask_sensitive_payload(content_payload)
    body = {
        "synthetic": bool(test_loopback),
        "source": source_module,
        "effect_type": effect_type,
        "plan_id": int(plan_id or 0),
        "node_id": clean_text(node_id),
        "trigger_event_id": clean_text(trigger_event_id),
        "chat_ids": chat_ids,
        "content_summary": clean_text(content_summary)[:500],
        "content_payload": content_payload_summary(content_payload),
        "operator_member_id": clean_text(operator_member_id),
        "owner_userid": clean_text(owner_userid) or clean_text(operator_member_id),
        "webhook_key": clean_text(webhook_key),
        "test_only": bool(test_loopback),
    }
    payload: dict[str, Any] = {
        "body": body,
        "plan_id": int(plan_id or 0),
        "node_id": clean_text(node_id),
        "trigger_event_id": clean_text(trigger_event_id),
        "chat_ids": chat_ids,
        "content_summary": clean_text(content_summary)[:500],
        "content_payload": content_payload_redacted,
        "operator_member_id": clean_text(operator_member_id),
        "owner_userid": clean_text(owner_userid) or clean_text(operator_member_id),
        "webhook_key": clean_text(webhook_key),
        "mention_all": False,
        "is_mention_all": False,
        "wecom_send_executed": False,
    }
    if test_loopback and clean_text(test_receiver_base_url):
        payload.update(
            _loopback_payload(
                base_url=clean_text(test_receiver_base_url),
                response_status=int(test_receiver_response_status or 200),
                body=body,
            )
        )
    payload_summary = {
        "plan_id": int(plan_id or 0),
        "node_id": clean_text(node_id),
        "trigger_event_id": clean_text(trigger_event_id),
        "chat_count": len(chat_ids),
        "content_summary": clean_text(content_summary)[:200],
        "content_payload": content_payload_summary(content_payload),
        "operator_member_id_present": bool(clean_text(operator_member_id)),
        "owner_userid_present": bool(clean_text(owner_userid) or clean_text(operator_member_id)),
        "webhook_key": clean_text(webhook_key),
        "execution_scope": payload.get("execution_scope", ""),
        "receiver_response_status": payload.get("receiver_response_status", 0),
        "expected_payload_hash": payload.get("expected_payload_hash", ""),
        "wecom_send_executed": False,
    }
    suffix = _stable_suffix(
        {
            "effect_type": effect_type,
            "plan_id": plan_id,
            "target_type": target_type,
            "target_id": target_id,
            "node_id": node_id,
            "trigger_event_id": trigger_event_id,
            "chat_ids": chat_ids,
            "source_command_id": source_command_id,
        }
    )
    key = clean_text(idempotency_key) or f"group-ops-external-effect:{effect_type}:{target_type}:{target_id}:{suffix}"
    return ExternalEffectService().plan_effect(
        effect_type=effect_type,
        adapter_name="wecom_group_message" if effect_type == WECOM_MESSAGE_GROUP_SEND else "outbound_webhook",
        operation="send_group_message" if effect_type == WECOM_MESSAGE_GROUP_SEND else "post",
        target_type=target_type,
        target_id=clean_text(target_id) or clean_text(node_id) or clean_text(trigger_event_id) or str(plan_id),
        business_type="group_ops_plan",
        business_id=clean_text(business_id) or str(plan_id),
        payload=payload,
        payload_summary=payload_summary,
        context=CommandContext(
            actor_id=clean_text(operator_member_id) or "group_ops",
            actor_type="system",
            request_id=key,
            trace_id=key,
            source_route=source_route,
        ),
        source_module=source_module,
        source_event_id=source_event_id,
        source_command_id=source_command_id,
        risk_level="medium",
        execution_mode=execution_mode,
        status=status,
        scheduled_at=parsed_scheduled_at,
        idempotency_key=key,
    )


def plan_group_ops_action_effect(
    *,
    plan_id: int,
    trigger_event_id: str,
    recipient: dict[str, Any],
    action: dict[str, Any],
    operator_member_id: str,
    source_route: str,
    idempotency_key: str,
    owner_userid: str = "",
    webhook_key: str = "",
    test_loopback: bool = False,
    test_receiver_base_url: str = "",
    test_receiver_response_status: int = 200,
) -> dict[str, Any]:
    action_type = clean_text(action.get("action_type"))
    if not group_ops_effect_action_type(action_type):
        raise ContractError(f"action type does not plan an external effect: {action_type}")
    chat_ids = [
        clean_text(action.get("chat_id")),
        clean_text(recipient.get("group_id")),
    ]
    chat_ids.extend(clean_text(item) for item in list(action.get("chat_ids") or []) if clean_text(item))
    target_id = clean_text(trigger_event_id) or f"plan_{int(plan_id or 0)}"
    effect_type = (
        GROUP_OPS_MESSAGE_LOOPBACK
        if test_loopback and action_type in GROUP_OPS_GROUP_EFFECT_ACTION_TYPES
        else WECOM_MESSAGE_GROUP_SEND
        if action_type in GROUP_OPS_GROUP_EFFECT_ACTION_TYPES
        else GROUP_OPS_WEBHOOK_ACTION_LOOPBACK
    )
    return plan_group_ops_external_effect(
        effect_type=effect_type,
        plan_id=plan_id,
        target_type="group_ops_trigger_event",
        target_id=target_id,
        business_id=str(plan_id),
        trigger_event_id=trigger_event_id,
        chat_ids=list(dict.fromkeys(item for item in chat_ids if item)),
        content_summary=clean_text(action.get("content") or action.get("title") or action_type),
        content_payload={"action": action, "recipient": recipient},
        operator_member_id=operator_member_id,
        owner_userid=clean_text(owner_userid) or operator_member_id,
        webhook_key=webhook_key,
        source_module="automation_engine.group_ops.webhook",
        source_route=source_route,
        source_event_id=trigger_event_id,
        source_command_id=action_type,
        idempotency_key=f"group-ops-webhook-action:{plan_id}:{trigger_event_id}:{clean_text(recipient.get('external_user_id') or recipient.get('group_id'))}:{action_type}:{clean_text(idempotency_key)}",
        test_loopback=test_loopback,
        test_receiver_base_url=test_receiver_base_url,
        test_receiver_response_status=test_receiver_response_status,
    )
