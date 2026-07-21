from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import DEFAULT_TENANT_ID, ExternalEffectCreateRequest, ExternalEffectJob, public_datetime, utcnow
from .repo import _execution_lane, _idempotency_key, _initial_status, _payload_summary, _public_job, _rate_scope_key


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str, separators=(",", ":"))


def enqueue_transactional_external_effect_job(conn: Any, request: ExternalEffectCreateRequest) -> ExternalEffectJob:
    """Insert an External Effect job through the caller-owned transaction.

    The helper deliberately never commits or rolls back. This lets a business
    record, its audit event, and the durable provider continuation share one
    PostgreSQL durability boundary.
    """

    tenant_id = str(request.tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    idempotency_key = _idempotency_key(request)
    payload_summary = dict(request.payload_summary or {}) or _payload_summary(request.payload)
    scheduled_at = request.scheduled_at or utcnow()
    available_at = request.available_at or scheduled_at
    execution_id = str(request.execution_id or "").strip() or "exe_" + uuid4().hex
    ordering_key = str(request.ordering_key or request.target_id or f"effect:{idempotency_key}").strip()
    fairness_key = str(request.fairness_key or request.business_id or request.target_id or "default").strip()
    rate_scope_key = _rate_scope_key(request)
    row = conn.execute(
        """
        INSERT INTO external_effect_job (
            tenant_id, effect_type, adapter_name, operation, target_type, target_id,
            business_type, business_id, source_module, source_route, source_event_id,
            source_command_id, trace_id, request_id, correlation_id, idempotency_key,
            execution_id, parent_execution_id, lane, available_at,
            ordering_key, fairness_key, rate_scope_key, policy_version,
            actor_id, actor_type, risk_level, requires_approval, execution_mode,
            payload_json, payload_summary_json, status, priority, scheduled_at,
            attempt_count, max_attempts, created_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s::timestamptz,
            %s, %s, %s,
            (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
            %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s, %s, %s::timestamptz,
            0, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
        RETURNING *
        """,
        (
            tenant_id,
            str(request.effect_type or "").strip(),
            str(request.adapter_name or "").strip(),
            str(request.operation or "").strip(),
            str(request.target_type or "").strip(),
            str(request.target_id or "").strip(),
            str(request.business_type or "").strip(),
            str(request.business_id or "").strip(),
            str(request.source_module or "").strip(),
            str(request.context.source_route or "").strip(),
            str(request.source_event_id or "").strip(),
            str(request.source_command_id or "").strip(),
            str(request.context.trace_id or "").strip(),
            str(request.context.request_id or "").strip(),
            str(request.correlation_id or "").strip(),
            idempotency_key,
            execution_id,
            str(request.parent_execution_id or "").strip(),
            _execution_lane(request),
            public_datetime(available_at),
            ordering_key,
            fairness_key,
            rate_scope_key,
            str(request.context.actor_id or "").strip(),
            str(request.context.actor_type or "system").strip() or "system",
            str(request.risk_level or "medium").strip() or "medium",
            bool(request.requires_approval),
            str(request.execution_mode or "execute").strip() or "execute",
            _json(request.payload),
            _json(payload_summary),
            _initial_status(request),
            int(request.priority or 100),
            public_datetime(scheduled_at),
            int(request.max_attempts or 5),
        ),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM external_effect_job WHERE tenant_id = %s AND idempotency_key = %s LIMIT 1",
            (tenant_id, idempotency_key),
        ).fetchone()
    job = _public_job(dict(row)) if row else None
    if job is None:
        raise RuntimeError("transactional external effect idempotent create failed")
    return job


def enqueue_external_effect_job_in_session(session: Session, request: ExternalEffectCreateRequest) -> ExternalEffectJob:
    """Insert an External Effect through a caller-owned SQLAlchemy transaction.

    Group Ops and other application commands use this variant when business
    rows and a complete effect graph must cross the durability boundary
    together.  The helper intentionally never commits or rolls back.
    """

    tenant_id = str(request.tenant_id or DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID
    idempotency_key = _idempotency_key(request)
    payload_summary = dict(request.payload_summary or {}) or _payload_summary(request.payload)
    scheduled_at = request.scheduled_at or utcnow()
    available_at = request.available_at or scheduled_at
    execution_id = str(request.execution_id or "").strip() or "exe_" + uuid4().hex
    ordering_key = str(request.ordering_key or request.target_id or f"effect:{idempotency_key}").strip()
    fairness_key = str(request.fairness_key or request.business_id or request.target_id or "default").strip()
    params = {
        "tenant_id": tenant_id,
        "effect_type": str(request.effect_type or "").strip(),
        "adapter_name": str(request.adapter_name or "").strip(),
        "operation": str(request.operation or "").strip(),
        "target_type": str(request.target_type or "").strip(),
        "target_id": str(request.target_id or "").strip(),
        "business_type": str(request.business_type or "").strip(),
        "business_id": str(request.business_id or "").strip(),
        "source_module": str(request.source_module or "").strip(),
        "source_route": str(request.context.source_route or "").strip(),
        "source_event_id": str(request.source_event_id or "").strip(),
        "source_command_id": str(request.source_command_id or "").strip(),
        "trace_id": str(request.context.trace_id or "").strip(),
        "request_id": str(request.context.request_id or "").strip(),
        "correlation_id": str(request.correlation_id or "").strip(),
        "idempotency_key": idempotency_key,
        "execution_id": execution_id,
        "parent_execution_id": str(request.parent_execution_id or "").strip(),
        "lane": _execution_lane(request),
        "available_at": public_datetime(available_at),
        "ordering_key": ordering_key,
        "fairness_key": fairness_key,
        "rate_scope_key": _rate_scope_key(request),
        "actor_id": str(request.context.actor_id or "").strip(),
        "actor_type": str(request.context.actor_type or "system").strip() or "system",
        "risk_level": str(request.risk_level or "medium").strip() or "medium",
        "requires_approval": bool(request.requires_approval),
        "execution_mode": str(request.execution_mode or "execute").strip() or "execute",
        "payload_json": _json(request.payload),
        "payload_summary_json": _json(payload_summary),
        "status": _initial_status(request),
        "priority": int(request.priority or 100),
        "scheduled_at": public_datetime(scheduled_at),
        "max_attempts": int(request.max_attempts or 5),
    }
    row = (
        session.execute(
            text(
                """
                INSERT INTO external_effect_job (
                    tenant_id, effect_type, adapter_name, operation, target_type, target_id,
                    business_type, business_id, source_module, source_route, source_event_id,
                    source_command_id, trace_id, request_id, correlation_id, idempotency_key,
                    execution_id, parent_execution_id, lane, available_at,
                    ordering_key, fairness_key, rate_scope_key, policy_version,
                    actor_id, actor_type, risk_level, requires_approval, execution_mode,
                    payload_json, payload_summary_json, status, priority, scheduled_at,
                    attempt_count, max_attempts, created_at, updated_at
                ) VALUES (
                    :tenant_id, :effect_type, :adapter_name, :operation, :target_type, :target_id,
                    :business_type, :business_id, :source_module, :source_route, :source_event_id,
                    :source_command_id, :trace_id, :request_id, :correlation_id, :idempotency_key,
                    :execution_id, :parent_execution_id, :lane, CAST(:available_at AS timestamptz),
                    :ordering_key, :fairness_key, :rate_scope_key,
                    (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
                    :actor_id, :actor_type, :risk_level, :requires_approval, :execution_mode,
                    CAST(:payload_json AS jsonb), CAST(:payload_summary_json AS jsonb), :status,
                    :priority, CAST(:scheduled_at AS timestamptz), 0, :max_attempts,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING *, TRUE AS created_on_plan
                """
            ),
            params,
        )
        .mappings()
        .fetchone()
    )
    if not row:
        row = (
            session.execute(
                text("SELECT *, FALSE AS created_on_plan FROM external_effect_job WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key LIMIT 1"),
                {"tenant_id": tenant_id, "idempotency_key": idempotency_key},
            )
            .mappings()
            .fetchone()
        )
    job = _public_job(dict(row)) if row else None
    if job is None:
        raise RuntimeError("transactional external effect idempotent create failed")
    return job


__all__ = [
    "enqueue_external_effect_job_in_session",
    "enqueue_transactional_external_effect_job",
]
