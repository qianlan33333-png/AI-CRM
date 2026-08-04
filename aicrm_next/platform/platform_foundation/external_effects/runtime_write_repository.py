from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text

from .claim_policy import (
    ai_automation_lane_canary_predicate,
    external_claim_scope_predicate,
    external_effect_claim_order_sql,
)
from .repo_contract import _release_sha


def _job_ids(values: Sequence[int]) -> list[int]:
    return sorted({int(value) for value in values if int(value) > 0})


class PostgresExternalEffectRuntimeWriteRepository:
    """Canonical writer for cross-module external-effect runtime mutations.

    Callers keep ownership of their surrounding transaction, audit facts and
    settlement events.  This repository never commits and never performs a
    provider call.
    """

    def make_eligible_now_sqlalchemy(
        self,
        executor: Any,
        *,
        item_id: int,
        expected_status: str,
        expected_version: str,
    ) -> dict[str, Any] | None:
        row = (
            executor.execute(
                text(
                    """
                    UPDATE external_effect_job
                    SET available_at = CURRENT_TIMESTAMP,
                        next_retry_at = CURRENT_TIMESTAMP,
                        worker_generation = 0,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :item_id
                      AND status = :expected_status
                      AND row_version::text = :expected_version
                      AND hold_reason = ''
                      AND provider_call_started_at IS NULL
                      AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                    RETURNING id, execution_id, lane, status, hold_reason,
                              row_version::text AS version_token
                    """
                ),
                {
                    "item_id": int(item_id),
                    "expected_status": str(expected_status or ""),
                    "expected_version": str(expected_version or ""),
                },
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else None

    def manual_action_sqlalchemy(
        self,
        executor: Any,
        *,
        action: str,
        item_id: int,
        expected_status: str,
        expected_version: str,
        actor: str,
        reason: str,
    ) -> dict[str, Any] | None:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "retry":
            statement = """
                UPDATE external_effect_job
                SET status = 'queued',
                    processed_release_sha = 'unknown',
                    health_classification_code = '',
                    locked_by = '', locked_at = NULL,
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    dispatch_started_at = CASE
                        WHEN side_effect_executed IS FALSE
                         AND provider_result_received IS FALSE
                         AND reconciliation_required IS FALSE
                        THEN NULL
                        ELSE dispatch_started_at
                    END,
                    provider_call_started_at = CASE
                        WHEN side_effect_executed IS FALSE
                         AND provider_result_received IS FALSE
                         AND reconciliation_required IS FALSE
                        THEN NULL
                        ELSE provider_call_started_at
                    END,
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    reconciliation_required = FALSE,
                    cancel_requested_at = NULL,
                    cancel_requested_by = '', cancel_reason = '',
                    max_attempts = GREATEST(max_attempts, attempt_count + 1),
                    completed_at = NULL,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND row_version::text = :expected_version
                  AND status IN (
                      'failed_retryable', 'failed_terminal', 'blocked',
                      'unknown_after_dispatch'
                  )
                  AND hold_reason = ''
                  AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                RETURNING id, execution_id, lane, status, hold_reason,
                          row_version::text AS version_token
            """
        elif normalized_action == "cancel":
            statement = """
                UPDATE external_effect_job
                SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                    cancel_requested_by = CASE
                        WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                    END,
                    cancel_reason = CASE
                        WHEN cancel_reason = '' THEN :reason ELSE cancel_reason
                    END,
                    status = CASE WHEN status = 'dispatching' THEN status ELSE 'cancelled' END,
                    locked_by = CASE WHEN status = 'dispatching' THEN locked_by ELSE '' END,
                    locked_at = CASE WHEN status = 'dispatching' THEN locked_at ELSE NULL END,
                    lease_token = CASE WHEN status = 'dispatching' THEN lease_token ELSE '' END,
                    lease_expires_at = CASE
                        WHEN status = 'dispatching' THEN lease_expires_at ELSE NULL
                    END,
                    heartbeat_at = CASE WHEN status = 'dispatching' THEN heartbeat_at ELSE NULL END,
                    worker_generation = CASE
                        WHEN status = 'dispatching' THEN worker_generation ELSE 0
                    END,
                    cancelled_at = CASE
                        WHEN status = 'dispatching' THEN cancelled_at ELSE CURRENT_TIMESTAMP
                    END,
                    completed_at = CASE
                        WHEN status = 'dispatching' THEN completed_at ELSE CURRENT_TIMESTAMP
                    END,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND row_version::text = :expected_version
                  AND status IN (
                      'planned', 'approved', 'queued', 'failed_retryable', 'dispatching'
                  )
                RETURNING id, execution_id, lane, status, hold_reason,
                          row_version::text AS version_token
            """
        else:
            raise ValueError(f"unsupported external-effect manual action: {normalized_action}")
        row = (
            executor.execute(
                text(statement),
                {
                    "item_id": int(item_id),
                    "expected_status": str(expected_status or ""),
                    "expected_version": str(expected_version or ""),
                    "actor": str(actor or "").strip(),
                    "reason": str(reason or "")[:500],
                },
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else None

    def request_cancel_dispatching_sqlalchemy(
        self,
        executor: Any,
        *,
        job_ids: Sequence[int],
        exclude_job_id: int,
        actor: str,
        reason: str,
    ) -> list[int]:
        normalized_ids = _job_ids(job_ids)
        if not normalized_ids:
            return []
        rows = (
            executor.execute(
                text(
                    """
                    UPDATE external_effect_job job
                    SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                        cancel_requested_by = CASE
                            WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                        END,
                        cancel_reason = CASE
                            WHEN cancel_reason = '' THEN :reason ELSE cancel_reason
                        END,
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job.id = ANY(:job_ids)
                      AND job.id <> :exclude_job_id
                      AND job.status = 'dispatching'
                      AND job.cancel_requested_at IS NULL
                      AND job.provider_call_started_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM external_effect_attempt attempt
                          WHERE attempt.job_id = job.id
                            AND attempt.provider_call_started_at IS NOT NULL
                      )
                    RETURNING job.id
                    """
                ),
                {
                    "job_ids": normalized_ids,
                    "exclude_job_id": int(exclude_job_id),
                    "actor": str(actor or "").strip(),
                    "reason": str(reason or "").strip(),
                },
            )
            .scalars()
            .all()
        )
        return [int(value) for value in rows]

    def cancel_pre_provider_sqlalchemy(
        self,
        executor: Any,
        *,
        job_ids: Sequence[int],
        exclude_job_id: int,
        actor: str,
        reason: str,
    ) -> list[dict[str, Any]]:
        normalized_ids = _job_ids(job_ids)
        if not normalized_ids:
            return []
        rows = (
            executor.execute(
                text(
                    """
                    UPDATE external_effect_job job
                    SET status = 'cancelled',
                        cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                        cancel_requested_by = CASE
                            WHEN cancel_requested_by = '' THEN :actor ELSE cancel_requested_by
                        END,
                        cancel_reason = CASE
                            WHEN cancel_reason = '' THEN :reason ELSE cancel_reason
                        END,
                        cancelled_at = COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE job.id = ANY(:job_ids)
                      AND job.id <> :exclude_job_id
                      AND job.status IN ('planned', 'approved', 'queued', 'failed_retryable')
                      AND job.provider_call_started_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM external_effect_attempt attempt
                          WHERE attempt.job_id = job.id
                            AND attempt.provider_call_started_at IS NOT NULL
                      )
                    RETURNING job.*
                    """
                ),
                {
                    "job_ids": normalized_ids,
                    "exclude_job_id": int(exclude_job_id),
                    "actor": str(actor or "").strip(),
                    "reason": str(reason or "").strip(),
                },
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def release_planned_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        payload_json: str,
        payload_summary_json: str,
        available_at_mode: str,
    ) -> int | None:
        mode = str(available_at_mode or "").strip().lower()
        if mode not in {"now", "scheduled"}:
            raise ValueError("available_at_mode must be 'now' or 'scheduled'")
        available_at = "CURRENT_TIMESTAMP" if mode == "now" else "scheduled_at"
        row = executor.execute(
            text(
                f"""
                UPDATE external_effect_job
                SET payload_json = CAST(:payload_json AS jsonb),
                    payload_summary_json = CAST(:payload_summary_json AS jsonb),
                    status = 'queued', available_at = {available_at},
                    processed_release_sha = 'unknown',
                    health_classification_code = '',
                    hold_reason = '', hold_at = NULL,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                  AND status = 'planned'
                  AND provider_call_started_at IS NULL
                RETURNING id
                """
            ),
            {
                "payload_json": str(payload_json or "{}"),
                "payload_summary_json": str(payload_summary_json or "{}"),
                "job_id": int(job_id),
            },
        ).scalar_one_or_none()
        return int(row) if row is not None else None

    def block_planned_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any] | None:
        row = (
            executor.execute(
                text(
                    """
                    UPDATE external_effect_job
                    SET status = 'blocked',
                        last_error_code = :error_code,
                        last_error_message = :error_message,
                        processed_release_sha = :processed_release_sha,
                        health_classification_code = 'unclassified_blocked',
                        completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                        row_version = row_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :job_id
                      AND status = 'planned'
                      AND provider_call_started_at IS NULL
                    RETURNING *
                    """
                ),
                {
                    "job_id": int(job_id),
                    "error_code": str(error_code or "").strip()[:200],
                    "error_message": str(error_message or "").strip()[:1000],
                    "processed_release_sha": _release_sha(),
                },
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else None

    def claim_dbapi(
        self,
        executor: Any,
        *,
        lane: str,
        generation: int,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
        test_only: bool,
    ) -> dict[str, Any] | None:
        test_predicate = "AND COALESCE(job.payload_json->>'execution_scope', '') = 'test_loopback'" if test_only else ""
        scope_predicate = external_claim_scope_predicate(
            row_alias="job",
            scope_expression=("(SELECT external_claim_scope FROM queue_runtime_control WHERE singleton = TRUE)"),
        )
        lane_canary_predicate = ai_automation_lane_canary_predicate(
            row_alias="job",
            lane_mode_expression=("(SELECT rollout_mode FROM queue_lane_policy WHERE lane = job.lane)"),
            policy_version_expression=("(SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE)"),
        )
        claim_order = external_effect_claim_order_sql()
        row = executor.execute(
            f"""
            WITH candidate AS (
                SELECT job.id
                FROM external_effect_job job
                LEFT JOIN queue_fairness_cursor fairness
                  ON fairness.lane = job.lane
                 AND fairness.fairness_key = job.fairness_key
                LEFT JOIN queue_rate_scope_cooldown cooldown
                  ON cooldown.rate_scope_key = job.rate_scope_key
                 AND cooldown.blocked_until > CURRENT_TIMESTAMP
                WHERE job.lane = %s
                  AND job.worker_generation IN (0, %s)
                  AND job.policy_version = (
                      SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                  )
                  AND job.status IN ('queued', 'failed_retryable')
                  AND job.hold_reason = ''
                  AND job.attempt_count < job.max_attempts
                  AND job.available_at <= CURRENT_TIMESTAMP
                  AND {scope_predicate}
                  AND {lane_canary_predicate}
                  AND (job.lease_expires_at IS NULL OR job.lease_expires_at <= CURRENT_TIMESTAMP)
                  AND cooldown.rate_scope_key IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM external_effect_job active
                      WHERE active.lane = job.lane
                        AND active.ordering_key = job.ordering_key
                        AND active.ordering_key <> ''
                        AND active.status = 'dispatching'
                        AND active.lease_expires_at > CURRENT_TIMESTAMP
                  )
                  {test_predicate}
                ORDER BY {claim_order}
                LIMIT 1
                FOR UPDATE OF job SKIP LOCKED
            )
            UPDATE external_effect_job job
            SET status = 'dispatching',
                locked_by = %s,
                lease_token = %s,
                locked_at = CURRENT_TIMESTAMP,
                dispatch_started_at = CURRENT_TIMESTAMP,
                provider_call_started_at = NULL,
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                heartbeat_at = CURRENT_TIMESTAMP,
                worker_generation = %s,
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING job.*
            """,
            (
                str(lane or "").strip(),
                int(generation),
                str(worker_id or "").strip(),
                str(lease_token or ""),
                max(10, min(int(lease_seconds or 30), 300)),
                int(generation),
            ),
        ).fetchone()
        return dict(row) if row else None

    def recover_expired_dbapi(
        self,
        executor: Any,
        *,
        lane: str,
    ) -> list[dict[str, Any]]:
        normalized_lane = str(lane or "").strip()
        executor.execute(
            """
            UPDATE external_effect_attempt attempt
            SET status = 'unknown_after_dispatch',
                response_summary_json = COALESCE(attempt.response_summary_json, '{}'::jsonb)
                    || '{"provider_result_received":false,"lease_expired":true}'::jsonb,
                error_code = CASE
                    WHEN attempt.error_code = '' THEN 'lease_expired_after_dispatch'
                    ELSE attempt.error_code
                END,
                error_message = CASE
                    WHEN attempt.error_message = ''
                    THEN 'Dispatch lease expired; reconcile provider outcome before retry.'
                    ELSE attempt.error_message
                END,
                completed_at = COALESCE(attempt.completed_at, CURRENT_TIMESTAMP)
            FROM external_effect_job job
            WHERE job.lane = %s
              AND job.status = 'dispatching'
              AND job.lease_expires_at IS NOT NULL
              AND job.lease_expires_at <= CURRENT_TIMESTAMP
              AND job.provider_call_started_at IS NOT NULL
              AND attempt.job_id = job.id
              AND attempt.attempt_id = job.last_attempt_id
              AND attempt.lease_token = job.lease_token
              AND attempt.status = 'dispatching'
            """,
            (normalized_lane,),
        )
        unknown_rows = executor.execute(
            """
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
            WHERE lane = %s
              AND status = 'dispatching'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= CURRENT_TIMESTAMP
              AND provider_call_started_at IS NOT NULL
            RETURNING *
            """,
            (normalized_lane,),
        ).fetchall()
        executor.execute(
            """
            UPDATE external_effect_job
            SET status = 'queued',
                processed_release_sha = 'unknown',
                health_classification_code = '',
                available_at = CURRENT_TIMESTAMP,
                next_retry_at = CURRENT_TIMESTAMP,
                worker_generation = 0,
                last_error_code = 'lease_expired_before_dispatch',
                last_error_message = 'Pre-dispatch lease expired and was safely requeued.',
                lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                locked_by = '', locked_at = NULL, dispatch_started_at = NULL,
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE lane = %s
              AND status = 'dispatching'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= CURRENT_TIMESTAMP
              AND provider_call_started_at IS NULL
            """,
            (normalized_lane,),
        )
        return [dict(row) for row in unknown_rows]

    def renew_lease_dbapi(
        self,
        executor: Any,
        *,
        item_id: int,
        lease_token: str,
        generation: int,
        lease_seconds: int,
    ) -> bool:
        row = executor.execute(
            """
            UPDATE external_effect_job
            SET heartbeat_at = CURRENT_TIMESTAMP,
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status = 'dispatching'
              AND lease_token = %s
              AND worker_generation = %s
              AND lease_expires_at > CURRENT_TIMESTAMP
            RETURNING id
            """,
            (
                max(10, min(int(lease_seconds or 30), 300)),
                int(item_id),
                str(lease_token or ""),
                int(generation),
            ),
        ).fetchone()
        return bool(row)

    def adopt_pre_provider_dbapi(
        self,
        executor: Any,
        *,
        job_ids: Sequence[int],
        generation: int,
        source_policy_version: str,
        target_policy_version: str,
    ) -> list[int]:
        normalized_ids = _job_ids(job_ids)
        if not normalized_ids:
            return []
        rows = executor.execute(
            """
            UPDATE external_effect_job
            SET status = 'queued',
                processed_release_sha = 'unknown',
                health_classification_code = '',
                worker_generation = %s,
                policy_version = %s,
                available_at = CURRENT_TIMESTAMP,
                scheduled_at = LEAST(scheduled_at, CURRENT_TIMESTAMP),
                next_retry_at = NULL,
                lease_token = '', lease_expires_at = NULL,
                locked_by = '', locked_at = NULL,
                dispatch_started_at = NULL, heartbeat_at = NULL,
                completed_at = NULL,
                last_error_code = '', last_error_message = '',
                result_summary_json = jsonb_build_object(
                    'pre_provider_attempt_preserved', TRUE,
                    'adoption_reason', 'authorized_all_scope_cutover'
                ),
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(%s)
              AND status = 'blocked'
              AND policy_version = %s
              AND provider_call_started_at IS NULL
              AND side_effect_executed = FALSE
              AND provider_result_received = FALSE
            RETURNING id
            """,
            (
                int(generation),
                str(target_policy_version or ""),
                normalized_ids,
                str(source_policy_version or ""),
            ),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def apply_history_freeze_dbapi(
        self,
        executor: Any,
        *,
        freeze_revision: str,
        cutoff_at: Any,
    ) -> None:
        executor.execute(
            """
            UPDATE external_effect_job target
            SET hold_reason = audit.hold_reason,
                hold_at = %s
            FROM queue_history_classification audit
            WHERE audit.freeze_revision = %s
              AND audit.queue_kind = 'external_effect'
              AND audit.queue_row_id = target.id
              AND target.hold_reason = ''
            """,
            (cutoff_at, str(freeze_revision or "")),
        )
        executor.execute(
            """
            UPDATE external_effect_job target
            SET status = 'unknown_after_dispatch',
                reconciliation_required = TRUE
            FROM queue_history_classification audit
            WHERE audit.freeze_revision = %s
              AND audit.queue_kind = 'external_effect'
              AND audit.queue_row_id = target.id
              AND audit.classification = 'inconsistent_quarantine'
              AND COALESCE(
                    (audit.evidence_json ->> 'provider_boundary_started')::BOOLEAN,
                    FALSE
                  )
            """,
            (str(freeze_revision or ""),),
        )


__all__ = ["PostgresExternalEffectRuntimeWriteRepository"]
