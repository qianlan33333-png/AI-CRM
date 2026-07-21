from __future__ import annotations

from .fanout import validate_fanout_manifest
from .repository_support import (
    Any,
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
    _audit_reason,
    _consumer_specs_payload,
    _hash_text,
    _idempotency_key,
    _payload_summary,
    _public_attempt,
    _public_event,
    _public_outbox,
    _public_run,
    _run_is_automatically_due,
    _text,
    _trace_hash_candidates,
    datetime,
    public_datetime,
    scrub_summary,
    timezone,
    utcnow,
    uuid4,
)


class InMemoryInternalEventRepository(InternalEventRepository):
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._runs: list[dict[str, Any]] = []
        self._attempts: list[dict[str, Any]] = []
        self._outbox: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._next_run_id = 1
        self._next_attempt_id = 1
        self._next_outbox_id = 1

    def create_event(self, request: InternalEventCreateRequest) -> InternalEvent:
        tenant_id = _text(request.tenant_id) or DEFAULT_TENANT_ID
        key = _idempotency_key(request)
        for row in self._events:
            if row["tenant_id"] == tenant_id and row["idempotency_key"] == key:
                event = _public_event(row)
                assert event is not None
                return event
        now = utcnow()
        payload_summary = dict(request.payload_summary or {}) or _payload_summary(request.payload)
        row = {
            "id": self._next_event_id,
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
            "execution_id": _text(request.execution_id) or f"exe_internal_{self._next_event_id}",
            "parent_execution_id": _text(request.parent_execution_id),
            "occurred_at": public_datetime(request.occurred_at or now),
            "payload_json": dict(request.payload or {}),
            "payload_summary_json": payload_summary,
            "fanout_manifest_version": "",
            "fanout_manifest_hash": "",
            "fanout_manifest_json": [],
            "expected_consumer_count": 0,
            "created_at": public_datetime(now),
        }
        self._next_event_id += 1
        self._events.append(row)
        event = _public_event(row)
        assert event is not None
        return event

    def create_event_with_consumer_runs(
        self,
        request: InternalEventCreateRequest,
        consumers: list[InternalEventConsumerSpec],
    ) -> tuple[InternalEvent, list[InternalEventConsumerRun]]:
        event = self.create_event(request)
        runs = [
            self.create_consumer_run(
                event=event,
                consumer_name=consumer.consumer_name,
                consumer_type=consumer.consumer_type,
                max_attempts=consumer.max_attempts,
            )
            for consumer in _consumer_specs_payload(consumers)
        ]
        return event, runs

    def get_event(self, event_id: str) -> InternalEvent | None:
        for row in self._events:
            if row.get("event_id") == _text(event_id):
                return _public_event(row)
        return None

    def list_events(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[InternalEvent], int]:
        rows = list(self._filtered_events(filters or {}))
        rows.sort(key=lambda row: (row.get("occurred_at") or "", int(row.get("id") or 0)), reverse=True)
        total = len(rows)
        window = rows[max(0, int(offset or 0)) : max(0, int(offset or 0)) + max(1, min(int(limit or 50), 200))]
        return [event for row in window if (event := _public_event(row)) is not None], total

    def create_consumer_run(
        self,
        *,
        event: InternalEvent,
        consumer_name: str,
        consumer_type: str = "projection",
        max_attempts: int = 5,
    ) -> InternalEventConsumerRun:
        consumer_name = _text(consumer_name)
        for row in self._runs:
            if row["tenant_id"] == event.tenant_id and row["event_id"] == event.event_id and row["consumer_name"] == consumer_name:
                run = _public_run(row)
                assert run is not None
                return run
        now = public_datetime(utcnow())
        row = {
            "id": self._next_run_id,
            "tenant_id": event.tenant_id,
            "event_id": event.event_id,
            "consumer_name": consumer_name,
            "consumer_type": _text(consumer_type) or "projection",
            "execution_id": f"exe_internal_run_{self._next_run_id}",
            "parent_execution_id": event.execution_id,
            "lane": "internal_financial" if event.event_type.startswith(("payment.", "refund.", "order.")) else "internal_general",
            "available_at": now,
            "ordering_key": f"{event.aggregate_type}:{event.aggregate_id}",
            "fairness_key": event.event_type,
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": max(1, int(max_attempts or 5)),
            "next_retry_at": "",
            "locked_at": "",
            "locked_by": "",
            "lease_token": "",
            "lease_expires_at": "",
            "heartbeat_at": "",
            "worker_generation": 0,
            "policy_version": "queue-v1",
            "row_version": str(self._next_run_id),
            "last_attempt_id": "",
            "last_error_code": "",
            "last_error_message": "",
            "result_summary_json": {},
            "created_at": now,
            "updated_at": now,
            "finished_at": "",
            "hold_reason": "",
            "hold_at": "",
        }
        self._next_run_id += 1
        self._runs.append(row)
        run = _public_run(row)
        assert run is not None
        return run

    def list_consumer_runs(self, filters: dict[str, Any] | None = None, *, limit: int = 100, offset: int = 0) -> tuple[list[InternalEventConsumerRun], int]:
        rows = list(self._filtered_runs(filters or {}))
        rows.sort(key=lambda row: (row.get("created_at") or "", int(row.get("id") or 0)), reverse=True)
        total = len(rows)
        window = rows[max(0, int(offset or 0)) : max(0, int(offset or 0)) + max(1, min(int(limit or 100), 200))]
        return [run for row in window if (run := _public_run(row)) is not None], total

    def list_consumer_runs_for_events(
        self,
        event_ids: list[str],
    ) -> dict[str, list[InternalEventConsumerRun]]:
        normalized_ids = list(dict.fromkeys(_text(event_id) for event_id in event_ids if _text(event_id)))
        grouped: dict[str, list[InternalEventConsumerRun]] = {event_id: [] for event_id in normalized_ids}
        for row in sorted(
            (row for row in self._runs if _text(row.get("event_id")) in grouped),
            key=lambda item: (item.get("created_at") or "", int(item.get("id") or 0)),
            reverse=True,
        ):
            run = _public_run(row)
            if run:
                grouped[run.event_id].append(run)
        return grouped

    def get_consumer_run(self, event_id: str, consumer_name: str) -> InternalEventConsumerRun | None:
        for row in self._runs:
            if row.get("event_id") == _text(event_id) and row.get("consumer_name") == _text(consumer_name):
                return _public_run(row)
        return None

    def get_consumer_run_by_id(self, run_id: int) -> InternalEventConsumerRun | None:
        return _public_run(self._find_run(run_id))

    def acquire_consumer_run(
        self,
        *,
        event_id: str,
        consumer_name: str,
        locked_by: str,
        force: bool = False,
    ) -> InternalEventConsumerRun | None:
        now = utcnow()
        for row in self._runs:
            if row.get("event_id") != _text(event_id) or row.get("consumer_name") != _text(consumer_name):
                continue
            if force:
                if _text(row.get("hold_reason")):
                    return None
                if row.get("status") not in {"pending", "failed_retryable", "failed_terminal", "blocked", "succeeded", "skipped"}:
                    return None
                if row.get("locked_at") and self._dt(row.get("locked_at")) > now - LEASE_TIMEOUT:
                    return None
            elif not _run_is_automatically_due(row, now=now):
                return None
            row["status"] = "running"
            row["locked_at"] = public_datetime(now)
            row["locked_by"] = _text(locked_by)
            row["lease_token"] = "iel_" + uuid4().hex
            row["updated_at"] = public_datetime(now)
            return _public_run(row)
        return None

    def list_attempts(self, consumer_run_id: int | None = None, *, event_id: str = "") -> list[InternalEventConsumerAttempt]:
        rows = list(self._attempts)
        if consumer_run_id is not None:
            rows = [row for row in rows if int(row.get("consumer_run_id") or 0) == int(consumer_run_id)]
        elif _text(event_id):
            run_ids = {int(row.get("id") or 0) for row in self._runs if row.get("event_id") == _text(event_id)}
            rows = [row for row in rows if int(row.get("consumer_run_id") or 0) in run_ids]
        rows.sort(key=lambda row: int(row.get("id") or 0))
        return [attempt for row in rows if (attempt := _public_attempt(row)) is not None]

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        now = utcnow()
        normalized_filters = dict(filters or {})
        rows = list(self._filtered_runs(normalized_filters))
        if _text(normalized_filters.get("consumer_status")):
            rows = [row for row in rows if _text(row.get("status")) == _text(normalized_filters.get("consumer_status"))]
        event_filter_keys = {
            "event_section",
            "event_type",
            "aggregate_type",
            "aggregate_id",
            "subject_type",
            "subject_id",
            "trace_id",
            "idempotency_key",
            "source_module",
            "created_from",
            "created_to",
        }
        if any(normalized_filters.get(key) not in (None, "") for key in event_filter_keys):
            event_ids = {row.get("event_id") for row in self._filtered_events(normalized_filters)}
            rows = [row for row in rows if row.get("event_id") in event_ids]
        event_type_set = {_text(item) for item in (filters or {}).get("event_types") or [] if _text(item)}
        consumer_set = {_text(item) for item in (filters or {}).get("consumer_names") or [] if _text(item)}
        pair_set = self._event_consumer_pair_set((filters or {}).get("event_consumers"))
        if event_type_set:
            rows = [row for row in rows if (self.get_event(row.get("event_id") or "") or InternalEvent()).event_type in event_type_set]
        if consumer_set:
            rows = [row for row in rows if _text(row.get("consumer_name")) in consumer_set]
        if pair_set:
            rows = [
                row
                for row in rows
                if ((self.get_event(row.get("event_id") or "") or InternalEvent()).event_type, _text(row.get("consumer_name"))) in pair_set
            ]
        due_rows = [row for row in rows if _run_is_automatically_due(row, now=now)]
        pending_due = [row for row in due_rows if row.get("status") == "pending"]
        oldest = min((self._dt(row.get("created_at")) for row in pending_due), default=None)
        by_event_type: dict[str, int] = {}
        by_consumer: dict[str, int] = {}
        for row in due_rows:
            event_type = (self.get_event(row.get("event_id") or "") or InternalEvent()).event_type
            consumer_name = _text(row.get("consumer_name"))
            by_event_type[event_type] = by_event_type.get(event_type, 0) + 1
            by_consumer[consumer_name] = by_consumer.get(consumer_name, 0) + 1
        return {
            "raw_open_count": len([row for row in rows if row.get("status") in {"pending", "running", "failed_retryable"}]),
            "held_count": len([
                row for row in rows
                if _text(row.get("hold_reason")) and row.get("status") in {"pending", "running", "failed_retryable"}
            ]),
            "due_count": len(due_rows),
            "eligible_due_count": len(due_rows),
            "scheduled_count": 0,
            "retry_wait_count": len([
                row for row in rows
                if not _text(row.get("hold_reason")) and row.get("status") == "failed_retryable"
                and row.get("next_retry_at") and self._dt(row.get("next_retry_at")) > now
            ]),
            "rate_limited_count": 0,
            "in_flight_count": len([
                row for row in rows
                if not _text(row.get("hold_reason")) and row.get("status") == "running"
                and row.get("locked_at") and self._dt(row.get("locked_at")) > now - LEASE_TIMEOUT
            ]),
            "unknown_count": 0,
            "dlq_count": len([row for row in rows if row.get("status") in {"failed_terminal", "blocked"}]),
            "failed_retryable_count": len([row for row in rows if row.get("status") == "failed_retryable"]),
            "failed_terminal_count": len([row for row in rows if row.get("status") == "failed_terminal"]),
            "oldest_pending_age_seconds": max(0, int((now - oldest).total_seconds())) if oldest else 0,
            "due_count_by_event_type": dict(sorted(by_event_type.items())),
            "due_count_by_consumer": dict(sorted(by_consumer.items())),
        }

    def list_due_runs(
        self,
        *,
        limit: int = 50,
        event_types: list[str] | None = None,
        consumer_names: list[str] | None = None,
        event_consumers: list[tuple[str, str]] | None = None,
    ) -> list[InternalEventConsumerRun]:
        now = utcnow()
        event_type_set = {_text(item) for item in event_types or [] if _text(item)}
        consumer_set = {_text(item) for item in consumer_names or [] if _text(item)}
        pair_set = self._event_consumer_pair_set(event_consumers)
        rows = [
            row
            for row in self._runs
            if _run_is_automatically_due(row, now=now)
            and (not consumer_set or row.get("consumer_name") in consumer_set)
            and (not event_type_set or (self.get_event(row.get("event_id") or "") or InternalEvent()).event_type in event_type_set)
            and (not pair_set or ((self.get_event(row.get("event_id") or "") or InternalEvent()).event_type, _text(row.get("consumer_name"))) in pair_set)
        ]
        rows.sort(key=lambda row: (row.get("next_retry_at") or row.get("created_at") or "", int(row.get("id") or 0)))
        return [run for row in rows[: max(1, min(int(limit or 50), 200))] if (run := _public_run(row)) is not None]

    def acquire_due_runs(
        self,
        *,
        limit: int = 50,
        locked_by: str,
        event_types: list[str] | None = None,
        consumer_names: list[str] | None = None,
        event_consumers: list[tuple[str, str]] | None = None,
    ) -> list[InternalEventConsumerRun]:
        runs = self.list_due_runs(limit=limit, event_types=event_types, consumer_names=consumer_names, event_consumers=event_consumers)
        now = public_datetime(utcnow())
        for run in runs:
            row = self._find_run(run.id)
            if row:
                row["status"] = "running"
                row["locked_at"] = now
                row["locked_by"] = _text(locked_by)
                row["lease_token"] = "iel_" + uuid4().hex
                row["updated_at"] = now
        return [run for run_id in [run.id for run in runs] if (run := self.get_consumer_run_by_id(run_id)) is not None]

    def mark_running(
        self,
        run_id: int,
        *,
        locked_by: str,
        expected_lease_token: str = "",
        expected_generation: int = 0,
    ) -> InternalEventConsumerRun | None:
        row = self._find_run(run_id)
        if not row or (_text(expected_lease_token) and _text(row.get("lease_token")) != _text(expected_lease_token)):
            return None
        if expected_generation and int(row.get("worker_generation") or 0) != int(expected_generation):
            return None
        return self._mutate(run_id, status="running", locked_by=_text(locked_by), locked_at=public_datetime(utcnow()))

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
        row = self._find_run(run_id)
        if not row or (_text(expected_lease_token) and _text(row.get("lease_token")) != _text(expected_lease_token)):
            return None
        if expected_generation and int(row.get("worker_generation") or 0) != int(expected_generation):
            return None
        if row:
            row["attempt_count"] = int(row.get("attempt_count") or 0) + 1
        finished_at = public_datetime(utcnow()) if status in {"succeeded", "failed_terminal", "blocked", "skipped"} else ""
        return self._mutate(
            run_id,
            status=status,
            next_retry_at=public_datetime(next_retry_at) if status == "failed_retryable" and next_retry_at else "",
            locked_by="",
            locked_at="",
            lease_token="",
            last_attempt_id=_text(attempt_id),
            last_error_code=_text(error_code),
            last_error_message=_text(error_message),
            result_summary_json=scrub_summary(result_summary or {}),
            finished_at=finished_at,
        )

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
        row = self._find_run(run.id)
        if not row or not _text(run.lease_token) or _text(row.get("lease_token")) != _text(run.lease_token) or row.get("status") != "running":
            return None
        attempt = self.record_attempt(
            run=run,
            status=status,
            request_summary=request_summary,
            response_summary=response_summary,
            error_code=error_code,
            error_message=error_message,
        )
        updated = self.mark_result(
            run.id,
            status=status,
            attempt_id=attempt.attempt_id,
            result_summary=result_summary,
            error_code=error_code,
            error_message=error_message,
            next_retry_at=next_retry_at,
            expected_lease_token=run.lease_token,
            expected_generation=run.worker_generation,
        )
        return (updated, attempt) if updated else None

    def retry_consumer_run(
        self,
        event_id: str,
        consumer_name: str,
        *,
        actor_id: str,
        actor_type: str,
        reason: str,
    ) -> tuple[InternalEventConsumerRun, InternalEventConsumerAttempt] | None:
        run = self.get_consumer_run(event_id, consumer_name)
        if not run or run.status not in {"failed_retryable", "failed_terminal", "blocked"} or not _text(actor_id) or not _text(reason):
            return None
        attempt = self.record_attempt(
            run=run,
            status="manual_retry",
            request_summary={
                "manual_retry": True,
                "actor_ref_hash": _hash_text(actor_id),
                "actor_type": _text(actor_type) or "operator",
                "reason": _audit_reason(reason),
                "from_status": run.status,
            },
            response_summary={"status": "pending"},
            error_code="manual_retry",
            error_message=_audit_reason(reason),
        )
        updated = self._mutate(
            run.id,
            status="pending",
            next_retry_at=public_datetime(utcnow()),
            locked_by="",
            locked_at="",
            lease_token="",
            last_attempt_id=attempt.attempt_id,
            last_error_code="",
            last_error_message="",
            finished_at="",
        )
        return (updated, attempt) if updated else None

    def skip_consumer_run(
        self,
        event_id: str,
        consumer_name: str,
        *,
        actor_id: str = "",
        actor_type: str = "",
        reason: str = "",
    ) -> tuple[InternalEventConsumerRun, InternalEventConsumerAttempt] | None:
        run = self.get_consumer_run(event_id, consumer_name)
        if not run or run.status in {"succeeded", "skipped"} or not _text(actor_id) or not _text(reason):
            return None
        attempt = self.record_attempt(
            run=run,
            status="skipped",
            request_summary={
                "manual_skip": True,
                "actor_ref_hash": _hash_text(actor_id),
                "actor_type": _text(actor_type) or "operator",
                "reason": _audit_reason(reason),
                "from_status": run.status,
            },
            response_summary={"skipped": True, "reason": _audit_reason(reason)},
            error_code="manual_skip",
            error_message=_audit_reason(reason),
        )
        updated = self._mutate(
            run.id,
            status="skipped",
            next_retry_at="",
            locked_by="",
            locked_at="",
            lease_token="",
            last_attempt_id=attempt.attempt_id,
            last_error_code="manual_skip",
            last_error_message=_audit_reason(reason),
            result_summary_json={"skipped": True, "reason": _audit_reason(reason)},
            finished_at=public_datetime(utcnow()),
        )
        if updated is None:
            return None
        return updated, attempt

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
        now = public_datetime(utcnow())
        row = {
            "id": self._next_attempt_id,
            "attempt_id": "iea_" + uuid4().hex,
            "consumer_run_id": int(run.id),
            "consumer_name": run.consumer_name,
            "status": _text(status) or "skipped",
            "request_summary_json": scrub_summary(request_summary or {}),
            "response_summary_json": scrub_summary(response_summary or {}),
            "error_code": _text(error_code),
            "error_message": _text(error_message),
            "started_at": now,
            "completed_at": now,
        }
        self._next_attempt_id += 1
        self._attempts.append(row)
        attempt = _public_attempt(row)
        assert attempt is not None
        return attempt

    def enqueue_outbox(self, request: InternalEventCreateRequest) -> InternalEventOutboxRecord:
        tenant_id = _text(request.tenant_id) or DEFAULT_TENANT_ID
        key = _idempotency_key(request)
        for row in self._outbox:
            if row.get("tenant_id") == tenant_id and row.get("idempotency_key") == key:
                record = _public_outbox(row)
                assert record is not None
                return record
        now = public_datetime(utcnow())
        row = {
            "id": self._next_outbox_id,
            "tenant_id": tenant_id,
            "outbox_id": "ieo_" + uuid4().hex,
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
            "occurred_at": public_datetime(request.occurred_at or utcnow()),
            "payload_json": dict(request.payload or {}),
            "payload_summary_json": dict(request.payload_summary or {}) or _payload_summary(request.payload),
            "status": "pending",
            "hold_reason": "",
            "hold_at": "",
            "attempt_count": 0,
            "max_attempts": 10,
            "next_retry_at": "",
            "lease_token": "",
            "locked_at": "",
            "locked_by": "",
            "internal_event_id": "",
            "last_error_code": "",
            "last_error_message": "",
            "created_at": now,
            "updated_at": now,
            "relayed_at": "",
        }
        self._next_outbox_id += 1
        self._outbox.append(row)
        record = _public_outbox(row)
        assert record is not None
        return record

    def list_due_outbox(self, *, limit: int = 50) -> list[InternalEventOutboxRecord]:
        now = utcnow()
        rows = [row for row in self._outbox if _run_is_automatically_due(row, now=now)]
        rows.sort(key=lambda row: (row.get("next_retry_at") or row.get("created_at") or "", int(row.get("id") or 0)))
        return [record for row in rows[: max(1, min(int(limit or 50), 200))] if (record := _public_outbox(row)) is not None]

    def acquire_due_outbox(self, *, limit: int = 50, locked_by: str) -> list[InternalEventOutboxRecord]:
        records = self.list_due_outbox(limit=limit)
        now = public_datetime(utcnow())
        acquired: list[InternalEventOutboxRecord] = []
        for record in records:
            row = self._find_outbox(record.id)
            if not row:
                continue
            row.update(
                {
                    "status": "running",
                    "attempt_count": int(row.get("attempt_count") or 0) + 1,
                    "lease_token": "ieol_" + uuid4().hex,
                    "locked_at": now,
                    "locked_by": _text(locked_by),
                    "updated_at": now,
                }
            )
            current = _public_outbox(row)
            if current:
                acquired.append(current)
        return acquired

    def relay_outbox(
        self,
        outbox: InternalEventOutboxRecord,
        consumers: list[InternalEventConsumerSpec],
        *,
        fanout_manifest: dict[str, Any],
    ) -> tuple[InternalEventOutboxRecord, InternalEvent, list[InternalEventConsumerRun]] | None:
        row = self._find_outbox(outbox.id)
        if not row or row.get("status") != "running" or _text(row.get("lease_token")) != _text(outbox.lease_token):
            return None
        normalized_consumers = _consumer_specs_payload(consumers)
        try:
            manifest_consumers = validate_fanout_manifest(
                outbox.event_type,
                fanout_manifest,
                consumers=normalized_consumers,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        expected_names = {item["consumer_name"] for item in manifest_consumers}
        manifest_version = _text(fanout_manifest.get("version"))
        manifest_hash = _text(fanout_manifest.get("hash"))
        expected_count = len(manifest_consumers)
        events_before = [dict(item) for item in self._events]
        runs_before = [dict(item) for item in self._runs]
        next_event_id_before = self._next_event_id
        next_run_id_before = self._next_run_id
        try:
            event = self.create_event(outbox.to_create_request())
            event_row = next((item for item in self._events if item.get("event_id") == event.event_id), None)
            if event_row is None:
                raise RuntimeError("internal_event_fanout_manifest_persist_failed")
            existing_hash = _text(event_row.get("fanout_manifest_hash"))
            if existing_hash and existing_hash != manifest_hash:
                raise RuntimeError("internal_event_fanout_manifest_mismatch")
            event_row.update(
                {
                    "fanout_manifest_version": manifest_version,
                    "fanout_manifest_hash": manifest_hash,
                    "fanout_manifest_json": manifest_consumers,
                    "expected_consumer_count": expected_count,
                }
            )
            event = _public_event(event_row)
            if event is None:
                raise RuntimeError("internal_event_fanout_manifest_persist_failed")
            runs = [
                self.create_consumer_run(
                    event=event,
                    consumer_name=consumer.consumer_name,
                    consumer_type=consumer.consumer_type,
                    max_attempts=consumer.max_attempts,
                )
                for consumer in normalized_consumers
            ]
            actual_names = {
                _text(item.get("consumer_name"))
                for item in self._runs
                if item.get("tenant_id") == event.tenant_id and item.get("event_id") == event.event_id
            }
            if actual_names != expected_names or len(runs) != expected_count:
                raise RuntimeError("internal_event_fanout_incomplete")
        except Exception:
            self._events = events_before
            self._runs = runs_before
            self._next_event_id = next_event_id_before
            self._next_run_id = next_run_id_before
            raise
        now = public_datetime(utcnow())
        row.update(
            {
                "status": "relayed",
                "internal_event_id": event.event_id,
                "lease_token": "",
                "locked_at": "",
                "locked_by": "",
                "next_retry_at": "",
                "last_error_code": "",
                "last_error_message": "",
                "updated_at": now,
                "relayed_at": now,
            }
        )
        updated = _public_outbox(row)
        return (updated, event, runs) if updated else None

    def mark_outbox_failure(
        self,
        outbox: InternalEventOutboxRecord,
        *,
        error_code: str,
        error_message: str,
        next_retry_at: datetime | None,
    ) -> InternalEventOutboxRecord | None:
        row = self._find_outbox(outbox.id)
        if not row or row.get("status") != "running" or _text(row.get("lease_token")) != _text(outbox.lease_token):
            return None
        status = "failed_terminal" if int(row.get("attempt_count") or 0) >= int(row.get("max_attempts") or 10) else "failed_retryable"
        row.update(
            {
                "status": status,
                "next_retry_at": public_datetime(next_retry_at) if status == "failed_retryable" and next_retry_at else "",
                "lease_token": "",
                "locked_at": "",
                "locked_by": "",
                "last_error_code": _text(error_code),
                "last_error_message": _text(error_message),
                "updated_at": public_datetime(utcnow()),
            }
        )
        return _public_outbox(row)

    def outbox_metrics(self) -> dict[str, Any]:
        now = utcnow()
        due = [row for row in self._outbox if _run_is_automatically_due(row, now=now)]
        unrelayed = [row for row in self._outbox if row.get("status") in {"pending", "failed_retryable"}]
        oldest = min((self._dt(row.get("created_at")) for row in unrelayed), default=None)
        return {
            "due_count": len(due),
            "failed_retryable_count": len([row for row in self._outbox if row.get("status") == "failed_retryable"]),
            "failed_terminal_count": len([row for row in self._outbox if row.get("status") == "failed_terminal"]),
            "running_count": len([row for row in self._outbox if row.get("status") == "running"]),
            "relayed_count": len([row for row in self._outbox if row.get("status") == "relayed"]),
            "oldest_unrelayed_age_seconds": max(0, int((now - oldest).total_seconds())) if oldest else 0,
        }

    def _filtered_events(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        rows = list(self._events)
        event_section = _text(filters.get("event_section"))
        for key in ("event_type", "aggregate_type", "aggregate_id", "subject_type", "subject_id", "trace_id", "idempotency_key", "source_module"):
            value = _text(filters.get(key))
            if value:
                rows = [row for row in rows if _text(row.get(key)) == value]
        if event_section and not _text(filters.get("event_type")):
            known_types = {event_type for values in EVENT_SECTION_EVENT_TYPES.values() for event_type in values}
            section_types = set(EVENT_SECTION_EVENT_TYPES.get(event_section, ()))
            if section_types:
                rows = [row for row in rows if _text(row.get("event_type")) in section_types]
            elif event_section == "other":
                rows = [row for row in rows if _text(row.get("event_type")) not in known_types]
        trace_hashes = set(_trace_hash_candidates(filters))
        if trace_hashes:
            rows = [
                row
                for row in rows
                if _text(
                    (((row.get("payload_json") or {}).get("broadcast_task") or {}).get("original_trace_hash"))
                    or (((row.get("payload_json") or {}).get("broadcast_task") or {}).get("trace_id_hash"))
                )
                in trace_hashes
            ]
        if _text(filters.get("created_from")):
            created_from = self._dt(filters.get("created_from"))
            rows = [row for row in rows if self._dt(row.get("created_at")) >= created_from]
        if _text(filters.get("created_to")):
            created_to = self._dt(filters.get("created_to"))
            rows = [row for row in rows if self._dt(row.get("created_at")) <= created_to]
        consumer_name = _text(filters.get("consumer_name"))
        if consumer_name:
            event_ids = {row.get("event_id") for row in self._runs if row.get("consumer_name") == consumer_name}
            rows = [row for row in rows if row.get("event_id") in event_ids]
        consumer_status = _text(filters.get("consumer_status"))
        if consumer_status:
            event_ids = {row.get("event_id") for row in self._runs if row.get("status") == consumer_status}
            rows = [row for row in rows if row.get("event_id") in event_ids]
        return rows

    def _filtered_runs(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        rows = list(self._runs)
        event_type = _text(filters.get("event_type"))
        for key in ("event_id", "consumer_name", "consumer_type", "status"):
            value = _text(filters.get(key))
            if value:
                rows = [row for row in rows if _text(row.get(key)) == value]
        if event_type:
            rows = [row for row in rows if (self.get_event(row.get("event_id") or "") or InternalEvent()).event_type == event_type]
        return rows

    def _event_consumer_pair_set(self, event_consumers: Any) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
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
                pairs.add((event_type, consumer_name))
        return pairs

    def _find_run(self, run_id: int) -> dict[str, Any] | None:
        for row in self._runs:
            if int(row.get("id") or 0) == int(run_id):
                return row
        return None

    def _find_outbox(self, outbox_id: int) -> dict[str, Any] | None:
        for row in self._outbox:
            if int(row.get("id") or 0) == int(outbox_id):
                return row
        return None

    def _mutate(self, run_id: int, **changes: Any) -> InternalEventConsumerRun | None:
        row = self._find_run(run_id)
        if not row:
            return None
        row.update(changes)
        row["updated_at"] = public_datetime(utcnow())
        return _public_run(row)

    def _dt(self, value: Any) -> datetime:
        text_value = _text(value)
        if not text_value:
            return datetime.min.replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
