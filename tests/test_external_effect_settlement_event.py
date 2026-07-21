from __future__ import annotations

from aicrm_next.platform_foundation.external_effects.continuations import (
    ExternalEffectContinuation,
    ExternalEffectContinuationConsumer,
)
from aicrm_next.platform_foundation.external_effects.models import WEBHOOK_GENERIC_PUSH
from aicrm_next.platform_foundation.external_effects.repo_memory import (
    InMemoryExternalEffectRepository,
)
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.settlement_events import (
    EXTERNAL_EFFECT_SETTLED_EVENT_TYPE,
    external_effect_settlement_consumer,
    register_external_effect_settled_consumers,
)
from aicrm_next.platform_foundation.internal_events import InternalEventConsumerRegistry
from aicrm_next.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerRun,
)


def _planned_job(repo: InMemoryExternalEffectRepository, key: str):
    planned = ExternalEffectService(repo).plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="settlement_test",
        operation="post",
        target_type="settlement_target",
        target_id=key,
        idempotency_key=key,
        status="queued",
        execution_mode="execute",
    )
    job = repo.get_job(int(planned["id"]))
    assert job is not None
    return job


def test_terminal_failure_emits_settlement_without_success_completion() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _planned_job(repo, "settlement-blocked")
    attempt = repo.record_attempt(
        job=job,
        status="blocked",
        adapter_mode="disabled",
        request_summary={"policy_checked": True},
        response_summary={"blocked": True},
        error_code="push_capability_disabled",
        error_message="disabled",
    )

    updated = repo.mark_blocked(
        job.id,
        attempt_id=attempt.attempt_id,
        error_code="push_capability_disabled",
        error_message="disabled",
    )

    assert updated is not None and updated.status == "blocked"
    assert repo.list_completion_events() == []
    events = repo.list_settlement_events()
    assert len(events) == 1
    assert events[0]["event_type"] == EXTERNAL_EFFECT_SETTLED_EVENT_TYPE
    assert events[0]["payload"] == {
        "job_id": job.id,
        "attempt_id": attempt.attempt_id,
        "effect_type": WEBHOOK_GENERIC_PUSH,
        "status": "blocked",
    }


def test_success_emits_both_completion_and_settlement() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _planned_job(repo, "settlement-success")
    attempt = repo.record_attempt(
        job=job,
        status="succeeded",
        adapter_mode="execute",
        request_summary={},
        response_summary={"status_code": 200},
    )

    updated = repo.mark_succeeded(job.id, attempt_id=attempt.attempt_id)

    assert updated is not None and updated.status == "succeeded"
    assert len(repo.list_completion_events()) == 1
    assert len(repo.list_settlement_events()) == 1


def test_pre_provider_cancel_emits_attemptless_settlement() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _planned_job(repo, "settlement-cancel")

    cancelled = repo.request_cancel(
        job.id,
        actor="operator-1",
        reason="plan disabled",
        expected_version=job.row_version,
    )

    assert cancelled is not None and cancelled.status == "cancelled"
    events = repo.list_settlement_events()
    assert len(events) == 1
    assert events[0]["payload"]["attempt_id"] == ""
    assert events[0]["payload"]["status"] == "cancelled"


def test_cancel_after_retryable_attempt_does_not_relabel_prior_attempt_as_cancelled() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _planned_job(repo, "settlement-cancel-after-retry")
    attempt = repo.record_attempt(
        job=job,
        status="failed_retryable",
        adapter_mode="execute",
        request_summary={},
        response_summary={"status_code": 503},
        error_code="provider_unavailable",
        error_message="retry later",
    )
    retry_at = attempt.started_at
    failed = repo.mark_failed_retryable(
        job.id,
        attempt_id=attempt.attempt_id,
        error_code="provider_unavailable",
        error_message="retry later",
        next_retry_at=retry_at,
    )
    assert failed is not None

    cancelled = repo.request_cancel(
        job.id,
        actor="operator-1",
        reason="plan disabled",
        expected_version=failed.row_version,
    )

    assert cancelled is not None and cancelled.status == "cancelled"
    event = repo.list_settlement_events()[0]
    assert event["payload"]["attempt_id"] == ""
    assert "row-version" in event["idempotency_key"]


def test_settlement_consumer_reloads_canonical_terminal_state() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _planned_job(repo, "settlement-consumer")
    attempt = repo.record_attempt(
        job=job,
        status="failed_terminal",
        adapter_mode="execute",
        request_summary={},
        response_summary={"status_code": 400},
        error_code="invalid_payload",
        error_message="invalid",
    )
    repo.mark_failed_terminal(
        job.id,
        attempt_id=attempt.attempt_id,
        error_code="invalid_payload",
        error_message="invalid",
    )
    payload = repo.list_settlement_events()[0]
    calls: list[tuple[int, str]] = []
    continuation = ExternalEffectContinuation(
        name="terminal_projection",
        matches=lambda _job, _result: True,
        run=lambda current, result: calls.append((current.id, result.status)) or {"ok": True},
    )
    event = InternalEvent(
        event_id="iev_settlement",
        event_type=EXTERNAL_EFFECT_SETTLED_EVENT_TYPE,
        aggregate_type="external_effect_job",
        aggregate_id=str(job.id),
        payload_json=dict(payload["payload"]),
    )

    result = external_effect_settlement_consumer(
        event,
        InternalEventConsumerRun(id=1, event_id=event.event_id, consumer_name="terminal_projection_consumer"),
        repository_factory=lambda: repo,
        continuation=continuation,
    )

    assert result.status == "succeeded"
    assert calls == [(job.id, "failed_terminal")]


def test_settlement_registration_rejects_provider_result_access() -> None:
    registry = InternalEventConsumerRegistry()
    consumer = ExternalEffectContinuationConsumer(
        "raw_terminal_consumer",
        ExternalEffectContinuation(
            name="raw_terminal",
            matches=lambda _job, _result: True,
            run=lambda _job, _result: {"ok": True},
            requires_provider_result=True,
        ),
    )

    try:
        register_external_effect_settled_consumers(
            registry,
            consumers=(consumer,),
            repository_factory=lambda: InMemoryExternalEffectRepository(),
        )
    except ValueError as exc:
        assert "cannot access provider result" in str(exc)
    else:  # pragma: no cover - hard safety assertion
        raise AssertionError("provider result access must be rejected for settlement consumers")
