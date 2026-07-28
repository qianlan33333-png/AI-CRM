from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy import text

from .broadcast_job_write_port import BroadcastJobCreate, ExecuteReturning


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


class PostgresBroadcastJobWriteRepository:
    """Canonical broadcast-job writer; surrounding transactions stay with callers."""

    def create_dbapi(self, executor: Any, request: BroadcastJobCreate) -> dict[str, Any] | None:
        row = executor.execute(
            """
            INSERT INTO broadcast_jobs (
                source_type, source_id, source_table, scheduled_for, priority, batch_key,
                business_domain, idempotency_key, channel, target_kind,
                retry_policy_json, metadata_json, status, requires_approval,
                target_unionids_json, target_count, target_summary,
                content_type, content_payload, content_summary,
                trace_id, created_by, execution_id
            ) VALUES (
                %s, %s, %s, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), %s, %s,
                %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s, %s,
                %s::jsonb, %s, %s,
                %s, %s::jsonb, %s,
                %s, %s, %s
            )
            ON CONFLICT (idempotency_key)
                WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
            DO NOTHING
            RETURNING *
            """,
            (
                str(request.source_type or "").strip(),
                str(request.source_id or "").strip(),
                str(request.source_table or "").strip(),
                request.scheduled_for,
                int(request.priority),
                str(request.batch_key or "").strip(),
                str(request.business_domain or "").strip(),
                str(request.idempotency_key or "").strip()[:255],
                str(request.channel or "").strip(),
                str(request.target_kind or "").strip(),
                _json(request.retry_policy),
                _json(request.metadata),
                str(request.status or "queued").strip(),
                bool(request.requires_approval),
                _json([str(value or "").strip() for value in request.target_unionids]),
                len([value for value in request.target_unionids if str(value or "").strip()]),
                str(request.target_summary or "")[:1000],
                str(request.content_type or "text").strip(),
                _json(request.content_payload),
                str(request.content_summary or "")[:1000],
                str(request.trace_id or "")[:100],
                str(request.created_by or "")[:100],
                str(request.execution_id or "").strip(),
            ),
        ).fetchone()
        return dict(row) if row else None

    def set_execution_id_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        execution_id: str,
    ) -> None:
        executor.execute(
            """
            UPDATE broadcast_jobs
            SET execution_id = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND COALESCE(execution_id, '') = ''
            """,
            (str(execution_id or "").strip(), int(job_id)),
        )

    def delegate_external_effect_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        external_effect_job_id: int,
        trace_id: str,
        actor: str,
    ) -> dict[str, Any] | None:
        current = executor.execute(
            "SELECT status, external_effect_job_id FROM broadcast_jobs WHERE id = %s FOR UPDATE",
            (int(job_id),),
        ).fetchone()
        if not current:
            return None
        status = str(current.get("status") or "").strip()
        current_effect_id = int(current.get("external_effect_job_id") or 0)
        if status not in {"queued", "delegated"}:
            return None
        if current_effect_id and current_effect_id != int(external_effect_job_id):
            raise RuntimeError("broadcast job is already linked to another external effect")
        outbound_task = executor.execute(
            """
            INSERT INTO outbound_tasks (
                broadcast_job_id, task_type, request_payload, response_payload,
                wecom_task_id, status, trace_id
            ) VALUES (
                %s, 'broadcast_job/external_effect_delegate',
                jsonb_build_object('broadcast_job_id', %s),
                jsonb_build_object('external_effect_job_ids', jsonb_build_array(%s)),
                '', 'delegated', %s
            )
            ON CONFLICT (broadcast_job_id) WHERE broadcast_job_id IS NOT NULL
            DO UPDATE SET
                task_type = EXCLUDED.task_type,
                request_payload = EXCLUDED.request_payload,
                response_payload = EXCLUDED.response_payload,
                status = EXCLUDED.status,
                trace_id = EXCLUDED.trace_id
            RETURNING id
            """,
            (
                int(job_id),
                int(job_id),
                int(external_effect_job_id),
                str(trace_id or "").strip(),
            ),
        ).fetchone()
        row = executor.execute(
            """
            UPDATE broadcast_jobs
            SET status = 'delegated',
                outbound_task_id = %s,
                external_effect_job_id = %s,
                execution_owner = 'external_effect_job',
                result_summary_json = jsonb_build_object(
                    'status', 'delegated',
                    'materialization', 'approval_transaction',
                    'external_effect_job_id', %s,
                    'side_effect_executed', FALSE,
                    'provider_result_received', FALSE
                ),
                claim_token = '',
                lease_expires_at = NULL,
                completed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status IN ('queued', 'delegated')
              AND (external_effect_job_id IS NULL OR external_effect_job_id = %s)
            RETURNING *
            """,
            (
                int((outbound_task or {}).get("id") or 0) or None,
                int(external_effect_job_id),
                int(external_effect_job_id),
                int(job_id),
                int(external_effect_job_id),
            ),
        ).fetchone()
        if row and status != "delegated":
            executor.execute(
                """
                INSERT INTO broadcast_job_events (
                    job_id, event_type, from_status, to_status,
                    event_payload, actor_type, actor_id
                ) VALUES (
                    %s, 'effect_materialized', %s, 'delegated',
                    jsonb_build_object(
                        'external_effect_job_id', %s,
                        'materialization', 'approval_transaction'
                    ),
                    'system', %s
                )
                """,
                (
                    int(job_id),
                    status,
                    int(external_effect_job_id),
                    str(actor or "immediate_broadcast_delegate")[:200],
                ),
            )
        return dict(row) if row else None

    def cancel_campaign_jobs_dbapi(
        self,
        executor: Any,
        *,
        campaign_ids: Sequence[int],
        actor: str,
        reason: str,
        source_type: str,
        source_table: str,
        open_statuses: Sequence[str],
    ) -> list[int]:
        normalized_ids = sorted({int(value) for value in campaign_ids if int(value) > 0})
        if not normalized_ids:
            return []
        rows = executor.execute(
            """
            UPDATE broadcast_jobs job
            SET status = 'cancelled',
                cancelled_by = %s,
                cancelled_at = CURRENT_TIMESTAMP,
                cancel_reason = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE job.source_type = %s
              AND COALESCE(job.source_table, %s) = %s
              AND job.status = ANY(%s)
              AND EXISTS (
                  SELECT 1
                  FROM unnest(%s::int[]) AS campaign_id
                  WHERE job.source_id LIKE (campaign_id::text || ':%%')
              )
            RETURNING job.id
            """,
            (
                str(actor or "").strip(),
                str(reason or "")[:1000],
                str(source_type or "").strip(),
                str(source_table or "").strip(),
                str(source_table or "").strip(),
                [str(value or "").strip() for value in open_statuses],
                normalized_ids,
            ),
        ).fetchall()
        return [int(row["id"]) for row in rows]

    def ack_message_batch_via(
        self,
        execute_returning: ExecuteReturning,
        *,
        batch_id: int,
        ack_note: str,
        acked_by: str,
    ) -> dict[str, Any] | None:
        return execute_returning(
            """
            UPDATE broadcast_jobs
            SET metadata_json = metadata_json || jsonb_build_object(
                    'message_batch_ack',
                    jsonb_build_object(
                        'ack_note', %s::text,
                        'acked_by', %s::text,
                        'acked_at', to_char(
                            CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS"Z"'
                        )
                    )
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id,
                      COALESCE(NULLIF(batch_key, ''), 'broadcast:' || id::text) AS batch_key,
                      scheduled_for AS window_start,
                      COALESCE(sent_at, updated_at, created_at) AS window_end,
                      'acked' AS status,
                      COALESCE(
                          NULLIF(target_count, 0),
                          jsonb_array_length(target_unionids_json)
                      ) AS message_count,
                      created_at,
                      metadata_json #>> '{message_batch_ack,acked_at}' AS acked_at,
                      metadata_json #>> '{message_batch_ack,ack_note}' AS ack_note,
                      metadata_json #>> '{message_batch_ack,acked_by}' AS acked_by
            """,
            (str(ack_note or ""), str(acked_by or ""), int(batch_id)),
        )

    def approve_via(
        self,
        execute_returning: ExecuteReturning,
        *,
        job_id: int,
        approved_by: str,
    ) -> dict[str, Any] | None:
        return execute_returning(
            """
            UPDATE broadcast_jobs
            SET status = 'queued', approved_by = %s,
                approved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'waiting_approval'
            RETURNING *
            """,
            (str(approved_by or "").strip(), int(job_id)),
        )

    def cancel_via(
        self,
        execute_returning: ExecuteReturning,
        *,
        job_id: int,
        cancelled_by: str,
        reason: str,
    ) -> dict[str, Any] | None:
        return execute_returning(
            """
            UPDATE broadcast_jobs
            SET status = 'cancelled', cancelled_by = %s,
                cancelled_at = CURRENT_TIMESTAMP, cancel_reason = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status IN ('queued', 'waiting_approval')
            RETURNING *
            """,
            (str(cancelled_by or "").strip(), str(reason or "")[:1000], int(job_id)),
        )

    def claim_due_dbapi(
        self,
        executor: Any,
        *,
        limit: int,
        now: Any,
        claim_token: str,
        lease_expires_at: Any,
    ) -> list[dict[str, Any]]:
        rows = executor.execute(
            """
            WITH due AS (
                SELECT id
                FROM broadcast_jobs
                WHERE scheduled_for <= %s
                  AND hold_reason = ''
                  AND attempt_count < max_attempts
                  AND (
                    status = 'queued'
                    OR (
                        status = 'claimed'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= %s
                    )
                    OR (
                        status = 'failed_retryable'
                        AND (next_retry_at IS NULL OR next_retry_at <= %s)
                    )
                  )
                ORDER BY priority ASC, scheduled_for ASC, id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE broadcast_jobs job
            SET status = 'claimed',
                claimed_at = %s,
                claim_token = %s,
                lease_expires_at = %s,
                attempt_count = attempt_count + 1,
                updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE job.id = due.id
            RETURNING job.*
            """,
            (
                now,
                now,
                now,
                int(limit),
                now,
                str(claim_token or "").strip(),
                lease_expires_at,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    def begin_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        claim_token: str,
        now: Any,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            UPDATE broadcast_jobs
            SET status = 'dispatching',
                dispatch_started_at = %s,
                reconciliation_required = FALSE,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND hold_reason = ''
              AND status = 'claimed'
              AND claim_token = %s
            RETURNING *
            """,
            (now, int(job_id), str(claim_token or "").strip()),
        ).fetchone()
        return dict(row) if row else None

    def finalize_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        claim_token: str,
        final_status: str,
        outbound_task_id: int | None,
        sent_count: int,
        failed_count: int,
        failure_type: str,
        error_text: str,
        side_effect_executed: bool,
        provider_result_received: bool,
        result_summary_json: str,
        reconciliation_required: bool,
        external_effect_job_id: int | None,
        retry_delay_seconds: int,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            UPDATE broadcast_jobs
            SET status = %s,
                outbound_task_id = %s,
                sent_count = %s,
                failed_count = %s,
                failure_type = %s,
                last_error = %s,
                side_effect_executed = %s,
                provider_result_received = %s,
                result_summary_json = CAST(%s AS jsonb),
                reconciliation_required = %s,
                external_effect_job_id = COALESCE(external_effect_job_id, %s),
                execution_owner = CASE
                    WHEN CAST(%s AS BIGINT) IS NULL THEN execution_owner
                    ELSE 'external_effect_job'
                END,
                claim_token = '', lease_expires_at = NULL,
                next_retry_at = CASE WHEN %s = 'failed_retryable'
                    THEN CURRENT_TIMESTAMP + (%s * INTERVAL '1 second') ELSE NULL END,
                sent_at = CASE WHEN %s = 'sent' THEN CURRENT_TIMESTAMP ELSE NULL END,
                completed_at = CASE
                    WHEN %s IN ('failed_retryable', 'delegated') THEN NULL
                    ELSE CURRENT_TIMESTAMP
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND status = 'dispatching'
              AND claim_token = %s
            RETURNING *
            """,
            (
                str(final_status or "").strip(),
                outbound_task_id,
                int(sent_count),
                int(failed_count),
                str(failure_type or "")[:200],
                str(error_text or "")[:1000],
                bool(side_effect_executed),
                bool(provider_result_received),
                str(result_summary_json or "{}"),
                bool(reconciliation_required),
                external_effect_job_id,
                external_effect_job_id,
                str(final_status or "").strip(),
                max(0, int(retry_delay_seconds)),
                str(final_status or "").strip(),
                str(final_status or "").strip(),
                int(job_id),
                str(claim_token or "").strip(),
            ),
        ).fetchone()
        return dict(row) if row else None

    def mark_unknown_after_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        claim_token: str,
        error_text: str,
        side_effect_executed: bool,
        provider_result_received: bool,
        result_summary_json: str,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            UPDATE broadcast_jobs
            SET status = 'unknown_after_dispatch',
                failure_type = 'post_provider_persistence_unknown',
                last_error = %s,
                side_effect_executed = %s,
                provider_result_received = %s,
                result_summary_json = CAST(%s AS jsonb),
                reconciliation_required = TRUE,
                claim_token = '', lease_expires_at = NULL, next_retry_at = NULL,
                completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'dispatching' AND claim_token = %s
            RETURNING *
            """,
            (
                str(error_text or "")[:1000],
                bool(side_effect_executed),
                bool(provider_result_received),
                str(result_summary_json or "{}"),
                int(job_id),
                str(claim_token or "").strip(),
            ),
        ).fetchone()
        return dict(row) if row else None

    def settle_from_external_effect_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        status: str,
        sent_count: int,
        failed_count: int,
        side_effect_executed: bool,
        provider_result_received: bool,
        reconciliation_required: bool,
        result_summary_json: str,
        last_error: str,
    ) -> None:
        executor.execute(
            text(
                """
                UPDATE broadcast_jobs
                SET status = :status,
                    sent_count = :sent_count,
                    failed_count = :failed_count,
                    side_effect_executed = :side_effect_executed,
                    provider_result_received = :provider_result_received,
                    reconciliation_required = :reconciliation_required,
                    result_summary_json = CAST(:result_summary AS jsonb),
                    last_error = :last_error,
                    sent_at = CASE
                        WHEN :status = 'sent' THEN COALESCE(sent_at, CURRENT_TIMESTAMP)
                        ELSE sent_at
                    END,
                    completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id AND execution_owner = 'external_effect_job'
                """
            ),
            {
                "job_id": int(job_id),
                "status": str(status or "").strip(),
                "sent_count": int(sent_count),
                "failed_count": int(failed_count),
                "side_effect_executed": bool(side_effect_executed),
                "provider_result_received": bool(provider_result_received),
                "reconciliation_required": bool(reconciliation_required),
                "result_summary": str(result_summary_json or "{}"),
                "last_error": str(last_error or "")[:1000],
            },
        )


__all__ = ["PostgresBroadcastJobWriteRepository"]
