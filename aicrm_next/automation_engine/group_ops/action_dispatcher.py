from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from aicrm_next.identity_contact.dto import IdentityResolveResult, ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import classify_identity_candidates, resolve_identity_with_dbapi, resolved_unionid
from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.external_effects import WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.shared.errors import ContractError
from aicrm_next.shared.postgres_connection import get_db
from aicrm_next.shared.runtime import database_mode

from .domain import clean_text, normalize_action_payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _recipient_external_userid(input_data: dict[str, Any]) -> str:
    recipient = input_data.get("recipient") if isinstance(input_data.get("recipient"), dict) else {}
    return clean_text(recipient.get("externalUserId") or recipient.get("external_user_id") or recipient.get("external_userid"))


def _recipient_unionid(input_data: dict[str, Any]) -> str:
    recipient = input_data.get("recipient") if isinstance(input_data.get("recipient"), dict) else {}
    return clean_text(recipient.get("unionid") or recipient.get("unionId") or input_data.get("unionid") or input_data.get("unionId"))


def _default_identity_resolver(request: ResolvePersonIdentityRequest) -> IdentityResolveResult:
    if database_mode() == "postgres":
        return resolve_identity_with_dbapi(get_db(), request, placeholder="?")
    external_userid = clean_text(request.external_userid)
    unionid = clean_text(request.unionid)
    if not unionid and external_userid:
        unionid = f"fixture_unionid_{hashlib.sha256(external_userid.encode('utf-8')).hexdigest()[:16]}"
    if not unionid:
        return IdentityResolveResult(status="not_found", reason="identity_not_found")
    return classify_identity_candidates(
        request,
        [
            {
                "unionid": unionid,
                "external_userid": external_userid,
                "status": "active",
                "matched_unionid": bool(request.unionid),
                "matched_external_userid": bool(request.external_userid),
                "matched_openid": False,
                "matched_mobile": False,
            }
        ],
    )


def _recipient_snapshot(input_data: dict[str, Any]) -> dict[str, str]:
    recipient = input_data.get("recipient") if isinstance(input_data.get("recipient"), dict) else {}
    return {
        "user_id": clean_text(recipient.get("userId") or recipient.get("user_id")),
        "external_user_id": _recipient_external_userid(input_data),
        "unionid": _recipient_unionid(input_data),
        "wechat_user_id": clean_text(recipient.get("wechatUserId") or recipient.get("wechat_user_id")),
        "group_id": clean_text(recipient.get("groupId") or recipient.get("group_id")),
    }


def _operator(input_data: dict[str, Any]) -> str:
    return clean_text(
        input_data.get("operatorMemberId") or input_data.get("operator_member_id") or input_data.get("operatorAccount") or input_data.get("operator_account")
    )


def _created_by(input_data: dict[str, Any]) -> str:
    return clean_text(input_data.get("operatorAccount") or input_data.get("operator_account") or "group_ops_webhook")


def _action_idempotency_key(input_data: dict[str, Any], action: dict[str, Any]) -> str:
    plan_id = int(input_data.get("planId") or input_data.get("plan_id") or 0)
    trigger_event_id = clean_text(input_data.get("triggerEventId") or input_data.get("trigger_event_id"))
    external_userid = _recipient_external_userid(input_data)
    return f"group_ops:{plan_id}:{trigger_event_id}:{external_userid}:{action['action_type']}"


@dataclass(frozen=True)
class GroupOpsActionCommand:
    plan_id: int
    trigger_event_id: str
    external_userid: str
    unionid: str
    sender: str
    created_by: str
    content: str
    action: dict[str, Any]
    recipient: dict[str, str]
    idempotency_key: str


class GroupOpsActionAudit:
    def record(
        self, *, command: GroupOpsActionCommand, action_type: str, status: str, side_effect_executed: bool, detail: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "audit_type": "group_ops_action",
            "plan_id": int(command.plan_id),
            "trigger_event_id": command.trigger_event_id,
            "action_type": action_type,
            "idempotency_key": command.idempotency_key,
            "external_userid": command.external_userid,
            "status": status,
            "side_effect_executed": bool(side_effect_executed),
            "detail": dict(detail or {}),
        }


class NextOutboundMessageQueueGateway:
    def __init__(
        self,
        *,
        insert_job: Callable[..., int] | None = None,
        fetch_job_by_idempotency_key: Callable[[str], dict[str, Any] | None] | None = None,
        external_effect_service: ExternalEffectService | None = None,
    ) -> None:
        self._insert_job = insert_job
        self._fetch_job_by_idempotency_key = fetch_job_by_idempotency_key
        self._external_effect_service = external_effect_service or ExternalEffectService()

    def enqueue_private_message(self, command: GroupOpsActionCommand) -> dict[str, Any]:
        payload = {
            "channel": "wecom_private",
            "sender": command.sender,
            "unionids": [command.unionid] if command.unionid else [],
            "text": {"content": command.content} if command.content else {},
            "action": command.action,
            "recipient": command.recipient,
        }
        source_id = f"{command.plan_id}:trigger:{command.trigger_event_id}:{command.external_userid}:{command.action['action_type']}"
        if self._insert_job is not None:
            job_id = int(self._insert_job(command=command, source_id=source_id, payload=payload) or 0)
            return {"status": "queued" if job_id else "duplicate", "job_id": job_id, "source_id": source_id, "content_payload": payload}
        planned = self._external_effect_service.plan_effect(
            effect_type=WECOM_MESSAGE_PRIVATE_SEND,
            adapter_name="wecom_private_message",
            operation="send_private_message",
            target_type="external_contact",
            target_id=command.external_userid,
            business_type="group_ops_action",
            business_id=f"{command.plan_id}:{command.trigger_event_id}",
            payload={
                "channel": "wecom_private",
                "owner_userid": command.sender,
                "sender": command.sender,
                "external_userids": [command.external_userid],
                "target_unionid": command.unionid,
                "content_text": command.content,
                "attachments": [],
                "source": "automation_engine.group_ops.action_dispatcher",
            },
            payload_summary={
                "channel": "wecom_private",
                "external_userid_count": 1,
                "content_text_length": len(command.content),
                "target_unionid_present": bool(command.unionid),
            },
            context=CommandContext(
                actor_id=command.sender or command.created_by,
                actor_type="system",
                request_id=command.idempotency_key,
                trace_id=command.idempotency_key,
                source_route="/api/automation/group-ops/webhooks/{webhook_key}",
            ),
            source_module="automation_engine.group_ops.action_dispatcher",
            source_event_id=command.trigger_event_id,
            source_command_id=source_id,
            risk_level="medium",
            execution_mode="execute",
            status="queued",
            idempotency_key=f"group-ops-private-effect:{command.idempotency_key}",
            lane="wecom_interactive",
            ordering_key=f"group_ops_contact:{command.external_userid}",
            fairness_key=f"group_ops_plan:{command.plan_id}",
        )
        return {
            "status": "queued" if planned.get("created_on_plan") is not False else "duplicate",
            "job_id": int(planned.get("id") or 0),
            "source_id": source_id,
            "content_payload": payload,
            "execution_id": clean_text(planned.get("execution_id")),
            "execution_owner": "external_effect_job",
        }


class GroupOpsActionDispatcher:
    def __init__(
        self,
        *,
        queue_gateway: NextOutboundMessageQueueGateway | None = None,
        audit: GroupOpsActionAudit | None = None,
        identity_resolver: Callable[[ResolvePersonIdentityRequest], IdentityResolveResult] | None = None,
    ) -> None:
        self._queue_gateway = queue_gateway or NextOutboundMessageQueueGateway()
        self._audit = audit or GroupOpsActionAudit()
        self._identity_resolver = identity_resolver or _default_identity_resolver

    def dispatch(self, input_data: dict[str, Any]) -> dict[str, Any]:
        action = normalize_action_payload(input_data.get("action"), default_action_type="record_only")
        action_type = action["action_type"]
        command = self._command(input_data, action)
        if action_type == "record_only":
            audit = self._audit.record(command=command, action_type=action_type, status="recorded", side_effect_executed=False)
            return {"ok": True, "status": "recorded", "action_ref_id": "", "side_effect_executed": False, "audit": audit}
        if action_type in {"enqueue", "publish_task", "send_message"}:
            queued = self._queue_gateway.enqueue_private_message(command)
            audit = self._audit.record(command=command, action_type=action_type, status=queued["status"], side_effect_executed=False, detail=queued)
            return {
                "ok": True,
                "status": queued["status"],
                "action_ref_id": str(queued.get("job_id") or ""),
                "execution_id": clean_text(queued.get("execution_id")),
                "execution_owner": clean_text(queued.get("execution_owner")),
                "side_effect_executed": False,
                "audit": audit,
            }
        if action_type == "add_to_audience":
            audit = self._audit.record(command=command, action_type=action_type, status="added", side_effect_executed=False)
            return {
                "ok": True,
                "status": "added",
                "action_ref_id": action.get("audience_id") or "",
                "side_effect_executed": False,
                "audit": audit,
            }
        if action_type in {"send_group_message", "group_notice", "webhook_notify"}:
            audit = self._audit.record(command=command, action_type=action_type, status="planned", side_effect_executed=False)
            return {
                "ok": True,
                "status": "planned",
                "action_ref_id": "",
                "side_effect_executed": False,
                "wecom_send_executed": False,
                "real_group_notice_executed": False,
                "audit": audit,
            }
        raise ContractError(f"unsupported group ops action: {action_type}")

    def _command(self, input_data: dict[str, Any], action: dict[str, Any]) -> GroupOpsActionCommand:
        external_userid = _recipient_external_userid(input_data)
        if not external_userid and action["action_type"] in {"enqueue", "publish_task", "send_message"}:
            raise ContractError(f"external_user_id is required for {action['action_type']}")
        resolution = self._identity_resolver(
            ResolvePersonIdentityRequest(
                unionid=_recipient_unionid(input_data) or None,
                external_userid=external_userid or None,
            )
        )
        unionid = resolved_unionid(resolution)
        if action["action_type"] in {"enqueue", "publish_task", "send_message"} and not unionid:
            raise ContractError("identity_pending_unionid")
        content = clean_text(action.get("content"))
        if action["action_type"] == "send_message" and not content:
            raise ContractError("content is required for send_message")
        sender = _operator(input_data)
        if action["action_type"] == "send_message" and not sender:
            raise ContractError("operatorMemberId or operatorAccount is required for send_message")
        recipient = _recipient_snapshot(input_data)
        if unionid:
            recipient["unionid"] = unionid
        return GroupOpsActionCommand(
            plan_id=int(input_data.get("planId") or input_data.get("plan_id") or 0),
            trigger_event_id=clean_text(input_data.get("triggerEventId") or input_data.get("trigger_event_id")),
            external_userid=external_userid,
            unionid=unionid,
            sender=sender,
            created_by=_created_by(input_data),
            content=content,
            action=dict(action),
            recipient=recipient,
            idempotency_key=_action_idempotency_key(input_data, action),
        )
