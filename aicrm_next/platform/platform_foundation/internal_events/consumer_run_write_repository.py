from __future__ import annotations

from typing import Any

from sqlalchemy import text


class PostgresInternalEventConsumerRunWriteRepository:
    """Canonical writer for runtime mutations of internal-event consumer runs.

    Callers retain their surrounding transaction and may append audit/outbox
    facts atomically.  This repository deliberately never commits.
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
                    UPDATE internal_event_consumer_run
                    SET available_at = CURRENT_TIMESTAMP,
                        next_retry_at = CURRENT_TIMESTAMP,
                        worker_generation = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :item_id
                      AND status = :expected_status
                      AND xmin::text = :expected_version
                      AND hold_reason = ''
                      AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                    RETURNING id, execution_id, lane, status, hold_reason,
                              xmin::text AS version_token
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
        actor_ref_hash: str,
        reason: str,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "retry":
            statement = """
                WITH target AS (
                    SELECT id, consumer_name, status
                    FROM internal_event_consumer_run
                    WHERE id = :item_id
                      AND status = :expected_status
                      AND xmin::text = :expected_version
                      AND status IN ('failed_retryable', 'failed_terminal', 'blocked')
                      AND hold_reason = ''
                      AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                    FOR UPDATE
                ), attempt AS (
                    INSERT INTO internal_event_consumer_attempt (
                        attempt_id, consumer_run_id, consumer_name, status,
                        request_summary_json, response_summary_json,
                        error_code, error_message, started_at, completed_at
                    )
                    SELECT :attempt_id, target.id, target.consumer_name, 'manual_retry',
                           jsonb_build_object(
                               'manual_retry', TRUE,
                               'actor_ref_hash', CAST(:actor_ref_hash AS TEXT),
                               'actor_type', 'operator',
                               'reason', CAST(:reason AS TEXT),
                               'from_status', target.status
                           ),
                           '{"status":"pending"}'::jsonb,
                           'manual_retry', :reason,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM target
                    RETURNING attempt_id, consumer_run_id
                )
                UPDATE internal_event_consumer_run run
                SET status = 'pending',
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    locked_by = '', locked_at = NULL,
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    max_attempts = GREATEST(max_attempts, attempt_count + 1),
                    last_attempt_id = attempt.attempt_id,
                    last_error_code = '', last_error_message = '',
                    finished_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                FROM target, attempt
                WHERE run.id = target.id
                  AND attempt.consumer_run_id = target.id
                RETURNING run.id, run.execution_id, run.lane, run.status,
                          run.hold_reason, run.xmin::text AS version_token
            """
        elif normalized_action == "skip":
            statement = """
                WITH target AS (
                    SELECT id, consumer_name, status
                    FROM internal_event_consumer_run
                    WHERE id = :item_id
                      AND status = :expected_status
                      AND xmin::text = :expected_version
                      AND status IN (
                          'pending', 'failed_retryable', 'failed_terminal', 'blocked'
                      )
                      AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
                    FOR UPDATE
                ), attempt AS (
                    INSERT INTO internal_event_consumer_attempt (
                        attempt_id, consumer_run_id, consumer_name, status,
                        request_summary_json, response_summary_json,
                        error_code, error_message, started_at, completed_at
                    )
                    SELECT :attempt_id, target.id, target.consumer_name, 'skipped',
                           jsonb_build_object(
                               'manual_skip', TRUE,
                               'actor_ref_hash', CAST(:actor_ref_hash AS TEXT),
                               'actor_type', 'operator',
                               'reason', CAST(:reason AS TEXT),
                               'from_status', target.status
                           ),
                           jsonb_build_object(
                               'skipped', TRUE,
                               'reason', CAST(:reason AS TEXT)
                           ),
                           'manual_skip', :reason,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM target
                    RETURNING attempt_id, consumer_run_id
                )
                UPDATE internal_event_consumer_run run
                SET status = 'skipped',
                    next_retry_at = NULL,
                    locked_by = '', locked_at = NULL,
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    last_attempt_id = attempt.attempt_id,
                    last_error_code = 'manual_skip',
                    last_error_message = :reason,
                    result_summary_json = jsonb_build_object(
                        'skipped', TRUE,
                        'reason', CAST(:reason AS TEXT)
                    ),
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                FROM target, attempt
                WHERE run.id = target.id
                  AND attempt.consumer_run_id = target.id
                RETURNING run.id, run.execution_id, run.lane, run.status,
                          run.hold_reason, run.xmin::text AS version_token
            """
        else:
            raise ValueError(f"unsupported internal-event consumer action: {normalized_action}")
        row = (
            executor.execute(
                text(statement),
                {
                    "item_id": int(item_id),
                    "expected_status": str(expected_status or ""),
                    "expected_version": str(expected_version or ""),
                    "actor_ref_hash": str(actor_ref_hash or ""),
                    "reason": str(reason or "")[:500],
                    "attempt_id": str(attempt_id or ""),
                },
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else None

    def quarantine_superseded_signal_owner_sqlalchemy(
        self,
        executor: Any,
        *,
        tenant_id: str,
        idempotency_key: str,
        consumer_name: str,
    ) -> list[dict[str, Any]]:
        rows = (
            executor.execute(
                text(
                    """
                    WITH control AS (
                        SELECT active_generation, policy_version
                        FROM queue_runtime_control
                        WHERE singleton = TRUE
                    ), signal_event AS (
                        SELECT event_id
                        FROM internal_event
                        WHERE tenant_id = :tenant_id
                          AND idempotency_key = :idempotency_key
                    )
                    UPDATE internal_event_consumer_run run
                    SET status = 'blocked',
                        hold_reason = 'superseded_missing_signal_owner',
                        lease_token = '', locked_by = '', locked_at = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        last_error_code = 'superseded_missing_signal_owner',
                        last_error_message = '', updated_at = CURRENT_TIMESTAMP
                    FROM control, signal_event
                    WHERE run.event_id = signal_event.event_id
                      AND run.consumer_name = :consumer_name
                      AND (
                          (
                              run.status IN ('pending', 'failed_retryable')
                              AND (
                                  run.policy_version <> control.policy_version
                                  OR run.worker_generation NOT IN (0, control.active_generation)
                                  OR run.hold_reason <> ''
                                  OR run.attempt_count >= run.max_attempts
                              )
                          )
                          OR (
                              run.status = 'running'
                              AND COALESCE(run.lease_expires_at, '-infinity'::timestamptz)
                                  <= CURRENT_TIMESTAMP
                          )
                      )
                    RETURNING run.id
                    """
                ),
                {
                    "tenant_id": str(tenant_id or ""),
                    "idempotency_key": str(idempotency_key or ""),
                    "consumer_name": str(consumer_name or ""),
                },
            )
            .mappings()
            .fetchall()
        )
        return [dict(row) for row in rows]

    def claim_dbapi(
        self,
        executor: Any,
        *,
        lane: str,
        generation: int,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            WITH candidate AS (
                SELECT run.id
                FROM internal_event_consumer_run run
                LEFT JOIN queue_fairness_cursor fairness
                  ON fairness.lane = run.lane
                 AND fairness.fairness_key = run.fairness_key
                WHERE run.lane = %s
                  AND run.worker_generation IN (0, %s)
                  AND run.policy_version = (
                      SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                  )
                  AND run.status IN ('pending', 'failed_retryable')
                  AND run.hold_reason = ''
                  AND run.attempt_count < run.max_attempts
                  AND run.available_at <= CURRENT_TIMESTAMP
                  AND (run.lease_expires_at IS NULL OR run.lease_expires_at <= CURRENT_TIMESTAMP)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM internal_event_consumer_run active
                      WHERE active.lane = run.lane
                        AND active.ordering_key = run.ordering_key
                        AND active.ordering_key <> ''
                        AND active.status = 'running'
                        AND active.lease_expires_at > CURRENT_TIMESTAMP
                  )
                ORDER BY COALESCE(fairness.last_claimed_at, '-infinity'),
                         run.available_at ASC, run.id ASC
                LIMIT 1
                FOR UPDATE OF run SKIP LOCKED
            )
            UPDATE internal_event_consumer_run run
            SET status = 'running',
                locked_by = %s,
                lease_token = %s,
                locked_at = CURRENT_TIMESTAMP,
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                heartbeat_at = CURRENT_TIMESTAMP,
                worker_generation = %s,
                updated_at = CURRENT_TIMESTAMP
            FROM candidate
            WHERE run.id = candidate.id
            RETURNING run.*
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

    def recover_expired_dbapi(self, executor: Any, *, lane: str) -> None:
        executor.execute(
            """
            UPDATE internal_event_consumer_run
            SET status = CASE
                    WHEN attempt_count + 1 >= max_attempts THEN 'failed_terminal'
                    ELSE 'failed_retryable'
                END,
                attempt_count = attempt_count + 1,
                available_at = CASE
                    WHEN attempt_count + 1 < max_attempts THEN CURRENT_TIMESTAMP
                    ELSE available_at
                END,
                next_retry_at = CASE
                    WHEN attempt_count + 1 < max_attempts THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END,
                worker_generation = CASE
                    WHEN attempt_count + 1 < max_attempts THEN 0
                    ELSE worker_generation
                END,
                last_error_code = 'lease_expired',
                last_error_message = 'Consumer lease expired before completion.',
                lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                locked_by = '', locked_at = NULL,
                finished_at = CASE
                    WHEN attempt_count + 1 >= max_attempts THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE lane = %s
              AND status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= CURRENT_TIMESTAMP
            """,
            (str(lane or "").strip(),),
        )

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
            UPDATE internal_event_consumer_run
            SET heartbeat_at = CURRENT_TIMESTAMP,
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status = 'running'
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


__all__ = ["PostgresInternalEventConsumerRunWriteRepository"]
