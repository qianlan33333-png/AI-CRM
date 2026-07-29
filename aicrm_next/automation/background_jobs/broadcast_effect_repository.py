from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import text

from aicrm_next.platform.platform_foundation.external_effects import WECOM_MEDIA_UPLOAD
from aicrm_next.platform.platform_foundation.external_effects.continuations import ExternalEffectContinuation
from aicrm_next.platform.platform_foundation.external_effects.runtime_write_port import (
    build_external_effect_runtime_write_port,
)
from aicrm_next.platform.platform_foundation.background_jobs.broadcast_job_write_port import (
    build_broadcast_job_write_port,
)
from aicrm_next.platform.platform_foundation.background_jobs.cloud_broadcast_projection_write_port import (
    build_cloud_broadcast_projection_write_port,
)
from aicrm_next.platform.shared.db_session import get_session_factory
from aicrm_next.platform.shared.runtime import fixture_mode
from aicrm_next.platform.platform_foundation.internal_events.shadow import (
    emit_broadcast_task_finalized_shadow_event,
    safe_emit,
)


BROADCAST_MATERIAL_DEPENDENCY_BUSINESS_TYPE = "broadcast_material_dependency"


def _matches(job, _dispatch_result) -> bool:
    return (
        (job.business_type == "broadcast_job" or _is_material_dependency(job))
        and str(job.business_id or "").strip().isdigit()
    )


def _is_material_dependency(job) -> bool:
    return (
        str(getattr(job, "effect_type", "") or "") == WECOM_MEDIA_UPLOAD
        and str(getattr(job, "business_type", "") or "") == BROADCAST_MATERIAL_DEPENDENCY_BUSINESS_TYPE
    )


def _project(job, _dispatch_result):
    if _is_material_dependency(job):
        return _release_after_material_dependency(job)
    if fixture_mode():
        return {"ok": True, "projected": False, "reason": "fixture_read_model_not_persisted"}
    broadcast_job_id = int(job.business_id)
    with get_session_factory()() as session:
        rows = [
            dict(row)
            for row in (
            session.execute(
                text(
                    """
                SELECT status, side_effect_executed, provider_result_received
                FROM external_effect_job
                WHERE business_type = 'broadcast_job' AND business_id = :business_id
                ORDER BY id ASC
                """
                ),
                {"business_id": str(broadcast_job_id)},
            )
            .mappings()
            .all()
            )
        ]
        statuses = [str(row["status"] or "").strip() for row in rows]
        terminal = {
            "succeeded",
            "simulated",
            "unknown_after_dispatch",
            "failed_terminal",
            "blocked",
            "cancelled",
        }
        if not rows or any(status not in terminal for status in statuses):
            session.rollback()
            return {
                "ok": True,
                "projected": False,
                "reason": "broadcast_effects_waiting",
                "effect_count": len(rows),
                "succeeded_count": statuses.count("succeeded"),
            }
        aggregate_status = "sent"
        for candidate in (
            "unknown_after_dispatch",
            "failed_terminal",
            "blocked",
            "cancelled",
            "simulated",
        ):
            if candidate in statuses:
                aggregate_status = candidate
                break
        projection_status = {
            "unknown_after_dispatch": "failed",
            "failed_terminal": "failed",
            "blocked": "failed",
        }.get(aggregate_status, aggregate_status)
        recipient_status = projection_status
        message_status = "skipped" if aggregate_status == "cancelled" else projection_status
        succeeded_count = statuses.count("succeeded")
        failed_count = len(statuses) - succeeded_count
        side_effect_executed = any(bool(row["side_effect_executed"]) for row in rows)
        provider_result_received = bool(rows) and all(bool(row["provider_result_received"]) for row in rows)
        reconciliation_required = aggregate_status == "unknown_after_dispatch"
        result_summary = {
            "projection_owner": "external_effect.settled",
            "effect_count": len(rows),
            "status_counts": {status: statuses.count(status) for status in sorted(set(statuses))},
            "aggregate_status": aggregate_status,
            "reconciliation_required": reconciliation_required,
        }
        build_broadcast_job_write_port().settle_from_external_effect_sqlalchemy(
            session,
            job_id=broadcast_job_id,
            status=aggregate_status,
            sent_count=succeeded_count,
            failed_count=failed_count,
            side_effect_executed=side_effect_executed,
            provider_result_received=provider_result_received,
            reconciliation_required=reconciliation_required,
            result_summary_json=json.dumps(
                result_summary,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            last_error=(
                "" if aggregate_status == "sent" else f"external_effect_{aggregate_status}"
            ),
        )
        build_cloud_broadcast_projection_write_port().settle_from_external_effect_sqlalchemy(
            session,
            job_id=broadcast_job_id,
            recipient_status=recipient_status,
            message_status=message_status,
            last_error=(
                "" if aggregate_status == "sent" else f"external_effect_{aggregate_status}"
            ),
        )
        plan_row = session.execute(
            text(
                """
                SELECT plan_id
                FROM cloud_broadcast_plan_recipients
                WHERE broadcast_job_id = :job_id
                LIMIT 1
                """
            ),
            {"job_id": broadcast_job_id},
        ).mappings().first()
        plan_id = str((plan_row or {}).get("plan_id") or "")
        session.commit()
    internal_event = safe_emit(
        "broadcast_task.finalized",
        emit_broadcast_task_finalized_shadow_event,
        broadcast_job_id=broadcast_job_id,
        plan_id=plan_id,
        status=aggregate_status,
        sent_count=succeeded_count,
        failed_count=failed_count,
    )
    return {
        "ok": True,
        "projected": True,
        "broadcast_job_id": broadcast_job_id,
        "effect_count": len(rows),
        "aggregate_status": aggregate_status,
        "internal_event_status": str(internal_event.get("status") or ""),
    }


def _matches_terminal(job, dispatch_result) -> bool:
    return _matches(job, dispatch_result) and job.status != "succeeded"


def _release_after_material_dependency(job) -> dict:
    if fixture_mode():
        return {"ok": True, "released": False, "reason": "fixture_dependency_graph_not_persisted"}
    broadcast_job_id = int(job.business_id)
    with get_session_factory()() as session:
        final = (
            session.execute(
                text(
                    """
                    SELECT id, status, payload_json, payload_summary_json
                    FROM external_effect_job
                    WHERE business_type = 'broadcast_job'
                      AND business_id = :business_id
                      AND effect_type = 'wecom.message.private.send'
                    ORDER BY id ASC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"business_id": str(broadcast_job_id)},
            )
            .mappings()
            .first()
        )
        if not final:
            session.rollback()
            return {"ok": False, "released": False, "reason": "broadcast_final_effect_missing"}
        final = dict(final)
        if str(final.get("status") or "") != "planned":
            session.rollback()
            return {
                "ok": True,
                "released": False,
                "reason": "broadcast_final_effect_already_resolved",
                "final_effect_job_id": int(final["id"]),
            }
        dependencies = [
            dict(row)
            for row in (
                session.execute(
                    text(
                        """
                        SELECT id, status, payload_json
                        FROM external_effect_job
                        WHERE business_type = :business_type
                          AND business_id = :business_id
                        ORDER BY id ASC
                        FOR UPDATE
                        """
                    ),
                    {
                        "business_type": BROADCAST_MATERIAL_DEPENDENCY_BUSINESS_TYPE,
                        "business_id": str(broadcast_job_id),
                    },
                )
                .mappings()
                .all()
            )
        ]
        terminal_failures = [
            row
            for row in dependencies
            if str(row.get("status") or "") in {"failed_terminal", "blocked", "cancelled", "unknown_after_dispatch"}
        ]
        if terminal_failures:
            runtime_port = build_external_effect_runtime_write_port()
            blocked = runtime_port.block_planned_sqlalchemy(
                session,
                job_id=int(final["id"]),
                error_code="broadcast_material_dependency_failed",
                error_message="One or more broadcast material dependencies failed before private-message dispatch.",
            )
            cancelled_dependencies = runtime_port.cancel_pre_provider_sqlalchemy(
                session,
                job_ids=[int(row["id"]) for row in dependencies],
                exclude_job_id=int(job.id),
                actor="broadcast_material_dependency",
                reason="sibling_material_dependency_failed",
            )
            session.commit()
            projected = _project(SimpleNamespace(business_id=str(broadcast_job_id), business_type="broadcast_job"), None)
            return {
                "ok": True,
                "released": False,
                "blocked": bool(blocked),
                "reason": "broadcast_material_dependency_failed",
                "failed_dependency_count": len(terminal_failures),
                "cancelled_dependency_count": len(cancelled_dependencies),
                "projection": projected,
            }
        if not dependencies or any(str(row.get("status") or "") != "succeeded" for row in dependencies):
            session.rollback()
            return {
                "ok": True,
                "released": False,
                "reason": "broadcast_material_dependencies_waiting",
                "dependency_count": len(dependencies),
                "succeeded_count": len([row for row in dependencies if str(row.get("status") or "") == "succeeded"]),
            }

        ready_by_key = {
            str((row.get("payload_json") or {}).get("material_key") or ""): _provider_media_id(session, row.get("payload_json") or {})
            for row in dependencies
        }
        if any(not key or not media_id for key, media_id in ready_by_key.items()):
            session.rollback()
            return {"ok": False, "released": False, "reason": "broadcast_material_provider_media_missing"}
        payload = dict(final.get("payload_json") or {})
        payload["attachments"] = _resolve_dependency_attachments(list(payload.get("attachments") or []), ready_by_key)
        summary = dict(final.get("payload_summary_json") or {})
        summary["material_dependencies_resolved"] = True
        summary["material_dependency_count"] = len(dependencies)
        released = build_external_effect_runtime_write_port().release_planned_sqlalchemy(
            session,
            job_id=int(final["id"]),
            payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            payload_summary_json=json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
            available_at_mode="now",
        )
        session.commit()
        return {
            "ok": True,
            "released": bool(released),
            "reason": "broadcast_material_dependencies_resolved" if released else "broadcast_final_effect_release_cas_lost",
            "final_effect_job_id": int(final["id"]),
            "dependency_count": len(dependencies),
        }


def _provider_media_id(session, payload: dict) -> str:
    material_kind = str(payload.get("material_kind") or "").strip()
    material_id = int(payload.get("material_id") or 0)
    query_by_kind = {
        "image": "SELECT thumb_media_id FROM image_library WHERE id = :material_id",
        "attachment": "SELECT media_id FROM attachment_library WHERE id = :material_id",
        "miniprogram": "SELECT thumb_media_id FROM miniprogram_library WHERE id = :material_id",
    }
    query = query_by_kind.get(material_kind)
    if not query or material_id <= 0:
        return ""
    return str(session.execute(text(query), {"material_id": material_id}).scalar_one_or_none() or "").strip()


def _resolve_dependency_attachments(attachments: list, ready_by_key: dict[str, str]) -> list[dict]:
    resolved: list[dict] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        current = json.loads(json.dumps(item, ensure_ascii=False))
        msgtype = str(current.get("msgtype") or "").strip()
        nested = current.get(msgtype) if isinstance(current.get(msgtype), dict) else {}
        if msgtype in {"image", "file"} and str(nested.get("media_dependency_key") or "").strip():
            key = str(nested.pop("media_dependency_key"))
            nested["media_id"] = ready_by_key[key]
        if msgtype == "miniprogram" and str(nested.get("pic_media_dependency_key") or "").strip():
            key = str(nested.pop("pic_media_dependency_key"))
            nested["pic_media_id"] = ready_by_key[key]
        current[msgtype] = nested
        resolved.append(current)
    return resolved


BROADCAST_EXTERNAL_EFFECT_READ_MODEL_CONTINUATION = ExternalEffectContinuation(
    name="broadcast_external_effect_read_model",
    matches=_matches,
    run=_project,
)

BROADCAST_EXTERNAL_EFFECT_SETTLEMENT_CONTINUATION = ExternalEffectContinuation(
    name="broadcast_external_effect_settlement",
    matches=_matches_terminal,
    run=_project,
)


__all__ = [
    "BROADCAST_EXTERNAL_EFFECT_READ_MODEL_CONTINUATION",
    "BROADCAST_EXTERNAL_EFFECT_SETTLEMENT_CONTINUATION",
]
