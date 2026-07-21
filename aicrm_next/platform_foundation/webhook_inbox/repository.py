from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from aicrm_next.shared.runtime import raw_database_url

PENDING_FAILED_STATUSES = ("received", "processing", "failed_retryable", "failed_terminal", "dead_letter")
WEBHOOK_PRIORITY_MAX_WAIT_SECONDS = 5


def _text(value: Any) -> str:
    return str(value or "").strip()


def _csv_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    return [item.strip() for item in _text(value).split(",") if item.strip()]


def _status_values(filters: dict[str, Any] | None) -> list[str]:
    filters = filters or {}
    if _text(filters.get("status")) == "pending_failed" or _text(filters.get("status_group")) == "pending_failed":
        return list(PENDING_FAILED_STATUSES)
    statuses = _csv_values(filters.get("statuses"))
    if statuses:
        return statuses
    status = _text(filters.get("status"))
    return [status] if status else []


def _filter_datetime(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.astimezone(timezone.utc)


def _is_time_sensitive_callback(row: dict[str, Any]) -> bool:
    summary = row.get("payload_summary_json") or {}
    return bool(isinstance(summary, dict) and summary.get("welcome_code_present") is True and summary.get("state_present") is True)


def _due_priority(row: dict[str, Any], *, now: datetime) -> tuple[int, datetime, int]:
    received_at = row.get("received_at")
    if not isinstance(received_at, datetime):
        received_at = now
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    aged = received_at <= now - timedelta(seconds=WEBHOOK_PRIORITY_MAX_WAIT_SECONDS)
    if aged:
        priority = 0
    elif _is_time_sensitive_callback(row):
        priority = 1
    else:
        priority = 2
    return priority, received_at, int(row.get("id") or 0)


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(json_safe(value if value is not None else {}), ensure_ascii=False)


def _connect(database_url: str | None = None):
    url = _psycopg_url(_text(database_url or raw_database_url()))
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL is required for webhook inbox repository")
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(url, row_factory=dict_row)


def _json(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(json_safe(value if value is not None else {}), dumps=_json_dumps)


class WebhookInboxRepository(Protocol):
    def ingest(self, **kwargs: Any) -> dict[str, Any]: ...

    def upsert_received(
        self,
        *,
        provider: str,
        event_family: str,
        route: str,
        method: str,
        tenant_id: str,
        corp_id: str,
        event_type: str,
        change_type: str,
        external_event_id: str,
        idempotency_key: str,
        raw_query_json: dict[str, Any],
        raw_headers_json: dict[str, Any],
        raw_body: bytes,
        payload_xml: str,
        payload_json: dict[str, Any],
        payload_summary_json: dict[str, Any],
        max_attempts: int = 8,
    ) -> dict[str, Any]: ...

    def preview_due(self, *, provider: str, limit: int = 50) -> list[dict[str, Any]]: ...

    def list_items(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]: ...

    def get_item(self, inbox_id: int) -> dict[str, Any] | None: ...

    def claim_due(self, *, provider: str, limit: int = 50, locked_by: str = "webhook-inbox-worker") -> list[dict[str, Any]]: ...

    def claim_one(self, inbox_id: int, *, locked_by: str = "webhook-inbox-worker") -> dict[str, Any] | None: ...

    def acquire_due(self, *, provider: str, limit: int = 50, locked_by: str = "webhook-inbox-worker") -> list[dict[str, Any]]: ...

    def mark_succeeded(
        self,
        inbox_id: int,
        *,
        processing_summary_json: dict[str, Any] | None = None,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> dict[str, Any] | None: ...

    def mark_failed(
        self,
        inbox_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        next_retry_at: datetime | None = None,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> dict[str, Any] | None: ...

    def mark_failed_retryable(self, inbox_id: int, *, error_code: str, error_message: str, next_retry_at: datetime | None = None) -> dict[str, Any] | None: ...

    def mark_failed_terminal(self, inbox_id: int, *, error_code: str, error_message: str) -> dict[str, Any] | None: ...

    def mark_dead_letter(self, inbox_id: int, *, error_code: str = "", error_message: str = "") -> dict[str, Any] | None: ...

    def mark_retryable_now(self, inbox_id: int, *, reason: str = "") -> dict[str, Any] | None: ...

    def mark_ignored(self, inbox_id: int, *, reason: str = "") -> dict[str, Any] | None: ...

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]: ...


class PostgresWebhookInboxRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url

    def ingest(self, **kwargs: Any) -> dict[str, Any]:
        return self.upsert_received(**kwargs)

    def upsert_received(
        self,
        *,
        provider: str,
        event_family: str,
        route: str,
        method: str,
        tenant_id: str,
        corp_id: str,
        event_type: str,
        change_type: str,
        external_event_id: str,
        idempotency_key: str,
        raw_query_json: dict[str, Any],
        raw_headers_json: dict[str, Any],
        raw_body: bytes,
        payload_xml: str,
        payload_json: dict[str, Any],
        payload_summary_json: dict[str, Any],
        max_attempts: int = 8,
    ) -> dict[str, Any]:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO webhook_inbox (
                    provider, event_family, route, method, tenant_id, corp_id,
                    event_type, change_type, external_event_id, idempotency_key,
                    execution_id, parent_execution_id, lane, available_at,
                    ordering_key, fairness_key, policy_version,
                    raw_query_json, raw_headers_json, raw_body, payload_xml,
                    payload_json, payload_summary_json, processing_summary_json, status, max_attempts,
                    received_at, last_seen_at, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, '', 'webhook_inbox', CURRENT_TIMESTAMP, %s, %s,
                    (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
                    %s, %s, %s, %s,
                    %s, %s, '{}'::jsonb, 'received', %s,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (tenant_id, provider, idempotency_key) DO UPDATE
                SET last_seen_at = CURRENT_TIMESTAMP,
                    duplicate_count = webhook_inbox.duplicate_count + 1,
                    raw_query_json = EXCLUDED.raw_query_json,
                    raw_headers_json = EXCLUDED.raw_headers_json,
                    raw_body = EXCLUDED.raw_body,
                    payload_xml = EXCLUDED.payload_xml,
                    payload_json = EXCLUDED.payload_json,
                    payload_summary_json = EXCLUDED.payload_summary_json,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (
                    _text(provider),
                    _text(event_family),
                    _text(route),
                    _text(method) or "POST",
                    _text(tenant_id) or "aicrm",
                    _text(corp_id),
                    _text(event_type),
                    _text(change_type),
                    _text(external_event_id),
                    _text(idempotency_key),
                    "exe_" + uuid4().hex,
                    ":".join(
                        (
                            _text(provider),
                            _text(corp_id) or _text(external_event_id) or _text(idempotency_key),
                        )
                    ),
                    f"{_text(provider)}:{_text(event_family)}",
                    _json(raw_query_json),
                    _json(raw_headers_json),
                    bytes(raw_body or b""),
                    _text(payload_xml),
                    _json(payload_json),
                    _json(payload_summary_json),
                    max(1, int(max_attempts or 8)),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else {}

    def preview_due(self, *, provider: str, limit: int = 50) -> list[dict[str, Any]]:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT webhook_inbox.*, xmin::text AS row_version
                FROM webhook_inbox
                WHERE provider = %s
                  AND hold_reason = ''
                  AND (
                    (
                      status IN ('received', 'failed_retryable')
                      AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                      AND (locked_at IS NULL OR locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes')
                    )
                    OR (
                      status = 'processing'
                      AND locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                    )
                  )
                ORDER BY
                  CASE
                    WHEN received_at <= CURRENT_TIMESTAMP - INTERVAL '5 seconds' THEN 0
                    WHEN payload_summary_json->>'welcome_code_present' = 'true'
                     AND payload_summary_json->>'state_present' = 'true' THEN 1
                    ELSE 2
                  END ASC,
                  received_at ASC,
                  id ASC
                LIMIT %s
                """,
                (_text(provider), max(1, min(int(limit or 50), 500))),
            )
            return [dict(row) for row in cur.fetchall() or []]

    def list_items(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column in ("tenant_id", "provider", "event_family", "route", "event_type", "change_type"):
            value = _text((filters or {}).get(column))
            if value:
                clauses.append(f"{column} = %s")
                params.append(value)
        status_values = _status_values(filters)
        if status_values:
            placeholders = ", ".join(["%s"] * len(status_values))
            clauses.append(f"status IN ({placeholders})")
            params.extend(status_values)
        received_from = _filter_datetime((filters or {}).get("received_from"))
        if received_from:
            clauses.append("received_at >= %s")
            params.append(received_from)
        received_to = _filter_datetime((filters or {}).get("received_to"))
        if received_to:
            clauses.append("received_at <= %s")
            params.append(received_to)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT webhook_inbox.*, xmin::text AS row_version
                FROM webhook_inbox
                {where_sql}
                ORDER BY received_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (*params, max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))),
            )
            return [dict(row) for row in cur.fetchall() or []]

    def get_item(self, inbox_id: int) -> dict[str, Any] | None:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT webhook_inbox.*, xmin::text AS row_version
                FROM webhook_inbox
                WHERE id = %s
                LIMIT 1
                """,
                (int(inbox_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def claim_due(self, *, provider: str, limit: int = 50, locked_by: str = "webhook-inbox-worker") -> list[dict[str, Any]]:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                WITH due AS (
                    SELECT id
                    FROM webhook_inbox
                    WHERE provider = %s
                      AND hold_reason = ''
                      AND (
                        (
                          status IN ('received', 'failed_retryable')
                          AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                          AND (locked_at IS NULL OR locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes')
                        )
                        OR (
                          status = 'processing'
                          AND locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                        )
                      )
                    ORDER BY
                      CASE
                        WHEN received_at <= CURRENT_TIMESTAMP - INTERVAL '5 seconds' THEN 0
                        WHEN payload_summary_json->>'welcome_code_present' = 'true'
                         AND payload_summary_json->>'state_present' = 'true' THEN 1
                        ELSE 2
                      END ASC,
                      received_at ASC,
                      id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE webhook_inbox i
                SET status = 'processing',
                    locked_at = CURRENT_TIMESTAMP,
                    locked_by = %s,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                FROM due
                WHERE i.id = due.id
                RETURNING i.*
                """,
                (_text(provider), max(1, min(int(limit or 50), 500)), _text(locked_by) or "webhook-inbox-worker"),
            )
            rows = [dict(row) for row in cur.fetchall() or []]
            conn.commit()
            return rows

    def claim_one(self, inbox_id: int, *, locked_by: str = "webhook-inbox-worker") -> dict[str, Any] | None:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE webhook_inbox
                SET status = 'processing',
                    locked_at = CURRENT_TIMESTAMP,
                    locked_by = %s,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND hold_reason = ''
                  AND status IN ('received', 'failed_retryable')
                  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                  AND (locked_at IS NULL OR locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes')
                RETURNING *
                """,
                (_text(locked_by) or "webhook-inbox-worker", int(inbox_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def acquire_due(self, *, provider: str, limit: int = 50, locked_by: str = "webhook-inbox-worker") -> list[dict[str, Any]]:
        return self.claim_due(provider=provider, limit=limit, locked_by=locked_by)

    def mark_succeeded(
        self,
        inbox_id: int,
        *,
        processing_summary_json: dict[str, Any] | None = None,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> dict[str, Any] | None:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE webhook_inbox
                SET status = 'succeeded',
                    processing_summary_json = CASE
                        WHEN %s THEN %s
                        ELSE processing_summary_json
                    END,
                    locked_at = NULL,
                    locked_by = '',
                    lease_token = '',
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    last_error_code = '',
                    last_error_message = '',
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND (%s = 0 OR status = 'processing')
                  AND (%s = '' OR lease_token = %s)
                  AND (
                      %s = 0
                      OR (worker_generation = %s AND lease_expires_at > CURRENT_TIMESTAMP)
                  )
                RETURNING *
                """,
                (
                    processing_summary_json is not None,
                    _json(processing_summary_json or {}),
                    int(inbox_id),
                    int(expected_generation),
                    _text(expected_lease_token),
                    _text(expected_lease_token),
                    int(expected_generation),
                    int(expected_generation),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def mark_failed(
        self,
        inbox_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        next_retry_at: datetime | None = None,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> dict[str, Any] | None:
        status_sql = """
            CASE
                WHEN NOT %s THEN 'failed_terminal'
                WHEN attempt_count + 1 >= max_attempts THEN 'dead_letter'
                ELSE 'failed_retryable'
            END
        """
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE webhook_inbox
                SET status = {status_sql},
                    attempt_count = attempt_count + 1,
                    next_retry_at = CASE
                        WHEN %s AND attempt_count + 1 < max_attempts THEN %s
                        ELSE NULL
                    END,
                    available_at = CASE
                        WHEN %s AND attempt_count + 1 < max_attempts THEN %s
                        ELSE available_at
                    END,
                    locked_at = NULL,
                    locked_by = '',
                    lease_token = '',
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    worker_generation = CASE
                        WHEN %s AND attempt_count + 1 < max_attempts THEN 0
                        ELSE worker_generation
                    END,
                    last_error_code = %s,
                    last_error_message = %s,
                    finished_at = CASE
                        WHEN (NOT %s) OR attempt_count + 1 >= max_attempts THEN CURRENT_TIMESTAMP
                        ELSE finished_at
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND (%s = 0 OR status = 'processing')
                  AND (%s = '' OR lease_token = %s)
                  AND (
                      %s = 0
                      OR (worker_generation = %s AND lease_expires_at > CURRENT_TIMESTAMP)
                  )
                RETURNING *
                """,
                (
                    bool(retryable),
                    bool(retryable),
                    next_retry_at,
                    bool(retryable),
                    next_retry_at,
                    bool(retryable),
                    _text(error_code)[:120],
                    _text(error_message)[:2000],
                    bool(retryable),
                    int(inbox_id),
                    int(expected_generation),
                    _text(expected_lease_token),
                    _text(expected_lease_token),
                    int(expected_generation),
                    int(expected_generation),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def mark_failed_retryable(self, inbox_id: int, *, error_code: str, error_message: str, next_retry_at: datetime | None = None) -> dict[str, Any] | None:
        return self.mark_failed(
            inbox_id,
            error_code=error_code,
            error_message=error_message,
            retryable=True,
            next_retry_at=next_retry_at,
        )

    def mark_failed_terminal(self, inbox_id: int, *, error_code: str, error_message: str) -> dict[str, Any] | None:
        return self.mark_failed(
            inbox_id,
            error_code=error_code,
            error_message=error_message,
            retryable=False,
            next_retry_at=None,
        )

    def mark_dead_letter(self, inbox_id: int, *, error_code: str = "", error_message: str = "") -> dict[str, Any] | None:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE webhook_inbox
                SET status = 'dead_letter',
                    next_retry_at = NULL,
                    locked_at = NULL,
                    locked_by = '',
                    last_error_code = %s,
                    last_error_message = %s,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """,
                (_text(error_code)[:120], _text(error_message)[:2000], int(inbox_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def mark_retryable_now(self, inbox_id: int, *, reason: str = "") -> dict[str, Any] | None:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE webhook_inbox
                SET status = 'failed_retryable',
                    next_retry_at = CURRENT_TIMESTAMP,
                    available_at = CURRENT_TIMESTAMP,
                    locked_at = NULL,
                    locked_by = '',
                    lease_token = '',
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    worker_generation = 0,
                    last_error_code = 'operator_retry',
                    last_error_message = %s,
                    finished_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status IN ('failed_retryable', 'failed_terminal', 'dead_letter', 'processing')
                RETURNING *
                """,
                (_text(reason)[:2000], int(inbox_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def mark_ignored(self, inbox_id: int, *, reason: str = "") -> dict[str, Any] | None:
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE webhook_inbox
                SET status = 'ignored',
                    next_retry_at = NULL,
                    locked_at = NULL,
                    locked_by = '',
                    last_error_code = 'operator_skip',
                    last_error_message = %s,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """,
                (_text(reason)[:2000], int(inbox_id)),
            )
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        for column in ("tenant_id", "provider", "event_family", "route"):
            value = _text((filters or {}).get(column))
            if value:
                clauses.append(f"{column} = %s")
                params.append(value)
        status_values = _status_values(filters)
        if status_values:
            placeholders = ", ".join(["%s"] * len(status_values))
            clauses.append(f"status IN ({placeholders})")
            params.extend(status_values)
        received_from = _filter_datetime((filters or {}).get("received_from"))
        if received_from:
            clauses.append("received_at >= %s")
            params.append(received_from)
        received_to = _filter_datetime((filters or {}).get("received_to"))
        if received_to:
            clauses.append("received_at <= %s")
            params.append(received_to)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with _connect(self._database_url) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (
                        WHERE status IN ('received', 'processing', 'failed_retryable')
                    ) AS raw_open_count,
                    COUNT(*) FILTER (
                        WHERE hold_reason <> '' AND status IN ('received', 'processing', 'failed_retryable')
                    ) AS held_count,
                    COUNT(*) FILTER (
                        WHERE (
                          hold_reason = ''
                          AND
                          (
                            (
                              status IN ('received', 'failed_retryable')
                              AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                              AND (locked_at IS NULL OR locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes')
                            )
                            OR (
                              status = 'processing'
                              AND locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                            )
                          )
                        )
                    ) AS due_count,
                    0::BIGINT AS scheduled_count,
                    COUNT(*) FILTER (
                        WHERE hold_reason = '' AND status = 'failed_retryable'
                          AND next_retry_at > CURRENT_TIMESTAMP
                    ) AS retry_wait_count,
                    0::BIGINT AS rate_limited_count,
                    COUNT(*) FILTER (
                        WHERE hold_reason = '' AND status = 'processing'
                          AND locked_at > CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                    ) AS in_flight_count,
                    0::BIGINT AS unknown_count,
                    COUNT(*) FILTER (WHERE status = 'processing') AS processing_count,
                    COUNT(*) FILTER (WHERE status = 'failed_retryable') AS failed_retryable_count,
                    COUNT(*) FILTER (WHERE status = 'dead_letter') AS dead_letter_count,
                    EXTRACT(EPOCH FROM (
                        CURRENT_TIMESTAMP - MIN(received_at) FILTER (
                            WHERE status IN ('received', 'failed_retryable', 'processing')
                        )
                    )) AS oldest_received_age_seconds
                FROM webhook_inbox
                {where_sql}
                """,
                tuple(params),
            )
            row = dict(cur.fetchone() or {})
            cur.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM webhook_inbox
                {where_sql}
                GROUP BY status
                """,
                tuple(params),
            )
            status_counts = {str(item.get("status") or ""): int(item.get("count") or 0) for item in cur.fetchall() or []}
            cur.execute(
                f"""
                SELECT provider, COUNT(*) AS count
                FROM webhook_inbox
                {where_sql}
                GROUP BY provider
                ORDER BY count DESC, provider ASC
                LIMIT 10
                """,
                tuple(params),
            )
            provider_distribution = [{"provider": str(item.get("provider") or ""), "count": int(item.get("count") or 0)} for item in cur.fetchall() or []]
            cur.execute(
                f"""
                SELECT route, COUNT(*) AS count
                FROM webhook_inbox
                {where_sql}
                GROUP BY route
                ORDER BY count DESC, route ASC
                LIMIT 10
                """,
                tuple(params),
            )
            route_distribution = [{"route": str(item.get("route") or ""), "count": int(item.get("count") or 0)} for item in cur.fetchall() or []]
            cur.execute(
                f"""
                SELECT
                    status,
                    last_error_code,
                    last_error_message,
                    COUNT(*) AS count,
                    MAX(updated_at) AS last_seen_at
                FROM webhook_inbox
                {where_sql}
                {"AND" if where_sql else "WHERE"} last_error_code <> ''
                GROUP BY status, last_error_code, last_error_message
                ORDER BY last_seen_at DESC, count DESC
                LIMIT 10
                """,
                tuple(params),
            )
            recent_errors = [
                {
                    "status": str(item.get("status") or ""),
                    "error_code": str(item.get("last_error_code") or ""),
                    "error_message": str(item.get("last_error_message") or ""),
                    "count": int(item.get("count") or 0),
                    "last_seen_at": item.get("last_seen_at"),
                }
                for item in cur.fetchall() or []
            ]
        oldest_age = row.get("oldest_received_age_seconds")
        return {
            "raw_open_count": int(row.get("raw_open_count") or 0),
            "held_count": int(row.get("held_count") or 0),
            "due_count": int(row.get("due_count") or 0),
            "eligible_due_count": int(row.get("due_count") or 0),
            "scheduled_count": int(row.get("scheduled_count") or 0),
            "retry_wait_count": int(row.get("retry_wait_count") or 0),
            "rate_limited_count": int(row.get("rate_limited_count") or 0),
            "in_flight_count": int(row.get("in_flight_count") or 0),
            "unknown_count": int(row.get("unknown_count") or 0),
            "dlq_count": int(row.get("dead_letter_count") or 0),
            "processing_count": int(row.get("processing_count") or 0),
            "failed_retryable_count": int(row.get("failed_retryable_count") or 0),
            "dead_letter_count": int(row.get("dead_letter_count") or 0),
            "oldest_received_age_seconds": int(float(oldest_age or 0)),
            "status_counts": status_counts,
            "provider_distribution": provider_distribution,
            "route_distribution": route_distribution,
            "recent_errors": recent_errors,
        }


class InMemoryWebhookInboxRepository:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._next_id = 1

    def ingest(self, **kwargs: Any) -> dict[str, Any]:
        return self.upsert_received(**kwargs)

    def upsert_received(self, **kwargs: Any) -> dict[str, Any]:
        tenant = _text(kwargs.get("tenant_id")) or "aicrm"
        provider = _text(kwargs.get("provider"))
        key = _text(kwargs.get("idempotency_key"))
        now = datetime.now(timezone.utc)
        for row in self.rows:
            if row["tenant_id"] == tenant and row["provider"] == provider and row["idempotency_key"] == key:
                row.update(
                    {
                        "last_seen_at": now,
                        "duplicate_count": int(row.get("duplicate_count") or 0) + 1,
                        "raw_query_json": deepcopy(kwargs.get("raw_query_json") or {}),
                        "raw_headers_json": deepcopy(kwargs.get("raw_headers_json") or {}),
                        "raw_body": bytes(kwargs.get("raw_body") or b""),
                        "payload_xml": _text(kwargs.get("payload_xml")),
                        "payload_json": deepcopy(kwargs.get("payload_json") or {}),
                        "payload_summary_json": deepcopy(kwargs.get("payload_summary_json") or {}),
                        "updated_at": now,
                    }
                )
                return deepcopy(row)
        row = {
            "id": self._next_id,
            "provider": provider,
            "event_family": _text(kwargs.get("event_family")),
            "route": _text(kwargs.get("route")),
            "method": _text(kwargs.get("method")) or "POST",
            "tenant_id": tenant,
            "corp_id": _text(kwargs.get("corp_id")),
            "event_type": _text(kwargs.get("event_type")),
            "change_type": _text(kwargs.get("change_type")),
            "external_event_id": _text(kwargs.get("external_event_id")),
            "idempotency_key": key,
            "execution_id": "exe_" + uuid4().hex,
            "parent_execution_id": "",
            "lane": "webhook_inbox",
            "available_at": now,
            "ordering_key": ":".join(
                (
                    provider,
                    _text(kwargs.get("corp_id")) or _text(kwargs.get("external_event_id")) or key,
                )
            ),
            "fairness_key": f"{provider}:{_text(kwargs.get('event_family'))}",
            "raw_query_json": deepcopy(kwargs.get("raw_query_json") or {}),
            "raw_headers_json": deepcopy(kwargs.get("raw_headers_json") or {}),
            "raw_body": bytes(kwargs.get("raw_body") or b""),
            "payload_xml": _text(kwargs.get("payload_xml")),
            "payload_json": deepcopy(kwargs.get("payload_json") or {}),
            "payload_summary_json": deepcopy(kwargs.get("payload_summary_json") or {}),
            "processing_summary_json": {},
            "status": "received",
            "hold_reason": "",
            "hold_at": None,
            "attempt_count": 0,
            "max_attempts": max(1, int(kwargs.get("max_attempts") or 8)),
            "next_retry_at": None,
            "locked_at": None,
            "locked_by": "",
            "lease_token": "",
            "lease_expires_at": None,
            "heartbeat_at": None,
            "worker_generation": 0,
            "policy_version": "queue-v1",
            "row_version": "1",
            "last_error_code": "",
            "last_error_message": "",
            "received_at": now,
            "last_seen_at": now,
            "started_at": None,
            "finished_at": None,
            "created_at": now,
            "updated_at": now,
            "duplicate_count": 0,
        }
        self._next_id += 1
        self.rows.append(row)
        return deepcopy(row)

    def preview_due(self, *, provider: str, limit: int = 50) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)

        def is_due(row: dict[str, Any]) -> bool:
            if _text(row.get("hold_reason")):
                return False
            status = row.get("status")
            locked_expired = bool(row.get("locked_at") and row["locked_at"] <= now - timedelta(minutes=5))
            if status == "processing":
                return locked_expired
            return (
                status in {"received", "failed_retryable"}
                and (not row.get("next_retry_at") or row["next_retry_at"] <= now)
                and (not row.get("locked_at") or locked_expired)
            )

        due = [row for row in self.rows if row.get("provider") == provider and is_due(row)]
        due.sort(key=lambda item: _due_priority(item, now=now))
        return [deepcopy(row) for row in due[: max(1, int(limit or 50))]]

    def list_items(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        status_values = set(_status_values(filters))
        received_from = _filter_datetime((filters or {}).get("received_from"))
        received_to = _filter_datetime((filters or {}).get("received_to"))

        def matches(row: dict[str, Any]) -> bool:
            for column in ("tenant_id", "provider", "event_family", "route", "event_type", "change_type"):
                expected = _text((filters or {}).get(column))
                if expected and _text(row.get(column)) != expected:
                    return False
            if status_values and _text(row.get("status")) not in status_values:
                return False
            received_at = row.get("received_at")
            if received_from and (not isinstance(received_at, datetime) or received_at < received_from):
                return False
            if received_to and (not isinstance(received_at, datetime) or received_at > received_to):
                return False
            return True

        rows = [row for row in self.rows if matches(row)]
        rows.sort(key=lambda item: (item.get("received_at") or datetime.min.replace(tzinfo=timezone.utc), int(item.get("id") or 0)), reverse=True)
        start = max(0, int(offset or 0))
        end = start + max(1, min(int(limit or 50), 200))
        return [deepcopy(row) for row in rows[start:end]]

    def get_item(self, inbox_id: int) -> dict[str, Any] | None:
        for row in self.rows:
            if int(row.get("id") or 0) == int(inbox_id):
                return deepcopy(row)
        return None

    def claim_due(self, *, provider: str, limit: int = 50, locked_by: str = "webhook-inbox-worker") -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        due_ids = {int(row["id"]) for row in self.preview_due(provider=provider, limit=limit)}
        for row in self.rows:
            if int(row.get("id") or 0) in due_ids:
                row["status"] = "processing"
                row["locked_at"] = now
                row["locked_by"] = locked_by
                row["started_at"] = row.get("started_at") or now
                row["updated_at"] = now
                claimed.append(deepcopy(row))
        claimed.sort(key=lambda item: _due_priority(item, now=now))
        return claimed

    def claim_one(self, inbox_id: int, *, locked_by: str = "webhook-inbox-worker") -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for row in self.rows:
            if int(row.get("id") or 0) != int(inbox_id):
                continue
            locked_expired = bool(row.get("locked_at") and row["locked_at"] <= now - timedelta(minutes=5))
            if (
                not _text(row.get("hold_reason"))
                and row.get("status") in {"received", "failed_retryable"}
                and (not row.get("next_retry_at") or row["next_retry_at"] <= now)
                and (not row.get("locked_at") or locked_expired)
            ):
                row["status"] = "processing"
                row["locked_at"] = now
                row["locked_by"] = locked_by
                row["started_at"] = row.get("started_at") or now
                row["updated_at"] = now
                return deepcopy(row)
        return None

    def acquire_due(self, *, provider: str, limit: int = 50, locked_by: str = "webhook-inbox-worker") -> list[dict[str, Any]]:
        return self.claim_due(provider=provider, limit=limit, locked_by=locked_by)

    def mark_succeeded(
        self,
        inbox_id: int,
        *,
        processing_summary_json: dict[str, Any] | None = None,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for row in self.rows:
            if int(row.get("id") or 0) == int(inbox_id):
                if expected_generation and row.get("status") != "processing":
                    return None
                if expected_lease_token and row.get("lease_token") != expected_lease_token:
                    return None
                if expected_generation and int(row.get("worker_generation") or 0) != int(expected_generation):
                    return None
                if expected_generation and (
                    not isinstance(row.get("lease_expires_at"), datetime)
                    or row["lease_expires_at"] <= now
                ):
                    return None
                update = {
                    "status": "succeeded",
                    "locked_at": None,
                    "locked_by": "",
                    "lease_token": "",
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "last_error_code": "",
                    "last_error_message": "",
                    "finished_at": now,
                    "updated_at": now,
                }
                if processing_summary_json is not None:
                    update["processing_summary_json"] = deepcopy(processing_summary_json)
                row.update(update)
                return deepcopy(row)
        return None

    def mark_failed_retryable(self, inbox_id: int, *, error_code: str, error_message: str, next_retry_at: datetime | None = None) -> dict[str, Any] | None:
        return self.mark_failed(
            inbox_id,
            error_code=error_code,
            error_message=error_message,
            retryable=True,
            next_retry_at=next_retry_at,
        )

    def mark_failed_terminal(self, inbox_id: int, *, error_code: str, error_message: str) -> dict[str, Any] | None:
        return self.mark_failed(
            inbox_id,
            error_code=error_code,
            error_message=error_message,
            retryable=False,
            next_retry_at=None,
        )

    def mark_dead_letter(self, inbox_id: int, *, error_code: str = "", error_message: str = "") -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for row in self.rows:
            if int(row.get("id") or 0) == int(inbox_id):
                row.update(
                    status="dead_letter",
                    next_retry_at=None,
                    locked_at=None,
                    locked_by="",
                    last_error_code=_text(error_code)[:120],
                    last_error_message=_text(error_message)[:2000],
                    finished_at=now,
                    updated_at=now,
                )
                return deepcopy(row)
        return None

    def mark_retryable_now(self, inbox_id: int, *, reason: str = "") -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for row in self.rows:
            if int(row.get("id") or 0) == int(inbox_id) and row.get("status") in {"failed_retryable", "failed_terminal", "dead_letter", "processing"}:
                row.update(
                    status="failed_retryable",
                    next_retry_at=now,
                    available_at=now,
                    locked_at=None,
                    locked_by="",
                    lease_token="",
                    lease_expires_at=None,
                    heartbeat_at=None,
                    last_error_code="operator_retry",
                    last_error_message=_text(reason)[:2000],
                    finished_at=None,
                    updated_at=now,
                )
                return deepcopy(row)
        return None

    def mark_ignored(self, inbox_id: int, *, reason: str = "") -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for row in self.rows:
            if int(row.get("id") or 0) == int(inbox_id):
                row.update(
                    status="ignored",
                    next_retry_at=None,
                    locked_at=None,
                    locked_by="",
                    last_error_code="operator_skip",
                    last_error_message=_text(reason)[:2000],
                    finished_at=now,
                    updated_at=now,
                )
                return deepcopy(row)
        return None

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        status_values = set(_status_values(filters))
        received_from = _filter_datetime((filters or {}).get("received_from"))
        received_to = _filter_datetime((filters or {}).get("received_to"))

        def matches(row: dict[str, Any]) -> bool:
            for column in ("tenant_id", "provider", "event_family", "route"):
                expected = _text((filters or {}).get(column))
                if expected and _text(row.get(column)) != expected:
                    return False
            if status_values and _text(row.get("status")) not in status_values:
                return False
            received_at = row.get("received_at")
            if received_from and (not isinstance(received_at, datetime) or received_at < received_from):
                return False
            if received_to and (not isinstance(received_at, datetime) or received_at > received_to):
                return False
            return True

        rows = [row for row in self.rows if matches(row)]

        def is_due(row: dict[str, Any]) -> bool:
            if _text(row.get("hold_reason")):
                return False
            status = row.get("status")
            locked_expired = bool(row.get("locked_at") and row["locked_at"] <= now - timedelta(minutes=5))
            if status == "processing":
                return locked_expired
            return (
                status in {"received", "failed_retryable"}
                and (not row.get("next_retry_at") or row["next_retry_at"] <= now)
                and (not row.get("locked_at") or locked_expired)
            )

        due_rows = [row for row in rows if is_due(row)]
        active_rows = [row for row in rows if row.get("status") in {"received", "failed_retryable", "processing"}]
        oldest_received_at = min((row.get("received_at") for row in active_rows if row.get("received_at")), default=None)
        status_counts: dict[str, int] = {}
        for row in rows:
            status = _text(row.get("status"))
            status_counts[status] = status_counts.get(status, 0) + 1
        provider_counts: dict[str, int] = {}
        route_counts: dict[str, int] = {}
        error_counts: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            provider = _text(row.get("provider"))
            route = _text(row.get("route"))
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            route_counts[route] = route_counts.get(route, 0) + 1
            error_code = _text(row.get("last_error_code"))
            if error_code:
                key = (_text(row.get("status")), error_code, _text(row.get("last_error_message")))
                current = error_counts.setdefault(
                    key,
                    {
                        "status": key[0],
                        "error_code": key[1],
                        "error_message": key[2],
                        "count": 0,
                        "last_seen_at": row.get("updated_at"),
                    },
                )
                current["count"] = int(current.get("count") or 0) + 1
                if row.get("updated_at") and (not current.get("last_seen_at") or row.get("updated_at") > current.get("last_seen_at")):
                    current["last_seen_at"] = row.get("updated_at")
        provider_distribution = [{"provider": key, "count": count} for key, count in sorted(provider_counts.items(), key=lambda item: (-item[1], item[0]))[:10]]
        route_distribution = [{"route": key, "count": count} for key, count in sorted(route_counts.items(), key=lambda item: (-item[1], item[0]))[:10]]
        recent_errors = sorted(
            error_counts.values(),
            key=lambda item: (item.get("last_seen_at") or datetime.min.replace(tzinfo=timezone.utc), int(item.get("count") or 0)),
            reverse=True,
        )[:10]
        return {
            "raw_open_count": len(active_rows),
            "held_count": len([row for row in active_rows if _text(row.get("hold_reason"))]),
            "due_count": len(due_rows),
            "eligible_due_count": len(due_rows),
            "scheduled_count": 0,
            "retry_wait_count": len([
                row for row in rows
                if not _text(row.get("hold_reason")) and row.get("status") == "failed_retryable"
                and row.get("next_retry_at") and row.get("next_retry_at") > now
            ]),
            "rate_limited_count": 0,
            "in_flight_count": len([
                row for row in rows
                if not _text(row.get("hold_reason")) and row.get("status") == "processing"
                and row.get("locked_at") and row.get("locked_at") > now - timedelta(minutes=5)
            ]),
            "unknown_count": 0,
            "dlq_count": len([row for row in rows if row.get("status") in {"dead_letter", "failed_terminal"}]),
            "processing_count": len([row for row in rows if row.get("status") == "processing"]),
            "failed_retryable_count": len([row for row in rows if row.get("status") == "failed_retryable"]),
            "dead_letter_count": len([row for row in rows if row.get("status") == "dead_letter"]),
            "oldest_received_age_seconds": int((now - oldest_received_at).total_seconds()) if oldest_received_at else 0,
            "status_counts": status_counts,
            "provider_distribution": provider_distribution,
            "route_distribution": route_distribution,
            "recent_errors": recent_errors,
        }

    def mark_failed(
        self,
        inbox_id: int,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        next_retry_at: datetime | None = None,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        for row in self.rows:
            if int(row.get("id") or 0) == int(inbox_id):
                if expected_generation and row.get("status") != "processing":
                    return None
                if expected_lease_token and row.get("lease_token") != expected_lease_token:
                    return None
                if expected_generation and int(row.get("worker_generation") or 0) != int(expected_generation):
                    return None
                if expected_generation and (
                    not isinstance(row.get("lease_expires_at"), datetime)
                    or row["lease_expires_at"] <= now
                ):
                    return None
                attempt_count = int(row.get("attempt_count") or 0) + 1
                row["attempt_count"] = attempt_count
                if not retryable:
                    status = "failed_terminal"
                elif attempt_count >= int(row.get("max_attempts") or 1):
                    status = "dead_letter"
                else:
                    status = "failed_retryable"
                row.update(
                    status=status,
                    next_retry_at=next_retry_at if status == "failed_retryable" else None,
                    available_at=next_retry_at if status == "failed_retryable" and next_retry_at else row.get("available_at"),
                    locked_at=None,
                    locked_by="",
                    lease_token="",
                    lease_expires_at=None,
                    heartbeat_at=None,
                    last_error_code=_text(error_code)[:120],
                    last_error_message=_text(error_message)[:2000],
                    finished_at=now if status in {"failed_terminal", "dead_letter"} else row.get("finished_at"),
                    updated_at=now,
                )
                return deepcopy(row)
        return None


def build_webhook_inbox_repository() -> WebhookInboxRepository:
    return PostgresWebhookInboxRepository()
