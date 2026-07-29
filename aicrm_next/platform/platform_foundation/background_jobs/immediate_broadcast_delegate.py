from __future__ import annotations

import json
from typing import Any

from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    WECOM_MESSAGE_PRIVATE_SEND,
)
from aicrm_next.platform.platform_foundation.external_effects.service import (
    ExternalEffectService,
)

from .broadcast_job_write_port import build_broadcast_job_write_port
from .cloud_broadcast_projection_write_port import (
    build_cloud_broadcast_projection_write_port,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


class AiAssistantImmediateBroadcastDelegate:
    """Materialize an approved AI private send before its DB transaction commits."""

    def __init__(self, effect_service: ExternalEffectService | None = None) -> None:
        self._effect_service = effect_service or ExternalEffectService()

    def delegate_dbapi(
        self,
        executor: Any,
        *,
        plan: dict[str, Any],
        recipient: dict[str, Any],
        job_id: int,
        operator: str,
        external_userid: str,
    ) -> dict[str, Any]:
        if _text(plan.get("content_strategy")) != "agent_generated_single":
            return {"status": "not_applicable", "reason": "not_agent_generated_single"}
        locked_job = executor.execute(
            "SELECT * FROM broadcast_jobs WHERE id = %s FOR UPDATE",
            (int(job_id),),
        ).fetchone()
        if not locked_job:
            return {"status": "not_materialized", "reason": "broadcast_job_missing"}
        job = dict(locked_job)
        if _text(job.get("business_domain")) != "ai_assistant":
            return {"status": "not_applicable", "reason": "not_ai_assistant"}
        if _text(job.get("status")) == "delegated" and int(job.get("external_effect_job_id") or 0):
            return {
                "status": "reused",
                "external_effect_job_id": int(job["external_effect_job_id"]),
            }
        if _text(job.get("status")) != "queued":
            return {
                "status": "not_materialized",
                "reason": f"broadcast_status_{_text(job.get('status')) or 'missing'}",
            }

        unionid = _text(recipient.get("unionid"))
        external_userid = _text(external_userid)
        if not external_userid:
            return {
                "status": "not_materialized",
                "reason": "identity_external_userid_missing",
            }
        message = executor.execute(
            """
            SELECT id, content_text, attachments_json
            FROM cloud_broadcast_plan_recipient_messages
            WHERE plan_id = %s
              AND recipient_id = %s
              AND status IN ('pending', 'queued', 'delegated')
            ORDER BY sequence_index ASC, id ASC
            LIMIT 1
            FOR UPDATE
            """,
            (_text(plan.get("plan_id")), int(recipient.get("id") or 0)),
        ).fetchone()
        content_text = _text((message or {}).get("content_text"))
        attachments = _json_list((message or {}).get("attachments_json"))
        sender = _text(recipient.get("owner_userid"))
        if not sender:
            return {"status": "not_materialized", "reason": "sender_userid_missing"}
        if not content_text and not attachments:
            return {
                "status": "not_materialized",
                "reason": "content_text_or_attachment_missing",
            }

        batch_key = _text(job.get("batch_key")) or str(job_id)
        effect = self._effect_service.plan_effect(
            connection=executor,
            effect_type=WECOM_MESSAGE_PRIVATE_SEND,
            adapter_name="wecom_private_message",
            operation="send_private_message",
            target_type="external_contact",
            target_id=external_userid,
            business_type="broadcast_job",
            business_id=str(job_id),
            payload={
                "channel": "wecom_private",
                "owner_userid": sender,
                "sender": sender,
                "target_unionid": unionid,
                "external_userids": [external_userid],
                "content_text": content_text,
                "attachments": attachments,
                "source": "cloud_plan_approval_transaction",
                "cloud_plan_message_id": int((message or {}).get("id") or 0),
            },
            payload_summary={
                "broadcast_job_id": int(job_id),
                "external_userid_count": 1,
                "content_text_length": len(content_text),
                "attachment_count": len(attachments),
                "materialization": "approval_transaction",
            },
            context=CommandContext(
                actor_id=_text(operator) or "cloud_plan_approval",
                actor_type="system",
                request_id=_text(job.get("idempotency_key")),
                trace_id=_text(job.get("trace_id")),
                source_route="cloud_plan_approval_transaction",
            ),
            source_module="platform.background_jobs.immediate_broadcast_delegate",
            source_command_id=str(job_id),
            status="queued",
            idempotency_key=f"broadcast-effect:{job_id}:private:{external_userid}",
            parent_execution_id=_text(job.get("execution_id")),
            lane="wecom_ai_assistant_bulk",
            ordering_key=f"external_contact:{external_userid}",
            fairness_key=f"broadcast:{sender}:{batch_key}",
        )
        effect_id = int(effect["id"])
        delegated = build_broadcast_job_write_port().delegate_external_effect_dbapi(
            executor,
            job_id=int(job_id),
            external_effect_job_id=effect_id,
            trace_id=_text(job.get("trace_id")),
            actor=_text(operator) or "cloud_plan_approval",
        )
        if not delegated:
            raise RuntimeError("broadcast effect materialization lost queued-state ownership")
        build_cloud_broadcast_projection_write_port().mark_delegated_dbapi(
            executor,
            job_id=int(job_id),
        )
        return {
            "status": "created" if bool(effect.get("created_on_plan")) else "reused",
            "external_effect_job_id": effect_id,
            "lane": "wecom_ai_assistant_bulk",
            "wakeup": "postgres_notify_after_commit",
        }


__all__ = ["AiAssistantImmediateBroadcastDelegate"]
