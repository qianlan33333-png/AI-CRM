from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import partial
from typing import Any

from .continuations import (
    ExternalEffectContinuation,
    ExternalEffectContinuationConsumer,
    run_external_effect_continuation,
)
from .models import ExternalEffectAttempt, ExternalEffectDispatchResult, ExternalEffectJob


EXTERNAL_EFFECT_SETTLED_EVENT_TYPE = "external_effect.settled"
EXTERNAL_EFFECT_TERMINAL_STATUSES = frozenset(
    {
        "succeeded",
        "simulated",
        "unknown_after_dispatch",
        "failed_terminal",
        "blocked",
        "cancelled",
    }
)


def build_external_effect_settled_event(
    *,
    job: ExternalEffectJob,
    attempt: ExternalEffectAttempt | None = None,
):
    """Build the durable, payload-minimal hand-off for every terminal state."""

    if job.status not in EXTERNAL_EFFECT_TERMINAL_STATUSES:
        raise ValueError("external effect settlement requires a terminal job")
    from aicrm_next.platform_foundation.command_bus.models import CommandContext
    from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest

    attempt_id = str((attempt.attempt_id if attempt else "") or "").strip()
    settlement_key = attempt_id or f"row-version-{int(job.row_version or 0)}"
    payload = {
        "job_id": int(job.id),
        "attempt_id": attempt_id,
        "effect_type": job.effect_type,
        "status": job.status,
    }
    return InternalEventCreateRequest(
        event_type=EXTERNAL_EFFECT_SETTLED_EVENT_TYPE,
        aggregate_type="external_effect_job",
        aggregate_id=str(int(job.id)),
        subject_type=job.target_type,
        subject_id="",
        idempotency_key=f"external_effect.settled:{int(job.id)}:{job.status}:{settlement_key}",
        source_module="aicrm_next.platform_foundation.external_effects",
        source_command_id=job.source_command_id,
        correlation_id=job.correlation_id,
        parent_execution_id=job.execution_id,
        context=CommandContext(
            actor_id=job.actor_id,
            actor_type=job.actor_type,
            trace_id=job.trace_id,
            request_id=job.request_id,
            source_route=job.source_route,
        ),
        payload=payload,
        payload_summary=payload,
        tenant_id=job.tenant_id,
    )


def enqueue_external_effect_settled_event_in_session(
    session,
    *,
    job: ExternalEffectJob,
    attempt: ExternalEffectAttempt | None = None,
) -> None:
    """Persist a terminal hand-off in the caller's transaction."""

    from aicrm_next.platform_foundation.internal_events.outbox import (
        enqueue_internal_event_outbox_in_session,
    )

    enqueue_internal_event_outbox_in_session(
        session,
        build_external_effect_settled_event(job=job, attempt=attempt),
    )


def enqueue_external_effect_terminal_events_in_session(
    session,
    *,
    job: ExternalEffectJob,
    attempt: ExternalEffectAttempt | None = None,
) -> None:
    """Persist success-only completion first, then the all-terminal settlement."""

    if job.status not in EXTERNAL_EFFECT_TERMINAL_STATUSES:
        return
    if job.status == "succeeded" and attempt is not None:
        from aicrm_next.platform_foundation.internal_events.outbox import (
            enqueue_internal_event_outbox_in_session,
        )

        from .completion_events import build_external_effect_completed_event

        enqueue_internal_event_outbox_in_session(
            session,
            build_external_effect_completed_event(job=job, attempt=attempt),
        )
    enqueue_external_effect_settled_event_in_session(session, job=job, attempt=attempt)


def enqueue_external_effect_settled_rows_in_session(session, rows: Iterable[Any]) -> list[int]:
    """Project UPDATE .. RETURNING rows and enqueue one settlement per row."""

    from .repo_contract import _public_job

    job_ids: list[int] = []
    for row in rows:
        job = _public_job(dict(row))
        if job is None:
            raise RuntimeError("terminal external effect row could not be projected")
        enqueue_external_effect_settled_event_in_session(session, job=job)
        job_ids.append(int(job.id))
    return job_ids


def _load_canonical_settlement(
    event,
    run,
    *,
    repository_factory: Callable[[], Any],
):
    from aicrm_next.platform_foundation.internal_events.models import InternalEventConsumerResult

    payload = dict(event.payload_json or {})
    try:
        job_id = int(payload.get("job_id") or event.aggregate_id or 0)
    except (TypeError, ValueError):
        job_id = 0
    attempt_id = str(payload.get("attempt_id") or "").strip()
    expected_status = str(payload.get("status") or "").strip()
    request_summary = {
        "event_id": event.event_id,
        "consumer_run_id": int(run.id or 0),
        "job_id": job_id,
        "attempt_id": attempt_id,
        "status": expected_status,
    }
    if job_id <= 0 or expected_status not in EXTERNAL_EFFECT_TERMINAL_STATUSES:
        return None, None, request_summary, InternalEventConsumerResult(
            status="failed_terminal",
            request_summary=request_summary,
            response_summary={"canonical_external_effect_identifiers_present": False},
            error_code="external_effect_settlement_identifiers_missing",
            error_message="external effect settlement event is missing canonical terminal identifiers",
        )

    repository = repository_factory()
    job = repository.get_job(job_id)
    attempt = repository.get_attempt(attempt_id) if attempt_id else None
    if job is None or (attempt_id and attempt is None):
        return None, None, request_summary, InternalEventConsumerResult(
            status="failed_retryable",
            request_summary=request_summary,
            response_summary={
                "job_found": job is not None,
                "attempt_found": attempt is not None if attempt_id else True,
            },
            error_code="external_effect_settlement_record_not_found",
            error_message="canonical external effect settlement record is not visible yet",
            retry_after_seconds=30,
        )
    assert job is not None
    linkage = {
        "job_is_terminal": job.status in EXTERNAL_EFFECT_TERMINAL_STATUSES,
        "job_status_matches": job.status == expected_status,
        "tenant_matches": str(event.tenant_id or "").strip() == str(job.tenant_id or "").strip(),
        "attempt_job_matches": attempt is None or int(attempt.job_id or 0) == job_id,
        "attempt_is_job_terminal_attempt": attempt is None or str(job.last_attempt_id or "").strip() == attempt_id,
        "attempt_status_matches": attempt is None or attempt.status == job.status,
    }
    if not all(linkage.values()):
        return None, None, request_summary, InternalEventConsumerResult(
            status="failed_terminal",
            request_summary=request_summary,
            response_summary={"canonical_external_effect_linkage": linkage},
            error_code="external_effect_settlement_state_mismatch",
            error_message="settlement event does not match the canonical terminal job and attempt",
        )

    response_summary = dict(attempt.response_summary_json or {}) if attempt else dict(job.result_summary_json or {})
    dispatch_result = ExternalEffectDispatchResult(
        status=job.status,
        adapter_mode=attempt.adapter_mode if attempt else job.execution_mode,
        request_summary=dict(attempt.request_summary_json or {}) if attempt else {},
        response_summary=response_summary,
        provider_result={},
        error_code=attempt.error_code if attempt else job.last_error_code,
        error_message=attempt.error_message if attempt else job.last_error_message,
        real_external_call_executed=bool(job.side_effect_executed),
        provider_result_received=bool(job.provider_result_received),
    )
    return job, dispatch_result, request_summary, None


def external_effect_settlement_consumer(
    event,
    run,
    *,
    repository_factory: Callable[[], Any],
    continuation: ExternalEffectContinuation,
):
    from aicrm_next.platform_foundation.internal_events.models import InternalEventConsumerResult

    job, dispatch_result, request_summary, error = _load_canonical_settlement(
        event,
        run,
        repository_factory=repository_factory,
    )
    if error is not None:
        return error
    assert job is not None and dispatch_result is not None
    result = run_external_effect_continuation(continuation, job, dispatch_result)
    if result.get("applicable") and not result.get("ok"):
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary=request_summary,
            response_summary={"settlement": result},
            result_summary={"settlement": result},
            error_code="external_effect_settlement_projection_failed",
            error_message=str(result.get("error") or "external effect settlement projection failed")[:500],
        )
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary=request_summary,
        response_summary={"settlement": result},
        result_summary={"settlement": result},
    )


def register_external_effect_settled_consumers(
    registry,
    *,
    consumers: Iterable[ExternalEffectContinuationConsumer],
    repository_factory: Callable[[], Any],
) -> None:
    registered = tuple(consumers)
    names = tuple(item.consumer_name for item in registered)
    if not names or len(names) != len(set(names)):
        raise ValueError("external effect settlement consumer names must be non-empty and unique")
    if any(item.continuation.requires_provider_result for item in registered):
        raise ValueError("external effect settlement consumers cannot access provider result payloads")
    for consumer in registered:
        registry.register(
            EXTERNAL_EFFECT_SETTLED_EVENT_TYPE,
            consumer.consumer_name,
            partial(
                external_effect_settlement_consumer,
                repository_factory=repository_factory,
                continuation=consumer.continuation,
            ),
            consumer_type="projection",
            max_attempts=consumer.max_attempts,
        )


__all__ = [
    "EXTERNAL_EFFECT_SETTLED_EVENT_TYPE",
    "EXTERNAL_EFFECT_TERMINAL_STATUSES",
    "build_external_effect_settled_event",
    "enqueue_external_effect_settled_event_in_session",
    "enqueue_external_effect_settled_rows_in_session",
    "enqueue_external_effect_terminal_events_in_session",
    "external_effect_settlement_consumer",
    "register_external_effect_settled_consumers",
]
