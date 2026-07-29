from __future__ import annotations

import os
import uuid
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import (
    WECOM_MEDIA_UPLOAD,
    WECOM_MESSAGE_GROUP_SEND,
    WECOM_MESSAGE_PRIVATE_SEND,
)
from aicrm_next.platform.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform.platform_foundation.background_jobs.broadcast_job_write_port import (
    build_broadcast_job_write_port,
)
from aicrm_next.platform.platform_foundation.background_jobs.cloud_broadcast_projection_write_port import (
    build_cloud_broadcast_projection_write_port,
)

from aicrm_next.platform.platform_foundation.external_effects.execution_gates import (
    WECOM_EXECUTION_DISABLED_CODE,
    explicit_wecom_execution_disabled,
    wecom_execution_disabled_message,
)
from aicrm_next.platform.shared.runtime_settings import managed_runtime_int, runtime_setting

from .db import connect, has_database_url, int_value, json_list, utcnow


class BroadcastDispatcher(Protocol):
    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]: ...


class BroadcastQueueRepository(Protocol):
    def claim_due_jobs(self, *, limit: int, now: datetime, claim_token: str, lease_seconds: int) -> list[dict[str, Any]]: ...
    def begin_dispatch(self, job_id: int, *, claim_token: str, now: datetime) -> dict[str, Any] | None: ...
    def finalize_dispatch(self, job_id: int, *, claim_token: str, outcome: dict[str, Any]) -> dict[str, Any] | None: ...
    def mark_unknown_after_dispatch(
        self,
        job_id: int,
        *,
        claim_token: str,
        error: str,
        side_effect_executed: bool,
        provider_result_received: bool,
    ) -> dict[str, Any] | None: ...


_dynamic_miniprogram_attachment_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None
_content_package_attachment_resolver: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def configure_dynamic_miniprogram_attachment_resolver(
    resolver: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> None:
    global _dynamic_miniprogram_attachment_resolver
    _dynamic_miniprogram_attachment_resolver = resolver


def configure_content_package_attachment_resolver(
    resolver: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> None:
    global _content_package_attachment_resolver
    _content_package_attachment_resolver = resolver


class SafeSkippedBroadcastDispatcher:
    """Compatibility planner; provider ownership belongs to External Effect."""

    def __init__(self, service: ExternalEffectService | None = None) -> None:
        # Production defers effect insertion to the Broadcast repository so
        # the owner link and effect rows cross one durability boundary.
        self._service = service

    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = _json_dict(job.get("content_payload"))
        if _is_wecom_private_job(job, payload):
            return self._plan_private_effects(job, payload)
        if _is_wecom_customer_group_job(job, payload):
            return self._plan_group_effect(job, payload)
        return {
            "ok": False,
            "status": "skipped",
            "reason": "next_native_dispatcher_missing",
            "source_type": str(job.get("source_type") or ""),
            "source_table": str(job.get("source_table") or ""),
            "content_type": str(job.get("content_type") or ""),
            "channel": str(job.get("channel") or ""),
            "target_kind": str(job.get("target_kind") or ""),
            "payload_channel": str(payload.get("channel") or ""),
        }

    def _plan_private_effects(self, job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        payload = _with_cloud_plan_recipient_message(payload)
        try:
            payload = _with_content_package_attachments(payload)
        except Exception as exc:
            error_code = _text(getattr(exc, "code", ""))
            return {
                "ok": False,
                "status": "failed_retryable" if bool(getattr(exc, "retryable", False)) else "failed_terminal",
                "failure_type": error_code or "material_resolve_failed",
                "error": error_code or _text(exc) or "material_resolve_failed",
                "side_effect_executed": bool(getattr(exc, "provider_call_executed", False)),
                "provider_result_received": bool(getattr(exc, "provider_result_received", False)),
            }
        target_unionids = _extract_target_unionids(job, payload)
        external_userids, missing_unionids = _resolve_private_targets_by_unionid(target_unionids)
        sender = _extract_private_sender(payload)
        content_text = _extract_private_text(payload)
        attachments = _json_list(payload.get("attachments")) or _json_list(payload.get("attachments_json"))
        validation_error = ""
        if not target_unionids:
            validation_error = "target_unionids_missing"
        elif int_value(job.get("target_count")) != len(target_unionids):
            validation_error = "target_count_mismatch"
        elif missing_unionids or not external_userids:
            validation_error = "identity_external_userid_missing"
        elif not sender:
            validation_error = "sender_userid_missing"
        elif not content_text and not attachments:
            validation_error = "content_text_or_attachment_missing"
        if validation_error:
            return {
                "ok": False,
                "status": "failed_terminal",
                "failure_type": "validation_failed",
                "error": validation_error,
                "side_effect_executed": False,
                "provider_result_received": False,
            }
        material_uploads = [item for item in _json_list(payload.pop("_material_uploads", [])) if isinstance(item, dict)]
        effect_plan_requests: list[dict[str, Any]] = []
        for target in external_userids:
            effect_plan_requests.append(
                _effect_plan_request(
                    job=job,
                    effect_type=WECOM_MESSAGE_PRIVATE_SEND,
                    adapter_name="wecom_private_message",
                    operation="send_private_message",
                    target_type="external_contact",
                    target_id=target,
                    payload={
                        "channel": "wecom_private",
                        "owner_userid": sender,
                        "sender": sender,
                        "external_userids": [target],
                        "content_text": content_text,
                        "attachments": attachments,
                        "source": "broadcast_read_model_delegate",
                    },
                    payload_summary={
                        "broadcast_job_id": int_value(job.get("id")),
                        "external_userid_count": 1,
                        "content_text_length": len(content_text),
                        "attachment_count": len(attachments),
                    },
                    idempotency_suffix=f"private:{target}",
                    ordering_key=f"external_contact:{target}",
                    status="planned" if material_uploads else "queued",
                    lane=(
                        "wecom_ai_assistant_bulk"
                        if _text(job.get("business_domain")) == "ai_assistant"
                        else "wecom_bulk"
                    ),
                )
            )
        for upload in material_uploads:
            material_key = _text(upload.get("material_key"))
            material_kind = _text(upload.get("material_kind"))
            material_id = int_value(upload.get("material_id"))
            upload_kind = _text(upload.get("upload_kind"))
            effect_plan_requests.append(
                _effect_plan_request(
                    job=job,
                    effect_type=WECOM_MEDIA_UPLOAD,
                    adapter_name="wecom_media_upload",
                    operation="refresh_temporary_media",
                    target_type="media_library_material",
                    target_id=f"{material_kind}:{material_id}:{upload_kind}",
                    payload={
                        "material_key": material_key,
                        "material_kind": material_kind,
                        "material_id": material_id,
                        "upload_kind": upload_kind,
                        "force_refresh": False,
                        "broadcast_job_id": int_value(job.get("id")),
                    },
                    payload_summary={
                        "broadcast_job_id": int_value(job.get("id")),
                        "material_key": material_key,
                        "material_kind": material_kind,
                        "material_id": material_id,
                    },
                    idempotency_suffix=f"material:{material_key}",
                    ordering_key=f"broadcast_material:{int_value(job.get('id'))}:{material_key}",
                    business_type="broadcast_material_dependency",
                    lane="wecom_media",
                )
            )
        effect_ids = self._plan_for_injected_repository(effect_plan_requests)
        return {
            "ok": True,
            "status": "delegated",
            "task_type": "broadcast_job/external_effect_delegate",
            "external_effect_job_ids": effect_ids,
            "effect_plan_requests": [] if effect_ids else effect_plan_requests,
            "target_count": len(external_userids),
            "side_effect_executed": False,
            "provider_result_received": False,
            "request_payload": {"broadcast_job_id": int_value(job.get("id"))},
            "response_payload": {"external_effect_job_ids": effect_ids} if effect_ids else {},
        }

    def _plan_group_effect(self, job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        chat_ids = [_text(item) for item in _json_list(payload.get("chat_ids")) if _text(item)]
        sender = _text(payload.get("sender") or payload.get("owner_userid"))
        if not chat_ids or not sender:
            return {
                "ok": False,
                "status": "failed_terminal",
                "failure_type": "broadcast_external_effect_contract_invalid",
                "error": "broadcast job could not be converted to a group external effect",
                "side_effect_executed": False,
                "provider_result_received": False,
            }
        effect_plan_requests = [
            _effect_plan_request(
                job=job,
                effect_type=WECOM_MESSAGE_GROUP_SEND,
                adapter_name="wecom_group_message",
                operation="send_group_message",
                target_type="broadcast_job",
                target_id=str(int_value(job.get("id"))),
                payload={
                    "chat_ids": chat_ids,
                    "owner_userid": sender,
                    "sender": sender,
                    "content_payload": payload,
                    "mention_all": False,
                    "source": "broadcast_read_model_delegate",
                },
                payload_summary={
                    "broadcast_job_id": int_value(job.get("id")),
                    "chat_count": len(chat_ids),
                },
                idempotency_suffix="group",
                ordering_key=f"broadcast_job:{int_value(job.get('id'))}",
            )
        ]
        effect_ids = self._plan_for_injected_repository(effect_plan_requests)
        return {
            "ok": True,
            "status": "delegated",
            "task_type": "broadcast_job/external_effect_delegate",
            "external_effect_job_ids": effect_ids,
            "effect_plan_requests": [] if effect_ids else effect_plan_requests,
            "target_count": len(chat_ids),
            "side_effect_executed": False,
            "provider_result_received": False,
            "request_payload": {"broadcast_job_id": int_value(job.get("id"))},
            "response_payload": {"external_effect_job_ids": effect_ids} if effect_ids else {},
        }

    def _plan_for_injected_repository(self, requests: list[dict[str, Any]]) -> list[int]:
        if self._service is None:
            return []
        return [int(self._service.plan_effect(**request)["id"]) for request in requests]


def _effect_plan_request(
    *,
    job: dict[str, Any],
    effect_type: str,
    adapter_name: str,
    operation: str,
    target_type: str,
    target_id: str,
    payload: dict[str, Any],
    payload_summary: dict[str, Any],
    idempotency_suffix: str,
    ordering_key: str,
    business_type: str = "broadcast_job",
    status: str = "queued",
    lane: str = "wecom_bulk",
) -> dict[str, Any]:
    job_id = int_value(job.get("id"))
    return {
        "effect_type": effect_type,
        "adapter_name": adapter_name,
        "operation": operation,
        "target_type": target_type,
        "target_id": target_id,
        "business_type": business_type,
        "business_id": str(job_id),
        "payload": payload,
        "payload_summary": payload_summary,
        "context": CommandContext(
            actor_id=_text(job.get("created_by")) or "broadcast_effect_delegate",
            actor_type="system",
            request_id=_text(job.get("idempotency_key")),
            trace_id=_text(job.get("trace_id")),
            source_route="broadcast_effect_delegate",
        ),
        "source_module": "background_jobs.broadcast_effect_delegate",
        "source_command_id": str(job_id),
        "status": status,
        "idempotency_key": f"broadcast-effect:{job_id}:{idempotency_suffix}",
        "parent_execution_id": _text(job.get("execution_id")),
        "lane": lane,
        "ordering_key": ordering_key,
        "fairness_key": (
            f"broadcast:{_text(payload.get('sender') or payload.get('owner_userid')) or 'unknown'}:"
            f"{_text(job.get('batch_key')) or job_id}"
        ),
    }


class PostgresBroadcastQueueRepository:
    _FINAL_STATUSES = {
        "delegated",
        "sent",
        "simulated",
        "failed_retryable",
        "failed_terminal",
        "blocked",
        "unknown_after_dispatch",
    }

    def __init__(self, *, fault_injector=None) -> None:
        self._fault_injector = fault_injector

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)

    def claim_due_jobs(self, *, limit: int, now: datetime, claim_token: str, lease_seconds: int) -> list[dict[str, Any]]:
        with connect() as conn:
            return build_broadcast_job_write_port().claim_due_dbapi(
                conn,
                limit=int(limit),
                now=now,
                claim_token=claim_token,
                lease_expires_at=now + timedelta(seconds=int(lease_seconds)),
            )

    def begin_dispatch(self, job_id: int, *, claim_token: str, now: datetime) -> dict[str, Any] | None:
        token = _text(claim_token)
        if not token:
            raise ValueError("claim_token is required")
        with connect() as conn:
            row = build_broadcast_job_write_port().begin_dispatch_dbapi(
                conn,
                job_id=int(job_id),
                claim_token=token,
                now=now,
            )
            if not row:
                return None
            build_cloud_broadcast_projection_write_port().mark_dispatching_dbapi(
                conn,
                job_id=int(job_id),
            )
            conn.execute(
                """
                INSERT INTO broadcast_job_events (
                    job_id, event_type, from_status, to_status, event_payload, actor_type, actor_id
                ) VALUES (%s, 'dispatch_started', 'claimed', 'dispatching', '{}'::jsonb, 'worker', %s)
                """,
                (int(job_id), token[:200]),
            )
            return row

    def finalize_dispatch(self, job_id: int, *, claim_token: str, outcome: dict[str, Any]) -> dict[str, Any] | None:
        token = _text(claim_token)
        if not token:
            raise ValueError("claim_token is required")
        final_status = _text(outcome.get("status"))
        if final_status not in self._FINAL_STATUSES:
            raise ValueError(f"unsupported broadcast final status: {final_status}")
        error_text = _text(outcome.get("error") or outcome.get("reason"))[:1000]
        failure_type = _text(outcome.get("failure_type"))[:200]
        side_effect_executed = bool(outcome.get("side_effect_executed"))
        provider_result_received = bool(outcome.get("provider_result_received"))
        reconciliation_required = final_status == "unknown_after_dispatch"
        request_payload = _json_dict(outcome.get("request_payload"))
        response_payload = _json_dict(outcome.get("response_payload"))
        external_effect_job_ids = [
            int_value(item)
            for item in _json_list(outcome.get("external_effect_job_ids") or response_payload.get("external_effect_job_ids"))
            if int_value(item) > 0
        ]
        external_effect_job_id = external_effect_job_ids[0] if external_effect_job_ids else None
        wecom_task_id = _text(
            outcome.get("wecom_msgid")
            or response_payload.get("wecom_msgid")
            or response_payload.get("msgid")
            or _json_dict(response_payload.get("result")).get("msgid")
            or _json_dict(response_payload.get("result")).get("task_id")
        )
        task_type = _text(outcome.get("task_type")) or "broadcast_job/group_ops"
        retry_delay_seconds = managed_runtime_int(
            "BROADCAST_QUEUE_RETRY_DELAY_SECONDS",
            300,
            minimum=0,
        )
        with connect() as conn:
            job = conn.execute(
                """
                SELECT *
                FROM broadcast_jobs
                WHERE id = %s
                  AND status = 'dispatching'
                  AND claim_token = %s
                FOR UPDATE
                """,
                (int(job_id), token),
            ).fetchone()
            if not job:
                return None
            deferred_effect_requests = [item for item in list(outcome.get("effect_plan_requests") or []) if isinstance(item, dict)]
            if deferred_effect_requests:
                external_effect_job_ids = [int(ExternalEffectService().plan_effect(connection=conn, **request)["id"]) for request in deferred_effect_requests]
                external_effect_job_id = external_effect_job_ids[0]
                response_payload = {
                    **response_payload,
                    "external_effect_job_ids": external_effect_job_ids,
                }
                outcome["external_effect_job_ids"] = external_effect_job_ids
            if final_status == "failed_retryable" and int_value(job.get("attempt_count")) >= max(1, int_value(job.get("max_attempts"))):
                final_status = "failed_terminal"
                failure_type = failure_type or "max_attempts_exhausted"
                if not error_text:
                    error_text = "Broadcast retry budget exhausted before provider dispatch succeeded."
            self._fault("before_outbound_task")
            outbound_task = conn.execute(
                """
                INSERT INTO outbound_tasks (
                    broadcast_job_id, task_type, request_payload, response_payload,
                    wecom_task_id, status, trace_id
                ) VALUES (%s, %s, CAST(%s AS jsonb), CAST(%s AS jsonb), %s, %s, %s)
                ON CONFLICT (broadcast_job_id) WHERE broadcast_job_id IS NOT NULL
                DO UPDATE SET
                    task_type = EXCLUDED.task_type,
                    request_payload = EXCLUDED.request_payload,
                    response_payload = EXCLUDED.response_payload,
                    wecom_task_id = EXCLUDED.wecom_task_id,
                    status = EXCLUDED.status,
                    trace_id = EXCLUDED.trace_id
                RETURNING id
                """,
                (
                    int(job_id),
                    task_type,
                    _json_dumps(request_payload),
                    _json_dumps(response_payload),
                    wecom_task_id,
                    final_status,
                    _text(job.get("trace_id")),
                ),
            ).fetchone()
            outbound_task_id = int((outbound_task or {}).get("id") or 0) or None
            self._fault("after_outbound_task")
            build_cloud_broadcast_projection_write_port().finalize_dispatch_dbapi(
                conn,
                job_id=int(job_id),
                status=final_status,
                last_error=error_text,
            )
            self._fault("after_projection_updates")
            result_summary = {
                "status": final_status,
                "failure_type": failure_type,
                "side_effect_executed": side_effect_executed,
                "provider_result_received": provider_result_received,
                "wecom_task_id_present": bool(wecom_task_id),
            }
            finalized = build_broadcast_job_write_port().finalize_dispatch_dbapi(
                conn,
                job_id=int(job_id),
                claim_token=token,
                final_status=final_status,
                outbound_task_id=outbound_task_id,
                sent_count=int_value(outcome.get("sent_count")),
                failed_count=int_value(outcome.get("failed_count")),
                failure_type=failure_type,
                error_text=error_text,
                side_effect_executed=side_effect_executed,
                provider_result_received=provider_result_received,
                result_summary_json=_json_dumps(result_summary),
                reconciliation_required=reconciliation_required,
                external_effect_job_id=external_effect_job_id,
                retry_delay_seconds=retry_delay_seconds,
            )
            if not finalized:
                raise RuntimeError("broadcast finalizer lost claim ownership")
            conn.execute(
                """
                INSERT INTO broadcast_job_events (
                    job_id, event_type, from_status, to_status, event_payload, actor_type, actor_id
                ) VALUES (%s, 'dispatch_finalized', 'dispatching', %s, CAST(%s AS jsonb), 'worker', %s)
                """,
                (int(job_id), final_status, _json_dumps(result_summary), token[:200]),
            )
            self._fault("before_commit")
            return finalized

    def mark_unknown_after_dispatch(
        self,
        job_id: int,
        *,
        claim_token: str,
        error: str,
        side_effect_executed: bool,
        provider_result_received: bool,
    ) -> dict[str, Any] | None:
        token = _text(claim_token)
        error_text = _text(error)[:1000]
        if not token:
            return None
        with connect() as conn:
            job = conn.execute(
                """
                SELECT id FROM broadcast_jobs
                WHERE id = %s AND status = 'dispatching' AND claim_token = %s
                FOR UPDATE
                """,
                (int(job_id), token),
            ).fetchone()
            if not job:
                return None
            build_cloud_broadcast_projection_write_port().mark_unknown_after_dispatch_dbapi(
                conn,
                job_id=int(job_id),
                last_error=error_text,
            )
            summary = {
                "status": "unknown_after_dispatch",
                "failure_type": "post_provider_persistence_unknown",
                "side_effect_executed": bool(side_effect_executed),
                "provider_result_received": bool(provider_result_received),
            }
            updated = build_broadcast_job_write_port().mark_unknown_after_dispatch_dbapi(
                conn,
                job_id=int(job_id),
                claim_token=token,
                error_text=error_text,
                side_effect_executed=bool(side_effect_executed),
                provider_result_received=bool(provider_result_received),
                result_summary_json=_json_dumps(summary),
            )
            conn.execute(
                """
                INSERT INTO broadcast_job_events (
                    job_id, event_type, from_status, to_status, event_payload, actor_type, actor_id
                ) VALUES (%s, 'dispatch_reconciliation_required', 'dispatching', 'unknown_after_dispatch',
                          CAST(%s AS jsonb), 'worker', %s)
                """,
                (int(job_id), _json_dumps(summary), token[:200]),
            )
            return updated


def _summary(*, limit: int, dry_run: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "job": "broadcast_queue_worker",
        "limit": int(limit),
        "dry_run": bool(dry_run),
        "scanned_at": utcnow().isoformat(),
        "claimed": 0,
        "delegated": 0,
        "sent_ok": 0,
        "simulated": 0,
        "sent_failed": 0,
        "unknown_after_dispatch": 0,
        "skipped": 0,
        "results": [],
        "errors": [],
        "real_external_call_executed": False,
    }


_TERMINAL_PRE_PROVIDER_FAILURES = {
    WECOM_EXECUTION_DISABLED_CODE,
    "before_external_call",
    "content_text_or_attachment_missing",
    "identity_external_userid_missing",
    "material_resolve_failed",
    "next_native_dispatch_skipped",
    "production_guard_failed",
    "sender_userid_missing",
    "target_count_mismatch",
    "target_unionids_missing",
    "validation_failed",
    "wecom_group_message_disabled",
}

_AMBIGUOUS_PROVIDER_FAILURES = {
    "external_call_unknown",
    "wecom_group_exact_target_not_verified",
    "wecom_group_message_partial_failure",
}


def _normalize_dispatch_outcome(job: dict[str, Any], raw: Any) -> dict[str, Any]:
    outcome = dict(raw) if isinstance(raw, dict) else {}
    raw_status = _text(outcome.get("status")).lower()
    failure_type = _text(outcome.get("failure_type") or outcome.get("error_code"))
    error = _text(outcome.get("error") or outcome.get("reason") or outcome.get("error_message"))
    response_payload = _json_dict(outcome.get("response_payload"))
    request_payload = _json_dict(outcome.get("request_payload"))
    side_effect_explicit = "side_effect_executed" in outcome
    side_effect_executed = bool(outcome.get("side_effect_executed")) if side_effect_explicit else bool(outcome.get("ok"))
    provider_explicit = "provider_result_received" in outcome
    provider_result_received = (
        bool(outcome.get("provider_result_received"))
        if provider_explicit
        else side_effect_executed
        and bool(
            _json_dict(response_payload.get("result")) or outcome.get("wecom_msgid") or response_payload.get("wecom_msgid") or response_payload.get("msgid")
        )
    )
    simulated = raw_status == "simulated" or _is_simulated_success(outcome)
    if raw_status == "delegated" and outcome.get("ok"):
        final_status = "delegated"
        side_effect_executed = False
        provider_result_received = False
    elif simulated:
        final_status = "simulated"
        side_effect_executed = False
        provider_result_received = False
    elif outcome.get("ok"):
        final_status = "sent"
    elif raw_status == "unknown_after_dispatch" or failure_type in _AMBIGUOUS_PROVIDER_FAILURES:
        final_status = "unknown_after_dispatch"
    elif side_effect_executed and not provider_result_received:
        final_status = "unknown_after_dispatch"
    elif side_effect_executed:
        provider_result = _json_dict(response_payload.get("result"))
        if provider_result and int_value(provider_result.get("errcode")) != 0:
            final_status = "failed_retryable"
        else:
            final_status = "unknown_after_dispatch"
    elif raw_status == "skipped" or failure_type in _TERMINAL_PRE_PROVIDER_FAILURES:
        final_status = "blocked"
    else:
        final_status = "failed_retryable"
    if not failure_type and not outcome.get("ok"):
        failure_type = "next_native_dispatch_skipped" if raw_status == "skipped" else "handler_error"
    if not error and not outcome.get("ok"):
        error = "next_native_dispatch_failed"
    target_count = int_value(outcome.get("target_count")) or _count_targets(job)
    sent_count = int_value(outcome.get("sent_count")) if final_status == "sent" else 0
    if final_status == "sent" and sent_count == 0:
        sent_count = target_count
    failed_count = int_value(outcome.get("failed_count"))
    if final_status not in {"delegated", "sent", "simulated"} and failed_count == 0:
        failed_count = target_count
    return {
        **outcome,
        "status": final_status,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "target_count": target_count,
        "failure_type": failure_type,
        "error": error,
        "side_effect_executed": side_effect_executed,
        "provider_result_received": provider_result_received,
        "request_payload": request_payload,
        "response_payload": response_payload,
        "task_type": _text(outcome.get("task_type")) or "broadcast_job/group_ops",
        "was_skipped": raw_status == "skipped",
    }


def _count_targets(job: dict[str, Any]) -> int:
    return len(json_list(job.get("target_unionids_json"))) or int_value(job.get("target_count"))


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_simulated_success(result: dict[str, Any]) -> bool:
    mode = _text(result.get("mode") or result.get("adapter_mode")).lower()
    return bool(result.get("ok")) and mode in {"fake", "fixture", "simulated", "test_fake"} and result.get("side_effect_executed") is False


def _is_wecom_customer_group_job(job: dict[str, Any], payload: dict[str, Any]) -> bool:
    return (
        str(payload.get("channel") or "").strip() == "wecom_customer_group"
        or str(job.get("content_type") or "").strip() == "wecom_customer_group"
        or str(job.get("channel") or "").strip() == "wecom_customer_group"
    )


def _is_wecom_private_job(job: dict[str, Any], payload: dict[str, Any]) -> bool:
    return (
        _text(payload.get("channel")) == "wecom_private"
        or _text(job.get("channel")) == "wecom_private"
        or (
            _text(job.get("source_type")) == "campaign"
            and _text(job.get("source_table")) == "campaign_members"
            and _text(job.get("content_type")) == "private_message"
        )
    )


def _extract_target_unionids(job: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    values = _json_list(job.get("target_unionids_json")) or _json_list(payload.get("target_unionids"))
    return [_text(item) for item in values if _text(item)]


def _resolve_private_targets_by_unionid(unionids: list[str]) -> tuple[list[str], list[str]]:
    unique_unionids = []
    for unionid in unionids:
        if unionid and unionid not in unique_unionids:
            unique_unionids.append(unionid)
    if not unique_unionids:
        return [], []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT unionid, primary_external_userid
            FROM crm_user_identity
            WHERE unionid = ANY(%s)
              AND COALESCE(primary_external_userid, '') <> ''
            """,
            (unique_unionids,),
        ).fetchall()
    by_unionid = {_text(dict(row).get("unionid")): _text(dict(row).get("primary_external_userid")) for row in rows}
    targets = [by_unionid[unionid] for unionid in unique_unionids if by_unionid.get(unionid)]
    missing = [unionid for unionid in unique_unionids if not by_unionid.get(unionid)]
    return targets, missing


def _extract_private_text(payload: dict[str, Any]) -> str:
    rendered = payload.get("rendered_content") if isinstance(payload.get("rendered_content"), dict) else {}
    step = payload.get("step") if isinstance(payload.get("step"), dict) else {}
    return _text(rendered.get("content_text") or rendered.get("text") or payload.get("content_text") or payload.get("text") or step.get("content_text"))


def _configured_wecom_sender(fallback: str = "") -> str:
    raw = runtime_setting("AICRM_EXTERNAL_EFFECT_ALLOWED_OWNER_USERIDS", "")
    candidates = [item.strip() for item in raw.replace("\n", ",").replace(" ", ",").split(",") if item.strip()]
    return candidates[0] if candidates else _text(fallback)


def _extract_private_sender(payload: dict[str, Any]) -> str:
    campaign = payload.get("campaign") if isinstance(payload.get("campaign"), dict) else {}
    fallback = _text(payload.get("sender_userid") or payload.get("owner_userid") or campaign.get("owner_userid"))
    return _configured_wecom_sender(fallback)


def _load_cloud_plan_recipient_message(payload: dict[str, Any]) -> dict[str, Any]:
    if _text(payload.get("message_mode")) != "recipient_messages":
        return {}
    plan_id = _text(payload.get("plan_id"))
    recipient_id = int_value(payload.get("recipient_id"))
    if not plan_id or not recipient_id:
        return {}
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, recipient_id, content_text, content_payload_json, attachments_json
            FROM cloud_broadcast_plan_recipient_messages
            WHERE plan_id = %s
              AND recipient_id = %s
              AND status IN ('queued', 'pending', 'dispatching')
            ORDER BY sequence_index ASC, id ASC
            LIMIT 1
            """,
            (plan_id, recipient_id),
        ).fetchone()
    if not row:
        return {}
    return {
        "cloud_plan_message_id": int_value(row.get("id")),
        "content_text": _text(row.get("content_text")),
        "content_payload_json": _json_dict(row.get("content_payload_json")),
        "attachments": _json_list(row.get("attachments_json")),
    }


def _with_cloud_plan_recipient_message(payload: dict[str, Any]) -> dict[str, Any]:
    message = _load_cloud_plan_recipient_message(payload)
    if not message:
        return payload
    hydrated = dict(payload)
    if message.get("content_text"):
        hydrated["content_text"] = message.get("content_text")
    if message.get("content_payload_json"):
        hydrated["content_payload_json"] = message.get("content_payload_json")
    if message.get("attachments"):
        hydrated["attachments"] = message.get("attachments")
    if message.get("cloud_plan_message_id"):
        hydrated["cloud_plan_message_id"] = message.get("cloud_plan_message_id")
    return hydrated


def _merge_content_package(target: dict[str, Any], source: Any) -> None:
    source_dict = _json_dict(source)
    if not source_dict:
        return
    for nested_key in ("content_payload_json", "content_package_json", "content_package", "attachments"):
        nested = _json_dict(source_dict.get(nested_key))
        if nested:
            _merge_content_package(target, nested)
    for key in ("image_library_ids", "miniprogram_library_ids", "attachment_library_ids", "group_invite_library_ids"):
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


def _extract_private_content_package(payload: dict[str, Any]) -> dict[str, Any]:
    rendered = payload.get("rendered_content") if isinstance(payload.get("rendered_content"), dict) else {}
    step = payload.get("step") if isinstance(payload.get("step"), dict) else {}
    content_package: dict[str, Any] = {}
    for source in (payload, rendered, step):
        _merge_content_package(content_package, source)
    return content_package


def _with_dynamic_miniprogram_attachment(payload: dict[str, Any]) -> dict[str, Any]:
    content_package = _extract_private_content_package(payload)
    card = content_package.get("dynamic_miniprogram_card")
    if not isinstance(card, dict) or not card:
        return payload
    if _text(runtime_setting("AICRM_DYNAMIC_MINIPROGRAM_CARD_V1_ENABLED", "")).lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise ValueError("dynamic_card_feature_disabled")
    resolver = _dynamic_miniprogram_attachment_resolver
    if resolver is None:
        raise ValueError("dynamic_card_resolver_not_configured")
    attachment = resolver(card)
    hydrated = dict(payload)
    existing = _json_list(payload.get("attachments")) or _json_list(payload.get("attachments_json"))
    hydrated["attachments"] = [*existing, attachment]
    return hydrated


def _resolve_private_attachments(content_package: dict[str, Any]) -> list[dict[str, Any]]:
    resolver = _content_package_attachment_resolver
    if resolver is None:
        raise ValueError("content_package_attachment_resolver_not_configured")
    plan = resolver(content_package)
    return [dict(item) for item in _json_list(plan.get("attachments")) if isinstance(item, dict)]


def _with_content_package_attachments(payload: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(payload)
    content_package = _extract_private_content_package(hydrated)
    direct = _json_list(hydrated.get("attachments")) or _json_list(hydrated.get("attachments_json"))
    has_library_materials = any(
        _json_list(content_package.get(field))
        for field in (
            "image_library_ids",
            "miniprogram_library_ids",
            "attachment_library_ids",
            "group_invite_library_ids",
        )
    ) or isinstance(content_package.get("dynamic_miniprogram_card"), dict)
    if not has_library_materials:
        if direct:
            attachments = _dedupe_private_attachments(direct)
            if len(attachments) > 9:
                raise ValueError("private_message_attachments_exceed_limit")
            hydrated["attachments"] = attachments
        return hydrated

    resolver = _content_package_attachment_resolver
    if resolver is None:
        raise ValueError("content_package_attachment_resolver_not_configured")
    material_plan = resolver(content_package)
    resolved = [dict(item) for item in _json_list(material_plan.get("attachments")) if isinstance(item, dict)]
    attachments = _dedupe_private_attachments([*direct, *resolved])
    if len(attachments) > 9:
        raise ValueError("private_message_attachments_exceed_limit")
    result = dict(hydrated)
    result["attachments"] = attachments
    result["_material_uploads"] = [dict(item) for item in _json_list(material_plan.get("uploads")) if isinstance(item, dict)]
    return result


def _dedupe_private_attachments(attachments: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("private_message_attachment_must_be_object")
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _normalize_private_attachments_for_wecom(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del attachments
    raise RuntimeError("retired_direct_broadcast_material_normalization_use_external_effect")


def _dispatch_wecom_private(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("retired_direct_broadcast_dispatch_use_external_effect")
    if explicit_wecom_execution_disabled():
        return {
            "ok": False,
            "error": wecom_execution_disabled_message(),
            "failure_type": WECOM_EXECUTION_DISABLED_CODE,
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    payload = _with_cloud_plan_recipient_message(payload)
    target_unionids = _extract_target_unionids(job, payload)
    targets, missing_unionids = _resolve_private_targets_by_unionid(target_unionids)
    target_count = int_value(job.get("target_count"))
    sender_userid = _extract_private_sender(payload)
    content_text = _extract_private_text(payload)
    if not target_unionids:
        return {
            "ok": False,
            "error": "target_unionids_missing",
            "failure_type": "validation_failed",
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    if missing_unionids:
        return {
            "ok": False,
            "error": "identity_external_userid_missing",
            "failure_type": "identity_external_userid_missing",
            "missing_unionids": missing_unionids,
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    if target_count != len(target_unionids):
        return {
            "ok": False,
            "error": "target_count_mismatch",
            "failure_type": "validation_failed",
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    if not sender_userid:
        return {
            "ok": False,
            "error": "sender_userid_missing",
            "failure_type": "validation_failed",
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    content_package = _extract_private_content_package(payload)
    try:
        attachments = _resolve_private_attachments(content_package)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "failure_type": "material_resolve_failed",
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    try:
        attachments = _normalize_private_attachments_for_wecom(attachments)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "failure_type": "material_resolve_failed",
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    direct_attachments = _json_list(payload.get("attachments")) or _json_list(payload.get("attachments_json"))
    if direct_attachments:
        attachments = direct_attachments + attachments
    if not content_text and not attachments:
        return {
            "ok": False,
            "error": "content_text_or_attachment_missing",
            "failure_type": "validation_failed",
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_private",
        }
    request_payload = {
        "job_id": int_value(job.get("id")),
        "source_type": _text(job.get("source_type")),
        "source_id": _text(job.get("source_id")),
        "sender_userid": sender_userid,
        "target_unionids": target_unionids,
        "external_userids": targets,
        "content_hash": _json_dict(payload.get("rendered_content")).get("content_hash") or "",
        "content_preview": content_text[:120],
    }
    if content_text:
        request_payload["text"] = {"content": content_text}
    if attachments:
        request_payload["attachments"] = attachments
    adapter_payload = {"sender": sender_userid, "external_userids": targets}
    if content_text:
        adapter_payload["text"] = {"content": content_text}
    if attachments:
        adapter_payload["attachments"] = attachments
    result: dict[str, Any] = {}
    failure_type = _text(result.get("error_code")) or "handler_error"
    simulated = _is_simulated_success(result)
    side_effect_executed = bool(result.get("side_effect_executed"))
    provider_result_received = side_effect_executed and bool(_json_dict(result.get("result")))
    evidence = {
        "request_payload": request_payload,
        "response_payload": result,
        "task_type": "broadcast_job/wecom_private",
        "side_effect_executed": side_effect_executed,
        "provider_result_received": provider_result_received,
    }
    if not result.get("ok"):
        return {
            "ok": False,
            "error": _text(result.get("error_message") or result.get("error_code") or "wecom private message dispatch failed"),
            "failure_type": failure_type,
            **evidence,
        }
    if simulated:
        return {
            "ok": True,
            "status": "simulated",
            "simulated": True,
            "sent_count": 0,
            "failed_count": 0,
            "target_count": len(targets),
            **evidence,
        }
    return {
        "ok": True,
        "status": "sent",
        "sent_count": len(targets),
        "failed_count": 0,
        "wecom_msgid": _text(result.get("wecom_msgid") or _json_dict(result.get("result")).get("msgid")),
        **evidence,
    }


def _dispatch_wecom_customer_group(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("retired_direct_broadcast_dispatch_use_external_effect")
    if explicit_wecom_execution_disabled():
        return {
            "ok": False,
            "error": wecom_execution_disabled_message(),
            "failure_type": WECOM_EXECUTION_DISABLED_CODE,
            "side_effect_executed": False,
            "provider_result_received": False,
            "task_type": "broadcast_job/wecom_group",
        }
    try:
        result: dict[str, Any] = {}
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "failure_type": "validation_failed",
            "request_payload": payload,
            "response_payload": {},
            "task_type": "broadcast_job/wecom_group",
            "side_effect_executed": False,
            "provider_result_received": False,
        }
    side_effect_executed = bool(result.get("side_effect_executed"))
    provider_result_received = side_effect_executed and bool(_json_dict(result.get("result")))
    evidence = {
        "request_payload": payload,
        "response_payload": result,
        "task_type": "broadcast_job/wecom_group",
        "side_effect_executed": side_effect_executed,
        "provider_result_received": provider_result_received,
    }
    if result.get("ok") and result.get("exact_target_verified") is not True:
        chats = ",".join([str(item) for item in list(result.get("requested_chat_ids") or payload.get("chat_ids") or [])])
        return {
            "ok": False,
            "error": f"exact target not verified for requested chat ids: {chats}",
            "failure_type": "wecom_group_exact_target_not_verified",
            **evidence,
        }
    simulated = _is_simulated_success(result)
    if not result.get("ok"):
        error = str(result.get("error_message") or result.get("error_code") or "wecom group message dispatch failed")
        return {
            "ok": False,
            "error": error,
            "failure_type": _text(result.get("error_code")) or "handler_error",
            **evidence,
        }
    if simulated:
        return {
            "ok": True,
            "status": "simulated",
            "simulated": True,
            "sent_count": 0,
            "failed_count": 0,
            "target_count": len(list(payload.get("chat_ids") or [])),
            **evidence,
        }
    return {
        "ok": True,
        "status": "sent",
        "sent_count": len(list(payload.get("chat_ids") or [])),
        "failed_count": 0,
        "wecom_msgid": _text(result.get("wecom_msgid") or _json_dict(result.get("result")).get("msgid")),
        **evidence,
    }


def run_broadcast_queue_worker(
    *,
    limit: int = 50,
    dry_run: bool = False,
    repo: BroadcastQueueRepository | None = None,
    dispatcher: BroadcastDispatcher | None = None,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> dict[str, Any]:
    summary = _summary(limit=limit, dry_run=dry_run)
    if int(limit) <= 0:
        return {**summary, "ok": False, "errors": [{"code": "invalid_limit", "message": "limit must be >= 1"}]}
    if dry_run and repo is None:
        return {
            **summary,
            "status": "skipped",
            "skipped": 1,
            "skipped_components": [{"component": "postgres_repository", "status": "skipped", "reason": "dry_run"}],
        }
    if repo is None and not has_database_url():
        return {**summary, "ok": False, "errors": [{"code": "database_url_missing", "message": "DATABASE_URL is required"}]}

    repo = repo or PostgresBroadcastQueueRepository()
    dispatcher = dispatcher or SafeSkippedBroadcastDispatcher()
    current_time = now or utcnow()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    lease = int(
        lease_seconds
        or managed_runtime_int(
            "BROADCAST_QUEUE_LEASE_SECONDS",
            900,
            minimum=1,
        )
    )
    try:
        claim_token = f"{os.getpid()}:{uuid.uuid4().hex}"
        jobs = repo.claim_due_jobs(limit=int(limit), now=current_time, claim_token=claim_token, lease_seconds=lease)
        summary["claimed"] = len(jobs)
        for job in jobs:
            job_id = int(job.get("id") or 0)
            try:
                dispatching_job = repo.begin_dispatch(job_id, claim_token=claim_token, now=current_time)
                if dispatching_job is None:
                    summary["ok"] = False
                    summary["sent_failed"] += 1
                    summary["results"].append(
                        {
                            "id": job_id,
                            "status": "claim_lost",
                            "reason": "broadcast dispatch ownership was lost before provider call",
                        }
                    )
                    continue
                try:
                    outcome = _normalize_dispatch_outcome(dispatching_job, dispatcher.dispatch(dispatching_job))
                except Exception as exc:
                    reason = str(exc)
                    repo.mark_unknown_after_dispatch(
                        job_id,
                        claim_token=claim_token,
                        error=reason,
                        side_effect_executed=True,
                        provider_result_received=False,
                    )
                    summary["ok"] = False
                    summary["sent_failed"] += 1
                    summary["unknown_after_dispatch"] += 1
                    summary["results"].append(
                        {
                            "id": job_id,
                            "status": "unknown_after_dispatch",
                            "reason": reason,
                            "failure_type": "dispatcher_exception_after_dispatch_started",
                        }
                    )
                    continue
                try:
                    finalized = repo.finalize_dispatch(job_id, claim_token=claim_token, outcome=outcome)
                    if finalized is None:
                        raise RuntimeError("broadcast finalizer lost dispatch ownership")
                except Exception as exc:
                    reason = str(exc)
                    repo.mark_unknown_after_dispatch(
                        job_id,
                        claim_token=claim_token,
                        error=reason,
                        side_effect_executed=bool(outcome.get("side_effect_executed")),
                        provider_result_received=bool(outcome.get("provider_result_received")),
                    )
                    summary["ok"] = False
                    summary["sent_failed"] += 1
                    summary["unknown_after_dispatch"] += 1
                    summary["results"].append(
                        {
                            "id": job_id,
                            "status": "unknown_after_dispatch",
                            "reason": reason,
                            "failure_type": "finalization_failed_after_dispatch",
                        }
                    )
                    continue
                status = _text(outcome.get("status"))
                if status == "sent":
                    summary["sent_ok"] += 1
                elif status == "delegated":
                    summary["delegated"] += 1
                elif status == "simulated":
                    summary["simulated"] += 1
                else:
                    summary["sent_failed"] += 1
                    if outcome.get("was_skipped"):
                        summary["skipped"] += 1
                    if status == "unknown_after_dispatch":
                        summary["ok"] = False
                        summary["unknown_after_dispatch"] += 1
                result_item = {"id": job_id, "status": status}
                if status == "sent":
                    result_item["sent_count"] = int_value(outcome.get("sent_count"))
                elif status == "delegated":
                    result_item.update(
                        {
                            "target_count": int_value(outcome.get("target_count")),
                            "external_effect_job_ids": list(outcome.get("external_effect_job_ids") or []),
                            "side_effect_executed": False,
                        }
                    )
                elif status == "simulated":
                    result_item.update(
                        {
                            "target_count": int_value(outcome.get("target_count")),
                            "side_effect_executed": False,
                        }
                    )
                else:
                    result_item.update(
                        {
                            "reason": _text(outcome.get("error")),
                            "failure_type": _text(outcome.get("failure_type")),
                        }
                    )
                summary["results"].append(result_item)
            except Exception as exc:
                reason = str(exc)
                summary["ok"] = False
                summary["sent_failed"] += 1
                summary["results"].append(
                    {
                        "id": job_id,
                        "status": "worker_error",
                        "reason": reason,
                        "failure_type": "worker_exception",
                    }
                )
        return summary
    except Exception as exc:
        return {**summary, "ok": False, "errors": [{"code": "broadcast_queue_worker_failed", "message": str(exc)}]}
