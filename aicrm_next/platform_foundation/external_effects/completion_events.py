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

EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE = "external_effect.completed"
LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER = "external_effect_completion_continuation_consumer"


def build_external_effect_completed_event(
    *,
    job: ExternalEffectJob,
    attempt: ExternalEffectAttempt,
):
    """Build the durable hand-off for post-provider projections.

    The event intentionally carries identifiers and non-secret state only.
    Consumers must reload the canonical job and attempt before projecting.
    """

    from aicrm_next.platform_foundation.command_bus.models import CommandContext
    from aicrm_next.platform_foundation.internal_events.models import InternalEventCreateRequest

    return InternalEventCreateRequest(
        event_type=EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
        aggregate_type="external_effect_job",
        aggregate_id=str(int(job.id)),
        subject_type=job.target_type,
        subject_id="",
        idempotency_key=f"external_effect.completed:{int(job.id)}:{attempt.attempt_id}",
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
        payload={
            "job_id": int(job.id),
            "attempt_id": attempt.attempt_id,
            "effect_type": job.effect_type,
            "status": job.status,
        },
        payload_summary={
            "job_id": int(job.id),
            "attempt_id": attempt.attempt_id,
            "effect_type": job.effect_type,
            "status": job.status,
        },
        tenant_id=job.tenant_id,
    )


def _load_canonical_completion(
    event,
    run,
    *,
    repository_factory: Callable[[], Any],
):
    """Reload provider truth without exposing the restricted provider payload."""

    from aicrm_next.platform_foundation.internal_events.models import InternalEventConsumerResult

    payload = dict(event.payload_json or {})
    try:
        job_id = int(payload.get("job_id") or event.aggregate_id or 0)
    except (TypeError, ValueError):
        job_id = 0
    attempt_id = str(payload.get("attempt_id") or "").strip()
    request_summary = {
        "event_id": event.event_id,
        "consumer_run_id": int(run.id or 0),
        "job_id": job_id,
        "attempt_id": attempt_id,
    }
    if job_id <= 0 or not attempt_id:
        return None, None, request_summary, None, InternalEventConsumerResult(
            status="failed_terminal",
            request_summary=request_summary,
            response_summary={"canonical_external_effect_identifiers_present": False},
            error_code="external_effect_completion_identifiers_missing",
            error_message="external effect completion event is missing job_id or attempt_id",
        )

    repository = repository_factory()
    job = repository.get_job(job_id)
    attempt = repository.get_attempt(attempt_id)
    if job is None or attempt is None:
        return None, None, request_summary, None, InternalEventConsumerResult(
            status="failed_retryable",
            request_summary=request_summary,
            response_summary={
                "job_found": job is not None,
                "attempt_found": attempt is not None,
            },
            error_code="external_effect_completion_record_not_found",
            error_message="canonical external effect completion record is not visible yet",
            retry_after_seconds=30,
        )
    if job.status != "succeeded" or attempt.status != "succeeded":
        return None, None, request_summary, None, InternalEventConsumerResult(
            status="failed_terminal",
            request_summary=request_summary,
            response_summary={"job_status": job.status, "attempt_status": attempt.status},
            error_code="external_effect_completion_state_mismatch",
            error_message="completion event does not point to a succeeded job and attempt",
        )
    linkage = {
        "attempt_job_matches": int(attempt.job_id or 0) == job_id,
        "attempt_is_job_terminal_attempt": str(job.last_attempt_id or "").strip() == attempt_id,
        "tenant_matches": str(event.tenant_id or "").strip() == str(job.tenant_id or "").strip(),
    }
    if not all(linkage.values()):
        return None, None, request_summary, None, InternalEventConsumerResult(
            status="failed_terminal",
            request_summary=request_summary,
            response_summary={"canonical_external_effect_linkage": linkage},
            error_code="external_effect_completion_linkage_mismatch",
            error_message="completion event job, attempt, or tenant linkage is inconsistent",
        )

    response_summary = dict(attempt.response_summary_json or {})
    provider_result_loader = getattr(repository, "get_attempt_provider_result", None)
    load_provider_result = (
        (lambda: dict(provider_result_loader(attempt_id, job_id=job_id) or {}))
        if callable(provider_result_loader)
        else (lambda: {})
    )
    dispatch_result = ExternalEffectDispatchResult(
        status="succeeded",
        adapter_mode=attempt.adapter_mode,
        request_summary=dict(attempt.request_summary_json or {}),
        response_summary=response_summary,
        provider_result={},
        error_code=attempt.error_code,
        error_message=attempt.error_message,
        real_external_call_executed=bool(response_summary.get("real_external_call_executed")),
        provider_result_received=bool(response_summary.get("provider_result_received")),
    )
    return job, dispatch_result, request_summary, load_provider_result, None


def _completion_consumer_result(*, request_summary: dict[str, Any], continuation: dict[str, Any]):
    from aicrm_next.platform_foundation.internal_events.models import InternalEventConsumerResult

    if continuation.get("applicable") and not continuation.get("ok"):
        return InternalEventConsumerResult(
            status="failed_retryable",
            request_summary=request_summary,
            response_summary={"continuation": continuation},
            result_summary={"continuation": continuation},
            error_code="external_effect_continuation_failed",
            error_message=str(continuation.get("error") or "external effect continuation failed")[:500],
        )
    return InternalEventConsumerResult(
        status="succeeded",
        request_summary=request_summary,
        response_summary={"continuation": continuation},
        result_summary={"continuation": continuation},
    )


def external_effect_continuation_consumer(
    event,
    run,
    *,
    repository_factory: Callable[[], Any],
    continuation: ExternalEffectContinuation,
):
    """Execute one named continuation in one independent consumer run."""

    job, dispatch_result, request_summary, provider_result_loader, error = _load_canonical_completion(
        event,
        run,
        repository_factory=repository_factory,
    )
    if error is not None:
        return error
    assert job is not None and dispatch_result is not None and provider_result_loader is not None
    result = run_external_effect_continuation(
        continuation,
        job,
        dispatch_result,
        provider_result_loader=provider_result_loader,
    )
    return _completion_consumer_result(request_summary=request_summary, continuation=result)


def external_effect_completion_consumer(
    event,
    run,
    *,
    repository_factory: Callable[[], Any],
    continuation_registry_factory: Callable[[], Any],
):
    """Handle only historical runs created under the first-match contract."""

    job, dispatch_result, request_summary, provider_result_loader, error = _load_canonical_completion(
        event,
        run,
        repository_factory=repository_factory,
    )
    if error is not None:
        return error
    assert job is not None and dispatch_result is not None and provider_result_loader is not None
    result = continuation_registry_factory().run(
        job,
        dispatch_result,
        provider_result_loader=provider_result_loader,
    )
    return _completion_consumer_result(request_summary=request_summary, continuation=result)


def register_external_effect_completed_consumers(
    registry,
    *,
    consumers: Iterable[ExternalEffectContinuationConsumer],
    repository_factory: Callable[[], Any],
    legacy_continuation_registry_factory: Callable[[], Any],
    provider_result_access_allowlist: Iterable[tuple[str, str]] = (),
) -> None:
    """Register the authoritative fan-out plus a non-fan-out history alias."""

    registered = tuple(consumers)
    names = tuple(item.consumer_name for item in registered)
    if len(names) != len(set(names)):
        raise ValueError("external effect continuation consumer names must be unique")
    if LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER in names:
        raise ValueError("legacy external effect completion consumer cannot enter new fanout")
    requested_provider_result_access = {
        (item.consumer_name, item.continuation.name)
        for item in registered
        if item.continuation.requires_provider_result
    }
    allowed_provider_result_access = {
        (str(consumer_name or "").strip(), str(continuation_name or "").strip())
        for consumer_name, continuation_name in provider_result_access_allowlist
    }
    if requested_provider_result_access != allowed_provider_result_access:
        raise ValueError("external effect continuation provider result access is not explicitly allowlisted")
    for consumer in registered:
        registry.register(
            EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
            consumer.consumer_name,
            partial(
                external_effect_continuation_consumer,
                repository_factory=repository_factory,
                continuation=consumer.continuation,
            ),
            consumer_type="orchestration",
            max_attempts=consumer.max_attempts,
        )
    registry.register_handler_alias(
        EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
        LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER,
        partial(
            external_effect_completion_consumer,
            repository_factory=repository_factory,
            continuation_registry_factory=legacy_continuation_registry_factory,
        ),
    )


__all__ = [
    "EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE",
    "LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER",
    "build_external_effect_completed_event",
    "external_effect_completion_consumer",
    "external_effect_continuation_consumer",
    "register_external_effect_completed_consumers",
]
