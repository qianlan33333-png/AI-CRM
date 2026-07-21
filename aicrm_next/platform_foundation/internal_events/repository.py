# ruff: noqa: F401
from __future__ import annotations

from aicrm_next.shared.runtime import production_data_ready, raw_database_url

from .fanout import validate_fanout_manifest
from .repository_support import (
    AUTOMATIC_PENDING_STATUSES,
    Any,
    Callable,
    DEFAULT_TENANT_ID,
    EVENT_SECTION_EVENT_TYPES,
    InternalEvent,
    InternalEventConsumerAttempt,
    InternalEventConsumerRun,
    InternalEventConsumerSpec,
    InternalEventCreateRequest,
    InternalEventOutboxRecord,
    InternalEventRepository,
    LEASE_TIMEOUT,
    Session,
    _SENSITIVE_PAYLOAD_KEYS,
    _audit_reason,
    _coerce_datetime,
    _consumer_specs_payload,
    _hash_text,
    _idempotency_key,
    _json_dumps,
    _json_obj,
    _payload_summary,
    _public_attempt,
    _public_event,
    _public_outbox,
    _public_run,
    _run_is_automatically_due,
    _text,
    _trace_hash_candidates,
    automatic_due_predicate_sql,
    datetime,
    fixture_mode,
    get_session_factory,
    hashlib,
    json,
    public_datetime,
    queue_metric_filter_sql,
    redact_sensitive_text,
    scrub_summary,
    text,
    timedelta,
    timezone,
    utcnow,
    uuid4,
)
from .repository_memory import InMemoryInternalEventRepository


_fixture_repo = InMemoryInternalEventRepository()


def read_wechat_pay_order_for_payment_event(*, lookup: str, aggregate_id: str) -> dict[str, Any]:
    if not production_data_ready():
        return {}
    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(raw_database_url(), row_factory=dict_row) as conn:
            if lookup:
                row = conn.execute("SELECT * FROM wechat_pay_orders WHERE out_trade_no = %s LIMIT 1", (lookup,)).fetchone()
                if row:
                    return dict(row)
            if aggregate_id:
                row = conn.execute(
                    "SELECT * FROM wechat_pay_orders WHERE id::text = %s OR out_trade_no = %s LIMIT 1",
                    (aggregate_id, aggregate_id),
                ).fetchone()
                if row:
                    return dict(row)
    except Exception as exc:
        raise RuntimeError("authoritative payment order read failed") from exc
    return {}


class SQLAlchemyInternalEventRepository(InternalEventRepository):
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

    def _create_event_in_session(self, session: Session, request: InternalEventCreateRequest) -> InternalEvent:
        key = _idempotency_key(request)
        occurred_at = request.occurred_at or utcnow()
        payload_summary = dict(request.payload_summary or {}) or _payload_summary(request.payload)
        tenant_id = _text(request.tenant_id) or DEFAULT_TENANT_ID
        row = (
            session.execute(
                text(
                    """
            INSERT INTO internal_event (
                tenant_id, event_id, event_type, event_version, aggregate_type, aggregate_id,
                subject_type, subject_id, idempotency_key, actor_id, actor_type,
                source_module, source_route, source_command_id, trace_id, request_id,
                correlation_id, execution_id, parent_execution_id,
                occurred_at, payload_json, payload_summary_json, created_at
            )
            VALUES (
                :tenant_id, :event_id, :event_type, :event_version, :aggregate_type, :aggregate_id,
                :subject_type, :subject_id, :idempotency_key, :actor_id, :actor_type,
                :source_module, :source_route, :source_command_id, :trace_id, :request_id,
                :correlation_id, :execution_id, :parent_execution_id,
                CAST(:occurred_at AS timestamptz), CAST(:payload_json AS jsonb),
                CAST(:payload_summary_json AS jsonb), CURRENT_TIMESTAMP
            )
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING *
            """
                ),
                {
                    "tenant_id": tenant_id,
                    "event_id": "iev_" + uuid4().hex,
                    "event_type": _text(request.event_type),
                    "event_version": int(request.event_version or 1),
                    "aggregate_type": _text(request.aggregate_type),
                    "aggregate_id": _text(request.aggregate_id),
                    "subject_type": _text(request.subject_type),
                    "subject_id": _text(request.subject_id),
                    "idempotency_key": key,
                    "actor_id": _text(request.context.actor_id),
                    "actor_type": _text(request.context.actor_type) or "system",
                    "source_module": _text(request.source_module),
                    "source_route": _text(request.context.source_route),
                    "source_command_id": _text(request.source_command_id),
                    "trace_id": _text(request.context.trace_id),
                    "request_id": _text(request.context.request_id),
                    "correlation_id": _text(request.correlation_id),
                    "execution_id": _text(request.execution_id) or "exe_" + uuid4().hex,
                    "parent_execution_id": _text(request.parent_execution_id),
                    "occurred_at": public_datetime(occurred_at),
                    "payload_json": _json_dumps(request.payload),
                    "payload_summary_json": _json_dumps(payload_summary),
                },
            )
            .mappings()
            .fetchone()
        )
        event = _public_event(dict(row) if row else None)
        if event:
            return event
        existing = (
            session.execute(
                text("SELECT * FROM internal_event WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key LIMIT 1"),
                {"tenant_id": tenant_id, "idempotency_key": key},
            )
            .mappings()
            .fetchone()
        )
        event = _public_event(dict(existing) if existing else None)
        if event is None:
            raise RuntimeError("internal event idempotent create failed")
        return event

    def create_event(self, request: InternalEventCreateRequest) -> InternalEvent:
        with self._session_factory() as session:
            event = self._create_event_in_session(session, request)
            session.commit()
            return event

    def create_event_with_consumer_runs(
        self,
        request: InternalEventCreateRequest,
        consumers: list[InternalEventConsumerSpec],
    ) -> tuple[InternalEvent, list[InternalEventConsumerRun]]:
        with self._session_factory() as session:
            event = self._create_event_in_session(session, request)
            runs = [self._create_consumer_run_in_session(session, event=event, consumer=consumer) for consumer in _consumer_specs_payload(consumers)]
            session.commit()
            return event, runs

    def get_event(self, event_id: str) -> InternalEvent | None:
        return _public_event(self._one("SELECT * FROM internal_event WHERE event_id = :event_id LIMIT 1", {"event_id": _text(event_id)}))

    def list_events(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[InternalEvent], int]:
        filters = dict(filters or {})
        clauses: list[str] = []
        params: dict[str, Any] = {}
        event_section = _text(filters.get("event_section"))
        for key in ("event_type", "aggregate_type", "aggregate_id", "subject_type", "subject_id", "trace_id", "idempotency_key", "source_module"):
            value = _text(filters.get(key))
            if value:
                clauses.append(f"{key} = :{key}")
                params[key] = value
        if event_section and not _text(filters.get("event_type")):
            known_types = sorted({event_type for values in EVENT_SECTION_EVENT_TYPES.values() for event_type in values})
            section_types = list(EVENT_SECTION_EVENT_TYPES.get(event_section, ()))
            if section_types:
                clauses.append("event_type = ANY(:event_section_types)")
                params["event_section_types"] = section_types
            elif event_section == "other" and known_types:
                clauses.append("NOT (event_type = ANY(:known_event_section_types))")
                params["known_event_section_types"] = known_types
        trace_hashes = _trace_hash_candidates(filters)
        if trace_hashes:
            trace_clauses: list[str] = []
            for index, trace_hash in enumerate(trace_hashes):
                param_key = f"original_trace_hash_{index}"
                trace_clauses.append(
                    f"""
                    (
                        payload_json -> 'broadcast_task' ->> 'original_trace_hash' = :{param_key}
                        OR payload_json -> 'broadcast_task' ->> 'trace_id_hash' = :{param_key}
                    )
                    """
                )
                params[param_key] = trace_hash
            clauses.append("(" + " OR ".join(trace_clauses) + ")")
        if _text(filters.get("created_from")):
            clauses.append("created_at >= CAST(:created_from AS timestamptz)")
            params["created_from"] = _text(filters.get("created_from"))
        if _text(filters.get("created_to")):
            clauses.append("created_at <= CAST(:created_to AS timestamptz)")
            params["created_to"] = _text(filters.get("created_to"))
        if _text(filters.get("consumer_name")):
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM internal_event_consumer_run r
                    WHERE r.event_id = internal_event.event_id
                      AND r.consumer_name = :consumer_name
                )
                """
            )
            params["consumer_name"] = _text(filters.get("consumer_name"))
        if _text(filters.get("consumer_status")):
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM internal_event_consumer_run r
                    WHERE r.event_id = internal_event.event_id
                      AND r.status = :consumer_status
                )
                """
            )
            params["consumer_status"] = _text(filters.get("consumer_status"))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        count_row = self._one(f"SELECT COUNT(*) AS total FROM internal_event {where}", params)
        rows = self._all(
            f"""
            SELECT *
            FROM internal_event
            {where}
            ORDER BY occurred_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": max(1, min(int(limit or 50), 200)), "offset": max(0, int(offset or 0))},
        )
        return [event for row in rows if (event := _public_event(row)) is not None], int((count_row or {}).get("total") or 0)

    def _create_consumer_run_in_session(
        self,
        session: Session,
        *,
        event: InternalEvent,
        consumer: InternalEventConsumerSpec,
    ) -> InternalEventConsumerRun:
        row = (
            session.execute(
                text(
                    """
            INSERT INTO internal_event_consumer_run (
                tenant_id, event_id, consumer_name, consumer_type, status,
                execution_id, parent_execution_id, lane, available_at,
                ordering_key, fairness_key, policy_version, attempt_count, max_attempts,
                created_at, updated_at
            )
            VALUES (
                :tenant_id, :event_id, :consumer_name, :consumer_type, 'pending',
                :execution_id, :parent_execution_id, :lane, CURRENT_TIMESTAMP,
                :ordering_key, :fairness_key, (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
                0, :max_attempts,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (tenant_id, event_id, consumer_name) DO NOTHING
            RETURNING *
            """
                ),
                {
                    "tenant_id": event.tenant_id,
                    "event_id": event.event_id,
                    "consumer_name": _text(consumer.consumer_name),
                    "consumer_type": _text(consumer.consumer_type) or "projection",
                    "execution_id": "exe_" + uuid4().hex,
                    "parent_execution_id": event.execution_id,
                    "lane": "internal_financial" if event.event_type.startswith(("payment.", "refund.", "order.")) else "internal_general",
                    "ordering_key": f"{event.aggregate_type}:{event.aggregate_id}",
                    "fairness_key": event.event_type,
                    "max_attempts": max(1, int(consumer.max_attempts or 5)),
                },
            )
            .mappings()
            .fetchone()
        )
        run = _public_run(dict(row) if row else None)
        if run:
            return run
        existing = (
            session.execute(
                text(
                    """
            SELECT *
            FROM internal_event_consumer_run
            WHERE tenant_id = :tenant_id AND event_id = :event_id AND consumer_name = :consumer_name
            LIMIT 1
            """
                ),
                {
                    "tenant_id": event.tenant_id,
                    "event_id": event.event_id,
                    "consumer_name": _text(consumer.consumer_name),
                },
            )
            .mappings()
            .fetchone()
        )
        run = _public_run(dict(existing) if existing else None)
        if run is None:
            raise RuntimeError("internal event consumer run idempotent create failed")
        return run

    def create_consumer_run(
        self,
        *,
        event: InternalEvent,
        consumer_name: str,
        consumer_type: str = "projection",
        max_attempts: int = 5,
    ) -> InternalEventConsumerRun:
        with self._session_factory() as session:
            run = self._create_consumer_run_in_session(
                session,
                event=event,
                consumer=InternalEventConsumerSpec(
                    consumer_name=consumer_name,
                    consumer_type=consumer_type,
                    max_attempts=max_attempts,
                ),
            )
            session.commit()
            return run

    def list_consumer_runs(self, filters: dict[str, Any] | None = None, *, limit: int = 100, offset: int = 0) -> tuple[list[InternalEventConsumerRun], int]:
        filters = dict(filters or {})
        clauses: list[str] = []
        params: dict[str, Any] = {}
        for key in ("event_id", "consumer_name", "consumer_type", "status"):
            value = _text(filters.get(key))
            if value:
                clauses.append(f"r.{key} = :{key}")
                params[key] = value
        event_type = _text(filters.get("event_type"))
        if event_type:
            clauses.append("e.event_type = :event_type")
            params["event_type"] = event_type
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        count_row = self._one(
            f"""
            SELECT COUNT(*) AS total
            FROM internal_event_consumer_run r
            JOIN internal_event e ON e.event_id = r.event_id
            {where}
            """,
            params,
        )
        rows = self._all(
            f"""
            SELECT r.*, r.xmin::text AS row_version
            FROM internal_event_consumer_run r
            JOIN internal_event e ON e.event_id = r.event_id
            {where}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": max(1, min(int(limit or 100), 200)), "offset": max(0, int(offset or 0))},
        )
        return [run for row in rows if (run := _public_run(row)) is not None], int((count_row or {}).get("total") or 0)

    def list_consumer_runs_for_events(
        self,
        event_ids: list[str],
    ) -> dict[str, list[InternalEventConsumerRun]]:
        normalized_ids = list(dict.fromkeys(_text(event_id) for event_id in event_ids if _text(event_id)))
        grouped: dict[str, list[InternalEventConsumerRun]] = {event_id: [] for event_id in normalized_ids}
        if not normalized_ids:
            return grouped
        rows = self._all(
            """
            SELECT r.*, r.xmin::text AS row_version
            FROM internal_event_consumer_run r
            WHERE r.event_id = ANY(:event_ids)
            ORDER BY r.created_at DESC, r.id DESC
            """,
            {"event_ids": normalized_ids},
        )
        for row in rows:
            run = _public_run(row)
            if run and run.event_id in grouped:
                grouped[run.event_id].append(run)
        return grouped

    def get_consumer_run(self, event_id: str, consumer_name: str) -> InternalEventConsumerRun | None:
        return _public_run(
            self._one(
                "SELECT internal_event_consumer_run.*, xmin::text AS row_version FROM internal_event_consumer_run WHERE event_id = :event_id AND consumer_name = :consumer_name LIMIT 1",
                {"event_id": _text(event_id), "consumer_name": _text(consumer_name)},
            )
        )

    def get_consumer_run_by_id(self, run_id: int) -> InternalEventConsumerRun | None:
        return _public_run(self._one("SELECT internal_event_consumer_run.*, xmin::text AS row_version FROM internal_event_consumer_run WHERE id = :run_id LIMIT 1", {"run_id": int(run_id)}))

    def acquire_consumer_run(
        self,
        *,
        event_id: str,
        consumer_name: str,
        locked_by: str,
        force: bool = False,
    ) -> InternalEventConsumerRun | None:
        executable = (
            "r.status IN ('pending','failed_retryable','failed_terminal','blocked','succeeded','skipped') "
            "AND r.hold_reason = '' "
            "AND (r.locked_at IS NULL OR r.locked_at <= CURRENT_TIMESTAMP - INTERVAL '5 minutes')"
            if force
            else automatic_due_predicate_sql("r")
        )
        lease_token = "iel_" + uuid4().hex
        row = self._write_one(
            f"""
            WITH target AS (
                SELECT r.id
                FROM internal_event_consumer_run r
                WHERE r.event_id = :event_id
                  AND r.consumer_name = :consumer_name
                  AND ({executable})
                ORDER BY r.created_at ASC, r.id ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE internal_event_consumer_run r
            SET status = 'running',
                locked_at = CURRENT_TIMESTAMP,
                locked_by = :locked_by,
                lease_token = :lease_token,
                updated_at = CURRENT_TIMESTAMP
            FROM target
            WHERE r.id = target.id
            RETURNING r.*
            """,
            {
                "event_id": _text(event_id),
                "consumer_name": _text(consumer_name),
                "locked_by": _text(locked_by),
                "lease_token": lease_token,
            },
        )
        return _public_run(row)

    def list_attempts(self, consumer_run_id: int | None = None, *, event_id: str = "") -> list[InternalEventConsumerAttempt]:
        params: dict[str, Any] = {}
        where = ""
        if consumer_run_id is not None:
            where = "WHERE a.consumer_run_id = :consumer_run_id"
            params["consumer_run_id"] = int(consumer_run_id)
        elif _text(event_id):
            where = "WHERE r.event_id = :event_id"
            params["event_id"] = _text(event_id)
        rows = self._all(
            f"""
            SELECT a.*
            FROM internal_event_consumer_attempt a
            JOIN internal_event_consumer_run r ON r.id = a.consumer_run_id
            {where}
            ORDER BY a.id ASC
            """,
            params,
        )
        return [attempt for row in rows if (attempt := _public_attempt(row)) is not None]

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = dict(filters or {})
        where, params = queue_metric_filter_sql(
            filters,
            event_consumer_pair_clause=self._event_consumer_pair_clause,
        )
        due_predicate = automatic_due_predicate_sql("r")
        row = (
            self._one(
                f"""
            SELECT
                COUNT(*) FILTER (
                    WHERE r.status IN ('pending', 'running', 'failed_retryable')
                ) AS raw_open_count,
                COUNT(*) FILTER (
                    WHERE r.hold_reason <> '' AND r.status IN ('pending', 'running', 'failed_retryable')
                ) AS held_count,
                COUNT(*) FILTER (
                    WHERE {due_predicate}
                ) AS due_count,
                0::BIGINT AS scheduled_count,
                COUNT(*) FILTER (
                    WHERE r.hold_reason = '' AND r.status = 'failed_retryable'
                      AND r.next_retry_at > CURRENT_TIMESTAMP
                ) AS retry_wait_count,
                0::BIGINT AS rate_limited_count,
                COUNT(*) FILTER (
                    WHERE r.hold_reason = '' AND r.status = 'running'
                      AND r.locked_at > CURRENT_TIMESTAMP - INTERVAL '5 minutes'
                ) AS in_flight_count,
                0::BIGINT AS unknown_count,
                COUNT(*) FILTER (WHERE r.status IN ('failed_terminal', 'blocked')) AS dlq_count,
                COUNT(*) FILTER (WHERE r.status = 'failed_retryable') AS failed_retryable_count,
                COUNT(*) FILTER (WHERE r.status = 'failed_terminal') AS failed_terminal_count,
                COALESCE(
                    EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - MIN(r.created_at) FILTER (
                        WHERE r.status = 'pending'
                          AND (r.next_retry_at IS NULL OR r.next_retry_at <= CURRENT_TIMESTAMP)
                    )),
                    0
                ) AS oldest_pending_age_seconds
            FROM internal_event_consumer_run r
            JOIN internal_event e ON e.event_id = r.event_id
            {where}
            """,
                params,
            )
            or {}
        )
        by_event_type = self._all(
            f"""
            SELECT e.event_type, COUNT(*) AS due_count
            FROM internal_event_consumer_run r
            JOIN internal_event e ON e.event_id = r.event_id
            {where}
            {"AND" if where else "WHERE"} {due_predicate}
            GROUP BY e.event_type
            ORDER BY e.event_type ASC
            """,
            params,
        )
        by_consumer = self._all(
            f"""
            SELECT r.consumer_name, COUNT(*) AS due_count
            FROM internal_event_consumer_run r
            JOIN internal_event e ON e.event_id = r.event_id
            {where}
            {"AND" if where else "WHERE"} {due_predicate}
            GROUP BY r.consumer_name
            ORDER BY r.consumer_name ASC
            """,
            params,
        )
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
            "dlq_count": int(row.get("dlq_count") or 0),
            "failed_retryable_count": int(row.get("failed_retryable_count") or 0),
            "failed_terminal_count": int(row.get("failed_terminal_count") or 0),
            "oldest_pending_age_seconds": int(float(row.get("oldest_pending_age_seconds") or 0)),
            "due_count_by_event_type": {_text(item.get("event_type")): int(item.get("due_count") or 0) for item in by_event_type},
            "due_count_by_consumer": {_text(item.get("consumer_name")): int(item.get("due_count") or 0) for item in by_consumer},
        }

    def list_due_runs(
        self,
        *,
        limit: int = 50,
        event_types: list[str] | None = None,
        consumer_names: list[str] | None = None,
        event_consumers: list[tuple[str, str]] | None = None,
    ) -> list[InternalEventConsumerRun]:
        filters, params = self._due_filters(event_types=event_types, consumer_names=consumer_names, event_consumers=event_consumers)
        rows = self._all(
            f"""
            SELECT r.*
            FROM internal_event_consumer_run r
            JOIN internal_event e ON e.event_id = r.event_id
            WHERE {filters}
            ORDER BY COALESCE(r.next_retry_at, r.created_at) ASC, r.id ASC
            LIMIT :limit
            """,
            {**params, "limit": max(1, min(int(limit or 50), 200))},
        )
        return [run for row in rows if (run := _public_run(row)) is not None]

    def acquire_due_runs(
        self,
        *,
        limit: int = 50,
        locked_by: str,
        event_types: list[str] | None = None,
        consumer_names: list[str] | None = None,
        event_consumers: list[tuple[str, str]] | None = None,
    ) -> list[InternalEventConsumerRun]:
        filters, params = self._due_filters(event_types=event_types, consumer_names=consumer_names, event_consumers=event_consumers)
        lease_prefix = "iel_" + uuid4().hex
        rows = self._write_all(
            f"""
            WITH due AS (
                SELECT r.id
                FROM internal_event_consumer_run r
                JOIN internal_event e ON e.event_id = r.event_id
                WHERE {filters}
                ORDER BY COALESCE(r.next_retry_at, r.created_at) ASC, r.id ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            UPDATE internal_event_consumer_run r
            SET status = 'running',
                locked_at = CURRENT_TIMESTAMP,
                locked_by = :locked_by,
                lease_token = :lease_prefix || '-' || r.id::text,
                updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE r.id = due.id
            RETURNING r.*
            """,
            {
                **params,
                "limit": max(1, min(int(limit or 50), 200)),
                "locked_by": _text(locked_by),
                "lease_prefix": lease_prefix,
            },
        )
        return [run for row in rows if (run := _public_run(row)) is not None]

    def mark_running(
        self,
        run_id: int,
        *,
        locked_by: str,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> InternalEventConsumerRun | None:
        lease_guard = "AND lease_token = :expected_lease_token" if _text(expected_lease_token) else ""
        row = self._write_one(
            f"""
            UPDATE internal_event_consumer_run
            SET status = 'running', locked_by = :locked_by,
                locked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = :run_id
              AND status = 'running'
              {lease_guard}
              AND (
                  :expected_generation = 0
                  OR (
                      worker_generation = :expected_generation
                      AND lease_expires_at > CURRENT_TIMESTAMP
                  )
              )
            RETURNING *
            """,
            {
                "run_id": int(run_id),
                "locked_by": _text(locked_by),
                "expected_lease_token": _text(expected_lease_token),
                "expected_generation": int(expected_generation or 0),
            },
        )
        return _public_run(row)

    def mark_result(
        self,
        run_id: int,
        *,
        status: str,
        attempt_id: str,
        result_summary: dict[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
        next_retry_at: datetime | None = None,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> InternalEventConsumerRun | None:
        status = _text(status)
        if status not in {"succeeded", "failed_retryable", "failed_terminal", "blocked", "skipped"}:
            status = "blocked"
        finished_sql = ", finished_at = CURRENT_TIMESTAMP" if status in {"succeeded", "failed_terminal", "blocked", "skipped"} else ", finished_at = NULL"
        retry_sql = (
            "next_retry_at = CAST(:next_retry_at AS timestamptz), available_at = CAST(:next_retry_at AS timestamptz),"
            if status == "failed_retryable" and next_retry_at
            else "next_retry_at = NULL,"
        )
        lease_guard = "AND lease_token = :expected_lease_token" if _text(expected_lease_token) else ""
        row = self._write_one(
            f"""
            UPDATE internal_event_consumer_run
            SET
            status = :status,
            attempt_count = attempt_count + 1,
            {retry_sql}
            locked_by = '',
            locked_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            worker_generation = CASE WHEN :status = 'failed_retryable' THEN 0 ELSE worker_generation END,
            last_attempt_id = :attempt_id,
            last_error_code = :error_code,
            last_error_message = :error_message,
            result_summary_json = CAST(:result_summary AS jsonb)
            {finished_sql},
            updated_at = CURRENT_TIMESTAMP
            WHERE id = :run_id
              {lease_guard}
              AND (
                  :expected_generation = 0
                  OR (
                      status = 'running'
                      AND worker_generation = :expected_generation
                      AND lease_expires_at > CURRENT_TIMESTAMP
                  )
              )
            RETURNING *
            """,
            {
                "run_id": int(run_id),
                "status": status,
                "attempt_id": _text(attempt_id),
                "error_code": _text(error_code),
                "error_message": _text(error_message),
                "next_retry_at": public_datetime(next_retry_at) if next_retry_at else None,
                "result_summary": _json_dumps(scrub_summary(result_summary or {})),
                "expected_lease_token": _text(expected_lease_token),
                "expected_generation": int(expected_generation or 0),
            },
        )
        return _public_run(row)

    def complete_consumer_attempt(
        self,
        *,
        run: InternalEventConsumerRun,
        status: str,
        request_summary: dict[str, Any],
        response_summary: dict[str, Any],
        result_summary: dict[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
        next_retry_at: datetime | None = None,
    ) -> tuple[InternalEventConsumerRun, InternalEventConsumerAttempt] | None:
        status = _text(status)
        if status not in {"succeeded", "failed_retryable", "failed_terminal", "blocked", "skipped"}:
            status = "blocked"
        lease_token = _text(run.lease_token)
        if not lease_token:
            return None
        attempt_id = "iea_" + uuid4().hex
        finished_at = status in {"succeeded", "failed_terminal", "blocked", "skipped"}
        with self._session_factory() as session:
            current = (
                session.execute(
                    text(
                        "SELECT * FROM internal_event_consumer_run "
                        "WHERE id = :run_id AND status = 'running' AND lease_token = :lease_token "
                        "AND (:worker_generation = 0 OR (worker_generation = :worker_generation "
                        "AND lease_expires_at > CURRENT_TIMESTAMP)) FOR UPDATE"
                    ),
                    {
                        "run_id": int(run.id),
                        "lease_token": lease_token,
                        "worker_generation": int(run.worker_generation or 0),
                    },
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
                    INSERT INTO internal_event_consumer_attempt (
                        attempt_id, consumer_run_id, consumer_name, status,
                        request_summary_json, response_summary_json, error_code, error_message,
                        started_at, completed_at
                    )
                    VALUES (
                        :attempt_id, :consumer_run_id, :consumer_name, :status,
                        CAST(:request_summary AS jsonb), CAST(:response_summary AS jsonb),
                        :error_code, :error_message, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING *
                    """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "consumer_run_id": int(run.id),
                        "consumer_name": run.consumer_name,
                        "status": status,
                        "request_summary": _json_dumps(scrub_summary(request_summary or {})),
                        "response_summary": _json_dumps(scrub_summary(response_summary or {})),
                        "error_code": _text(error_code),
                        "error_message": _text(error_message),
                    },
                )
                .mappings()
                .fetchone()
            )
            updated_row = (
                session.execute(
                    text(
                        """
                    UPDATE internal_event_consumer_run
                    SET status = :status,
                        attempt_count = attempt_count + 1,
                        next_retry_at = CAST(:next_retry_at AS timestamptz),
                        available_at = CASE
                            WHEN :status = 'failed_retryable'
                            THEN CAST(:next_retry_at AS timestamptz)
                            ELSE available_at
                        END,
                        locked_by = '', locked_at = NULL, lease_token = '',
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        worker_generation = CASE WHEN :status = 'failed_retryable' THEN 0 ELSE worker_generation END,
                        last_attempt_id = :attempt_id,
                        last_error_code = :error_code,
                        last_error_message = :error_message,
                        result_summary_json = CAST(:result_summary AS jsonb),
                        finished_at = CASE WHEN :finished_at THEN CURRENT_TIMESTAMP ELSE NULL END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :run_id AND status = 'running' AND lease_token = :lease_token
                      AND (
                          :worker_generation = 0
                          OR (
                              worker_generation = :worker_generation
                              AND lease_expires_at > CURRENT_TIMESTAMP
                          )
                      )
                    RETURNING *
                    """
                    ),
                    {
                        "run_id": int(run.id),
                        "lease_token": lease_token,
                        "status": status,
                        "next_retry_at": public_datetime(next_retry_at) if status == "failed_retryable" and next_retry_at else None,
                        "attempt_id": attempt_id,
                        "error_code": _text(error_code),
                        "error_message": _text(error_message),
                        "result_summary": _json_dumps(scrub_summary(result_summary or {})),
                        "finished_at": finished_at,
                        "worker_generation": int(run.worker_generation or 0),
                    },
                )
                .mappings()
                .fetchone()
            )
            if not updated_row or not attempt_row:
                session.rollback()
                return None
            session.commit()
            updated = _public_run(dict(updated_row))
            attempt = _public_attempt(dict(attempt_row))
            return (updated, attempt) if updated and attempt else None

    def retry_consumer_run(
        self,
        event_id: str,
        consumer_name: str,
        *,
        actor_id: str,
        actor_type: str,
        reason: str,
    ) -> tuple[InternalEventConsumerRun, InternalEventConsumerAttempt] | None:
        if not _text(actor_id) or not _text(reason):
            return None
        with self._session_factory() as session:
            current = (
                session.execute(
                    text(
                        "SELECT * FROM internal_event_consumer_run "
                        "WHERE event_id = :event_id AND consumer_name = :consumer_name "
                        "AND status IN ('failed_retryable', 'failed_terminal', 'blocked') FOR UPDATE"
                    ),
                    {"event_id": _text(event_id), "consumer_name": _text(consumer_name)},
                )
                .mappings()
                .fetchone()
            )
            run = _public_run(dict(current) if current else None)
            if run is None:
                session.rollback()
                return None
            attempt_id = "iea_" + uuid4().hex
            attempt_row = (
                session.execute(
                    text(
                        """
                    INSERT INTO internal_event_consumer_attempt (
                        attempt_id, consumer_run_id, consumer_name, status,
                        request_summary_json, response_summary_json, error_code, error_message,
                        started_at, completed_at
                    ) VALUES (
                        :attempt_id, :consumer_run_id, :consumer_name, 'manual_retry',
                        CAST(:request_summary AS jsonb), CAST(:response_summary AS jsonb),
                        'manual_retry', :reason, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    ) RETURNING *
                    """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "consumer_run_id": int(run.id),
                        "consumer_name": run.consumer_name,
                        "reason": _audit_reason(reason),
                        "request_summary": _json_dumps(
                            {
                                "manual_retry": True,
                                "actor_ref_hash": _hash_text(actor_id),
                                "actor_type": _text(actor_type) or "operator",
                                "reason": _audit_reason(reason),
                                "from_status": run.status,
                            }
                        ),
                        "response_summary": _json_dumps({"status": "pending"}),
                    },
                )
                .mappings()
                .fetchone()
            )
            updated_row = (
                session.execute(
                    text(
                        """
                    UPDATE internal_event_consumer_run
                    SET status = 'pending', next_retry_at = CURRENT_TIMESTAMP,
                        available_at = CURRENT_TIMESTAMP,
                        locked_by = '', locked_at = NULL, lease_token = '',
                        lease_expires_at = NULL, heartbeat_at = NULL, worker_generation = 0,
                        last_attempt_id = :attempt_id,
                        last_error_code = '', last_error_message = '',
                        finished_at = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :run_id
                    RETURNING *
                    """
                    ),
                    {"run_id": int(run.id), "attempt_id": attempt_id},
                )
                .mappings()
                .fetchone()
            )
            session.commit()
            updated = _public_run(dict(updated_row) if updated_row else None)
            attempt = _public_attempt(dict(attempt_row) if attempt_row else None)
            return (updated, attempt) if updated and attempt else None

    def skip_consumer_run(
        self,
        event_id: str,
        consumer_name: str,
        *,
        actor_id: str = "",
        actor_type: str = "",
        reason: str = "",
    ) -> tuple[InternalEventConsumerRun, InternalEventConsumerAttempt] | None:
        if not _text(actor_id) or not _text(reason):
            return None
        with self._session_factory() as session:
            current = (
                session.execute(
                    text(
                        "SELECT * FROM internal_event_consumer_run "
                        "WHERE event_id = :event_id AND consumer_name = :consumer_name "
                        "AND status NOT IN ('succeeded', 'skipped') FOR UPDATE"
                    ),
                    {"event_id": _text(event_id), "consumer_name": _text(consumer_name)},
                )
                .mappings()
                .fetchone()
            )
            run = _public_run(dict(current) if current else None)
            if run is None:
                session.rollback()
                return None
            attempt_id = "iea_" + uuid4().hex
            attempt_row = (
                session.execute(
                    text(
                        """
                    INSERT INTO internal_event_consumer_attempt (
                        attempt_id, consumer_run_id, consumer_name, status,
                        request_summary_json, response_summary_json, error_code, error_message,
                        started_at, completed_at
                    ) VALUES (
                        :attempt_id, :consumer_run_id, :consumer_name, 'skipped',
                        CAST(:request_summary AS jsonb), CAST(:response_summary AS jsonb),
                        'manual_skip', :reason, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    ) RETURNING *
                    """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "consumer_run_id": int(run.id),
                        "consumer_name": run.consumer_name,
                        "reason": _audit_reason(reason),
                        "request_summary": _json_dumps(
                            {
                                "manual_skip": True,
                                "actor_ref_hash": _hash_text(actor_id),
                                "actor_type": _text(actor_type) or "operator",
                                "reason": _audit_reason(reason),
                                "from_status": run.status,
                            }
                        ),
                        "response_summary": _json_dumps({"skipped": True, "reason": _audit_reason(reason)}),
                    },
                )
                .mappings()
                .fetchone()
            )
            updated_row = (
                session.execute(
                    text(
                        """
                    UPDATE internal_event_consumer_run
                    SET status = 'skipped', next_retry_at = NULL,
                        locked_by = '', locked_at = NULL, lease_token = '',
                        last_attempt_id = :attempt_id,
                        last_error_code = 'manual_skip', last_error_message = :reason,
                        result_summary_json = CAST(:result_summary AS jsonb),
                        finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :run_id
                    RETURNING *
                    """
                    ),
                    {
                        "run_id": int(run.id),
                        "attempt_id": attempt_id,
                        "reason": _audit_reason(reason),
                        "result_summary": _json_dumps({"skipped": True, "reason": _audit_reason(reason)}),
                    },
                )
                .mappings()
                .fetchone()
            )
            session.commit()
            updated = _public_run(dict(updated_row) if updated_row else None)
            attempt = _public_attempt(dict(attempt_row) if attempt_row else None)
            return (updated, attempt) if updated and attempt else None

    def record_attempt(
        self,
        *,
        run: InternalEventConsumerRun,
        status: str,
        request_summary: dict[str, Any],
        response_summary: dict[str, Any],
        error_code: str = "",
        error_message: str = "",
    ) -> InternalEventConsumerAttempt:
        attempt_id = "iea_" + uuid4().hex
        row = self._write_one(
            """
            INSERT INTO internal_event_consumer_attempt (
                attempt_id, consumer_run_id, consumer_name, status,
                request_summary_json, response_summary_json, error_code, error_message,
                started_at, completed_at
            )
            VALUES (
                :attempt_id, :consumer_run_id, :consumer_name, :status,
                CAST(:request_summary AS jsonb), CAST(:response_summary AS jsonb),
                :error_code, :error_message, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING *
            """,
            {
                "attempt_id": attempt_id,
                "consumer_run_id": int(run.id),
                "consumer_name": run.consumer_name,
                "status": _text(status) or "skipped",
                "request_summary": _json_dumps(scrub_summary(request_summary or {})),
                "response_summary": _json_dumps(scrub_summary(response_summary or {})),
                "error_code": _text(error_code),
                "error_message": _text(error_message),
            },
        )
        attempt = _public_attempt(row)
        if attempt is None:
            raise RuntimeError("internal event consumer attempt insert failed")
        return attempt

    def enqueue_outbox(self, request: InternalEventCreateRequest) -> InternalEventOutboxRecord:
        params = self._outbox_insert_params(request)
        row = self._write_one(
            """
            INSERT INTO internal_event_outbox (
                tenant_id, outbox_id, event_type, event_version, aggregate_type, aggregate_id,
                subject_type, subject_id, idempotency_key, actor_id, actor_type,
                source_module, source_route, source_command_id, trace_id, request_id,
                correlation_id, execution_id, parent_execution_id, lane, available_at,
                ordering_key, fairness_key, policy_version, occurred_at, payload_json, payload_summary_json,
                status, attempt_count, max_attempts, created_at, updated_at
            ) VALUES (
                :tenant_id, :outbox_id, :event_type, :event_version, :aggregate_type, :aggregate_id,
                :subject_type, :subject_id, :idempotency_key, :actor_id, :actor_type,
                :source_module, :source_route, :source_command_id, :trace_id, :request_id,
                :correlation_id, :execution_id, :parent_execution_id, :lane,
                CAST(:available_at AS timestamptz), :ordering_key, :fairness_key, (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
                CAST(:occurred_at AS timestamptz), CAST(:payload_json AS jsonb),
                CAST(:payload_summary_json AS jsonb), 'pending', 0, 10,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            params,
        )
        record = _public_outbox(row)
        if record:
            return record
        existing = self._one(
            "SELECT * FROM internal_event_outbox WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key LIMIT 1",
            {"tenant_id": params["tenant_id"], "idempotency_key": params["idempotency_key"]},
        )
        record = _public_outbox(existing)
        if record is None:
            raise RuntimeError("internal event outbox idempotent create failed")
        return record

    def list_due_outbox(self, *, limit: int = 50) -> list[InternalEventOutboxRecord]:
        rows = self._all(
            f"""
            SELECT * FROM internal_event_outbox o
            WHERE {automatic_due_predicate_sql("o")}
            ORDER BY COALESCE(next_retry_at, created_at) ASC, id ASC
            LIMIT :limit
            """,
            {"limit": max(1, min(int(limit or 50), 200))},
        )
        return [record for row in rows if (record := _public_outbox(row)) is not None]

    def acquire_due_outbox(self, *, limit: int = 50, locked_by: str) -> list[InternalEventOutboxRecord]:
        lease_prefix = "ieol_" + uuid4().hex
        rows = self._write_all(
            f"""
            WITH due AS (
                SELECT id
                FROM internal_event_outbox o
                WHERE {automatic_due_predicate_sql("o")}
                ORDER BY COALESCE(next_retry_at, created_at) ASC, id ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            UPDATE internal_event_outbox o
            SET status = 'running', attempt_count = attempt_count + 1,
                locked_at = CURRENT_TIMESTAMP, locked_by = :locked_by,
                lease_token = :lease_prefix || '-' || o.id::text,
                updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE o.id = due.id
            RETURNING o.*
            """,
            {
                "limit": max(1, min(int(limit or 50), 200)),
                "locked_by": _text(locked_by),
                "lease_prefix": lease_prefix,
            },
        )
        return [record for row in rows if (record := _public_outbox(row)) is not None]

    def relay_outbox(
        self,
        outbox: InternalEventOutboxRecord,
        consumers: list[InternalEventConsumerSpec],
        *,
        fanout_manifest: dict[str, Any],
    ) -> tuple[InternalEventOutboxRecord, InternalEvent, list[InternalEventConsumerRun]] | None:
        if not _text(outbox.lease_token):
            return None
        with self._session_factory() as session:
            current = (
                session.execute(
                    text(
                        "SELECT * FROM internal_event_outbox "
                        "WHERE id = :outbox_id AND status = 'running' AND lease_token = :lease_token "
                        "AND (:worker_generation = 0 OR (worker_generation = :worker_generation "
                        "AND lease_expires_at > CURRENT_TIMESTAMP)) FOR UPDATE"
                    ),
                    {
                        "outbox_id": int(outbox.id),
                        "lease_token": _text(outbox.lease_token),
                        "worker_generation": int(outbox.worker_generation or 0),
                    },
                )
                .mappings()
                .fetchone()
            )
            current_outbox = _public_outbox(dict(current) if current else None)
            if current_outbox is None:
                session.rollback()
                return None
            normalized_consumers = _consumer_specs_payload(consumers)
            try:
                manifest_consumers = validate_fanout_manifest(
                    current_outbox.event_type,
                    fanout_manifest,
                    consumers=normalized_consumers,
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            expected_names = {item["consumer_name"] for item in manifest_consumers}
            manifest_version = _text(fanout_manifest.get("version"))
            manifest_hash = _text(fanout_manifest.get("hash"))
            expected_count = len(manifest_consumers)
            event = self._create_event_in_session(session, current_outbox.to_create_request())
            manifest_row = (
                session.execute(
                    text(
                        """
                    UPDATE internal_event
                    SET fanout_manifest_version = :manifest_version,
                        fanout_manifest_hash = :manifest_hash,
                        fanout_manifest_json = CAST(:manifest_json AS jsonb),
                        expected_consumer_count = :expected_consumer_count
                    WHERE id = :event_id
                      AND (fanout_manifest_hash = '' OR fanout_manifest_hash = :manifest_hash)
                    RETURNING *
                    """
                    ),
                    {
                        "event_id": int(event.id),
                        "manifest_version": manifest_version,
                        "manifest_hash": manifest_hash,
                        "manifest_json": _json_dumps(manifest_consumers),
                        "expected_consumer_count": expected_count,
                    },
                )
                .mappings()
                .fetchone()
            )
            if not manifest_row:
                raise RuntimeError("internal_event_fanout_manifest_mismatch")
            event = _public_event(dict(manifest_row))
            if event is None:
                raise RuntimeError("internal_event_fanout_manifest_persist_failed")
            runs = [self._create_consumer_run_in_session(session, event=event, consumer=consumer) for consumer in normalized_consumers]
            actual_rows = (
                session.execute(
                    text("SELECT consumer_name FROM internal_event_consumer_run WHERE tenant_id = :tenant_id AND event_id = :event_id"),
                    {"tenant_id": event.tenant_id, "event_id": event.event_id},
                )
                .mappings()
                .fetchall()
            )
            actual_names = {_text(row.get("consumer_name")) for row in actual_rows if _text(row.get("consumer_name"))}
            if actual_names != expected_names or len(runs) != expected_count:
                raise RuntimeError("internal_event_fanout_incomplete")
            updated_row = (
                session.execute(
                    text(
                        """
                    UPDATE internal_event_outbox
                    SET status = 'relayed', internal_event_id = :internal_event_id,
                        lease_token = '', locked_at = NULL, locked_by = '',
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        next_retry_at = NULL, last_error_code = '', last_error_message = '',
                        relayed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :outbox_id AND status = 'running' AND lease_token = :lease_token
                      AND (
                          :worker_generation = 0
                          OR (
                              worker_generation = :worker_generation
                              AND lease_expires_at > CURRENT_TIMESTAMP
                          )
                      )
                    RETURNING *
                    """
                    ),
                    {
                        "outbox_id": int(outbox.id),
                        "lease_token": _text(outbox.lease_token),
                        "internal_event_id": event.event_id,
                        "worker_generation": int(outbox.worker_generation or 0),
                    },
                )
                .mappings()
                .fetchone()
            )
            if not updated_row:
                session.rollback()
                return None
            session.commit()
            updated = _public_outbox(dict(updated_row))
            return (updated, event, runs) if updated else None

    def mark_outbox_failure(
        self,
        outbox: InternalEventOutboxRecord,
        *,
        error_code: str,
        error_message: str,
        next_retry_at: datetime | None,
    ) -> InternalEventOutboxRecord | None:
        if not _text(outbox.lease_token):
            return None
        status = "failed_terminal" if int(outbox.attempt_count or 0) >= int(outbox.max_attempts or 10) else "failed_retryable"
        row = self._write_one(
            """
            UPDATE internal_event_outbox
            SET status = :status,
                next_retry_at = CAST(:next_retry_at AS timestamptz),
                available_at = CASE
                    WHEN :status = 'failed_retryable'
                    THEN CAST(:next_retry_at AS timestamptz)
                    ELSE available_at
                END,
                lease_token = '', locked_at = NULL, locked_by = '',
                lease_expires_at = NULL, heartbeat_at = NULL,
                worker_generation = CASE WHEN :status = 'failed_retryable' THEN 0 ELSE worker_generation END,
                last_error_code = :error_code, last_error_message = :error_message,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :outbox_id AND status = 'running' AND lease_token = :lease_token
              AND (
                  :worker_generation = 0
                  OR (
                      worker_generation = :worker_generation
                      AND lease_expires_at > CURRENT_TIMESTAMP
                  )
              )
            RETURNING *
            """,
            {
                "outbox_id": int(outbox.id),
                "lease_token": _text(outbox.lease_token),
                "status": status,
                "next_retry_at": public_datetime(next_retry_at) if status == "failed_retryable" and next_retry_at else None,
                "error_code": _text(error_code),
                "error_message": _text(error_message),
                "worker_generation": int(outbox.worker_generation or 0),
            },
        )
        return _public_outbox(row)

    def outbox_metrics(self) -> dict[str, Any]:
        row = (
            self._one(
                f"""
            SELECT
                COUNT(*) FILTER (WHERE {automatic_due_predicate_sql("o")}) AS due_count,
                COUNT(*) FILTER (WHERE status = 'failed_retryable') AS failed_retryable_count,
                COUNT(*) FILTER (WHERE status = 'failed_terminal') AS failed_terminal_count,
                COUNT(*) FILTER (WHERE status = 'running') AS running_count,
                COUNT(*) FILTER (WHERE status = 'relayed') AS relayed_count,
                COALESCE(EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - MIN(created_at)
                    FILTER (WHERE status IN ('pending', 'failed_retryable'))), 0) AS oldest_unrelayed_age_seconds
            FROM internal_event_outbox o
            """
            )
            or {}
        )
        return {
            key: int(float(row.get(key) or 0))
            for key in (
                "due_count",
                "failed_retryable_count",
                "failed_terminal_count",
                "running_count",
                "relayed_count",
                "oldest_unrelayed_age_seconds",
            )
        }

    def _outbox_insert_params(self, request: InternalEventCreateRequest) -> dict[str, Any]:
        return {
            "tenant_id": _text(request.tenant_id) or DEFAULT_TENANT_ID,
            "outbox_id": "ieo_" + uuid4().hex,
            "event_type": _text(request.event_type),
            "event_version": int(request.event_version or 1),
            "aggregate_type": _text(request.aggregate_type),
            "aggregate_id": _text(request.aggregate_id),
            "subject_type": _text(request.subject_type),
            "subject_id": _text(request.subject_id),
            "idempotency_key": _idempotency_key(request),
            "actor_id": _text(request.context.actor_id),
            "actor_type": _text(request.context.actor_type) or "system",
            "source_module": _text(request.source_module),
            "source_route": _text(request.context.source_route),
            "source_command_id": _text(request.source_command_id),
            "trace_id": _text(request.context.trace_id),
            "request_id": _text(request.context.request_id),
            "correlation_id": _text(request.correlation_id),
            "execution_id": _text(request.execution_id) or "exe_" + uuid4().hex,
            "parent_execution_id": _text(request.parent_execution_id),
            "lane": "internal_financial" if _text(request.event_type).startswith(("payment.", "refund.", "order.")) else "internal_general",
            "available_at": public_datetime(request.occurred_at or utcnow()),
            "ordering_key": f"{_text(request.aggregate_type)}:{_text(request.aggregate_id)}",
            "fairness_key": _text(request.event_type),
            "occurred_at": public_datetime(request.occurred_at or utcnow()),
            "payload_json": _json_dumps(request.payload),
            "payload_summary_json": _json_dumps(dict(request.payload_summary or {}) or _payload_summary(request.payload)),
        }

    def _due_filters(
        self,
        *,
        event_types: list[str] | None,
        consumer_names: list[str] | None,
        event_consumers: list[tuple[str, str]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        filters = [automatic_due_predicate_sql("r")]
        params: dict[str, Any] = {}
        if event_types:
            filters.append("e.event_type = ANY(:event_types)")
            params["event_types"] = [_text(item) for item in event_types if _text(item)]
        if consumer_names:
            filters.append("r.consumer_name = ANY(:consumer_names)")
            params["consumer_names"] = [_text(item) for item in consumer_names if _text(item)]
        pair_clause = self._event_consumer_pair_clause(event_consumers, params)
        if pair_clause:
            filters.append(pair_clause)
        return " AND ".join(filters), params

    def _event_consumer_pair_clause(self, event_consumers: Any, params: dict[str, Any]) -> str:
        pairs: list[tuple[str, str]] = []
        for item in event_consumers or []:
            if isinstance(item, str) and ":" in item:
                event_type, consumer_name = item.split(":", 1)
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                event_type, consumer_name = item
            else:
                continue
            event_type = _text(event_type)
            consumer_name = _text(consumer_name)
            if event_type and consumer_name:
                pairs.append((event_type, consumer_name))
        clauses: list[str] = []
        for index, (event_type, consumer_name) in enumerate(pairs):
            event_key = f"pair_event_type_{index}"
            consumer_key = f"pair_consumer_name_{index}"
            params[event_key] = event_type
            params[consumer_key] = consumer_name
            clauses.append(f"(e.event_type = :{event_key} AND r.consumer_name = :{consumer_key})")
        return "(" + " OR ".join(clauses) + ")" if clauses else ""

    def _update(self, run_id: int, set_sql: str, params: dict[str, Any]) -> InternalEventConsumerRun | None:
        row = self._write_one(
            f"""
            UPDATE internal_event_consumer_run
            SET {set_sql},
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :run_id
            RETURNING *
            """,
            {**params, "run_id": int(run_id)},
        )
        return _public_run(row)


def reset_internal_event_fixture_state() -> None:
    global _fixture_repo
    _fixture_repo = InMemoryInternalEventRepository()


def build_internal_event_repository() -> InternalEventRepository:
    if fixture_mode():
        return _fixture_repo
    return SQLAlchemyInternalEventRepository()
