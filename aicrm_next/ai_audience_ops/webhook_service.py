from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from aicrm_next.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, WECOM_MESSAGE_PRIVATE_SEND
from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest
from aicrm_next.platform_foundation.internal_events.outbox import enqueue_internal_event_outbox_in_session
from aicrm_next.send_content.application import normalize_send_content_package
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime_settings import runtime_bool

from .repository import AudienceRepository, build_audience_repository, _json_dumps, _text
from .event_types import INBOUND_RECEIVED_EVENT


class AudienceInboundWebhookService:
    def __init__(
        self,
        repository: AudienceRepository | None = None,
        external_effects: ExternalEffectService | None = None,
    ):
        self._repo = repository or build_audience_repository()
        self._external_effects = external_effects or ExternalEffectService()

    def handle(self, package_key: str, payload: dict[str, Any], *, raw_body: bytes) -> dict[str, Any]:
        package = self._repo.get_package_by_key(package_key)
        if not package:
            return {"ok": False, "error": "package_not_found"}
        normalized = dict(payload or {})
        normalized["idempotency_key"] = self._idempotency_key(package, normalized)
        execution_id = "exe_ai_audience_inbound_" + uuid4().hex
        with get_session_factory()() as session:
            recorded = session.execute(
                text(
                    """
                    INSERT INTO ai_audience_inbound_webhook_event (
                        package_id, external_event_id, member_event_id, status,
                        message_json, action_json, payload_json, signature_valid,
                        idempotency_key, external_effect_job_id,
                        execution_id, parent_execution_id, lane, available_at,
                        created_at
                    ) VALUES (
                        :package_id, :external_event_id, :member_event_id, 'received',
                        CAST(:message_json AS jsonb), CAST(:action_json AS jsonb),
                        CAST(:payload_json AS jsonb), TRUE,
                        :idempotency_key, NULL,
                        :execution_id, '', 'internal_general', CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING *
                    """
                ),
                {
                    "package_id": int(package["id"]),
                    "external_event_id": _text(normalized.get("external_event_id")),
                    "member_event_id": int(normalized.get("member_event_id") or 0) or None,
                    "message_json": _json_dumps(normalized.get("message") or {}),
                    "action_json": _json_dumps(normalized.get("action") or {}),
                    "payload_json": _json_dumps(normalized),
                    "idempotency_key": normalized["idempotency_key"],
                    "execution_id": execution_id,
                },
            ).mappings().fetchone()
            created = recorded is not None
            if not recorded:
                recorded = session.execute(
                    text("SELECT * FROM ai_audience_inbound_webhook_event WHERE idempotency_key = :idempotency_key"),
                    {"idempotency_key": normalized["idempotency_key"]},
                ).mappings().one()
            signal = None
            if created:
                signal = enqueue_internal_event_outbox_in_session(
                    session,
                    InternalEventCreateRequest(
                        event_type=INBOUND_RECEIVED_EVENT,
                        aggregate_type="ai_audience_inbound_webhook_event",
                        aggregate_id=str(int(recorded["id"])),
                        subject_type="ai_audience_package",
                        subject_id=str(int(package["id"])),
                        idempotency_key=f"ai_audience.inbound.received:{int(recorded['id'])}",
                        source_module="ai_audience_ops.webhook_service",
                        payload={"inbound_event_id": int(recorded["id"]), "package_id": int(package["id"])},
                        payload_summary={"inbound_event_id": int(recorded["id"]), "package_id": int(package["id"]), "lane": "internal_general"},
                        context=CommandContext(
                            actor_id="ai_audience_inbound_webhook",
                            actor_type="external_agent",
                            source_route="ai_audience.inbound_webhook",
                        ),
                        execution_id=execution_id,
                        parent_execution_id="",
                    ),
                )
            session.commit()
        return {
            "ok": True,
            "accepted": True,
            "deduplicated": not created,
            "recorded": {
                "id": int(recorded["id"]),
                "package_id": int(recorded["package_id"]),
                "status": _text(recorded.get("status")),
                "execution_id": _text(recorded.get("execution_id")),
                "parent_execution_id": _text(recorded.get("parent_execution_id")),
                "created_at": recorded.get("created_at"),
            },
            "signal": dict(signal) if signal else None,
            "execution_id": _text(recorded.get("execution_id")),
            "automation_send_plan": None,
            "external_effect_job_id": recorded.get("external_effect_job_id"),
            "record_only": True,
            "real_external_call_executed": False,
        }

    def process_record(self, inbound_event_id: int, *, parent_execution_id: str = "") -> dict[str, Any]:
        with get_session_factory()() as session:
            recorded = session.execute(
                text("SELECT * FROM ai_audience_inbound_webhook_event WHERE id = :id FOR UPDATE"),
                {"id": int(inbound_event_id)},
            ).mappings().fetchone()
            if not recorded:
                return {"ok": False, "error": "inbound_event_not_found", "real_external_call_executed": False}
            if recorded.get("processed_at") is not None:
                return {
                    "ok": True,
                    "deduplicated": True,
                    "inbound_event_id": int(inbound_event_id),
                    "external_effect_job_id": recorded.get("external_effect_job_id"),
                    "real_external_call_executed": False,
                }
            payload = recorded.get("payload_json") if isinstance(recorded.get("payload_json"), dict) else {}
            package = session.execute(
                text("SELECT * FROM ai_audience_package WHERE id = :package_id"),
                {"package_id": int(recorded["package_id"])},
            ).mappings().one()
            session.commit()
        automation_send_plan = self._maybe_enqueue_automation_send_plan(dict(package), dict(payload))
        external_effect_job_id = self._maybe_plan_action(
            dict(package),
            dict(payload),
            parent_execution_id=_text(parent_execution_id) or _text(recorded.get("execution_id")),
        )
        with get_session_factory()() as session:
            finalized = session.execute(
                text(
                    """
                    UPDATE ai_audience_inbound_webhook_event
                    SET status = 'processed',
                        external_effect_job_id = COALESCE(:external_effect_job_id, external_effect_job_id),
                        processed_at = CURRENT_TIMESTAMP,
                        row_version = row_version + 1
                    WHERE id = :id AND processed_at IS NULL
                    RETURNING *
                    """
                ),
                {"id": int(inbound_event_id), "external_effect_job_id": external_effect_job_id},
            ).mappings().fetchone()
            session.commit()
        return {
            "ok": True,
            "deduplicated": finalized is None,
            "inbound_event_id": int(inbound_event_id),
            "automation_send_plan": automation_send_plan,
            "external_effect_job_id": external_effect_job_id,
            "real_external_call_executed": False,
        }

    def _idempotency_key(self, package: dict[str, Any], payload: dict[str, Any]) -> str:
        external_event_id = _text(payload.get("external_event_id"))
        if external_event_id:
            return f"ai_audience_inbound:{package['id']}:{external_event_id}"
        return f"ai_audience_inbound:{package['id']}:{hashlib.sha256(_json_dumps(payload).encode('utf-8')).hexdigest()}"

    def _maybe_plan_action(self, package: dict[str, Any], payload: dict[str, Any], *, parent_execution_id: str = "") -> int | None:
        if not runtime_bool("AICRM_AI_AUDIENCE_INBOUND_ACTION_EXECUTE"):
            return None
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        if _text(action.get("type")) != "send_private_message":
            return None
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        target = _text(action.get("target_external_userid"))
        sender = _text(action.get("sender_userid"))
        content = _text(message.get("text"))
        if not target or not sender or not content:
            return None
        job = self._external_effects.plan_effect(
            effect_type=WECOM_MESSAGE_PRIVATE_SEND,
            adapter_name="wecom_private_message",
            operation="send",
            target_type="external_user",
            target_id=target,
            payload={
                "channel": "wecom_private",
                "external_userids": [target],
                "owner_userid": sender,
                "content_text": content,
                "source": "ai_audience_inbound_webhook",
                **_test_scope(package),
            },
            payload_summary={
                "package_key": package.get("package_key"),
                "target_external_userid": target,
                "sender_userid": sender,
                "content_text_length": len(content),
            },
            business_type="ai_audience_inbound_webhook",
            business_id=_text(payload.get("external_event_id")),
            source_module="ai_audience_ops.webhook_service",
            idempotency_key=f"ai_audience_inbound_action:{package['id']}:{_text(payload.get('external_event_id'))}",
            execution_id="exe_ai_audience_effect_" + uuid4().hex,
            parent_execution_id=_text(parent_execution_id),
            lane="wecom_interactive",
            ordering_key=f"external_user:{target}",
            fairness_key=_text(package.get("package_key")) or str(package.get("id")),
            execution_mode="execute",
            status="queued",
            context=CommandContext(actor_id="ai_audience_agent", actor_type="external_agent", source_route="ai_audience.inbound_webhook"),
        )
        return int(job.get("id") or 0) or None

    def _maybe_enqueue_automation_send_plan(self, package: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        if _text(action.get("type")) != "enqueue_automation_send_plan":
            return None
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        content_package = message.get("content_package") if isinstance(message.get("content_package"), dict) else {}
        if not content_package:
            content_package = {"content_text": _text(message.get("text"))}
        normalized_package = normalize_send_content_package(content_package, text_enabled=True, require_body=True)
        target = _text(action.get("target_external_userid"))
        sender = _text(action.get("sender_userid"))
        external_event_id = _text(payload.get("external_event_id"))
        if not external_event_id or not target or not sender:
            return {"status": "skipped", "reason": "missing_required_action_fields"}
        from aicrm_next.cloud_orchestrator.repository import build_cloud_plan_repository

        send_plan = build_cloud_plan_repository().create_or_reuse_agent_send_plan(
            external_event_id=external_event_id,
            package_key=_text(package.get("package_key")),
            external_userid=target,
            owner_userid=sender,
            content_package=normalized_package,
            operator="automation_agent",
        )
        plan_id = _text(send_plan.get("plan_id"))
        recipient_id = int(send_plan.get("recipient_id") or 0)
        if not plan_id or recipient_id <= 0:
            return send_plan

        # This runs only from the durable inbound-event consumer.  Approval is
        # idempotent and creates durable broadcast intent; it never calls a
        # provider in this transaction.
        from aicrm_next.cloud_orchestrator.application import ApproveCloudPlanRecipientCommand

        approval = ApproveCloudPlanRecipientCommand().execute(
            plan_id,
            recipient_id,
            operator="automation_agent_inbound_consumer",
        )
        return {
            **send_plan,
            "approval": {
                "status": _text(approval.get("status")),
                "job_id": int(approval.get("job_id") or 0),
            },
        }


def _test_scope(package: dict[str, Any]) -> dict[str, Any]:
    if _text(package.get("package_key")).startswith("prod_e2e_"):
        return {"is_test": True, "execution_scope": "test_loopback"}
    return {}
