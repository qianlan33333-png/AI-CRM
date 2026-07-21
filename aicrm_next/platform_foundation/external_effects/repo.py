# ruff: noqa: F401
from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform_foundation.external_calls import scrub_summary
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime import fixture_mode

from .models import (
    DEFAULT_TENANT_ID,
    ExternalEffectAttempt,
    ExternalEffectCreateRequest,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
    ExternalEffectTestReceipt,
    public_datetime,
    utcnow,
)
from .settlement_events import enqueue_external_effect_terminal_events_in_session
from .canary_repository import ExternalEffectCanaryAuthorizationRepositoryMixin
from .direct_claim_repository import ExternalEffectDirectClaimRepositoryMixin
from .provider_result_repository import ExternalEffectProviderResultRepositoryMixin, encode_provider_result
from .rate_limit import persist_rate_limit_cooldown
from .repo_contract import (
    ExternalEffectRepository,
    _execution_lane,
    _idempotency_key,
    _initial_status,
    _json_dumps,
    _json_obj,
    _payload_summary,
    _public_attempt,
    _public_job,
    _public_receipt,
    _rate_scope_key,
    _safe_error_message,
    _text,
)
class SQLAlchemyExternalEffectRepository(
    ExternalEffectCanaryAuthorizationRepositoryMixin,
    ExternalEffectDirectClaimRepositoryMixin,
    ExternalEffectProviderResultRepositoryMixin,
    ExternalEffectRepository,
):
    def __init__(self, session_factory: Callable[[], Session] | None = None):
        self._session_factory = session_factory or get_session_factory()
    def _one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params or {}).mappings().fetchone()
            return dict(row) if row else None
    def _all(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(text(statement), params or {}).mappings().fetchall()
            return [dict(row) for row in rows]
    def _write_one(self, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.execute(text(statement), params or {}).mappings().fetchone()
            session.commit()
            return dict(row) if row else None
    def _write_all(self, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.execute(text(statement), params or {}).mappings().fetchall()
            session.commit()
            return [dict(row) for row in rows]
    def create_job(self, request: ExternalEffectCreateRequest) -> ExternalEffectJob:
        key = _idempotency_key(request)
        scheduled_at = request.scheduled_at or utcnow()
        payload_summary = dict(request.payload_summary or {}) or _payload_summary(request.payload)
        row = self._write_one(
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
            )
            VALUES (
                :tenant_id, :effect_type, :adapter_name, :operation, :target_type, :target_id,
                :business_type, :business_id, :source_module, :source_route, :source_event_id,
                :source_command_id, :trace_id, :request_id, :correlation_id, :idempotency_key,
                :execution_id, :parent_execution_id, :lane, CAST(:available_at AS timestamptz),
                :ordering_key, :fairness_key, :rate_scope_key, (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
                :actor_id, :actor_type, :risk_level, :requires_approval, :execution_mode,
                CAST(:payload_json AS jsonb), CAST(:payload_summary_json AS jsonb), :status,
                :priority, CAST(:scheduled_at AS timestamptz), 0, :max_attempts,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING *, TRUE AS created_on_plan
            """,
            {
                "tenant_id": _text(request.tenant_id) or DEFAULT_TENANT_ID,
                "effect_type": _text(request.effect_type),
                "adapter_name": _text(request.adapter_name),
                "operation": _text(request.operation),
                "target_type": _text(request.target_type),
                "target_id": _text(request.target_id),
                "business_type": _text(request.business_type),
                "business_id": _text(request.business_id),
                "source_module": _text(request.source_module),
                "source_route": _text(request.context.source_route),
                "source_event_id": _text(request.source_event_id),
                "source_command_id": _text(request.source_command_id),
                "trace_id": _text(request.context.trace_id),
                "request_id": _text(request.context.request_id),
                "correlation_id": _text(request.correlation_id),
                "idempotency_key": key,
                "execution_id": _text(request.execution_id) or "exe_" + uuid4().hex,
                "parent_execution_id": _text(request.parent_execution_id),
                "lane": _execution_lane(request),
                "available_at": public_datetime(request.available_at or scheduled_at),
                "ordering_key": _text(request.ordering_key) or _text(request.target_id) or f"effect:{key}",
                "fairness_key": _text(request.fairness_key) or _text(request.business_id) or _text(request.target_id) or "default",
                "rate_scope_key": _rate_scope_key(request),
                "actor_id": _text(request.context.actor_id),
                "actor_type": _text(request.context.actor_type) or "system",
                "risk_level": _text(request.risk_level) or "medium",
                "requires_approval": bool(request.requires_approval),
                "execution_mode": _text(request.execution_mode) or "execute",
                "payload_json": _json_dumps(request.payload),
                "payload_summary_json": _json_dumps(payload_summary),
                "status": _initial_status(request),
                "priority": int(request.priority or 100),
                "scheduled_at": public_datetime(scheduled_at),
                "max_attempts": int(request.max_attempts or 5),
            },
        )
        if row:
            job = _public_job(row)
            assert job is not None
            return job
        existing = self._one(
            "SELECT *, FALSE AS created_on_plan FROM external_effect_job WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key LIMIT 1",
            {"tenant_id": _text(request.tenant_id) or DEFAULT_TENANT_ID, "idempotency_key": key},
        )
        job = _public_job(existing)
        if job is None:
            raise RuntimeError("external effect idempotent create failed")
        return job
    def get_job(self, job_id: int) -> ExternalEffectJob | None:
        return _public_job(self._one("SELECT * FROM external_effect_job WHERE id = :job_id LIMIT 1", {"job_id": int(job_id)}))
    def list_jobs(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[ExternalEffectJob], int]:
        filters = dict(filters or {})
        clauses: list[str] = []
        params: dict[str, Any] = {}
        for key in (
            "effect_type",
            "status",
            "target_type",
            "target_id",
            "business_type",
            "business_id",
            "trace_id",
            "source_event_id",
            "source_module",
        ):
            value = _text(filters.get(key))
            if value:
                clauses.append(f"{key} = :{key}")
                params[key] = value
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        count_row = self._one(f"SELECT COUNT(*) AS total FROM external_effect_job {where}", params)
        rows = self._all(
            f"""
            SELECT *
            FROM external_effect_job
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": max(1, min(int(limit or 50), 200)), "offset": max(0, int(offset or 0))},
        )
        return [job for row in rows if (job := _public_job(row)) is not None], int((count_row or {}).get("total") or 0)

    def list_attempts(self, job_id: int) -> list[ExternalEffectAttempt]:
        rows = self._all(
            "SELECT * FROM external_effect_attempt WHERE job_id = :job_id ORDER BY id ASC",
            {"job_id": int(job_id)},
        )
        return [attempt for row in rows if (attempt := _public_attempt(row)) is not None]

    def get_attempt(self, attempt_id: str) -> ExternalEffectAttempt | None:
        return _public_attempt(
            self._one(
                "SELECT * FROM external_effect_attempt WHERE attempt_id = :attempt_id LIMIT 1",
                {"attempt_id": _text(attempt_id)},
            )
        )

    def list_attempts_for_jobs(self, job_ids: list[int]) -> dict[int, list[ExternalEffectAttempt]]:
        normalized = sorted({int(job_id) for job_id in job_ids})
        grouped: dict[int, list[ExternalEffectAttempt]] = {job_id: [] for job_id in normalized}
        if not normalized:
            return grouped
        placeholders = ", ".join(f":job_id_{index}" for index in range(len(normalized)))
        params = {f"job_id_{index}": job_id for index, job_id in enumerate(normalized)}
        rows = self._all(
            f"SELECT * FROM external_effect_attempt WHERE job_id IN ({placeholders}) ORDER BY job_id ASC, id ASC",
            params,
        )
        for row in rows:
            attempt = _public_attempt(row)
            if attempt is not None:
                grouped.setdefault(int(attempt.job_id), []).append(attempt)
        return grouped

    def count_jobs(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = dict(filters or {})
        clauses: list[str] = []
        params: dict[str, Any] = {}
        for key in (
            "effect_type",
            "status",
            "target_type",
            "target_id",
            "business_type",
            "business_id",
            "trace_id",
            "source_event_id",
            "source_module",
        ):
            value = _text(filters.get(key))
            if value:
                clauses.append(f"{key} = :{key}")
                params[key] = value
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._all(
            f"""
            SELECT status, COUNT(*) AS count
            FROM external_effect_job
            {where}
            GROUP BY status
            """,
            params,
        )
        by_status = {_text(row.get("status")): int(row.get("count") or 0) for row in rows}
        total = sum(by_status.values())
        return {
            "total": total,
            "by_status": by_status,
            "planned": by_status.get("planned", 0),
            "queued": by_status.get("queued", 0),
            "blocked": by_status.get("blocked", 0),
            "simulated": by_status.get("simulated", 0),
            "unknown_after_dispatch": by_status.get("unknown_after_dispatch", 0),
            "failed": by_status.get("failed_retryable", 0) + by_status.get("failed_terminal", 0),
            "succeeded": by_status.get("succeeded", 0),
            "cancelled": by_status.get("cancelled", 0),
        }

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = dict(filters or {})
        clauses: list[str] = []
        params: dict[str, Any] = {}
        for key in (
            "effect_type",
            "status",
            "target_type",
            "target_id",
            "business_type",
            "business_id",
            "trace_id",
            "source_event_id",
            "source_module",
        ):
            value = _text(filters.get(key))
            if value:
                clauses.append(f"{key} = :{key}")
                params[key] = value
        completed_from = _text(filters.get("completed_from"))
        if completed_from:
            clauses.append("completed_at >= CAST(:completed_from AS timestamptz)")
            params["completed_from"] = completed_from
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        row = (
            self._one(
                f"""
            SELECT
                COUNT(*) FILTER (
                    WHERE status IN ('planned', 'approved', 'queued', 'dispatching', 'failed_retryable')
                ) AS raw_open_count,
                COUNT(*) FILTER (
                    WHERE hold_reason <> ''
                      AND status IN ('planned', 'approved', 'queued', 'dispatching', 'failed_retryable')
                ) AS held_count,
                COUNT(*) FILTER (
                    WHERE status IN ('queued', 'failed_retryable')
                      AND hold_reason = ''
                      AND attempt_count < max_attempts
                      AND scheduled_at <= CURRENT_TIMESTAMP
                      AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                      AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                ) AS eligible_due_count,
                COUNT(*) FILTER (
                    WHERE hold_reason = '' AND status = 'queued' AND scheduled_at > CURRENT_TIMESTAMP
                ) AS scheduled_count,
                COUNT(*) FILTER (
                    WHERE hold_reason = '' AND status = 'failed_retryable'
                      AND next_retry_at > CURRENT_TIMESTAMP
                ) AS retry_wait_count,
                COUNT(*) FILTER (
                    WHERE hold_reason = '' AND status = 'dispatching'
                      AND lease_expires_at > CURRENT_TIMESTAMP
                ) AS in_flight_count,
                0::BIGINT AS rate_limited_count,
                COUNT(*) FILTER (
                    WHERE status = 'unknown_after_dispatch' OR reconciliation_required = TRUE
                ) AS unknown_count,
                COUNT(*) FILTER (WHERE status IN ('failed_terminal', 'blocked')) AS dlq_count,
                COUNT(*) FILTER (WHERE status = 'dispatching') AS dispatching_count,
                COUNT(*) FILTER (
                    WHERE status = 'dispatching'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at <= CURRENT_TIMESTAMP
                ) AS stale_dispatching_count,
                COUNT(*) FILTER (WHERE status = 'unknown_after_dispatch') AS unknown_after_dispatch_count,
                COUNT(*) FILTER (WHERE status = 'simulated') AS simulated_count,
                COUNT(*) FILTER (WHERE reconciliation_required = TRUE) AS reconciliation_required_count,
                COUNT(*) FILTER (
                    WHERE status = 'dispatching'
                      AND (lease_token = '' OR lease_expires_at IS NULL)
                ) AS dispatching_without_active_lease_count,
                COUNT(*) FILTER (
                    WHERE status <> 'dispatching'
                      AND (lease_token <> '' OR lease_expires_at IS NOT NULL)
                ) AS lease_on_non_dispatching_count,
                COUNT(*) FILTER (
                    WHERE status = 'succeeded'
                      AND (
                          (
                              side_effect_executed = FALSE
                              AND LOWER(COALESCE(
                                  result_summary_json->>'internal_side_effect_executed',
                                  'false'
                              )) <> 'true'
                          )
                          OR provider_result_received = FALSE
                      )
                ) AS succeeded_without_evidence_count,
                COUNT(*) FILTER (
                    WHERE status = 'succeeded'
                      AND side_effect_executed = FALSE
                      AND LOWER(COALESCE(
                          result_summary_json->>'mode',
                          result_summary_json->>'adapter_mode',
                          ''
                      )) IN ('fake', 'fixture', 'simulated', 'test_fake')
                ) AS simulated_recorded_as_succeeded_count,
                COUNT(*) FILTER (WHERE status = 'failed_retryable') AS failed_retryable_count,
                COUNT(*) FILTER (WHERE status = 'failed_terminal') AS failed_terminal_count,
                COALESCE(
                    EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - MIN(scheduled_at) FILTER (
                        WHERE status = 'queued' AND scheduled_at <= CURRENT_TIMESTAMP
                          AND hold_reason = ''
                          AND attempt_count < max_attempts
                    )),
                    0
                ) AS oldest_queued_age_seconds,
                COALESCE(
                    EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - MIN(COALESCE(next_retry_at, scheduled_at)) FILTER (
                        WHERE status = 'failed_retryable'
                          AND hold_reason = ''
                          AND attempt_count < max_attempts
                          AND scheduled_at <= CURRENT_TIMESTAMP
                          AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                    )),
                    0
                ) AS oldest_failed_retryable_age_seconds
            FROM external_effect_job
            {where}
            """,
                params,
            )
            or {}
        )
        return {
            "raw_open_count": int(row.get("raw_open_count") or 0),
            "held_count": int(row.get("held_count") or 0),
            "eligible_due_count": int(row.get("eligible_due_count") or 0),
            "scheduled_count": int(row.get("scheduled_count") or 0),
            "retry_wait_count": int(row.get("retry_wait_count") or 0),
            "rate_limited_count": int(row.get("rate_limited_count") or 0),
            "in_flight_count": int(row.get("in_flight_count") or 0),
            "unknown_count": int(row.get("unknown_count") or 0),
            "dlq_count": int(row.get("dlq_count") or 0),
            "dispatching_count": int(row.get("dispatching_count") or 0),
            "stale_dispatching_count": int(row.get("stale_dispatching_count") or 0),
            "unknown_after_dispatch_count": int(row.get("unknown_after_dispatch_count") or 0),
            "simulated_count": int(row.get("simulated_count") or 0),
            "reconciliation_required_count": int(row.get("reconciliation_required_count") or 0),
            "dispatching_without_active_lease_count": int(row.get("dispatching_without_active_lease_count") or 0),
            "lease_on_non_dispatching_count": int(row.get("lease_on_non_dispatching_count") or 0),
            "succeeded_without_evidence_count": int(row.get("succeeded_without_evidence_count") or 0),
            "simulated_recorded_as_succeeded_count": int(row.get("simulated_recorded_as_succeeded_count") or 0),
            "failed_retryable_count": int(row.get("failed_retryable_count") or 0),
            "failed_terminal_count": int(row.get("failed_terminal_count") or 0),
            "oldest_queued_age_seconds": int(float(row.get("oldest_queued_age_seconds") or 0)),
            "oldest_failed_retryable_age_seconds": int(float(row.get("oldest_failed_retryable_age_seconds") or 0)),
        }

    def get_active_claim(self, job_id: int, *, lease_token: str) -> ExternalEffectJob | None:
        return _public_job(self._one(
                """
                SELECT *
                FROM external_effect_job
                WHERE id = :job_id
                  AND hold_reason = ''
                  AND status = 'dispatching'
                  AND lease_token = :lease_token
                  AND lease_expires_at > CURRENT_TIMESTAMP
                LIMIT 1
                """,
                {"job_id": int(job_id), "lease_token": _text(lease_token)},
            ))

    def quarantine_stale_dispatching(self) -> int:
        with self._session_factory() as session:
            control = session.execute(
                text(
                    """
                    SELECT claim_enabled, active_generation
                    FROM queue_runtime_control
                    WHERE singleton = TRUE
                    FOR SHARE
                    """
                )
            ).mappings().fetchone()
            if (
                not control
                or (
                    bool(control.get("claim_enabled"))
                    and int(control.get("active_generation") or 0) > 0
                )
            ):
                session.rollback()
                return 0
            stale_rows = session.execute(text(
                    """
                        SELECT job.id, job.last_attempt_id, job.lease_token,
                               EXISTS (
                                   SELECT 1
                                   FROM external_effect_attempt attempt
                                   WHERE attempt.job_id = job.id
                                     AND attempt.attempt_id = job.last_attempt_id
                                     AND attempt.lease_token = job.lease_token
                                     AND attempt.status = 'dispatching'
                                     AND attempt.provider_call_started_at IS NOT NULL
                               ) AS provider_boundary_crossed
                        FROM external_effect_job job
                        WHERE job.status = 'dispatching'
                          AND job.hold_reason = ''
                          AND job.lease_expires_at IS NOT NULL
                          AND job.lease_expires_at <= CURRENT_TIMESTAMP
                        ORDER BY job.id ASC
                        FOR UPDATE SKIP LOCKED
                    """
            )).mappings().fetchall()
            count = 0
            for stale in stale_rows:
                provider_boundary_crossed = bool(stale.get("provider_boundary_crossed"))
                attempt_id = _text(stale.get("last_attempt_id"))
                lease_token = _text(stale.get("lease_token"))
                if provider_boundary_crossed and attempt_id and lease_token:
                    session.execute(
                        text(
                            """
                            UPDATE external_effect_attempt
                            SET status = 'unknown_after_dispatch',
                                response_summary_json = response_summary_json ||
                                    '{"provider_result_received": false, "lease_expired": true}'::jsonb,
                                error_code = CASE
                                    WHEN error_code = '' THEN 'lease_expired_after_dispatch'
                                    ELSE error_code
                                END,
                                error_message = CASE
                                    WHEN error_message = ''
                                    THEN 'Dispatch lease expired; reconcile provider outcome before retry.'
                                    ELSE error_message
                                END,
                                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
                            WHERE attempt_id = :attempt_id
                              AND job_id = :job_id
                              AND lease_token = :lease_token
                              AND status = 'dispatching'
                            """
                        ),
                        {
                            "attempt_id": attempt_id,
                            "job_id": int(stale["id"]),
                            "lease_token": lease_token,
                        },
                    )
                if provider_boundary_crossed:
                    update_statement = """
                        UPDATE external_effect_job
                        SET status = 'unknown_after_dispatch',
                            attempt_count = attempt_count + 1,
                            reconciliation_required = TRUE,
                            last_error_code = 'lease_expired_after_dispatch',
                            last_error_message = 'Dispatch lease expired; reconcile provider outcome before retry.',
                            lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                            locked_by = '', locked_at = NULL,
                            completed_at = CURRENT_TIMESTAMP,
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :job_id
                          AND hold_reason = ''
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at <= CURRENT_TIMESTAMP
                        RETURNING *
                    """
                else:
                    update_statement = """
                        UPDATE external_effect_job
                        SET status = 'queued',
                            available_at = CURRENT_TIMESTAMP,
                            next_retry_at = CURRENT_TIMESTAMP,
                            worker_generation = 0,
                            reconciliation_required = FALSE,
                            last_error_code = 'lease_expired_before_dispatch',
                            last_error_message = 'Pre-dispatch lease expired and was safely requeued.',
                            lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                            locked_by = '', locked_at = NULL,
                            dispatch_started_at = NULL,
                            provider_call_started_at = NULL,
                            completed_at = NULL,
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :job_id
                          AND hold_reason = ''
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at <= CURRENT_TIMESTAMP
                        RETURNING *
                    """
                updated_row = session.execute(
                    text(update_statement),
                    {"job_id": int(stale["id"]), "lease_token": lease_token},
                ).mappings().first()
                if updated_row and provider_boundary_crossed:
                    updated = _public_job(dict(updated_row))
                    attempt_row = session.execute(
                        text(
                            "SELECT * FROM external_effect_attempt WHERE attempt_id = :attempt_id AND job_id = :job_id"
                        ),
                        {"attempt_id": attempt_id, "job_id": int(stale["id"])},
                    ).mappings().first()
                    attempt = _public_attempt(dict(attempt_row)) if attempt_row else None
                    if updated is not None:
                        enqueue_external_effect_terminal_events_in_session(
                            session,
                            job=updated,
                            attempt=attempt,
                        )
                count += 1 if updated_row else 0
            session.commit()
            return count

    def begin_provider_attempt(
        self,
        *,
        job: ExternalEffectJob,
        request_summary: dict[str, Any],
    ) -> tuple[ExternalEffectJob, ExternalEffectAttempt] | None:
        lease_token = _text(job.lease_token)
        if not lease_token:
            return None
        attempt_id = "eea_" + uuid4().hex
        summary = scrub_summary(
            {
                **dict(request_summary or {}),
                "provider_boundary_crossed": True,
            }
        )
        request_hash = hashlib.sha256(
            _json_dumps(
                {
                    "effect_type": job.effect_type,
                    "operation": job.operation,
                    "target_type": job.target_type,
                    "target_id": job.target_id,
                    "payload": dict(job.payload_json or {}),
                }
            ).encode("utf-8")
        ).hexdigest()
        with self._session_factory() as session:
            current = (
                session.execute(
                    text(
                        """
                        SELECT *
                        FROM external_effect_job
                        WHERE id = :job_id
                          AND hold_reason = ''
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                          AND lease_expires_at > CURRENT_TIMESTAMP
                          AND cancel_requested_at IS NULL
                        FOR UPDATE
                        """
                    ),
                    {"job_id": int(job.id), "lease_token": lease_token},
                )
                .mappings()
                .fetchone()
            )
            if not current:
                session.rollback()
                return None
            attempt_row = (
                session.execute(
                    text(
                        """
                        INSERT INTO external_effect_attempt (
                            attempt_id, job_id, adapter_name, adapter_mode, operation, trace_id,
                            request_id, lease_token, request_hash, provider_call_started_at,
                            worker_generation, status, request_summary_json, response_summary_json,
                            error_code, error_message, started_at, completed_at
                        ) VALUES (
                            :attempt_id, :job_id, :adapter_name, :adapter_mode, :operation, :trace_id,
                            :request_id, :lease_token, :request_hash, CURRENT_TIMESTAMP,
                            :worker_generation, 'dispatching', CAST(:request_summary AS jsonb), '{}'::jsonb,
                            '', '', CURRENT_TIMESTAMP, NULL
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "job_id": int(job.id),
                        "adapter_name": job.adapter_name,
                        "adapter_mode": _text(job.execution_mode) or "execute",
                        "operation": job.operation,
                        "trace_id": job.trace_id,
                        "request_id": job.request_id,
                        "lease_token": lease_token,
                        "request_hash": request_hash,
                        "worker_generation": int(current.get("worker_generation") or job.worker_generation or 0),
                        "request_summary": _json_dumps(summary),
                    },
                )
                .mappings()
                .fetchone()
            )
            updated_row = (
                session.execute(
                    text(
                        """
                        UPDATE external_effect_job
                        SET last_attempt_id = :attempt_id,
                            provider_call_started_at = CURRENT_TIMESTAMP,
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :job_id
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                          AND cancel_requested_at IS NULL
                        RETURNING *
                        """
                    ),
                    {"attempt_id": attempt_id, "job_id": int(job.id), "lease_token": lease_token},
                )
                .mappings()
                .fetchone()
            )
            if not attempt_row or not updated_row:
                session.rollback()
                return None
            session.commit()
            updated = _public_job(dict(updated_row))
            attempt = _public_attempt(dict(attempt_row))
            return (updated, attempt) if updated and attempt else None

    def complete_dispatch(
        self,
        *,
        job: ExternalEffectJob,
        result: ExternalEffectDispatchResult,
        next_retry_at: datetime | None = None,
    ) -> tuple[ExternalEffectJob, ExternalEffectAttempt] | None:
        status = _text(result.status)
        allowed = {
            "succeeded",
            "simulated",
            "unknown_after_dispatch",
            "failed_retryable",
            "failed_terminal",
            "blocked",
        }
        if status not in allowed or not _text(job.lease_token):
            return None
        request_summary = scrub_summary(dict(result.request_summary or {}))
        response_summary = scrub_summary(
            {
                **dict(result.response_summary or {}),
                "real_external_call_executed": bool(result.real_external_call_executed),
                "provider_result_received": bool(result.provider_result_received),
            }
        )
        provider_result_json, provider_result_hash = encode_provider_result(result.provider_result)
        with self._session_factory() as session:
            current = (
                session.execute(
                    text(
                        "SELECT * FROM external_effect_job "
                        "WHERE id = :job_id AND status = 'dispatching' AND lease_token = :lease_token "
                        "AND lease_expires_at > CURRENT_TIMESTAMP FOR UPDATE"
                    ),
                    {"job_id": int(job.id), "lease_token": _text(job.lease_token)},
                )
                .mappings()
                .fetchone()
            )
            if not current:
                session.rollback()
                return None
            current_job = _public_job(dict(current))
            open_attempt = None
            if current_job and _text(current_job.last_attempt_id):
                open_attempt = (
                    session.execute(
                        text("SELECT * FROM external_effect_attempt WHERE attempt_id = :attempt_id AND job_id = :job_id AND status = 'dispatching' FOR UPDATE"),
                        {"attempt_id": current_job.last_attempt_id, "job_id": int(job.id)},
                    )
                    .mappings()
                    .fetchone()
                )
            if open_attempt:
                attempt_id = _text(open_attempt.get("attempt_id"))
                merged_request_summary = scrub_summary(
                    {
                        **_json_obj(open_attempt.get("request_summary_json")),
                        **request_summary,
                        "provider_boundary_crossed": True,
                    }
                )
                attempt_row = (
                    session.execute(
                        text(
                            """
                            UPDATE external_effect_attempt
                            SET status = :status,
                                adapter_mode = :adapter_mode,
                                request_summary_json = CAST(:request_summary AS jsonb),
                                response_summary_json = CAST(:response_summary AS jsonb),
                                provider_result_json = CAST(:provider_result AS jsonb), provider_result_hash = :provider_result_hash,
                                provider_result_consumed_at = NULL,
                                error_code = :error_code,
                                error_message = :error_message,
                                completed_at = CURRENT_TIMESTAMP
                            WHERE attempt_id = :attempt_id
                              AND job_id = :job_id
                              AND status = 'dispatching'
                            RETURNING *
                            """
                        ),
                        {
                            "attempt_id": attempt_id,
                            "job_id": int(job.id),
                            "status": status,
                            "adapter_mode": _text(result.adapter_mode) or "none",
                            "request_summary": _json_dumps(merged_request_summary),
                            "response_summary": _json_dumps(response_summary),
                            "provider_result": provider_result_json, "provider_result_hash": provider_result_hash,
                            "error_code": _text(result.error_code),
                            "error_message": _safe_error_message(result.error_message),
                        },
                    )
                    .mappings()
                    .fetchone()
                )
            else:
                if status != "blocked":
                    session.rollback()
                    return None
                attempt_id = "eea_" + uuid4().hex
                attempt_row = (
                    session.execute(
                        text(
                            """
                            INSERT INTO external_effect_attempt (
                                attempt_id, job_id, adapter_name, adapter_mode, operation, trace_id,
                                request_id, status, request_summary_json, response_summary_json,
                                provider_result_json, provider_result_hash,
                                error_code, error_message, started_at, completed_at
                            ) VALUES (
                                :attempt_id, :job_id, :adapter_name, :adapter_mode, :operation, :trace_id,
                                :request_id, :status, CAST(:request_summary AS jsonb),
                                CAST(:response_summary AS jsonb), CAST(:provider_result AS jsonb), :provider_result_hash,
                                :error_code, :error_message,
                                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            RETURNING *
                            """
                        ),
                        {
                            "attempt_id": attempt_id,
                            "job_id": int(job.id),
                            "adapter_name": job.adapter_name,
                            "adapter_mode": _text(result.adapter_mode) or "none",
                            "operation": job.operation,
                            "trace_id": job.trace_id,
                            "request_id": job.request_id,
                            "status": status,
                            "request_summary": _json_dumps(request_summary),
                            "response_summary": _json_dumps(response_summary),
                            "provider_result": provider_result_json, "provider_result_hash": provider_result_hash,
                            "error_code": _text(result.error_code),
                            "error_message": _safe_error_message(result.error_message),
                        },
                    )
                    .mappings()
                    .fetchone()
                )
            updated_row = (
                session.execute(
                    text(
                        """
                    UPDATE external_effect_job
                    SET status = :status,
                        attempt_count = attempt_count + 1,
                        next_retry_at = CASE
                            WHEN :status = 'failed_retryable'
                            THEN CAST(:next_retry_at AS timestamptz)
                            ELSE NULL
                        END,
                        available_at = CASE
                            WHEN :status = 'failed_retryable'
                            THEN CAST(:next_retry_at AS timestamptz)
                            ELSE available_at
                        END,
                        last_attempt_id = :attempt_id,
                        last_error_code = :error_code,
                        last_error_message = :error_message,
                        side_effect_executed = :side_effect_executed,
                        provider_result_received = :provider_result_received,
                        result_summary_json = CAST(:result_summary AS jsonb),
                        reconciliation_required = :reconciliation_required,
                        worker_generation = CASE
                            WHEN :status = 'failed_retryable' THEN 0
                            ELSE worker_generation
                        END,
                        lease_token = '', lease_expires_at = NULL,
                        locked_by = '', locked_at = NULL,
                        row_version = row_version + 1,
                        executed_at = CASE WHEN :status = 'succeeded' THEN CURRENT_TIMESTAMP ELSE executed_at END,
                        completed_at = CASE
                            WHEN :status IN ('succeeded', 'simulated', 'unknown_after_dispatch', 'failed_terminal', 'blocked')
                            THEN CURRENT_TIMESTAMP ELSE NULL
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :job_id
                      AND status = 'dispatching'
                      AND lease_token = :lease_token
                    RETURNING *
                    """
                    ),
                    {
                        "job_id": int(job.id),
                        "lease_token": _text(job.lease_token),
                        "status": status,
                        "next_retry_at": public_datetime(next_retry_at) if status == "failed_retryable" and next_retry_at else None,
                        "attempt_id": attempt_id,
                        "error_code": _text(result.error_code),
                        "error_message": _safe_error_message(result.error_message),
                        "side_effect_executed": bool(result.real_external_call_executed),
                        "provider_result_received": bool(result.provider_result_received),
                        "result_summary": _json_dumps(response_summary),
                        "reconciliation_required": status == "unknown_after_dispatch",
                    },
                )
                .mappings()
                .fetchone()
            )
            if not updated_row or not attempt_row:
                session.rollback()
                return None
            updated = _public_job(dict(updated_row))
            attempt = _public_attempt(dict(attempt_row))
            if not updated or not attempt:
                session.rollback()
                return None
            persist_rate_limit_cooldown(
                session,
                job=updated,
                attempt=attempt,
                result=result,
                blocked_until=next_retry_at,
            )
            enqueue_external_effect_terminal_events_in_session(
                session,
                job=updated,
                attempt=attempt,
            )
            session.commit()
            return updated, attempt

    def mark_dispatch_unknown(
        self,
        *,
        job: ExternalEffectJob,
        error_code: str,
        error_message: str,
        side_effect_executed: bool = True,
        provider_result_received: bool = False,
    ) -> ExternalEffectJob | None:
        lease_token = _text(job.lease_token)
        if not lease_token:
            return None
        normalized_error_code = _text(error_code) or "result_persistence_failed"
        normalized_error_message = _safe_error_message(error_message)
        with self._session_factory() as session:
            current = (
                session.execute(
                    text("SELECT * FROM external_effect_job WHERE id = :job_id AND status = 'dispatching' AND lease_token = :lease_token FOR UPDATE"),
                    {"job_id": int(job.id), "lease_token": lease_token},
                )
                .mappings()
                .fetchone()
            )
            if not current:
                session.rollback()
                return None
            last_attempt_id = _text(current.get("last_attempt_id"))
            if last_attempt_id:
                session.execute(
                    text(
                        """
                        UPDATE external_effect_attempt
                        SET status = 'unknown_after_dispatch',
                            response_summary_json = response_summary_json ||
                                CAST(:response_summary AS jsonb),
                            error_code = :error_code,
                            error_message = :error_message,
                            completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
                        WHERE attempt_id = :attempt_id
                          AND job_id = :job_id
                          AND status = 'dispatching'
                        """
                    ),
                    {
                        "attempt_id": last_attempt_id,
                        "job_id": int(job.id),
                        "response_summary": _json_dumps(
                            {
                                "provider_result_received": bool(provider_result_received),
                                "result_persistence_failed": True,
                            }
                        ),
                        "error_code": normalized_error_code,
                        "error_message": normalized_error_message,
                    },
                )
            updated_row = (
                session.execute(
                    text(
                        """
                        UPDATE external_effect_job
                        SET status = 'unknown_after_dispatch',
                            attempt_count = attempt_count + 1,
                            reconciliation_required = TRUE,
                            side_effect_executed = :side_effect_executed,
                            provider_result_received = :provider_result_received,
                            last_error_code = :error_code,
                            last_error_message = :error_message,
                            lease_token = '', lease_expires_at = NULL,
                            locked_by = '', locked_at = NULL,
                            completed_at = CURRENT_TIMESTAMP,
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :job_id
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                        RETURNING *
                        """
                    ),
                    {
                        "job_id": int(job.id),
                        "lease_token": lease_token,
                        "side_effect_executed": bool(side_effect_executed),
                        "provider_result_received": bool(provider_result_received),
                        "error_code": normalized_error_code,
                        "error_message": normalized_error_message,
                    },
                )
                .mappings()
                .fetchone()
            )
            if not updated_row:
                session.rollback()
                return None
            updated = _public_job(dict(updated_row))
            attempt = None
            if last_attempt_id:
                attempt_row = session.execute(
                    text(
                        "SELECT * FROM external_effect_attempt WHERE attempt_id = :attempt_id AND job_id = :job_id"
                    ),
                    {"attempt_id": last_attempt_id, "job_id": int(job.id)},
                ).mappings().first()
                attempt = _public_attempt(dict(attempt_row)) if attempt_row else None
            if updated is None:
                session.rollback()
                return None
            enqueue_external_effect_terminal_events_in_session(
                session,
                job=updated,
                attempt=attempt,
            )
            session.commit()
            return updated

    def mark_dispatching(self, job_id: int, *, locked_by: str) -> ExternalEffectJob | None:
        return self.acquire_job(job_id, locked_by=locked_by)

    def mark_succeeded(self, job_id: int, *, attempt_id: str) -> ExternalEffectJob | None:
        return self._update_terminal(
            job_id,
            "status = 'succeeded', last_attempt_id = :attempt_id, locked_by = '', locked_at = NULL, lease_token = '', lease_expires_at = NULL, executed_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP",
            {"attempt_id": _text(attempt_id)},
        )

    def mark_simulated(self, job_id: int, *, attempt_id: str, result_summary: dict[str, Any]) -> ExternalEffectJob | None:
        return self._update_terminal(
            job_id,
            "status = 'simulated', attempt_count = attempt_count + 1, last_attempt_id = :attempt_id, side_effect_executed = FALSE, provider_result_received = FALSE, result_summary_json = CAST(:result_summary AS jsonb), reconciliation_required = FALSE, locked_by = '', locked_at = NULL, lease_token = '', lease_expires_at = NULL, completed_at = CURRENT_TIMESTAMP",
            {"attempt_id": _text(attempt_id), "result_summary": _json_dumps(scrub_summary(result_summary or {}))},
        )

    def mark_failed_retryable(self, job_id: int, *, attempt_id: str, error_code: str, error_message: str, next_retry_at: datetime) -> ExternalEffectJob | None:
        return self._update(
            job_id,
            "status = 'failed_retryable', attempt_count = attempt_count + 1, next_retry_at = CAST(:next_retry_at AS timestamptz), available_at = CAST(:next_retry_at AS timestamptz), last_attempt_id = :attempt_id, last_error_code = :error_code, last_error_message = :error_message, locked_by = '', locked_at = NULL, lease_token = '', lease_expires_at = NULL",
            {
                "attempt_id": _text(attempt_id),
                "error_code": _text(error_code),
                "error_message": _safe_error_message(error_message),
                "next_retry_at": public_datetime(next_retry_at),
            },
        )

    def mark_failed_terminal(self, job_id: int, *, attempt_id: str, error_code: str, error_message: str) -> ExternalEffectJob | None:
        return self._update_terminal(
            job_id,
            "status = 'failed_terminal', attempt_count = attempt_count + 1, last_attempt_id = :attempt_id, last_error_code = :error_code, last_error_message = :error_message, locked_by = '', locked_at = NULL, lease_token = '', lease_expires_at = NULL, completed_at = CURRENT_TIMESTAMP",
            {"attempt_id": _text(attempt_id), "error_code": _text(error_code), "error_message": _safe_error_message(error_message)},
        )

    def mark_blocked(self, job_id: int, *, attempt_id: str, error_code: str, error_message: str) -> ExternalEffectJob | None:
        return self._update_terminal(
            job_id,
            "status = 'blocked', attempt_count = attempt_count + 1, last_attempt_id = :attempt_id, last_error_code = :error_code, last_error_message = :error_message, locked_by = '', locked_at = NULL, lease_token = '', lease_expires_at = NULL, completed_at = CURRENT_TIMESTAMP",
            {"attempt_id": _text(attempt_id), "error_code": _text(error_code), "error_message": _safe_error_message(error_message)},
        )

    def request_cancel(
        self,
        job_id: int,
        *,
        actor: str = "",
        reason: str = "",
        expected_version: int | None = None,
    ) -> ExternalEffectJob | None:
        version_clause = "AND row_version = :expected_version" if expected_version is not None else ""
        with self._session_factory() as session:
            row = session.execute(
                text(
                    f"""
                    UPDATE external_effect_job
                    SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                        cancel_requested_by = CASE
                            WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                        END,
                        cancel_reason = CASE WHEN cancel_reason = '' THEN :reason ELSE cancel_reason END,
                        status = CASE WHEN status = 'dispatching' THEN status ELSE 'cancelled' END,
                        locked_by = CASE WHEN status = 'dispatching' THEN locked_by ELSE '' END,
                        locked_at = CASE WHEN status = 'dispatching' THEN locked_at ELSE NULL END,
                        lease_token = CASE WHEN status = 'dispatching' THEN lease_token ELSE '' END,
                        lease_expires_at = CASE WHEN status = 'dispatching' THEN lease_expires_at ELSE NULL END,
                        cancelled_at = CASE WHEN status = 'dispatching' THEN cancelled_at ELSE CURRENT_TIMESTAMP END,
                        completed_at = CASE WHEN status = 'dispatching' THEN completed_at ELSE CURRENT_TIMESTAMP END,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :job_id
                      AND status IN ('planned', 'approved', 'queued', 'failed_retryable', 'dispatching')
                      {version_clause}
                    RETURNING *
                    """
                ),
                {
                    "job_id": int(job_id),
                    "actor": _text(actor),
                    "reason": _safe_error_message(reason),
                    "expected_version": int(expected_version or 0),
                },
            ).mappings().first()
            updated = _public_job(dict(row)) if row else None
            if updated is None:
                session.rollback()
                return None
            if updated.status == "cancelled":
                enqueue_external_effect_terminal_events_in_session(session, job=updated)
            session.commit()
            return updated

    def settle_cancel(self, *, job: ExternalEffectJob) -> ExternalEffectJob | None:
        with self._session_factory() as session:
            row = session.execute(
                text(
                    """
                    UPDATE external_effect_job j
                    SET status = 'cancelled',
                        locked_by = '', locked_at = NULL,
                        lease_token = '', lease_expires_at = NULL,
                        heartbeat_at = NULL, worker_generation = 0,
                        cancelled_at = CURRENT_TIMESTAMP,
                        completed_at = CURRENT_TIMESTAMP,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE j.id = :job_id
                      AND j.status = 'dispatching'
                      AND j.lease_token = :lease_token
                      AND j.cancel_requested_at IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM external_effect_attempt a
                          WHERE a.job_id = j.id
                            AND a.attempt_id = j.last_attempt_id
                            AND a.status = 'dispatching'
                      )
                    RETURNING j.*
                    """
                ),
                {"job_id": int(job.id), "lease_token": _text(job.lease_token)},
            ).mappings().first()
            updated = _public_job(dict(row)) if row else None
            if updated is None:
                session.rollback()
                return None
            enqueue_external_effect_terminal_events_in_session(session, job=updated)
            session.commit()
            return updated

    def cancel_job(
        self,
        job_id: int,
        *,
        actor: str = "",
        reason: str = "",
        expected_version: int | None = None,
    ) -> ExternalEffectJob | None:
        return self.request_cancel(
            job_id,
            actor=actor,
            reason=reason,
            expected_version=expected_version,
        )

    def enqueue_job(
        self,
        job_id: int,
        *,
        allow_unknown_after_dispatch: bool = False,
        extend_attempt_budget: bool = False,
    ) -> ExternalEffectJob | None:
        unknown_clause = "OR status = 'unknown_after_dispatch'" if allow_unknown_after_dispatch else ""
        return _public_job(
            self._write_one(
                f"""
                UPDATE external_effect_job
                SET status = 'queued',
                    locked_by = '', locked_at = NULL,
                    lease_token = '', lease_expires_at = NULL,
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    reconciliation_required = FALSE,
                    cancel_requested_at = NULL,
                    cancel_requested_by = '',
                    cancel_reason = '',
                    max_attempts = CASE
                        WHEN :extend_attempt_budget THEN GREATEST(max_attempts, attempt_count + 1)
                        ELSE max_attempts
                    END,
                    completed_at = NULL,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                  AND (
                    status IN ('planned', 'approved', 'queued', 'failed_retryable', 'failed_terminal', 'blocked')
                    {unknown_clause}
                  )
                RETURNING *
                """,
                {"job_id": int(job_id), "extend_attempt_budget": bool(extend_attempt_budget)},
            )
        )

    def approve_job(self, job_id: int) -> ExternalEffectJob | None:
        return self._update(
            job_id,
            "status = 'queued', approved_at = CURRENT_TIMESTAMP, locked_by = '', locked_at = NULL, lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL, worker_generation = 0, next_retry_at = CURRENT_TIMESTAMP, available_at = CURRENT_TIMESTAMP, reconciliation_required = FALSE",
            {},
        )

    def record_attempt(
        self,
        *,
        job: ExternalEffectJob,
        status: str,
        adapter_mode: str,
        request_summary: dict[str, Any],
        response_summary: dict[str, Any],
        error_code: str = "",
        error_message: str = "",
    ) -> ExternalEffectAttempt:
        attempt_id = "eea_" + __import__("uuid").uuid4().hex
        row = self._write_one(
            """
            INSERT INTO external_effect_attempt (
                attempt_id, job_id, adapter_name, adapter_mode, operation, trace_id,
                request_id, status, request_summary_json, response_summary_json,
                error_code, error_message, started_at, completed_at
            )
            VALUES (
                :attempt_id, :job_id, :adapter_name, :adapter_mode, :operation, :trace_id,
                :request_id, :status, CAST(:request_summary AS jsonb), CAST(:response_summary AS jsonb),
                :error_code, :error_message, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING *
            """,
            {
                "attempt_id": attempt_id,
                "job_id": int(job.id),
                "adapter_name": job.adapter_name,
                "adapter_mode": _text(adapter_mode) or "none",
                "operation": job.operation,
                "trace_id": job.trace_id,
                "request_id": job.request_id,
                "status": _text(status) or "skipped",
                "request_summary": _json_dumps(scrub_summary(request_summary or {})),
                "response_summary": _json_dumps(scrub_summary(response_summary or {})),
                "error_code": _text(error_code),
                "error_message": _safe_error_message(error_message),
            },
        )
        attempt = _public_attempt(row)
        if attempt is None:
            raise RuntimeError("external effect attempt insert failed")
        return attempt

    def get_job_by_event_id(self, event_id: str) -> ExternalEffectJob | None:
        return _public_job(
            self._one(
                """
                SELECT *
                FROM external_effect_job
                WHERE idempotency_key = :event_id
                  AND payload_json->>'execution_scope' = 'test_loopback'
                LIMIT 1
                """,
                {"event_id": _text(event_id)},
            )
        )

    def create_test_receipt(
        self,
        *,
        event_id: str,
        job: ExternalEffectJob,
        request_method: str,
        request_path: str,
        headers_summary: dict[str, Any],
        payload_summary: dict[str, Any],
        payload_hash: str,
        body_json: dict[str, Any],
        signature_valid: bool | None,
        response_status: int,
    ) -> ExternalEffectTestReceipt:
        receipt_id = "eer_" + __import__("uuid").uuid4().hex
        row = self._write_one(
            """
            INSERT INTO external_effect_test_receipt (
                receipt_id, event_id, job_id, effect_type, trace_id, idempotency_key,
                target_type, target_id, business_type, business_id, request_method,
                request_path, headers_summary_json, payload_summary_json, payload_hash,
                body_json, signature_valid, response_status, received_at
            )
            VALUES (
                :receipt_id, :event_id, :job_id, :effect_type, :trace_id, :idempotency_key,
                :target_type, :target_id, :business_type, :business_id, :request_method,
                :request_path, CAST(:headers_summary AS jsonb), CAST(:payload_summary AS jsonb),
                :payload_hash, CAST(:body_json AS jsonb), :signature_valid, :response_status,
                CURRENT_TIMESTAMP
            )
            RETURNING *
            """,
            {
                "receipt_id": receipt_id,
                "event_id": _text(event_id),
                "job_id": int(job.id),
                "effect_type": job.effect_type,
                "trace_id": job.trace_id,
                "idempotency_key": job.idempotency_key,
                "target_type": job.target_type,
                "target_id": job.target_id,
                "business_type": job.business_type,
                "business_id": job.business_id,
                "request_method": _text(request_method) or "POST",
                "request_path": _text(request_path),
                "headers_summary": _json_dumps(scrub_summary(headers_summary or {})),
                "payload_summary": _json_dumps(scrub_summary(payload_summary or {})),
                "payload_hash": _text(payload_hash),
                "body_json": _json_dumps(body_json or {}),
                "signature_valid": signature_valid,
                "response_status": int(response_status or 200),
            },
        )
        receipt = _public_receipt(row)
        if receipt is None:
            raise RuntimeError("external effect test receipt insert failed")
        return receipt

    def list_test_receipts(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[ExternalEffectTestReceipt], int]:
        filters = dict(filters or {})
        clauses: list[str] = []
        params: dict[str, Any] = {}
        for key in ("job_id", "effect_type", "trace_id", "event_id"):
            value = _text(filters.get(key))
            if value:
                clauses.append(f"{key} = :{key}")
                params[key] = int(value) if key == "job_id" else value
        if _text(filters.get("received_from")):
            clauses.append("received_at >= CAST(:received_from AS timestamptz)")
            params["received_from"] = _text(filters.get("received_from"))
        if _text(filters.get("received_to")):
            clauses.append("received_at <= CAST(:received_to AS timestamptz)")
            params["received_to"] = _text(filters.get("received_to"))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        count_row = self._one(f"SELECT COUNT(*) AS total FROM external_effect_test_receipt {where}", params)
        rows = self._all(
            f"""
            SELECT *
            FROM external_effect_test_receipt
            {where}
            ORDER BY received_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": max(1, min(int(limit or 50), 200)), "offset": max(0, int(offset or 0))},
        )
        return [receipt for row in rows if (receipt := _public_receipt(row)) is not None], int((count_row or {}).get("total") or 0)

    def get_test_receipt(self, receipt_id: str) -> ExternalEffectTestReceipt | None:
        return _public_receipt(
            self._one("SELECT * FROM external_effect_test_receipt WHERE receipt_id = :receipt_id LIMIT 1", {"receipt_id": _text(receipt_id)})
        )

    def test_receipt_metrics(self) -> dict[str, Any]:
        row = (
            self._one(
                """
            SELECT
                COUNT(*) FILTER (WHERE received_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours') AS test_receipt_count_24h,
                MAX(received_at) AS latest_test_receipt_at,
                COUNT(*) FILTER (
                    WHERE response_status BETWEEN 200 AND 299
                      AND received_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                ) AS real_external_call_executed_to_test_receiver_count
            FROM external_effect_test_receipt
            """
            )
            or {}
        )
        blocked = self._one("SELECT COUNT(*) AS count FROM external_effect_job WHERE last_error_code = 'test_execution_only_required'") or {}
        loopback_due = (
            self._one(
                """
            SELECT COUNT(*) AS count
            FROM external_effect_job
            WHERE payload_json->>'execution_scope' = 'test_loopback'
              AND hold_reason = ''
              AND status IN ('queued', 'failed_retryable')
              AND scheduled_at <= CURRENT_TIMESTAMP
              AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
              AND (locked_at IS NULL OR locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes')
            """
            )
            or {}
        )
        return {
            "test_receipt_count_24h": int(row.get("test_receipt_count_24h") or 0),
            "latest_test_receipt_at": public_datetime(row.get("latest_test_receipt_at")),
            "loopback_eligible_job_count": int(loopback_due.get("count") or 0),
            "non_test_execution_blocked_count": int(blocked.get("count") or 0),
            "real_external_call_executed_to_test_receiver_count": int(row.get("real_external_call_executed_to_test_receiver_count") or 0),
        }

    def list_record_only_jobs(self, *, limit: int = 100) -> list[ExternalEffectJob]:
        rows = self._all(
            """
            SELECT *
            FROM external_effect_job
            WHERE attempt_count = 0
              AND status NOT IN ('succeeded', 'failed_retryable', 'failed_terminal', 'cancelled', 'expired', 'dispatching')
              AND (
                    execution_mode IN ('shadow', 'plan_only', 'disabled', 'execute_dryrun')
                 OR status = 'planned'
              )
            ORDER BY created_at ASC, id ASC
            LIMIT :limit
            """,
            {"limit": max(1, min(int(limit or 100), 1000))},
        )
        return [job for row in rows if (job := _public_job(row)) is not None]

    def _update(self, job_id: int, set_sql: str, params: dict[str, Any]) -> ExternalEffectJob | None:
        row = self._write_one(
            f"""
            UPDATE external_effect_job
            SET {set_sql},
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id
            RETURNING *
            """,
            {**params, "job_id": int(job_id)},
        )
        return _public_job(row)

    def _update_terminal(self, job_id: int, set_sql: str, params: dict[str, Any]) -> ExternalEffectJob | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        f"""
                        UPDATE external_effect_job
                        SET {set_sql},
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :job_id
                        RETURNING *
                        """
                    ),
                    {**params, "job_id": int(job_id)},
                )
                .mappings()
                .first()
            )
            updated = _public_job(dict(row)) if row else None
            if updated is None:
                session.rollback()
                return None
            attempt = None
            if _text(updated.last_attempt_id):
                attempt_row = session.execute(
                    text(
                        "SELECT * FROM external_effect_attempt WHERE attempt_id = :attempt_id AND job_id = :job_id"
                    ),
                    {"attempt_id": updated.last_attempt_id, "job_id": int(updated.id)},
                ).mappings().first()
                attempt = _public_attempt(dict(attempt_row)) if attempt_row else None
            enqueue_external_effect_terminal_events_in_session(
                session,
                job=updated,
                attempt=attempt,
            )
            session.commit()
            return updated


from .repo_memory import InMemoryExternalEffectRepository


_fixture_repo = InMemoryExternalEffectRepository()


def reset_external_effect_fixture_state() -> None:
    global _fixture_repo
    _fixture_repo = InMemoryExternalEffectRepository()


def build_external_effect_repository() -> ExternalEffectRepository:
    if fixture_mode():
        return _fixture_repo
    return SQLAlchemyExternalEffectRepository()
