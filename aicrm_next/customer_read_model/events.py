from __future__ import annotations

from typing import Any

from aicrm_next.platform_foundation.internal_events import (
    InternalEvent,
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
    current_internal_event_consumer_registry,
)

from .refresh_intents import (
    CUSTOMER_DIRTY_CONSUMER,
    CUSTOMER_REFRESH_COMPLETED_CONSUMER,
    CUSTOMER_REFRESH_COMPLETED_EVENT,
    CUSTOMER_REFRESH_CONSUMER,
    CUSTOMER_REFRESH_REQUESTED_EVENT,
    CUSTOMER_SOURCE_EVENTS,
    CustomerReadModelRefreshIntentService,
)
from .timeline_projection import (
    TIMELINE_PROJECTION_CONSUMER,
    TIMELINE_SOURCE_EVENTS,
    customer_timeline_projection_consumer,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def customer_read_model_dirty_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
) -> InternalEventConsumerResult:
    result = CustomerReadModelRefreshIntentService().request_refresh(
        source_event_key=event.event_id,
        source_event_type=event.event_type,
        parent_execution_id=event.execution_id,
    )
    return InternalEventConsumerResult(
        status="succeeded" if result.get("ok") else "failed_retryable",
        request_summary={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "consumer_name": run.consumer_name,
        },
        response_summary=_summary(result),
        result_summary=_summary(result),
        error_code="" if result.get("ok") else "customer_read_model_dirty_failed",
        error_message="" if result.get("ok") else _text(result.get("error")),
        retry_after_seconds=None if result.get("ok") else 30,
    )


def customer_read_model_refresh_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
) -> InternalEventConsumerResult:
    payload = dict(event.payload_json or {})
    generation = int(payload.get("generation") or 0)
    result = CustomerReadModelRefreshIntentService().process_requested(
        signal_generation=generation,
        owner_consumer_run_id=int(run.id or 0),
        owner_lease_token=_text(run.lease_token),
    )
    ok = bool(result.get("ok"))
    return InternalEventConsumerResult(
        status="succeeded" if ok else "failed_retryable",
        request_summary={
            "event_id": event.event_id,
            "consumer_name": run.consumer_name,
            "signal_generation": generation,
        },
        response_summary=_summary(result),
        result_summary=_summary(result),
        error_code="" if ok else _text(result.get("reason")) or "customer_read_model_refresh_failed",
        error_message="" if ok else _text(result.get("error")) or _text(result.get("reason")),
        retry_after_seconds=None if ok else 60,
    )


def customer_read_model_refresh_completed_audit_consumer(
    event: InternalEvent,
    run: InternalEventConsumerRun,
) -> InternalEventConsumerResult:
    """Persist the consumer-run receipt for a completed refresh event."""

    generation = int(dict(event.payload_json or {}).get("generation") or 0)
    receipt = {
        "ok": True,
        "acknowledged": True,
        "event_id": event.event_id,
        "consumer_name": run.consumer_name,
        "generation": generation,
    }
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "consumer_name": run.consumer_name,
            "generation": generation,
        },
        response_summary=receipt,
        result_summary=receipt,
    )


def register_customer_read_model_event_consumers(
    registry: InternalEventConsumerRegistry | None = None,
) -> None:
    registry = registry or current_internal_event_consumer_registry()
    for event_type in CUSTOMER_SOURCE_EVENTS:
        registry.register(
            event_type,
            CUSTOMER_DIRTY_CONSUMER,
            customer_read_model_dirty_consumer,
            consumer_type="projection",
            max_attempts=10,
        )
    for event_type in TIMELINE_SOURCE_EVENTS:
        registry.register(
            event_type,
            TIMELINE_PROJECTION_CONSUMER,
            customer_timeline_projection_consumer,
            consumer_type="projection",
            max_attempts=10,
        )
    registry.register(
        CUSTOMER_REFRESH_REQUESTED_EVENT,
        CUSTOMER_REFRESH_CONSUMER,
        customer_read_model_refresh_consumer,
        consumer_type="projection",
        max_attempts=5,
    )
    registry.register(
        CUSTOMER_REFRESH_COMPLETED_EVENT,
        CUSTOMER_REFRESH_COMPLETED_CONSUMER,
        customer_read_model_refresh_completed_audit_consumer,
        consumer_type="projection",
        max_attempts=3,
    )


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ok",
        "deduplicated",
        "generation",
        "signal_created",
        "claimed",
        "reason",
        "source_count",
        "target_count_before",
        "target_count_after",
        "duration_ms",
        "real_external_call_executed",
    )
    return {key: payload.get(key) for key in keys if key in payload}


__all__ = [
    "customer_read_model_dirty_consumer",
    "customer_read_model_refresh_completed_audit_consumer",
    "customer_read_model_refresh_consumer",
    "customer_timeline_projection_consumer",
    "register_customer_read_model_event_consumers",
]
