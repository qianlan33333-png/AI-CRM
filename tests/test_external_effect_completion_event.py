from __future__ import annotations

import pytest

from aicrm_next.automation_agents import external_effect_continuation as automation_continuation
from aicrm_next.external_effect_composition import (
    EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST,
    build_external_effect_continuation_consumers,
)
from aicrm_next.external_push import external_effect_continuation as external_push_continuation
from aicrm_next.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform_foundation.external_effects.completion_events import (
    EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
    LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER,
    external_effect_continuation_consumer,
    register_external_effect_completed_consumers,
)
from aicrm_next.platform_foundation.external_effects.continuations import (
    ExternalEffectContinuation,
    ExternalEffectContinuationConsumer,
    ExternalEffectContinuationRegistry,
)
from aicrm_next.platform_foundation.external_effects.models import (
    WEBHOOK_GENERIC_PUSH,
    ExternalEffectDispatchResult,
)
from aicrm_next.platform_foundation.external_effects.repo import InMemoryExternalEffectRepository
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform_foundation.internal_events import (
    InMemoryInternalEventRepository,
    InternalEventConsumerRegistry,
    InternalEventService,
)
from aicrm_next.platform_foundation.internal_events.models import (
    InternalEvent,
    InternalEventConsumerRun,
    InternalEventCreateRequest,
)
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker


class _SuccessAdapter:
    def dispatch(self, _job) -> ExternalEffectDispatchResult:
        return ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            request_summary={"operation": "post"},
            response_summary={"status_code": 200},
            real_external_call_executed=True,
            provider_result_received=True,
        )


def _completed_job(
    repo: InMemoryExternalEffectRepository,
    *,
    key: str = "external-effect-completion-consumer",
):
    job = ExternalEffectService(repo).plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="completion_test",
        operation="post",
        target_type="test_target",
        target_id=key,
        idempotency_key=key,
        status="queued",
        execution_mode="execute",
    )
    adapters = ExternalEffectAdapterRegistry()
    adapters._adapters["completion_test"] = _SuccessAdapter()  # type: ignore[attr-defined]
    result = ExternalEffectWorker(repo, adapters).dispatch_one(job["id"])
    return result["job"], result["attempt"], repo.list_completion_events()[0]


def _fanout_registry(
    external_repo: InMemoryExternalEffectRepository,
    consumers: tuple[ExternalEffectContinuationConsumer, ...],
    *,
    provider_result_access_allowlist: frozenset[tuple[str, str]] = frozenset(),
) -> InternalEventConsumerRegistry:
    registry = InternalEventConsumerRegistry()
    register_external_effect_completed_consumers(
        registry,
        consumers=consumers,
        repository_factory=lambda: external_repo,
        legacy_continuation_registry_factory=lambda: ExternalEffectContinuationRegistry(
            item.continuation for item in consumers
        ),
        provider_result_access_allowlist=provider_result_access_allowlist,
    )
    registry.seal_fanout_contract()
    return registry


def _emit_completion(
    *,
    internal_repo: InMemoryInternalEventRepository,
    registry: InternalEventConsumerRegistry,
    event_payload: dict,
    suffix: str,
) -> str:
    emitted = InternalEventService(
        repository=internal_repo,
        consumer_registry=registry,
    ).emit_event(
        event_type=EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
        aggregate_type="external_effect_job",
        aggregate_id=str(event_payload["aggregate_id"]),
        idempotency_key=f"external-effect-completion-fanout:{suffix}",
        payload=dict(event_payload["payload"]),
    )
    return str(emitted["event"]["event_id"])


def test_completion_consumer_failure_does_not_change_provider_truth() -> None:
    repo = InMemoryExternalEffectRepository()
    job, attempt, event_payload = _completed_job(repo)
    calls: list[int] = []
    continuation = ExternalEffectContinuation(
        name="failing_projection",
        matches=lambda _job, _result: True,
        run=lambda current_job, _result: calls.append(current_job.id)
        or {"ok": False, "error": "projection unavailable"},
    )
    event = InternalEvent(
        event_id="iev_external_effect_completion",
        event_type=EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
        aggregate_type="external_effect_job",
        aggregate_id=str(job["id"]),
        payload_json=dict(event_payload["payload"]),
    )
    run = InternalEventConsumerRun(
        id=1,
        event_id=event.event_id,
        consumer_name="external_effect_test_continuation_consumer",
    )

    result = external_effect_continuation_consumer(
        event,
        run,
        repository_factory=lambda: repo,
        continuation=continuation,
    )

    assert calls == [job["id"]]
    assert result.status == "failed_retryable"
    assert result.error_code == "external_effect_continuation_failed"
    assert repo.get_job(job["id"]).status == "succeeded"  # type: ignore[union-attr]
    assert repo.get_attempt(attempt["attempt_id"]).status == "succeeded"  # type: ignore[union-attr]


def test_completion_consumer_no_match_is_a_successful_noop_without_loading_provider_result() -> None:
    class _RawResultGuardRepository(InMemoryExternalEffectRepository):
        def get_attempt_provider_result(self, attempt_id: str):
            raise AssertionError(f"non-applicable continuation read private provider result: {attempt_id}")

    repo = _RawResultGuardRepository()
    job, _attempt, event_payload = _completed_job(repo)
    event = InternalEvent(
        event_id="iev_external_effect_completion_noop",
        event_type=EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
        aggregate_type="external_effect_job",
        aggregate_id=str(job["id"]),
        payload_json=dict(event_payload["payload"]),
    )
    run = InternalEventConsumerRun(
        id=2,
        event_id=event.event_id,
        consumer_name="external_effect_noop_continuation_consumer",
    )
    continuation = ExternalEffectContinuation(
        name="not_applicable",
        matches=lambda _job, _result: False,
        run=lambda _job, _result: {"ok": True},
        requires_provider_result=True,
    )

    result = external_effect_continuation_consumer(
        event,
        run,
        repository_factory=lambda: repo,
        continuation=continuation,
    )

    assert result.status == "succeeded"
    assert result.response_summary["continuation"] == {
        "applicable": False,
        "ok": True,
        "continuation": "not_applicable",
        "reason": "continuation_not_applicable",
    }


def test_provider_result_access_requires_exact_consumer_and_continuation_allowlist() -> None:
    raw_continuation = ExternalEffectContinuation(
        name="unapproved_raw_reader",
        matches=lambda _job, _result: True,
        run=lambda _job, _result: {"ok": True},
        requires_provider_result=True,
    )
    consumer = ExternalEffectContinuationConsumer(
        "external_effect_unapproved_raw_consumer",
        raw_continuation,
    )

    with pytest.raises(ValueError, match="provider result access is not explicitly allowlisted"):
        register_external_effect_completed_consumers(
            InternalEventConsumerRegistry(),
            consumers=(consumer,),
            repository_factory=InMemoryExternalEffectRepository,
            legacy_continuation_registry_factory=ExternalEffectContinuationRegistry,
        )


def test_completion_consumer_rejects_cross_job_or_cross_tenant_attempt_linkage() -> None:
    class _RawResultGuardRepository(InMemoryExternalEffectRepository):
        def get_attempt_provider_result(self, attempt_id: str, *, job_id: int | None = None):
            raise AssertionError(f"mismatched completion read private provider result: {attempt_id}/{job_id}")

    repo = _RawResultGuardRepository()
    job_a, _attempt_a, event_a = _completed_job(repo, key="completion-linkage-a")
    _job_b, attempt_b, _event_b = _completed_job(repo, key="completion-linkage-b")
    payload = dict(event_a["payload"])
    payload["attempt_id"] = attempt_b["attempt_id"]
    event = InternalEvent(
        tenant_id="different-tenant",
        event_id="iev_external_effect_completion_mismatch",
        event_type=EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
        aggregate_type="external_effect_job",
        aggregate_id=str(job_a["id"]),
        payload_json=payload,
    )
    continuation = ExternalEffectContinuation(
        name="raw_reader",
        matches=lambda _job, _result: True,
        run=lambda _job, _result: {"ok": True},
        requires_provider_result=True,
    )

    result = external_effect_continuation_consumer(
        event,
        InternalEventConsumerRun(id=3, event_id=event.event_id, consumer_name="raw_reader_consumer"),
        repository_factory=lambda: repo,
        continuation=continuation,
    )

    assert result.status == "failed_terminal"
    assert result.error_code == "external_effect_completion_linkage_mismatch"
    assert result.response_summary["canonical_external_effect_linkage"] == {
        "attempt_job_matches": False,
        "attempt_is_job_terminal_attempt": False,
        "tenant_matches": False,
    }


def test_overlapping_continuations_each_receive_their_own_run_attempt_and_retry_budget() -> None:
    external_repo = InMemoryExternalEffectRepository()
    job, attempt, event_payload = _completed_job(external_repo)
    calls: list[str] = []
    consumers = (
        ExternalEffectContinuationConsumer(
            "external_effect_overlap_a_consumer",
            ExternalEffectContinuation(
                name="overlap_a",
                matches=lambda _job, _result: True,
                run=lambda _job, _result: calls.append("a") or {"ok": True},
            ),
            max_attempts=2,
        ),
        ExternalEffectContinuationConsumer(
            "external_effect_overlap_b_consumer",
            ExternalEffectContinuation(
                name="overlap_b",
                matches=lambda _job, _result: True,
                run=lambda _job, _result: calls.append("b") or {"ok": True},
            ),
            max_attempts=7,
        ),
    )
    registry = _fanout_registry(external_repo, consumers)
    internal_repo = InMemoryInternalEventRepository()
    event_id = _emit_completion(
        internal_repo=internal_repo,
        registry=registry,
        event_payload=event_payload,
        suffix="overlap-success",
    )
    runs, _ = internal_repo.list_consumer_runs({"event_id": event_id})

    worker = InternalEventWorker(repository=internal_repo, consumer_registry=registry)
    results = [worker.dispatch_one(run) for run in runs]
    updated_runs, _ = internal_repo.list_consumer_runs({"event_id": event_id})
    attempts = internal_repo.list_attempts(event_id=event_id)

    assert set(calls) == {"a", "b"}
    assert len(results) == len(updated_runs) == len(attempts) == 2
    assert {run.consumer_name: run.max_attempts for run in updated_runs} == {
        "external_effect_overlap_a_consumer": 2,
        "external_effect_overlap_b_consumer": 7,
    }
    assert {run.status for run in updated_runs} == {"succeeded"}
    assert {item.consumer_name for item in attempts} == {
        "external_effect_overlap_a_consumer",
        "external_effect_overlap_b_consumer",
    }
    assert repo_status(external_repo, job["id"], attempt["attempt_id"]) == ("succeeded", "succeeded")


def test_real_external_push_and_automation_predicates_both_execute(monkeypatch) -> None:
    external_push_calls: list[dict] = []
    automation_calls: list[str] = []

    class _ExternalPushRepository:
        def mark_delivery_succeeded_from_external_effect(self, delivery_id: str, **kwargs):
            external_push_calls.append({"delivery_id": delivery_id, **kwargs})
            return {"delivery_id": delivery_id, "status": "success", "response_status": kwargs.get("response_status")}

    class _AutomationWorker:
        def run_batch_and_enqueue_broadcast_jobs(self, batch_id: str, *, operator: str):
            automation_calls.append(batch_id)
            return {"ok": True, "batch_id": batch_id, "operator": operator}

    class _OverlapAdapter:
        def dispatch(self, _job):
            return ExternalEffectDispatchResult(
                status="succeeded",
                adapter_mode="execute",
                request_summary={"operation": "post"},
                response_summary={
                    "status_code": 202,
                    "automation_agent_batch_id": "agent_batch_overlap_001",
                },
                real_external_call_executed=True,
                provider_result_received=True,
            )

    monkeypatch.setattr(
        external_push_continuation.repo,
        "build_external_push_repository",
        lambda: _ExternalPushRepository(),
    )
    monkeypatch.setattr(automation_continuation, "AutomationAgentWorker", _AutomationWorker)
    external_repo = InMemoryExternalEffectRepository()
    planned = ExternalEffectService(external_repo).plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="overlap_adapter",
        operation="post",
        target_type="external_push_delivery",
        target_id="deliv_overlap_001",
        payload={"webhook_url": "/api/ai/agents/activation_agent/audience-webhook"},
        idempotency_key="external-effect-real-overlap",
        status="queued",
        execution_mode="execute",
    )
    adapters = ExternalEffectAdapterRegistry()
    adapters._adapters["overlap_adapter"] = _OverlapAdapter()  # type: ignore[attr-defined]
    completed = ExternalEffectWorker(external_repo, adapters).dispatch_one(planned["id"])
    event_payload = external_repo.list_completion_events()[0]
    consumers = build_external_effect_continuation_consumers()
    registry = _fanout_registry(
        external_repo,
        consumers,
        provider_result_access_allowlist=EXTERNAL_EFFECT_PROVIDER_RESULT_ACCESS_ALLOWLIST,
    )
    internal_repo = InMemoryInternalEventRepository()
    event_id = _emit_completion(
        internal_repo=internal_repo,
        registry=registry,
        event_payload=event_payload,
        suffix="real-overlap",
    )
    runs, _ = internal_repo.list_consumer_runs({"event_id": event_id})

    worker = InternalEventWorker(repository=internal_repo, consumer_registry=registry)
    for run in runs:
        worker.dispatch_one(run)
    updated, _ = internal_repo.list_consumer_runs({"event_id": event_id})

    assert len(updated) == len(consumers)
    assert {run.status for run in updated} == {"succeeded"}
    assert external_push_calls == [
        {
            "delivery_id": "deliv_overlap_001",
            "external_effect_job_id": planned["id"],
            "response_status": 202,
        }
    ]
    assert automation_calls == ["agent_batch_overlap_001"]
    assert repo_status(
        external_repo,
        planned["id"],
        completed["attempt"]["attempt_id"],
    ) == ("succeeded", "succeeded")


def test_failing_overlap_does_not_block_sibling_and_only_failed_run_retries(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    external_repo = InMemoryExternalEffectRepository()
    job, attempt, event_payload = _completed_job(external_repo)
    calls = {"failed": 0, "succeeded": 0}

    def fail(_job, _result):
        calls["failed"] += 1
        return {"ok": False, "error": "projection unavailable"}

    def succeed(_job, _result):
        calls["succeeded"] += 1
        return {"ok": True}

    consumers = (
        ExternalEffectContinuationConsumer(
            "external_effect_failing_overlap_consumer",
            ExternalEffectContinuation(name="failing_overlap", matches=lambda _job, _result: True, run=fail),
            max_attempts=3,
        ),
        ExternalEffectContinuationConsumer(
            "external_effect_succeeding_overlap_consumer",
            ExternalEffectContinuation(name="succeeding_overlap", matches=lambda _job, _result: True, run=succeed),
            max_attempts=3,
        ),
    )
    registry = _fanout_registry(external_repo, consumers)
    internal_repo = InMemoryInternalEventRepository()
    event_id = _emit_completion(
        internal_repo=internal_repo,
        registry=registry,
        event_payload=event_payload,
        suffix="overlap-failure",
    )
    runs, _ = internal_repo.list_consumer_runs({"event_id": event_id})
    worker = InternalEventWorker(repository=internal_repo, consumer_registry=registry)

    for run in runs:
        worker.dispatch_one(run)
    after_first, _ = internal_repo.list_consumer_runs({"event_id": event_id})
    by_name = {run.consumer_name: run for run in after_first}
    failed_name = "external_effect_failing_overlap_consumer"
    succeeded_name = "external_effect_succeeding_overlap_consumer"
    assert by_name[failed_name].status == "failed_retryable"
    assert by_name[succeeded_name].status == "succeeded"
    assert calls == {"failed": 1, "succeeded": 1}
    assert repo_status(external_repo, job["id"], attempt["attempt_id"]) == ("succeeded", "succeeded")

    retried = worker.dispatch_one_consumer(
        event_id,
        failed_name,
        dry_run=False,
        force=True,
        reason="test independent continuation retry",
    )

    assert retried["consumer_run"]["status"] == "failed_retryable"
    assert calls == {"failed": 2, "succeeded": 1}
    attempts = internal_repo.list_attempts(event_id=event_id)
    assert [item.consumer_name for item in attempts].count(failed_name) == 2
    assert [item.consumer_name for item in attempts].count(succeeded_name) == 1
    assert repo_status(external_repo, job["id"], attempt["attempt_id"]) == ("succeeded", "succeeded")


def test_legacy_first_match_consumer_is_handler_alias_only_and_can_finish_historical_run() -> None:
    external_repo = InMemoryExternalEffectRepository()
    job, _attempt, event_payload = _completed_job(external_repo)
    calls: list[str] = []
    consumers = (
        ExternalEffectContinuationConsumer(
            "external_effect_new_a_consumer",
            ExternalEffectContinuation(
                name="legacy_first",
                matches=lambda _job, _result: True,
                run=lambda _job, _result: calls.append("first") or {"ok": True},
            ),
        ),
        ExternalEffectContinuationConsumer(
            "external_effect_new_b_consumer",
            ExternalEffectContinuation(
                name="legacy_second",
                matches=lambda _job, _result: True,
                run=lambda _job, _result: calls.append("second") or {"ok": True},
            ),
        ),
    )
    registry = _fanout_registry(external_repo, consumers)
    assert LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER not in {
        item.consumer_name for item in registry.list_for_event_type(EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE)
    }
    internal_repo = InMemoryInternalEventRepository()
    event = internal_repo.create_event(
        InternalEventCreateRequest(
            event_type=EXTERNAL_EFFECT_COMPLETED_EVENT_TYPE,
            aggregate_type="external_effect_job",
            aggregate_id=str(job["id"]),
            idempotency_key="legacy-completion-event",
            payload=dict(event_payload["payload"]),
        )
    )
    historical = internal_repo.create_consumer_run(
        event=event,
        consumer_name=LEGACY_EXTERNAL_EFFECT_COMPLETION_CONSUMER,
        consumer_type="orchestration",
    )

    result = InternalEventWorker(repository=internal_repo, consumer_registry=registry).dispatch_one(historical)

    assert result["consumer_run"]["status"] == "succeeded"
    assert calls == ["first"]


def repo_status(repo: InMemoryExternalEffectRepository, job_id: int, attempt_id: str) -> tuple[str, str]:
    job = repo.get_job(job_id)
    attempt = repo.get_attempt(attempt_id)
    assert job is not None and attempt is not None
    return job.status, attempt.status
