from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .consumer_registry import InternalEventConsumerRegistry, current_internal_event_consumer_registry
from .models import (
    DEFAULT_TENANT_ID,
    InternalEventConsumerSpec,
    InternalEventCreateRequest,
    public_datetime,
    utcnow,
)
from .repository import InternalEventRepository, build_internal_event_repository


def _text(value: Any) -> str:
    return str(value or "").strip()


def _idempotency_key(request: InternalEventCreateRequest) -> str:
    if _text(request.idempotency_key):
        return _text(request.idempotency_key)
    parts = (
        request.event_type,
        request.event_version,
        request.aggregate_type,
        request.aggregate_id,
        request.subject_type,
        request.subject_id,
        request.source_command_id or request.context.request_id or request.context.trace_id,
    )
    key = ":".join(_text(part) for part in parts if _text(part))
    return key or f"{request.event_type}:{request.aggregate_type}:{request.aggregate_id}"


def _payload_summary(request: InternalEventCreateRequest) -> dict[str, Any]:
    if request.payload_summary:
        return dict(request.payload_summary)
    summary: dict[str, Any] = {}
    for key, value in dict(request.payload or {}).items():
        if key.lower() in {"token", "secret", "password", "authorization", "access_token", "refresh_token"}:
            summary[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
        else:
            summary[key] = type(value).__name__
    return summary


def enqueue_transactional_internal_event_outbox(conn: Any, request: InternalEventCreateRequest) -> dict[str, Any]:
    """Insert an event envelope through the caller's open business transaction.

    This function deliberately never commits. The caller owns commit/rollback, so
    the business row and outbox row have one durability boundary.
    """

    tenant_id = _text(request.tenant_id) or DEFAULT_TENANT_ID
    key = _idempotency_key(request)
    occurred_at = public_datetime(request.occurred_at or utcnow())
    lane = "internal_financial" if _text(request.event_type).startswith(("payment.", "refund.", "order.")) else "internal_general"
    params = (
        tenant_id,
        "ieo_" + uuid4().hex,
        _text(request.event_type),
        int(request.event_version or 1),
        _text(request.aggregate_type),
        _text(request.aggregate_id),
        _text(request.subject_type),
        _text(request.subject_id),
        key,
        _text(request.context.actor_id),
        _text(request.context.actor_type) or "system",
        _text(request.source_module),
        _text(request.context.source_route),
        _text(request.source_command_id),
        _text(request.context.trace_id),
        _text(request.context.request_id),
        _text(request.correlation_id),
        _text(request.execution_id) or "exe_" + uuid4().hex,
        _text(request.parent_execution_id),
        lane,
        occurred_at,
        f"{_text(request.aggregate_type)}:{_text(request.aggregate_id)}",
        _text(request.event_type),
        occurred_at,
        json.dumps(dict(request.payload or {}), ensure_ascii=False, default=str, separators=(",", ":")),
        json.dumps(_payload_summary(request), ensure_ascii=False, default=str, separators=(",", ":")),
    )
    row = conn.execute(
        """
        INSERT INTO internal_event_outbox (
            tenant_id, outbox_id, event_type, event_version, aggregate_type, aggregate_id,
            subject_type, subject_id, idempotency_key, actor_id, actor_type,
            source_module, source_route, source_command_id, trace_id, request_id,
            correlation_id, execution_id, parent_execution_id, lane, available_at,
            ordering_key, fairness_key, policy_version,
            occurred_at, payload_json, payload_summary_json,
            status, attempt_count, max_attempts, created_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s::timestamptz, %s, %s,
            (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
            %s::timestamptz,
            %s::jsonb, %s::jsonb, 'pending', 0, 10, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
        RETURNING *
        """,
        params,
    ).fetchone()
    if row:
        return dict(row)
    existing = conn.execute(
        "SELECT * FROM internal_event_outbox WHERE tenant_id = %s AND idempotency_key = %s LIMIT 1",
        (tenant_id, key),
    ).fetchone()
    if not existing:
        raise RuntimeError("internal event outbox idempotent create failed")
    return dict(existing)


def enqueue_internal_event_outbox_in_session(
    session: Session,
    request: InternalEventCreateRequest,
) -> dict[str, Any]:
    """Insert an outbox envelope in a caller-owned SQLAlchemy transaction."""

    tenant_id = _text(request.tenant_id) or DEFAULT_TENANT_ID
    key = _idempotency_key(request)
    occurred_at = public_datetime(request.occurred_at or utcnow())
    lane = "internal_financial" if _text(request.event_type).startswith(("payment.", "refund.", "order.")) else "internal_general"
    params = {
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
        "execution_id": _text(request.execution_id) or "exe_" + uuid4().hex,
        "parent_execution_id": _text(request.parent_execution_id),
        "lane": lane,
        "available_at": occurred_at,
        "ordering_key": f"{_text(request.aggregate_type)}:{_text(request.aggregate_id)}",
        "fairness_key": _text(request.event_type),
        "occurred_at": occurred_at,
        "payload_json": json.dumps(dict(request.payload or {}), ensure_ascii=False, default=str, separators=(",", ":")),
        "payload_summary_json": json.dumps(_payload_summary(request), ensure_ascii=False, default=str, separators=(",", ":")),
    }
    row = (
        session.execute(
            text(
                """
                INSERT INTO internal_event_outbox (
                    tenant_id, outbox_id, event_type, event_version, aggregate_type, aggregate_id,
                    subject_type, subject_id, idempotency_key, actor_id, actor_type,
                    source_module, source_route, source_command_id, trace_id, request_id,
                    correlation_id, execution_id, parent_execution_id, lane, available_at,
                    ordering_key, fairness_key, policy_version,
                    occurred_at, payload_json, payload_summary_json,
                    status, attempt_count, max_attempts, created_at, updated_at
                ) VALUES (
                    :tenant_id, :outbox_id, :event_type, :event_version, :aggregate_type, :aggregate_id,
                    :subject_type, :subject_id, :idempotency_key, :actor_id, :actor_type,
                    :source_module, :source_route, :source_command_id, :trace_id, :request_id,
                    :correlation_id, :execution_id, :parent_execution_id, :lane,
                    CAST(:available_at AS timestamptz), :ordering_key, :fairness_key,
                    (SELECT policy_version FROM queue_runtime_control WHERE singleton = TRUE FOR SHARE),
                    CAST(:occurred_at AS timestamptz), CAST(:payload_json AS jsonb),
                    CAST(:payload_summary_json AS jsonb), 'pending', 0, 10,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                RETURNING *
                """
            ),
            params,
        )
        .mappings()
        .fetchone()
    )
    if row:
        return dict(row)
    existing = (
        session.execute(
            text("SELECT * FROM internal_event_outbox WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key LIMIT 1"),
            {"tenant_id": tenant_id, "idempotency_key": key},
        )
        .mappings()
        .fetchone()
    )
    if not existing:
        raise RuntimeError("internal event outbox idempotent create failed")
    return dict(existing)


def _retry_at(attempt_count: int):
    delays = (60, 300, 900, 3600, 6 * 3600)
    return utcnow() + timedelta(seconds=delays[min(max(int(attempt_count or 1) - 1, 0), len(delays) - 1)])


class InternalEventOutboxRelay:
    def __init__(
        self,
        repository: InternalEventRepository | None = None,
        consumer_registry: InternalEventConsumerRegistry | None = None,
        *,
        locked_by: str = "",
    ) -> None:
        self._repo = repository or build_internal_event_repository()
        self._registry = consumer_registry or current_internal_event_consumer_registry()
        if not self._registry.is_fanout_authoritative:
            raise RuntimeError("authoritative fanout registry required for internal event outbox relay")
        self._locked_by = locked_by or f"internal-event-relay-{uuid4().hex[:8]}"

    def _consumer_specs(self, event_type: str) -> list[InternalEventConsumerSpec]:
        manifest = self._registry.fanout_manifest_for(event_type)
        return [
            InternalEventConsumerSpec(
                consumer_name=str(consumer.get("consumer_name") or ""),
                consumer_type=str(consumer.get("consumer_type") or "projection"),
                max_attempts=max(1, int(consumer.get("max_attempts") or 5)),
            )
            for consumer in manifest["consumers"]
        ]

    def _fanout_manifest(self, event_type: str) -> dict[str, Any]:
        return self._registry.fanout_manifest_for(event_type)

    def preview_due(self, *, limit: int = 50) -> dict[str, Any]:
        records = self._repo.list_due_outbox(limit=limit)
        return {
            "ok": True,
            "dry_run": True,
            "candidate_count": len(records),
            "items": [
                {
                    "outbox_id": record.outbox_id,
                    "event_type": record.event_type,
                    "status": record.status,
                    "attempt_count": record.attempt_count,
                }
                for record in records
            ],
            "metrics": self._repo.outbox_metrics(),
            "real_external_call_executed": False,
        }

    def relay_due(self, *, limit: int = 50) -> dict[str, Any]:
        try:
            records = self._repo.acquire_due_outbox(limit=limit, locked_by=self._locked_by)
        except Exception as exc:
            return {
                "ok": False,
                "dry_run": False,
                "error": "outbox_acquire_failed",
                "error_class": exc.__class__.__name__,
                "items": [],
                "counts": {
                    "candidate_count": 0,
                    "relayed_count": 0,
                    "failed_retryable_count": 0,
                    "failed_terminal_count": 0,
                    "lost_lease_count": 0,
                    "unhandled_failure_count": 1,
                },
                "real_external_call_executed": False,
            }
        items: list[dict[str, Any]] = []
        counts = {
            "candidate_count": len(records),
            "relayed_count": 0,
            "failed_retryable_count": 0,
            "failed_terminal_count": 0,
            "lost_lease_count": 0,
            "unhandled_failure_count": 0,
        }
        for record in records:
            try:
                relayed = self._repo.relay_outbox(
                    record,
                    self._consumer_specs(record.event_type),
                    fanout_manifest=self._fanout_manifest(record.event_type),
                )
            except Exception as exc:
                try:
                    failed = self._repo.mark_outbox_failure(
                        record,
                        error_code="relay_exception",
                        error_message=exc.__class__.__name__,
                        next_retry_at=_retry_at(record.attempt_count),
                    )
                except Exception as persist_exc:
                    counts["unhandled_failure_count"] += 1
                    items.append(
                        {
                            "outbox_id": record.outbox_id,
                            "status": "failure_persist_failed",
                            "error_code": "outbox_failure_persist_failed",
                            "error_class": persist_exc.__class__.__name__,
                        }
                    )
                    continue
                status = failed.status if failed else "lost_lease"
                if status in {"failed_retryable", "failed_terminal"}:
                    counts[f"{status}_count"] += 1
                else:
                    counts["lost_lease_count"] += 1
                items.append({"outbox_id": record.outbox_id, "status": status, "error_code": "relay_exception"})
                continue
            if relayed is None:
                counts["lost_lease_count"] += 1
                items.append({"outbox_id": record.outbox_id, "status": "lost_lease"})
                continue
            updated, event, runs = relayed
            counts["relayed_count"] += 1
            items.append(
                {
                    "outbox_id": updated.outbox_id,
                    "status": updated.status,
                    "event_id": event.event_id,
                    "consumer_run_count": len(runs),
                }
            )
        failed_count = counts["failed_retryable_count"] + counts["failed_terminal_count"] + counts["lost_lease_count"] + counts["unhandled_failure_count"]
        return {
            "ok": failed_count == 0,
            "dry_run": False,
            "items": items,
            "counts": counts,
            "metrics": self._repo.outbox_metrics(),
            "real_external_call_executed": False,
        }

    def relay_claimed(self, record) -> dict[str, Any]:
        """Relay exactly one outbox row already leased by the lane runtime."""

        try:
            relayed = self._repo.relay_outbox(
                record,
                self._consumer_specs(record.event_type),
                fanout_manifest=self._fanout_manifest(record.event_type),
            )
        except Exception as exc:
            try:
                failed = self._repo.mark_outbox_failure(
                    record,
                    error_code="relay_exception",
                    error_message=exc.__class__.__name__,
                    next_retry_at=_retry_at(record.attempt_count),
                )
            except Exception as persist_exc:
                return {
                    "ok": False,
                    "outbox_id": record.outbox_id,
                    "status": "failure_persist_failed",
                    "error": "outbox_failure_persist_failed",
                    "error_class": persist_exc.__class__.__name__,
                    "real_external_call_executed": False,
                }
            return {
                "ok": False,
                "outbox_id": record.outbox_id,
                "status": failed.status if failed else "lost_lease",
                "error": "relay_exception",
                "error_class": exc.__class__.__name__,
                "real_external_call_executed": False,
            }
        if relayed is None:
            return {
                "ok": False,
                "outbox_id": record.outbox_id,
                "status": "lost_lease",
                "error": "lost_lease",
                "real_external_call_executed": False,
            }
        updated, event, runs = relayed
        return {
            "ok": True,
            "outbox_id": updated.outbox_id,
            "status": updated.status,
            "event_id": event.event_id,
            "consumer_run_count": len(runs),
            "real_external_call_executed": False,
        }
