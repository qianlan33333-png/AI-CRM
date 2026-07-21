from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from aicrm_next.shared.runtime import production_data_ready

from .domain import BROADCAST_SOURCE_TYPES, BROADCAST_STATUSES


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def _json_load(value: Any, *, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


class AdminJobsRepository(Protocol):
    source_status: str

    def list_sync_runs(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]: ...
    def sync_run_counts(self) -> dict[str, int]: ...
    def list_callback_logs(self, *, process_status: str = "", query: str = "", limit: int = 20) -> list[dict[str, Any]]: ...
    def callback_counts(self) -> dict[str, int]: ...
    def list_message_batches(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]: ...
    def message_batch_counts(self) -> dict[str, int]: ...
    def get_message_batch(self, batch_id: int) -> dict[str, Any] | None: ...
    def list_batch_messages(self, batch_id: int, *, limit: int = 50) -> list[dict[str, Any]]: ...
    def count_batch_messages(self, batch_id: int) -> int: ...
    def ack_message_batch(self, batch_id: int, *, ack_note: str, acked_by: str) -> dict[str, Any] | None: ...
    def list_deferred_jobs(self, *, status: str = "", owner_userid: str = "", external_userid: str = "", limit: int = 20) -> list[dict[str, Any]]: ...
    def deferred_job_counts(self) -> dict[str, int]: ...
    def webhook_counts(self) -> dict[str, int]: ...
    def list_webhook_deliveries(self, *, event_type: str = "", status: str = "", limit: int = 20) -> list[dict[str, Any]]: ...
    def get_webhook_delivery(self, delivery_id: int) -> dict[str, Any] | None: ...
    def update_webhook_delivery(self, delivery_id: int, updates: dict[str, Any]) -> dict[str, Any] | None: ...
    def due_webhook_deliveries(self, *, limit: int) -> list[dict[str, Any]]: ...
    def list_broadcast_jobs(self, *, statuses: list[str] | None = None, source_types: list[str] | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]: ...
    def broadcast_total(self, *, statuses: list[str] | None = None, source_types: list[str] | None = None) -> int: ...
    def broadcast_counts(self, *, statuses: list[str] | None = None, source_types: list[str] | None = None) -> dict[str, int]: ...
    def get_broadcast_job(self, job_id: int) -> dict[str, Any] | None: ...
    def approve_broadcast_job(self, job_id: int, *, approved_by: str) -> dict[str, Any] | None: ...
    def cancel_broadcast_job(self, job_id: int, *, cancelled_by: str, reason: str) -> dict[str, Any] | None: ...
    def get_broadcast_notification_setting(self, channel: str) -> dict[str, Any] | None: ...
    def broadcast_hourly_summary(self, *, window_start: datetime, window_end: datetime) -> dict[str, int]: ...
    def create_broadcast_hourly_report_pending(self, *, report_key: str, window_start: datetime, window_end: datetime, channel: str) -> str: ...
    def mark_broadcast_hourly_report_sent(self, *, report_key: str, payload_json: dict[str, Any]) -> None: ...
    def mark_broadcast_hourly_report_failed(self, *, report_key: str, error_message: str) -> None: ...
    def upsert_broadcast_notification_setting(
        self,
        *,
        channel: str,
        enabled: bool,
        webhook_url: str,
        validation_status: str,
        validated_at: datetime | str | None,
        last_validation_error: str | None,
    ) -> dict[str, Any]: ...
    def insert_audit(self, *, operator: str, action_type: str, target_type: str, target_id: str, before: dict[str, Any], after: dict[str, Any]) -> None: ...


class PostgresAdminJobsRepository:
    source_status = "production_postgres"

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(os.getenv("DATABASE_URL", ""), row_factory=dict_row)

    def _rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]

    def _one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._rows(query, params)
        return rows[0] if rows else None

    def _execute_returning(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                conn.commit()
                return dict(row) if row else None

    def _execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                conn.commit()

    def list_sync_runs(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        return self._rows(
            """
            SELECT id, status, start_time, end_time, owner_userid, cursor, fetched_count, inserted_count,
                   error_message, created_at, finished_at
            FROM sync_runs
            """ + where + " ORDER BY id DESC LIMIT %s",
            tuple(params),
        )

    def sync_run_counts(self) -> dict[str, int]:
        return _count_row(
            self._one(
                """
                SELECT COUNT(*) AS total_count,
                       COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS success_count,
                       COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count
                FROM sync_runs
                """
            )
        )

    def list_callback_logs(self, *, process_status: str = "", query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if process_status:
            clauses.append("process_status = %s")
            params.append(process_status)
        if query:
            like_value = f"%{query.lower()}%"
            clauses.append(
                "(LOWER(event_type) LIKE %s OR LOWER(change_type) LIKE %s OR LOWER(external_userid) LIKE %s "
                "OR LOWER(user_id) LIKE %s OR LOWER(error_message) LIKE %s OR LOWER(event_key) LIKE %s)"
            )
            params.extend([like_value] * 6)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        return self._rows(
            """
            SELECT id, corp_id, event_type, change_type, external_userid, user_id, event_time, event_key,
                   process_status, retry_count, error_message, created_at, updated_at
            FROM wecom_external_contact_event_logs
            """ + where + " ORDER BY id DESC LIMIT %s",
            tuple(params),
        )

    def callback_counts(self) -> dict[str, int]:
        return _count_row(
            self._one(
                """
                SELECT COUNT(*) AS total_count,
                       COALESCE(SUM(CASE WHEN process_status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                       COALESCE(SUM(CASE WHEN process_status = 'processing' THEN 1 ELSE 0 END), 0) AS processing_count,
                       COALESCE(SUM(CASE WHEN process_status = 'success' THEN 1 ELSE 0 END), 0) AS success_count,
                       COALESCE(SUM(CASE WHEN process_status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count
                FROM wecom_external_contact_event_logs
                """
            )
        )

    def list_message_batches(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        params: list[Any] = []
        clauses: list[str] = []
        if status == "acked":
            clauses.append("metadata_json ? 'message_batch_ack'")
        elif status == "pending":
            clauses.append("NOT (metadata_json ? 'message_batch_ack')")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        return self._rows(
            """
            SELECT id,
                   COALESCE(NULLIF(batch_key, ''), 'broadcast:' || id::text) AS batch_key,
                   scheduled_for AS window_start,
                   COALESCE(sent_at, updated_at, created_at) AS window_end,
                   CASE WHEN metadata_json ? 'message_batch_ack' THEN 'acked' ELSE 'pending' END AS status,
                   COALESCE(NULLIF(target_count, 0), jsonb_array_length(target_unionids_json)) AS message_count,
                   created_at,
                   metadata_json #>> '{message_batch_ack,acked_at}' AS acked_at,
                   metadata_json #>> '{message_batch_ack,ack_note}' AS ack_note,
                   metadata_json #>> '{message_batch_ack,acked_by}' AS acked_by
            FROM broadcast_jobs
            """ + where + " ORDER BY id DESC LIMIT %s",
            tuple(params),
        )

    def message_batch_counts(self) -> dict[str, int]:
        return _count_row(
            self._one(
                """
                SELECT COUNT(*) AS total_count,
                       COALESCE(SUM(CASE WHEN NOT (metadata_json ? 'message_batch_ack') THEN 1 ELSE 0 END), 0) AS pending_count,
                       COALESCE(SUM(CASE WHEN metadata_json ? 'message_batch_ack' THEN 1 ELSE 0 END), 0) AS acked_count
                FROM broadcast_jobs
                """
            )
        )

    def get_message_batch(self, batch_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT id,
                   COALESCE(NULLIF(batch_key, ''), 'broadcast:' || id::text) AS batch_key,
                   scheduled_for AS window_start,
                   COALESCE(sent_at, updated_at, created_at) AS window_end,
                   CASE WHEN metadata_json ? 'message_batch_ack' THEN 'acked' ELSE 'pending' END AS status,
                   COALESCE(NULLIF(target_count, 0), jsonb_array_length(target_unionids_json)) AS message_count,
                   created_at,
                   metadata_json #>> '{message_batch_ack,acked_at}' AS acked_at,
                   metadata_json #>> '{message_batch_ack,ack_note}' AS ack_note,
                   metadata_json #>> '{message_batch_ack,acked_by}' AS acked_by
            FROM broadcast_jobs
            WHERE id = %s
            """,
            (batch_id,),
        )

    def list_batch_messages(self, batch_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT job_id AS batch_id,
                   id AS message_id,
                   COALESCE(event_payload->>'msgid', '') AS msgid,
                   COALESCE(event_payload->>'chat_type', '') AS chat_type,
                   COALESCE(event_payload->>'chat_id', '') AS chat_id,
                   COALESCE(event_payload->>'external_userid', event_payload->>'unionid', '') AS external_userid,
                   COALESCE(actor_id, '') AS owner_userid,
                   created_at AS send_time,
                   COALESCE(event_payload->>'content', event_payload->>'content_summary', event_type) AS content,
                   event_type AS msgtype,
                   COALESCE(actor_id, '') AS sender,
                   '' AS receiver
            FROM broadcast_job_events
            WHERE job_id = %s
            ORDER BY created_at ASC, id ASC
            LIMIT %s
            """,
            (batch_id, limit),
        )

    def count_batch_messages(self, batch_id: int) -> int:
        row = self._one("SELECT COUNT(*) AS total FROM broadcast_job_events WHERE job_id = %s", (batch_id,))
        return int((row or {}).get("total") or 0)

    def ack_message_batch(self, batch_id: int, *, ack_note: str, acked_by: str) -> dict[str, Any] | None:
        return self._execute_returning(
            """
            UPDATE broadcast_jobs
            SET metadata_json = metadata_json || jsonb_build_object(
                    'message_batch_ack',
                    jsonb_build_object(
                        'ack_note', %s::text,
                        'acked_by', %s::text,
                        'acked_at', to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                    )
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id,
                      COALESCE(NULLIF(batch_key, ''), 'broadcast:' || id::text) AS batch_key,
                      scheduled_for AS window_start,
                      COALESCE(sent_at, updated_at, created_at) AS window_end,
                      'acked' AS status,
                      COALESCE(NULLIF(target_count, 0), jsonb_array_length(target_unionids_json)) AS message_count,
                      created_at,
                      metadata_json #>> '{message_batch_ack,acked_at}' AS acked_at,
                      metadata_json #>> '{message_batch_ack,ack_note}' AS ack_note,
                      metadata_json #>> '{message_batch_ack,acked_by}' AS acked_by
            """,
            (ack_note, acked_by, batch_id),
        )

    def list_deferred_jobs(self, *, status: str = "", owner_userid: str = "", external_userid: str = "", limit: int = 20) -> list[dict[str, Any]]:
        del status, owner_userid, external_userid, limit
        return []

    def deferred_job_counts(self) -> dict[str, int]:
        return _status_counts([])

    def webhook_counts(self) -> dict[str, int]:
        row = self._one(
            """
            SELECT COUNT(*) AS total_count,
                   COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS success_count,
                   COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count,
                   COALESCE(SUM(CASE WHEN status = 'retry_scheduled' THEN 1 ELSE 0 END), 0) AS retry_scheduled_count,
                   COALESCE(SUM(CASE WHEN status = 'exhausted' THEN 1 ELSE 0 END), 0) AS exhausted_count
            FROM outbound_webhook_deliveries
            """
        )
        return _count_row(row)

    def list_webhook_deliveries(self, *, event_type: str = "", status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type = %s")
            params.append(event_type)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        return self._rows(
            """
            SELECT id, event_type, source_key, source_id, target_url, payload_json, payload_summary,
                   token_configured, status, attempt_count, max_attempts, response_status_code,
                   response_body_summary, last_error, last_attempted_at, next_retry_at, created_at, updated_at
            FROM outbound_webhook_deliveries
            """ + where + " ORDER BY id DESC LIMIT %s",
            tuple(params),
        )

    def get_webhook_delivery(self, delivery_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT id, event_type, source_key, source_id, target_url, payload_json, payload_summary,
                   token_configured, status, attempt_count, max_attempts, response_status_code,
                   response_body_summary, last_error, last_attempted_at, next_retry_at, created_at, updated_at
            FROM outbound_webhook_deliveries
            WHERE id = %s
            """,
            (delivery_id,),
        )

    def update_webhook_delivery(self, delivery_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {
            "target_url",
            "token_configured",
            "status",
            "attempt_count",
            "response_status_code",
            "response_body_summary",
            "last_error",
            "last_attempted_at",
            "next_retry_at",
        }
        assignments = [f"{key} = %s" for key in updates if key in allowed]
        params = [updates[key] for key in updates if key in allowed]
        if not assignments:
            return self.get_webhook_delivery(delivery_id)
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        params.append(delivery_id)
        return self._execute_returning(
            f"""
            UPDATE outbound_webhook_deliveries
            SET {', '.join(assignments)}
            WHERE id = %s
            RETURNING id, event_type, source_key, source_id, target_url, payload_json, payload_summary,
                      token_configured, status, attempt_count, max_attempts, response_status_code,
                      response_body_summary, last_error, last_attempted_at, next_retry_at, created_at, updated_at
            """,
            tuple(params),
        )

    def due_webhook_deliveries(self, *, limit: int) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT id, event_type, source_key, source_id, target_url, payload_json, payload_summary,
                   token_configured, status, attempt_count, max_attempts, response_status_code,
                   response_body_summary, last_error, last_attempted_at, next_retry_at, created_at, updated_at
            FROM outbound_webhook_deliveries
            WHERE status = 'retry_scheduled'
              AND (next_retry_at IS NULL OR next_retry_at = '' OR next_retry_at::timestamptz <= CURRENT_TIMESTAMP)
            ORDER BY next_retry_at ASC NULLS FIRST, id ASC
            LIMIT %s
            """,
            (limit,),
        )

    def list_broadcast_jobs(self, *, statuses: list[str] | None = None, source_types: list[str] | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if statuses:
            where.append("status = ANY(%s)")
            params.append(statuses)
        if source_types:
            where.append("source_type = ANY(%s)")
            params.append(source_types)
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        params.extend([limit, offset])
        return self._rows(
            """
            SELECT bj.id, bj.source_type, bj.source_id, bj.source_table, bj.scheduled_for, bj.priority, bj.batch_key,
                   bj.business_domain, bj.channel, bj.target_kind, bj.failure_type,
                   CASE WHEN COALESCE(bj.idempotency_key, '') <> '' THEN TRUE ELSE FALSE END AS has_idempotency_key,
                   bj.status, bj.requires_approval, bj.approved_by, bj.approved_at,
                   bj.cancelled_by, bj.cancelled_at, bj.cancel_reason,
                   bj.target_count, bj.target_summary, bj.content_type, bj.content_summary,
                   bj.attempt_count, bj.last_error, bj.outbound_task_id, bj.sent_count, bj.failed_count,
                   bj.trace_id, bj.created_by, bj.created_at, bj.updated_at, bj.claimed_at, bj.sent_at,
                   ot.status AS outbound_task_status
            FROM broadcast_jobs bj
            LEFT JOIN outbound_tasks ot ON ot.id = bj.outbound_task_id
            """ + where_sql + " ORDER BY bj.scheduled_for DESC, bj.id DESC LIMIT %s OFFSET %s",
            tuple(params),
        )

    def broadcast_total(
        self,
        *,
        statuses: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> int:
        where: list[str] = []
        params: list[Any] = []
        if statuses:
            where.append("status = ANY(%s)")
            params.append(statuses)
        if source_types:
            where.append("source_type = ANY(%s)")
            params.append(source_types)
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        row = self._one("SELECT COUNT(*) AS total FROM broadcast_jobs" + where_sql, tuple(params))
        return int((row or {}).get("total") or 0)

    def broadcast_counts(
        self,
        *,
        statuses: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> dict[str, int]:
        where: list[str] = []
        params: list[Any] = []
        if statuses:
            where.append("status = ANY(%s)")
            params.append(statuses)
        if source_types:
            where.append("source_type = ANY(%s)")
            params.append(source_types)
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        return _status_counts(
            self._rows(
                "SELECT status, COUNT(*) AS cnt FROM broadcast_jobs"
                + where_sql
                + " GROUP BY status",
                tuple(params),
            )
        )

    def broadcast_hourly_summary(self, *, window_start: datetime, window_end: datetime) -> dict[str, int]:
        row = self._one(
            """
            SELECT
              COUNT(*) AS total_jobs,
              COALESCE(SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END), 0) AS success_jobs,
              COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_jobs,
              COALESCE(SUM(CASE WHEN status IN ('queued', 'claimed', 'waiting_approval') THEN 1 ELSE 0 END), 0) AS pending_jobs,
              COALESCE(SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END), 0) AS cancelled_jobs
            FROM broadcast_jobs
            WHERE scheduled_for >= %s
              AND scheduled_for < %s
            """,
            (window_start, window_end),
        )
        return _count_row(row)

    def get_broadcast_job(self, job_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM broadcast_jobs WHERE id = %s", (job_id,))

    def approve_broadcast_job(self, job_id: int, *, approved_by: str) -> dict[str, Any] | None:
        return self._execute_returning(
            """
            UPDATE broadcast_jobs
            SET status = 'queued', approved_by = %s, approved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status = 'waiting_approval'
            RETURNING *
            """,
            (approved_by, job_id),
        )

    def cancel_broadcast_job(self, job_id: int, *, cancelled_by: str, reason: str) -> dict[str, Any] | None:
        return self._execute_returning(
            """
            UPDATE broadcast_jobs
            SET status = 'cancelled', cancelled_by = %s, cancelled_at = CURRENT_TIMESTAMP,
                cancel_reason = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND status IN ('queued', 'waiting_approval')
            RETURNING *
            """,
            (cancelled_by, reason[:1000], job_id),
        )

    def get_broadcast_notification_setting(self, channel: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT channel, enabled, webhook_url, validation_status, validated_at,
                   last_validation_error, created_at, updated_at
            FROM broadcast_queue_notification_settings
            WHERE channel = %s
            """,
            (channel,),
        )

    def create_broadcast_hourly_report_pending(self, *, report_key: str, window_start: datetime, window_end: datetime, channel: str) -> str:
        row = self._execute_returning(
            """
            INSERT INTO broadcast_job_hourly_reports (
                report_key, window_start, window_end, channel, status, payload_json,
                created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, 'pending', '{}'::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(report_key) DO NOTHING
            RETURNING report_key
            """,
            (report_key, window_start, window_end, channel),
        )
        return "created" if row else "duplicate"

    def mark_broadcast_hourly_report_sent(self, *, report_key: str, payload_json: dict[str, Any]) -> None:
        self._execute(
            """
            UPDATE broadcast_job_hourly_reports
            SET status = 'sent',
                payload_json = %s::jsonb,
                error_message = NULL,
                sent_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE report_key = %s
            """,
            (json.dumps(payload_json, ensure_ascii=False), report_key),
        )

    def mark_broadcast_hourly_report_failed(self, *, report_key: str, error_message: str) -> None:
        self._execute(
            """
            UPDATE broadcast_job_hourly_reports
            SET status = 'failed',
                error_message = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE report_key = %s
            """,
            (error_message[:200], report_key),
        )

    def upsert_broadcast_notification_setting(
        self,
        *,
        channel: str,
        enabled: bool,
        webhook_url: str,
        validation_status: str,
        validated_at: datetime | str | None,
        last_validation_error: str | None,
    ) -> dict[str, Any]:
        return self._execute_returning(
            """
            INSERT INTO broadcast_queue_notification_settings (
                channel, enabled, webhook_url, validation_status, validated_at,
                last_validation_error, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(channel) DO UPDATE SET
                enabled = excluded.enabled,
                webhook_url = excluded.webhook_url,
                validation_status = excluded.validation_status,
                validated_at = excluded.validated_at,
                last_validation_error = excluded.last_validation_error,
                updated_at = CURRENT_TIMESTAMP
            RETURNING channel, enabled, webhook_url, validation_status, validated_at,
                      last_validation_error, created_at, updated_at
            """,
            (channel, enabled, webhook_url, validation_status, validated_at, last_validation_error),
        ) or {
            "channel": channel,
            "enabled": enabled,
            "webhook_url": webhook_url,
            "validation_status": validation_status,
            "validated_at": validated_at,
            "last_validation_error": last_validation_error,
            "created_at": _now_text(),
            "updated_at": _now_text(),
        }

    def insert_audit(self, *, operator: str, action_type: str, target_type: str, target_id: str, before: dict[str, Any], after: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO admin_operation_logs (operator, action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, CURRENT_TIMESTAMP)
            """,
            (operator, action_type, target_type, target_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)),
        )


def _count_row(row: dict[str, Any] | None) -> dict[str, int]:
    return {key: int(value or 0) for key, value in dict(row or {}).items()}


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    statuses = (*BROADCAST_STATUSES, "pending", "running", "success", "conflict", "skipped")
    counts = {status: 0 for status in statuses}
    counts.update({f"{status}_count": 0 for status in statuses})
    counts["total_count"] = 0
    for row in rows:
        status = str(row.get("status") or "")
        value = int(row.get("cnt") or row.get("count") or 0)
        counts[status] = value
        counts[f"{status}_count"] = value
        counts["total_count"] += value
    return counts


class FixtureAdminJobsRepository:
    source_status = "fixture_admin_jobs_repository"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.sync_runs = [
            {"id": 2, "status": "success", "start_time": "2026-04-02 09:00:00", "end_time": "2026-04-02 09:06:00", "owner_userid": "owner-a", "cursor": "", "fetched_count": 30, "inserted_count": 30, "error_message": "", "created_at": "2026-04-02 09:00:00", "finished_at": "2026-04-02 09:06:00"},
            {"id": 1, "status": "failed", "start_time": "2026-04-01 09:00:00", "end_time": "2026-04-01 09:05:00", "owner_userid": "owner-a", "cursor": "", "fetched_count": 12, "inserted_count": 8, "error_message": "sync failed once", "created_at": "2026-04-01 09:00:00", "finished_at": "2026-04-01 09:05:00"},
        ]
        self.callbacks = [
            {"id": 2, "corp_id": "ww-test", "event_type": "change_external_contact", "change_type": "update_by_user", "external_userid": "ext-2", "user_id": "owner-b", "event_time": 1712023260, "event_key": "event-2", "process_status": "failed", "retry_count": 1, "error_message": "callback failed", "created_at": "2026-04-02 10:21:00", "updated_at": "2026-04-02 10:22:00"},
            {"id": 1, "corp_id": "ww-test", "event_type": "change_external_contact", "change_type": "add_external_contact", "external_userid": "ext-1", "user_id": "owner-a", "event_time": 1712023200, "event_key": "event-1", "process_status": "success", "retry_count": 0, "error_message": "", "created_at": "2026-04-02 10:20:00", "updated_at": "2026-04-02 10:20:00"},
        ]
        self.batches = [
            {"id": 2, "batch_key": "batch-2", "window_start": "2026-04-02 10:03:00", "window_end": "2026-04-02 10:05:59", "status": "acked", "message_count": 1, "created_at": "2026-04-02 10:06:00", "acked_at": "2026-04-02 10:10:00", "ack_note": "checked", "acked_by": "tester-old"},
            {"id": 1, "batch_key": "batch-1", "window_start": "2026-04-02 10:00:00", "window_end": "2026-04-02 10:02:59", "status": "pending", "message_count": 1, "created_at": "2026-04-02 10:03:00", "acked_at": "", "ack_note": "", "acked_by": ""},
        ]
        self.messages = {1: [{"send_time": "2026-04-02 10:00:01", "chat_type": "private", "external_userid": "ext-1", "owner_userid": "owner-a", "content": "hello batch"}]}
        self.deferred_jobs = [
            {"id": 1, "job_type": "sync_tags", "external_userid": "ext-1", "owner_userid": "owner-a", "run_after": "2026-04-02 11:00:00", "status": "pending", "attempt_count": 0, "payload_json": {}, "result_json": {}, "created_at": "2026-04-02 10:59:00", "updated_at": "2026-04-02 10:59:00"},
            {"id": 2, "job_type": "sync_tags", "external_userid": "ext-2", "owner_userid": "owner-b", "run_after": "2026-04-02 11:05:00", "status": "failed", "attempt_count": 2, "payload_json": {}, "result_json": {}, "created_at": "2026-04-02 11:04:00", "updated_at": "2026-04-02 11:06:00"},
        ]
        self.webhooks = [
            {"id": 1, "event_type": "openclaw_focus_message", "source_key": "external_userid", "source_id": "ext-1", "target_url": "https://openclaw.local/focus", "payload_json": {"external_userid": "ext-1"}, "payload_summary": '{"external_userid":"ext-1"}', "token_configured": True, "status": "success", "attempt_count": 1, "max_attempts": 3, "response_status_code": 202, "response_body_summary": '{"ok":true}', "last_error": "", "last_attempted_at": "2026-04-02 12:00:00", "next_retry_at": "", "created_at": "2026-04-02 12:00:00", "updated_at": "2026-04-02 12:00:00"},
            {"id": 2, "event_type": "questionnaire_submit", "source_key": "submission_id", "source_id": "sub-2", "target_url": "", "payload_json": {"mobile": "13800138000"}, "payload_summary": '{"mobile":"13800138000"}', "token_configured": False, "status": "failed", "attempt_count": 0, "max_attempts": 3, "response_status_code": None, "response_body_summary": "", "last_error": "webhook_not_configured", "last_attempted_at": "2026-04-02 12:10:00", "next_retry_at": "", "created_at": "2026-04-02 12:10:00", "updated_at": "2026-04-02 12:10:00"},
            {"id": 3, "event_type": "openclaw_focus_message", "source_key": "external_userid", "source_id": "ext-3", "target_url": "https://openclaw.local/focus", "payload_json": {"external_userid": "ext-3"}, "payload_summary": '{"external_userid":"ext-3"}', "token_configured": True, "status": "retry_scheduled", "attempt_count": 1, "max_attempts": 3, "response_status_code": 500, "response_body_summary": "server error", "last_error": "http_status_500", "last_attempted_at": "2026-04-02 12:20:00", "next_retry_at": "2026-04-02 12:25:00", "created_at": "2026-04-02 12:20:00", "updated_at": "2026-04-02 12:20:00"},
            {"id": 4, "event_type": "questionnaire_submit", "source_key": "submission_id", "source_id": "sub-4", "target_url": "https://hooks.local/q", "payload_json": {"mobile": "13800138001"}, "payload_summary": '{"mobile":"13800138001"}', "token_configured": True, "status": "exhausted", "attempt_count": 3, "max_attempts": 3, "response_status_code": 500, "response_body_summary": "server error", "last_error": "http_status_500", "last_attempted_at": "2026-04-02 12:30:00", "next_retry_at": "", "created_at": "2026-04-02 12:30:00", "updated_at": "2026-04-02 12:30:00"},
        ]
        self.broadcast_jobs = [
            {"id": 1, "source_type": "campaign", "source_id": "camp-1", "source_table": "campaigns", "scheduled_for": "2026-04-02 13:00:00", "priority": 100, "batch_key": "batch-a", "status": "waiting_approval", "requires_approval": True, "approved_by": "", "approved_at": "", "cancelled_by": "", "cancelled_at": "", "cancel_reason": "", "target_count": 2, "target_summary": "2 个客户", "content_type": "text", "content_summary": "审批后发送", "attempt_count": 0, "last_error": "", "outbound_task_id": None, "sent_count": 0, "failed_count": 0, "trace_id": "trace-1", "created_by": "fixture", "created_at": "2026-04-02 12:40:00", "updated_at": "2026-04-02 12:40:00", "claimed_at": "", "sent_at": ""},
            {"id": 2, "source_type": "focus_send", "source_id": "focus-2", "source_table": "focus_tasks", "scheduled_for": "2026-04-02 13:05:00", "priority": 90, "batch_key": "batch-b", "status": "queued", "requires_approval": False, "approved_by": "", "approved_at": "", "cancelled_by": "", "cancelled_at": "", "cancel_reason": "", "target_count": 1, "target_summary": "1 个客户", "content_type": "text", "content_summary": "排队中内容", "attempt_count": 0, "last_error": "", "outbound_task_id": 9, "sent_count": 0, "failed_count": 0, "trace_id": "trace-2", "created_by": "fixture", "created_at": "2026-04-02 12:41:00", "updated_at": "2026-04-02 12:41:00", "claimed_at": "", "sent_at": ""},
            {"id": 3, "source_type": "manual", "source_id": "manual-3", "source_table": "manual_sends", "scheduled_for": "2026-04-02 13:10:00", "priority": 80, "batch_key": "batch-c", "status": "sent", "requires_approval": False, "approved_by": "", "approved_at": "", "cancelled_by": "", "cancelled_at": "", "cancel_reason": "", "target_count": 3, "target_summary": "3 个客户", "content_type": "text", "content_summary": "已发送内容", "attempt_count": 1, "last_error": "", "outbound_task_id": 10, "sent_count": 3, "failed_count": 0, "trace_id": "trace-3", "created_by": "fixture", "created_at": "2026-04-02 12:42:00", "updated_at": "2026-04-02 13:11:00", "claimed_at": "2026-04-02 13:10:00", "sent_at": "2026-04-02 13:11:00"},
        ]
        for row in self.broadcast_jobs:
            source_type = str(row.get("source_type") or "")
            row.setdefault("business_domain", "manual" if source_type == "manual" else "automation_ops")
            row.setdefault("channel", "manual" if source_type == "manual" else "wecom_private")
            row.setdefault("target_kind", "unknown" if source_type == "manual" else "external_userid")
            row.setdefault("failure_type", "")
            row.setdefault("has_idempotency_key", source_type != "manual")
        self.broadcast_notification_settings: dict[str, dict[str, Any]] = {}
        self.broadcast_hourly_reports: dict[str, dict[str, Any]] = {}
        self.audit_logs: list[dict[str, Any]] = []

    def _filtered(self, rows: list[dict[str, Any]], *, limit: int, **filters: Any) -> list[dict[str, Any]]:
        result = list(rows)
        for key, value in filters.items():
            if value:
                result = [row for row in result if str(row.get(key) or "") == str(value)]
        return copy.deepcopy(result[:limit])

    def list_sync_runs(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return self._filtered(self.sync_runs, status=status, limit=limit)

    def sync_run_counts(self) -> dict[str, int]:
        return _simple_counts(self.sync_runs, "status", total_name="total_count")

    def list_callback_logs(self, *, process_status: str = "", query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        rows = self._filtered(self.callbacks, process_status=process_status, limit=200)
        if query:
            q = query.lower()
            rows = [row for row in rows if q in json.dumps(row, ensure_ascii=False).lower()]
        return rows[:limit]

    def callback_counts(self) -> dict[str, int]:
        return _simple_counts(self.callbacks, "process_status", total_name="total_count")

    def list_message_batches(self, *, status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return self._filtered(self.batches, status=status, limit=limit)

    def message_batch_counts(self) -> dict[str, int]:
        return _simple_counts(self.batches, "status", total_name="total_count")

    def get_message_batch(self, batch_id: int) -> dict[str, Any] | None:
        return copy.deepcopy(next((row for row in self.batches if int(row["id"]) == int(batch_id)), None))

    def list_batch_messages(self, batch_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        return copy.deepcopy((self.messages.get(int(batch_id)) or [])[:limit])

    def count_batch_messages(self, batch_id: int) -> int:
        return len(self.messages.get(int(batch_id)) or [])

    def ack_message_batch(self, batch_id: int, *, ack_note: str, acked_by: str) -> dict[str, Any] | None:
        for row in self.batches:
            if int(row["id"]) == int(batch_id):
                row.update({"status": "acked", "ack_note": ack_note, "acked_by": acked_by, "acked_at": _now_text()})
                return copy.deepcopy(row)
        return None

    def list_deferred_jobs(self, *, status: str = "", owner_userid: str = "", external_userid: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return self._filtered(self.deferred_jobs, status=status, owner_userid=owner_userid, external_userid=external_userid, limit=limit)

    def deferred_job_counts(self) -> dict[str, int]:
        return _simple_counts(self.deferred_jobs, "status", total_name="total_count")

    def webhook_counts(self) -> dict[str, int]:
        return _simple_counts(self.webhooks, "status", total_name="total_count")

    def list_webhook_deliveries(self, *, event_type: str = "", status: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return self._filtered(self.webhooks, event_type=event_type, status=status, limit=limit)

    def get_webhook_delivery(self, delivery_id: int) -> dict[str, Any] | None:
        return copy.deepcopy(next((row for row in self.webhooks if int(row["id"]) == int(delivery_id)), None))

    def update_webhook_delivery(self, delivery_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
        for row in self.webhooks:
            if int(row["id"]) == int(delivery_id):
                row.update(updates)
                row["updated_at"] = _now_text()
                return copy.deepcopy(row)
        return None

    def due_webhook_deliveries(self, *, limit: int) -> list[dict[str, Any]]:
        return [copy.deepcopy(row) for row in self.webhooks if row["status"] == "retry_scheduled"][:limit]

    def list_broadcast_jobs(self, *, statuses: list[str] | None = None, source_types: list[str] | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.broadcast_jobs
        if statuses:
            rows = [row for row in rows if row["status"] in statuses]
        if source_types:
            rows = [row for row in rows if row["source_type"] in source_types]
        return copy.deepcopy(rows[offset : offset + limit])

    def broadcast_total(
        self,
        *,
        statuses: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> int:
        rows = self.broadcast_jobs
        if statuses:
            rows = [row for row in rows if row["status"] in statuses]
        if source_types:
            rows = [row for row in rows if row["source_type"] in source_types]
        return len(rows)

    def broadcast_counts(
        self,
        *,
        statuses: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> dict[str, int]:
        rows = self.broadcast_jobs
        if statuses:
            rows = [row for row in rows if row["status"] in statuses]
        if source_types:
            rows = [row for row in rows if row["source_type"] in source_types]
        grouped: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "")
            grouped[status] = grouped.get(status, 0) + 1
        return _status_counts(
            [{"status": status, "cnt": count} for status, count in grouped.items()]
        )

    def broadcast_hourly_summary(self, *, window_start: datetime, window_end: datetime) -> dict[str, int]:
        rows = []
        start_utc = _as_utc(window_start)
        end_utc = _as_utc(window_end)
        for row in self.broadcast_jobs:
            scheduled = _as_utc(_parse_datetime(row.get("scheduled_for"), default_tz=window_start.tzinfo))
            if start_utc <= scheduled < end_utc:
                rows.append(row)
        return {
            "total_jobs": len(rows),
            "success_jobs": sum(1 for row in rows if row.get("status") == "sent"),
            "failed_jobs": sum(1 for row in rows if row.get("status") == "failed"),
            "pending_jobs": sum(1 for row in rows if row.get("status") in {"queued", "claimed", "waiting_approval"}),
            "cancelled_jobs": sum(1 for row in rows if row.get("status") == "cancelled"),
        }

    def get_broadcast_job(self, job_id: int) -> dict[str, Any] | None:
        return copy.deepcopy(next((row for row in self.broadcast_jobs if int(row["id"]) == int(job_id)), None))

    def approve_broadcast_job(self, job_id: int, *, approved_by: str) -> dict[str, Any] | None:
        for row in self.broadcast_jobs:
            if int(row["id"]) == int(job_id) and row["status"] == "waiting_approval":
                row.update({"status": "queued", "approved_by": approved_by, "approved_at": _now_text(), "updated_at": _now_text()})
                return copy.deepcopy(row)
        return None

    def cancel_broadcast_job(self, job_id: int, *, cancelled_by: str, reason: str) -> dict[str, Any] | None:
        for row in self.broadcast_jobs:
            if int(row["id"]) == int(job_id) and row["status"] in {"queued", "waiting_approval"}:
                row.update({"status": "cancelled", "cancelled_by": cancelled_by, "cancelled_at": _now_text(), "cancel_reason": reason, "updated_at": _now_text()})
                return copy.deepcopy(row)
        return None

    def get_broadcast_notification_setting(self, channel: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.broadcast_notification_settings.get(str(channel)))

    def create_broadcast_hourly_report_pending(self, *, report_key: str, window_start: datetime, window_end: datetime, channel: str) -> str:
        if report_key in self.broadcast_hourly_reports:
            return "duplicate"
        self.broadcast_hourly_reports[report_key] = {
            "report_key": report_key,
            "window_start": window_start,
            "window_end": window_end,
            "channel": channel,
            "status": "pending",
            "payload_json": {},
            "error_message": None,
            "sent_at": None,
            "created_at": _now_text(),
            "updated_at": _now_text(),
        }
        return "created"

    def mark_broadcast_hourly_report_sent(self, *, report_key: str, payload_json: dict[str, Any]) -> None:
        row = self.broadcast_hourly_reports.get(report_key)
        if not row:
            return
        row.update({"status": "sent", "payload_json": copy.deepcopy(payload_json), "error_message": None, "sent_at": _now_text(), "updated_at": _now_text()})

    def mark_broadcast_hourly_report_failed(self, *, report_key: str, error_message: str) -> None:
        row = self.broadcast_hourly_reports.get(report_key)
        if not row:
            return
        row.update({"status": "failed", "error_message": str(error_message or "")[:200], "updated_at": _now_text()})

    def upsert_broadcast_notification_setting(
        self,
        *,
        channel: str,
        enabled: bool,
        webhook_url: str,
        validation_status: str,
        validated_at: datetime | str | None,
        last_validation_error: str | None,
    ) -> dict[str, Any]:
        existing = self.broadcast_notification_settings.get(channel) or {"created_at": _now_text()}
        row = {
            **existing,
            "channel": channel,
            "enabled": bool(enabled),
            "webhook_url": webhook_url,
            "validation_status": validation_status,
            "validated_at": validated_at,
            "last_validation_error": last_validation_error,
            "updated_at": _now_text(),
        }
        self.broadcast_notification_settings[channel] = row
        return copy.deepcopy(row)

    def insert_audit(self, *, operator: str, action_type: str, target_type: str, target_id: str, before: dict[str, Any], after: dict[str, Any]) -> None:
        self.audit_logs.append({"operator": operator, "action_type": action_type, "target_type": target_type, "target_id": target_id, "before": before, "after": after, "created_at": _now_text()})


def _simple_counts(rows: list[dict[str, Any]], key: str, *, total_name: str) -> dict[str, int]:
    counts: dict[str, int] = {total_name: len(rows)}
    for row in rows:
        value = str(row.get(key) or "")
        counts[f"{value}_count"] = counts.get(f"{value}_count", 0) + 1
    for name in (
        "success_count",
        "failed_count",
        "pending_count",
        "processing_count",
        "acked_count",
        "running_count",
        "conflict_count",
        "skipped_count",
        "retry_scheduled_count",
        "exhausted_count",
        "waiting_approval_count",
        "queued_count",
        "claimed_count",
        "sent_count",
        "cancelled_count",
    ):
        counts.setdefault(name, 0)
    return counts


def _parse_datetime(value: Any, *, default_tz: Any = timezone.utc) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            parsed = datetime.now(timezone.utc)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz or timezone.utc)
    return parsed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_FIXTURE_REPO = FixtureAdminJobsRepository()


def reset_admin_jobs_fixture_state() -> None:
    if not production_data_ready():
        _FIXTURE_REPO.reset()


def build_admin_jobs_repository() -> AdminJobsRepository:
    if production_data_ready():
        return PostgresAdminJobsRepository()
    return _FIXTURE_REPO


def clean_broadcast_filters(statuses: list[str] | None, source_types: list[str] | None) -> tuple[list[str] | None, list[str] | None]:
    clean_statuses = [item for item in (statuses or []) if item in BROADCAST_STATUSES]
    clean_sources = [item for item in (source_types or []) if item in BROADCAST_SOURCE_TYPES]
    return clean_statuses or None, clean_sources or None
