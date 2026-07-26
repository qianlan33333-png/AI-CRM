from __future__ import annotations

from typing import Any

from sqlalchemy import text


class PostgresInternalEventOutboxRuntimeWriteRepository:
    """Canonical writer for cross-module internal-event outbox mutations.

    The surrounding business/runtime transaction remains owned by the caller;
    this repository never commits.
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
                    UPDATE internal_event_outbox
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

    def quarantine_superseded_signal_owner_sqlalchemy(
        self,
        executor: Any,
        *,
        tenant_id: str,
        idempotency_key: str,
    ) -> list[dict[str, Any]]:
        rows = (
            executor.execute(
                text(
                    """
                    WITH control AS (
                        SELECT active_generation, policy_version
                        FROM queue_runtime_control
                        WHERE singleton = TRUE
                    )
                    UPDATE internal_event_outbox outbox
                    SET status = 'failed_terminal',
                        hold_reason = 'superseded_missing_signal_owner',
                        lease_token = '', locked_by = '', locked_at = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        last_error_code = 'superseded_missing_signal_owner',
                        last_error_message = '', updated_at = CURRENT_TIMESTAMP
                    FROM control
                    WHERE outbox.tenant_id = :tenant_id
                      AND outbox.idempotency_key = :idempotency_key
                      AND (
                          (
                              outbox.status IN ('pending', 'failed_retryable')
                              AND (
                                  outbox.policy_version <> control.policy_version
                                  OR outbox.worker_generation NOT IN (0, control.active_generation)
                                  OR outbox.hold_reason <> ''
                                  OR outbox.attempt_count >= outbox.max_attempts
                              )
                          )
                          OR (
                              outbox.status = 'running'
                              AND COALESCE(
                                  outbox.lease_expires_at,
                                  '-infinity'::timestamptz
                              ) <= CURRENT_TIMESTAMP
                          )
                      )
                    RETURNING outbox.id
                    """
                ),
                {
                    "tenant_id": str(tenant_id or ""),
                    "idempotency_key": str(idempotency_key or ""),
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
                SELECT outbox.id
                FROM internal_event_outbox outbox
                LEFT JOIN queue_fairness_cursor fairness
                  ON fairness.lane = outbox.lane
                 AND fairness.fairness_key = outbox.fairness_key
                WHERE outbox.lane = %s
                  AND outbox.worker_generation IN (0, %s)
                  AND outbox.policy_version = (
                      SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                  )
                  AND outbox.status IN ('pending', 'failed_retryable')
                  AND outbox.hold_reason = ''
                  AND outbox.attempt_count < outbox.max_attempts
                  AND outbox.available_at <= CURRENT_TIMESTAMP
                  AND (outbox.lease_expires_at IS NULL OR outbox.lease_expires_at <= CURRENT_TIMESTAMP)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM internal_event_outbox active
                      WHERE active.lane = outbox.lane
                        AND active.ordering_key = outbox.ordering_key
                        AND active.ordering_key <> ''
                        AND active.status = 'running'
                        AND active.lease_expires_at > CURRENT_TIMESTAMP
                  )
                ORDER BY COALESCE(fairness.last_claimed_at, '-infinity'),
                         outbox.available_at ASC, outbox.id ASC
                LIMIT 1
                FOR UPDATE OF outbox SKIP LOCKED
            )
            UPDATE internal_event_outbox outbox
            SET status = 'running',
                attempt_count = attempt_count + 1,
                locked_by = %s,
                lease_token = %s,
                locked_at = CURRENT_TIMESTAMP,
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                heartbeat_at = CURRENT_TIMESTAMP,
                worker_generation = %s,
                updated_at = CURRENT_TIMESTAMP
            FROM candidate
            WHERE outbox.id = candidate.id
            RETURNING outbox.*
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
            UPDATE internal_event_outbox
            SET status = CASE
                    WHEN attempt_count >= max_attempts THEN 'failed_terminal'
                    ELSE 'failed_retryable'
                END,
                available_at = CASE
                    WHEN attempt_count < max_attempts THEN CURRENT_TIMESTAMP
                    ELSE available_at
                END,
                next_retry_at = CASE
                    WHEN attempt_count < max_attempts THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END,
                worker_generation = CASE
                    WHEN attempt_count < max_attempts THEN 0
                    ELSE worker_generation
                END,
                last_error_code = 'lease_expired',
                last_error_message = 'Outbox relay lease expired before completion.',
                lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                locked_by = '', locked_at = NULL,
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
            UPDATE internal_event_outbox
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


__all__ = ["PostgresInternalEventOutboxRuntimeWriteRepository"]
