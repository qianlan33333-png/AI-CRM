from __future__ import annotations

from typing import Any

from sqlalchemy import text


class PostgresWebhookInboxRuntimeWriteRepository:
    """Canonical webhook_inbox writer for queue-runtime operations."""

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
                    UPDATE webhook_inbox
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
        reason: str,
    ) -> dict[str, Any] | None:
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "retry":
            statement = """
                UPDATE webhook_inbox
                SET status = 'failed_retryable',
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    locked_at = NULL, locked_by = '',
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    max_attempts = GREATEST(max_attempts, attempt_count + 1),
                    last_error_code = 'operator_retry',
                    last_error_message = :reason,
                    finished_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND xmin::text = :expected_version
                  AND status IN (
                      'failed_retryable', 'failed_terminal', 'dead_letter', 'processing'
                  )
                  AND hold_reason = ''
                  AND (
                      status <> 'processing'
                      OR lease_expires_at <= CURRENT_TIMESTAMP
                      OR (
                          lease_expires_at IS NULL
                          AND locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                      )
                  )
                RETURNING id, execution_id, lane, status, hold_reason,
                          xmin::text AS version_token
            """
        elif normalized_action == "skip":
            statement = """
                UPDATE webhook_inbox
                SET status = 'ignored',
                    next_retry_at = NULL,
                    locked_at = NULL, locked_by = '',
                    lease_token = '', lease_expires_at = NULL,
                    heartbeat_at = NULL, worker_generation = 0,
                    last_error_code = 'operator_skip',
                    last_error_message = :reason,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :item_id
                  AND status = :expected_status
                  AND xmin::text = :expected_version
                  AND status IN (
                      'received', 'failed_retryable', 'failed_terminal',
                      'dead_letter', 'processing'
                  )
                  AND (
                      status <> 'processing'
                      OR lease_expires_at <= CURRENT_TIMESTAMP
                      OR (
                          lease_expires_at IS NULL
                          AND locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                      )
                  )
                RETURNING id, execution_id, lane, status, hold_reason,
                          xmin::text AS version_token
            """
        else:
            raise ValueError(f"unsupported webhook inbox action: {normalized_action}")
        row = (
            executor.execute(
                text(statement),
                {
                    "item_id": int(item_id),
                    "expected_status": str(expected_status or ""),
                    "expected_version": str(expected_version or ""),
                    "reason": str(reason or "")[:500],
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
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            WITH candidate AS (
                SELECT inbox.id
                FROM webhook_inbox inbox
                LEFT JOIN queue_fairness_cursor fairness
                  ON fairness.lane = inbox.lane
                 AND fairness.fairness_key = inbox.fairness_key
                WHERE inbox.lane = %s
                  AND inbox.worker_generation IN (0, %s)
                  AND inbox.policy_version = (
                      SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE
                  )
                  AND inbox.status IN ('received', 'failed_retryable')
                  AND inbox.hold_reason = ''
                  AND inbox.attempt_count < inbox.max_attempts
                  AND inbox.available_at <= CURRENT_TIMESTAMP
                  AND (inbox.lease_expires_at IS NULL OR inbox.lease_expires_at <= CURRENT_TIMESTAMP)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM webhook_inbox active
                      WHERE active.lane = inbox.lane
                        AND active.ordering_key = inbox.ordering_key
                        AND active.ordering_key <> ''
                        AND active.status = 'processing'
                        AND active.lease_expires_at > CURRENT_TIMESTAMP
                  )
                ORDER BY COALESCE(fairness.last_claimed_at, '-infinity'),
                         inbox.received_at ASC, inbox.id ASC
                LIMIT 1
                FOR UPDATE OF inbox SKIP LOCKED
            )
            UPDATE webhook_inbox inbox
            SET status = 'processing',
                locked_by = %s,
                lease_token = %s,
                locked_at = CURRENT_TIMESTAMP,
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                heartbeat_at = CURRENT_TIMESTAMP,
                worker_generation = %s,
                updated_at = CURRENT_TIMESTAMP
            FROM candidate
            WHERE inbox.id = candidate.id
            RETURNING inbox.*
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
            UPDATE webhook_inbox
            SET status = CASE
                    WHEN attempt_count + 1 >= max_attempts THEN 'dead_letter'
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
                last_error_message = 'Webhook processing lease expired before completion.',
                lease_token = '', lease_expires_at = NULL, heartbeat_at = NULL,
                locked_by = '', locked_at = NULL,
                finished_at = CASE
                    WHEN attempt_count + 1 >= max_attempts THEN CURRENT_TIMESTAMP
                    ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE lane = %s
              AND status = 'processing'
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
            UPDATE webhook_inbox
            SET heartbeat_at = CURRENT_TIMESTAMP,
                lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status = 'processing'
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


__all__ = ["PostgresWebhookInboxRuntimeWriteRepository"]
