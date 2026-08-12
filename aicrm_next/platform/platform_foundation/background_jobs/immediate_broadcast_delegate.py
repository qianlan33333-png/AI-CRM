from __future__ import annotations

import json
from typing import Any, Callable

from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    WECOM_MEDIA_UPLOAD,
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


_broadcast_material_plan_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def configure_broadcast_material_plan_resolver(
    resolver: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> None:
    global _broadcast_material_plan_resolver
    _broadcast_material_plan_resolver = resolver


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _merge_content_package(target: dict[str, Any], source: Any) -> None:
    source_dict = _json_dict(source)
    if not source_dict:
        return
    for nested_key in ("content_payload_json", "content_package_json", "content_package"):
        nested = _json_dict(source_dict.get(nested_key))
        if nested:
            _merge_content_package(target, nested)
    for key in (
        "image_library_ids",
        "miniprogram_library_ids",
        "attachment_library_ids",
        "group_invite_library_ids",
    ):
        values = _json_list(source_dict.get(key))
        if values:
            existing = list(target.get(key) or [])
            for value in values:
                if value not in existing:
                    existing.append(value)
            target[key] = existing
    field_by_media_kind = {
        "image": "image_library_ids",
        "miniprogram": "miniprogram_library_ids",
        "file": "attachment_library_ids",
        "attachment": "attachment_library_ids",
        "link": "group_invite_library_ids",
        "group_invite": "group_invite_library_ids",
    }
    for media_ref in _json_list(source_dict.get("media_refs")):
        if not isinstance(media_ref, dict):
            continue
        field = field_by_media_kind.get(_text(media_ref.get("kind")).lower())
        try:
            library_id = int(media_ref.get("library_id") or 0)
        except (TypeError, ValueError):
            library_id = 0
        if not field or library_id <= 0:
            continue
        existing = list(target.get(field) or [])
        if library_id not in existing:
            existing.append(library_id)
            target[field] = existing
    card = source_dict.get("dynamic_miniprogram_card")
    if isinstance(card, dict) and card:
        target["dynamic_miniprogram_card"] = dict(card)


def _content_package(value: Any) -> dict[str, Any]:
    package: dict[str, Any] = {}
    _merge_content_package(package, value)
    return package


def _dedupe_private_attachments(attachments: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("private_message_attachment_must_be_object")
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            result.append(dict(item))
    return result


def _materialized_attachments(
    *,
    content_payload: Any,
    direct_attachments: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    package = _content_package(content_payload)
    has_library_materials = any(
        _json_list(package.get(field))
        for field in (
            "image_library_ids",
            "miniprogram_library_ids",
            "attachment_library_ids",
            "group_invite_library_ids",
        )
    ) or isinstance(package.get("dynamic_miniprogram_card"), dict)
    if not has_library_materials:
        attachments = _dedupe_private_attachments(direct_attachments)
        if len(attachments) > 9:
            raise ValueError("private_message_attachments_exceed_limit")
        return attachments, []
    resolver = _broadcast_material_plan_resolver
    if resolver is None:
        raise ValueError("broadcast_material_plan_resolver_not_configured")
    material_plan = resolver(package)
    resolved = [
        dict(item)
        for item in _json_list(material_plan.get("attachments"))
        if isinstance(item, dict)
    ]
    attachments = _dedupe_private_attachments([*direct_attachments, *resolved])
    if len(attachments) > 9:
        raise ValueError("private_message_attachments_exceed_limit")
    uploads = [
        dict(item)
        for item in _json_list(material_plan.get("uploads"))
        if isinstance(item, dict)
    ]
    return attachments, uploads


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
            SELECT id, content_text, content_payload_json, attachments_json
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
        attachments, material_uploads = _materialized_attachments(
            content_payload=(message or {}).get("content_payload_json"),
            direct_attachments=_json_list((message or {}).get("attachments_json")),
        )
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
            status="planned" if material_uploads else "queued",
            idempotency_key=f"broadcast-effect:{job_id}:private:{external_userid}",
            parent_execution_id=_text(job.get("execution_id")),
            lane="wecom_ai_assistant_bulk",
            ordering_key=f"external_contact:{external_userid}",
            fairness_key=f"broadcast:{sender}:{batch_key}",
        )
        effect_id = int(effect["id"])
        for upload in material_uploads:
            material_key = _text(upload.get("material_key"))
            material_kind = _text(upload.get("material_kind"))
            material_id = int(upload.get("material_id") or 0)
            upload_kind = _text(upload.get("upload_kind"))
            if not material_key or not material_kind or material_id <= 0 or not upload_kind:
                raise ValueError("broadcast_material_dependency_invalid")
            self._effect_service.plan_effect(
                connection=executor,
                effect_type=WECOM_MEDIA_UPLOAD,
                adapter_name="wecom_media_upload",
                operation="refresh_temporary_media",
                target_type="media_library_material",
                target_id=f"{material_kind}:{material_id}:{upload_kind}",
                business_type="broadcast_material_dependency",
                business_id=str(job_id),
                payload={
                    "material_key": material_key,
                    "material_kind": material_kind,
                    "material_id": material_id,
                    "upload_kind": upload_kind,
                    "force_refresh": False,
                    "broadcast_job_id": int(job_id),
                },
                payload_summary={
                    "broadcast_job_id": int(job_id),
                    "material_key": material_key,
                    "material_kind": material_kind,
                    "material_id": material_id,
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
                idempotency_key=f"broadcast-effect:{job_id}:material:{material_key}",
                parent_execution_id=_text(job.get("execution_id")),
                lane="wecom_media",
                ordering_key=f"broadcast_material:{job_id}:{material_key}",
                fairness_key=f"broadcast:{sender}:{batch_key}",
            )
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


__all__ = [
    "AiAssistantImmediateBroadcastDelegate",
    "configure_broadcast_material_plan_resolver",
]
