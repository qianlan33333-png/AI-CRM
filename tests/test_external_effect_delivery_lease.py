from __future__ import annotations

import os
from datetime import timedelta
from threading import Event, Thread
from typing import Any
from uuid import uuid4

import pytest

from aicrm_next.platform_foundation.external_effects.adapters import ExternalEffectAdapterRegistry
from aicrm_next.platform_foundation.external_effects.models import (
    WEBHOOK_GENERIC_PUSH,
    ExternalEffectDispatchResult,
    utcnow,
)
from aicrm_next.platform_foundation.external_effects.repo import (
    InMemoryExternalEffectRepository,
    SQLAlchemyExternalEffectRepository,
)
from aicrm_next.platform_foundation.external_effects.service import ExternalEffectService
from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.shared.db_session import get_session_factory


def _database_url() -> str:
    return str(os.getenv("AICRM_TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def _plan(
    repo,
    *,
    key: str,
    adapter_name: str = "test_adapter",
    max_attempts: int = 5,
) -> dict[str, Any]:
    return ExternalEffectService(repo).plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name=adapter_name,
        operation="post",
        target_type="test_target",
        target_id=key,
        payload={"body": {"key": key}},
        business_type="r07_test",
        business_id=key,
        source_module="tests.test_external_effect_delivery_lease",
        idempotency_key=key,
        execution_mode="execute",
        status="queued",
        max_attempts=max_attempts,
    )


def _registry(adapter: Any) -> ExternalEffectAdapterRegistry:
    registry = ExternalEffectAdapterRegistry()
    registry._adapters["test_adapter"] = adapter  # type: ignore[attr-defined]
    return registry


class _StaticAdapter:
    def __init__(self, result: ExternalEffectDispatchResult) -> None:
        self.result = result
        self.calls = 0

    def dispatch(self, job) -> ExternalEffectDispatchResult:
        self.calls += 1
        return self.result


def _success_result() -> ExternalEffectDispatchResult:
    return ExternalEffectDispatchResult(
        status="succeeded",
        adapter_mode="execute",
        request_summary={"operation": "post"},
        response_summary={"status_code": 200, "real_external_call_executed": True},
        real_external_call_executed=True,
        provider_result_received=True,
    )


def test_fake_success_is_persisted_as_simulated_without_provider_success() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-fake-simulated")
    adapter = _StaticAdapter(
        ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="fake",
            response_summary={"mode": "fake", "real_external_call_executed": False},
            real_external_call_executed=False,
        )
    )

    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-fake").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])
    attempts = repo.list_attempts(job["id"])

    assert result["ok"] is True
    assert adapter.calls == 1
    assert updated is not None
    assert updated.status == "simulated"
    assert updated.side_effect_executed is False
    assert updated.provider_result_received is False
    assert attempts[0].status == "simulated"


def test_dry_run_is_reported_as_skipped_without_mutating_job_or_attempts() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-dry-run-skipped")
    before = repo.get_job(job["id"])

    result = ExternalEffectWorker(repo, locked_by="worker-preview").run_due(batch_size=1, dry_run=True)

    assert result["counts"]["candidate_count"] == 1
    assert result["counts"]["skipped_count"] == 1
    assert result["counts"]["processed_count"] == 0
    assert result["items"][0]["dispatch_status"] == "skipped"
    assert result["items"][0]["preview_only"] is True
    assert result["real_external_call_executed"] is False
    assert repo.get_job(job["id"]) == before
    assert repo.list_attempts(job["id"]) == []


def test_success_requires_real_execution_and_provider_evidence() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-success-without-evidence")
    adapter = _StaticAdapter(
        ExternalEffectDispatchResult(
            status="succeeded",
            adapter_mode="execute",
            response_summary={"real_external_call_executed": True},
            real_external_call_executed=True,
            provider_result_received=False,
        )
    )

    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-missing-evidence").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])

    assert result["ok"] is False
    assert updated is not None
    assert updated.status == "unknown_after_dispatch"
    assert updated.reconciliation_required is True
    assert updated.side_effect_executed is True
    assert updated.provider_result_received is False


def test_timeout_after_dispatch_becomes_unknown_and_is_not_due_again() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-timeout-unknown")
    adapter = _StaticAdapter(
        ExternalEffectDispatchResult(
            status="failed_retryable",
            adapter_mode="execute",
            response_summary={"real_external_call_executed": True},
            error_code="timeout",
            error_message="provider response timed out",
            real_external_call_executed=True,
        )
    )

    ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-timeout").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])

    assert updated is not None
    assert updated.status == "unknown_after_dispatch"
    assert repo.list_due_jobs(limit=10) == []
    assert adapter.calls == 1


def test_provider_rejection_with_response_remains_safely_retryable() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-http-500-retryable")
    adapter = _StaticAdapter(
        ExternalEffectDispatchResult(
            status="failed_retryable",
            adapter_mode="execute",
            response_summary={"status_code": 500, "real_external_call_executed": True},
            error_code="http_5xx",
            error_message="provider rejected request",
            real_external_call_executed=True,
            provider_result_received=True,
        )
    )

    ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-500").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])

    assert updated is not None
    assert updated.status == "failed_retryable"
    assert updated.next_retry_at
    assert updated.reconciliation_required is False


def test_last_attempt_429_keeps_scope_cooldown_deadline_for_repository() -> None:
    class CapturingRepository(InMemoryExternalEffectRepository):
        def __init__(self) -> None:
            super().__init__()
            self.completed_result = None
            self.completed_retry_at = None

        def complete_dispatch(self, *, job, result, next_retry_at=None):
            self.completed_result = result
            self.completed_retry_at = next_retry_at
            return super().complete_dispatch(job=job, result=result, next_retry_at=next_retry_at)

    repo = CapturingRepository()
    job = _plan(repo, key="r07-final-429", max_attempts=1)
    adapter = _StaticAdapter(
        ExternalEffectDispatchResult(
            status="failed_retryable",
            adapter_mode="execute",
            response_summary={"status_code": 429, "retry_after_seconds": 7},
            error_code="http_429",
            error_message="provider throttled",
            retry_after_seconds=7,
            real_external_call_executed=True,
            provider_result_received=True,
        )
    )

    before = utcnow()
    ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-final-429").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])

    assert updated is not None and updated.status == "failed_terminal"
    assert repo.completed_result is not None and repo.completed_result.status == "failed_terminal"
    assert repo.completed_retry_at is not None
    assert before + timedelta(seconds=6) <= repo.completed_retry_at <= before + timedelta(seconds=8)


class _BlockingAdapter:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.calls = 0

    def dispatch(self, job) -> ExternalEffectDispatchResult:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=5)
        return _success_result()


class _InspectDurableAttemptAdapter:
    def __init__(self, repository: InMemoryExternalEffectRepository) -> None:
        self._repository = repository
        self.seen_attempt_id = ""

    def dispatch(self, job) -> ExternalEffectDispatchResult:
        attempts = self._repository.list_attempts(job.id)
        assert len(attempts) == 1
        assert attempts[0].status == "dispatching"
        assert attempts[0].completed_at == ""
        assert attempts[0].attempt_id == job.last_attempt_id
        assert attempts[0].request_summary_json["provider_boundary_crossed"] is True
        self.seen_attempt_id = attempts[0].attempt_id
        return _success_result()


class _CancelDuringDispatchAdapter:
    def __init__(self, service: ExternalEffectService) -> None:
        self._service = service
        self.cancel_result = None
        self.calls = 0

    def dispatch(self, job) -> ExternalEffectDispatchResult:
        self.calls += 1
        self.cancel_result = self._service.cancel(
            job.id,
            actor="operator",
            reason="too late after provider boundary",
            expected_version=job.row_version,
        )
        return _success_result()


def test_realtime_and_worker_share_one_claim_provider_call_at_most_once() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-concurrent-claim")
    adapter = _BlockingAdapter()
    registry = _registry(adapter)
    first_result: dict[str, Any] = {}

    def run_first() -> None:
        first_result.update(ExternalEffectWorker(repo, registry, locked_by="worker-a").dispatch_one(job["id"]))

    thread = Thread(target=run_first)
    thread.start()
    assert adapter.entered.wait(timeout=5)
    second = ExternalEffectWorker(repo, registry, locked_by="worker-b").dispatch_one(job["id"])
    adapter.release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert first_result["job"]["status"] == "succeeded"
    assert second["error"] == "not_claimed"
    assert adapter.calls == 1
    assert len(repo.list_attempts(job["id"])) == 1


def test_provider_attempt_is_durable_before_adapter_and_same_attempt_is_completed() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-provider-boundary")
    adapter = _InspectDurableAttemptAdapter(repo)

    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-boundary").dispatch_one(job["id"])
    attempts = repo.list_attempts(job["id"])

    assert result["ok"] is True
    assert len(attempts) == 1
    assert attempts[0].attempt_id == adapter.seen_attempt_id
    assert attempts[0].status == "succeeded"
    assert attempts[0].completed_at
    assert result["completion_event_queued"] is True
    assert repo.list_completion_events()[0]["payload"]["attempt_id"] == attempts[0].attempt_id


def test_repository_rejects_provider_success_without_durable_begin_attempt() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-provider-boundary-required")
    claimed = repo.acquire_job(job["id"], locked_by="worker-boundary-required")
    assert claimed is not None

    completed = repo.complete_dispatch(job=claimed, result=_success_result())

    assert completed is None
    assert repo.list_attempts(job["id"]) == []
    assert repo.get_job(job["id"]).status == "dispatching"  # type: ignore[union-attr]


def test_cancel_uses_row_version_cas_and_settles_before_provider_boundary() -> None:
    repo = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repo)
    job = _plan(repo, key="r07-cancel-before-provider")
    claimed = repo.acquire_job(job["id"], locked_by="worker-cancel")
    assert claimed is not None

    assert service.cancel(job["id"], expected_version=claimed.row_version + 1) is None
    requested = service.cancel(
        job["id"],
        actor="operator",
        reason="stop before provider",
        expected_version=claimed.row_version,
    )

    assert requested is not None
    assert requested.status == "dispatching"
    assert requested.row_version == claimed.row_version + 1
    assert requested.cancel_requested_by == "operator"
    assert requested.cancel_reason == "stop before provider"
    adapter = _StaticAdapter(_success_result())
    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-cancel")._dispatch_claimed(claimed)

    assert result["ok"] is True
    assert result["job"]["status"] == "cancelled"
    assert result["job"]["row_version"] == requested.row_version + 1
    assert adapter.calls == 0
    assert repo.list_attempts(job["id"]) == []


def test_cancel_after_provider_boundary_cannot_overwrite_provider_success() -> None:
    repo = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repo)
    job = _plan(repo, key="r07-cancel-after-boundary")
    adapter = _CancelDuringDispatchAdapter(service)

    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-cancel-late").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])

    assert adapter.calls == 1
    assert adapter.cancel_result is not None
    assert adapter.cancel_result.status == "dispatching"
    assert result["ok"] is True
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.cancel_requested_at
    assert updated.cancelled_at == ""


def test_exhausted_retryable_job_is_not_due_but_manual_retry_extends_one_attempt() -> None:
    repo = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repo)
    job = service.plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="test_adapter",
        operation="post",
        target_type="test_target",
        target_id="r07-max-attempts",
        idempotency_key="r07-max-attempts",
        execution_mode="execute",
        status="queued",
        max_attempts=1,
    )
    row = repo._find(job["id"])
    assert row is not None
    row.update({"status": "failed_retryable", "attempt_count": 1})

    assert repo.list_due_jobs(limit=10) == []
    not_claimed = ExternalEffectWorker(repo, _registry(_StaticAdapter(_success_result()))).dispatch_one(job["id"])
    assert not_claimed["error"] == "not_claimed"

    retried = service.retry(job["id"], actor="operator", reason="one audited retry")
    assert retried is not None
    assert retried.status == "queued"
    assert retried.max_attempts == 2
    assert [item.id for item in repo.list_due_jobs(limit=10)] == [job["id"]]


def test_explicit_dispatch_respects_future_schedule() -> None:
    repo = InMemoryExternalEffectRepository()
    job = ExternalEffectService(repo).plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="test_adapter",
        operation="post",
        target_type="test_target",
        target_id="future-explicit-dispatch",
        idempotency_key="r07-future-explicit-dispatch",
        scheduled_at=utcnow() + timedelta(hours=1),
        status="queued",
        execution_mode="execute",
    )
    adapter = _StaticAdapter(_success_result())

    assert repo.list_due_jobs(limit=10) == []
    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="trusted-explicit-worker").dispatch_one(job["id"])

    assert result["ok"] is False
    assert result["error"] == "not_claimed"
    assert result["job"]["status"] == "queued"
    assert adapter.calls == 0


class _LoseLeaseOnCompleteRepository(InMemoryExternalEffectRepository):
    def complete_dispatch(self, *, job, result, next_retry_at=None):
        row = self._find(job.id)
        assert row is not None
        row["lease_token"] = "eel_new_owner"
        row["locked_by"] = "worker-new"
        return super().complete_dispatch(job=job, result=result, next_retry_at=next_retry_at)


def test_lost_lease_preserves_durable_open_attempt_without_writing_result() -> None:
    repo = _LoseLeaseOnCompleteRepository()
    job = _plan(repo, key="r07-lost-lease")
    adapter = _StaticAdapter(_success_result())

    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-old").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])

    assert result["error"] == "lost_lease"
    assert adapter.calls == 1
    attempts = repo.list_attempts(job["id"])
    assert len(attempts) == 1
    assert attempts[0].status == "dispatching"
    assert attempts[0].completed_at == ""
    assert updated is not None
    assert updated.status == "dispatching"
    assert updated.lease_token == "eel_new_owner"


class _FailResultPersistenceRepository(InMemoryExternalEffectRepository):
    def complete_dispatch(self, *, job, result, next_retry_at=None):
        raise RuntimeError("injected result persistence failure")


def test_provider_success_then_result_persistence_failure_is_unknown_not_retried() -> None:
    repo = _FailResultPersistenceRepository()
    job = _plan(repo, key="r07-result-persistence-failure")
    adapter = _StaticAdapter(_success_result())

    result = ExternalEffectWorker(repo, _registry(adapter), locked_by="worker-persist").dispatch_one(job["id"])
    updated = repo.get_job(job["id"])

    assert result["error"] == "result_persistence_failed"
    assert adapter.calls == 1
    assert updated is not None
    assert updated.status == "unknown_after_dispatch"
    assert updated.reconciliation_required is True
    assert repo.list_due_jobs(limit=10) == []
    attempts = repo.list_attempts(job["id"])
    assert len(attempts) == 1
    assert attempts[0].status == "unknown_after_dispatch"
    assert attempts[0].error_code == "result_persistence_failed"


def test_stale_pre_provider_dispatch_is_safely_requeued() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-stale-dispatching")
    claimed = repo.acquire_job(job["id"], locked_by="worker-crashed")
    assert claimed is not None
    row = repo._find(job["id"])
    assert row is not None
    row["lease_expires_at"] = "2000-01-01T00:00:00Z"

    count = repo.quarantine_stale_dispatching()
    updated = repo.get_job(job["id"])

    assert count == 1
    assert updated is not None
    assert updated.status == "queued"
    assert updated.reconciliation_required is False
    assert updated.attempt_count == 0
    assert [item.id for item in repo.list_due_jobs(limit=10)] == [job["id"]]


def test_stale_post_provider_dispatch_is_quarantined_as_unknown() -> None:
    repo = InMemoryExternalEffectRepository()
    job = _plan(repo, key="r07-stale-post-provider")
    claimed = repo.acquire_job(job["id"], locked_by="worker-crashed")
    assert claimed is not None
    begun = repo.begin_provider_attempt(job=claimed, request_summary={"provider_call": True})
    assert begun is not None
    row = repo._find(job["id"])
    assert row is not None
    row["lease_expires_at"] = "2000-01-01T00:00:00Z"

    assert repo.quarantine_stale_dispatching() == 1
    updated = repo.get_job(job["id"])

    assert updated is not None
    assert updated.status == "unknown_after_dispatch"
    assert updated.reconciliation_required is True
    assert updated.attempt_count == 1
    assert repo.list_due_jobs(limit=10) == []


def test_unknown_dispatch_requires_explicit_duplicate_risk_acknowledgement_to_retry() -> None:
    repo = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repo)
    job = _plan(repo, key="r07-unknown-manual-retry")
    claimed = repo.acquire_job(job["id"], locked_by="worker-unknown")
    assert claimed is not None
    updated = repo.mark_dispatch_unknown(
        job=claimed,
        error_code="timeout",
        error_message="provider result unknown",
        side_effect_executed=True,
    )
    assert updated is not None

    assert service.retry(job["id"]) is None
    assert service.retry(job["id"], actor="operator", reason="checked provider") is None
    assert service.retry(job["id"], actor="", reason="checked provider", confirm_duplicate_risk=True) is None
    assert service.enqueue(job["id"]) is None
    assert service.approve(job["id"]) is None
    assert repo.get_job(job["id"]).status == "unknown_after_dispatch"  # type: ignore[union-attr]

    retried = service.retry(
        job["id"],
        actor="operator",
        reason="provider confirms no delivery",
        confirm_duplicate_risk=True,
    )

    assert retried is not None
    assert retried.status == "queued"
    attempts = repo.list_attempts(job["id"])
    assert len(attempts) == 1
    assert attempts[0].status == "skipped"
    assert attempts[0].adapter_mode == "manual_retry_authorization"
    assert attempts[0].request_summary_json["confirm_duplicate_risk"] is True
    assert attempts[0].response_summary_json["real_external_call_executed"] is False


@pytest.mark.skipif(not _database_url(), reason="PostgreSQL integration database is not configured")
def test_postgres_concurrent_claim_has_one_winner_and_lease_cas() -> None:
    database_url = _database_url()
    repo = SQLAlchemyExternalEffectRepository(get_session_factory(database_url))
    key = "r07-postgres-claim-" + uuid4().hex
    job = _plan(repo, key=key)
    results: list[Any] = []

    def claim(worker: str) -> None:
        results.append(repo.acquire_job(job["id"], locked_by=worker))

    first = Thread(target=claim, args=("pg-worker-a",))
    second = Thread(target=claim, args=("pg-worker-b",))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    winner = winners[0]
    begun = repo.begin_provider_attempt(
        job=winner,
        request_summary={"provider_boundary": "postgres_concurrent_claim_test"},
    )
    assert begun is not None
    winner, open_attempt = begun
    assert open_attempt.status == "dispatching"
    completed = repo.complete_dispatch(job=winner, result=_success_result())
    assert completed is not None
    updated, attempt = completed
    assert updated.status == "succeeded"
    assert updated.side_effect_executed is True
    assert updated.provider_result_received is True
    assert updated.row_version == winner.row_version + 1
    assert attempt.status == "succeeded"
    outbox = repo._one(
        """
        SELECT event_type, aggregate_id, idempotency_key, status
        FROM internal_event_outbox
        WHERE idempotency_key = :idempotency_key
        """,
        {"idempotency_key": f"external_effect.completed:{updated.id}:{attempt.attempt_id}"},
    )
    assert outbox == {
        "event_type": "external_effect.completed",
        "aggregate_id": str(updated.id),
        "idempotency_key": f"external_effect.completed:{updated.id}:{attempt.attempt_id}",
        "status": "pending",
    }
    settlement = repo._one(
        """
        SELECT event_type, aggregate_id, idempotency_key, status
        FROM internal_event_outbox
        WHERE idempotency_key = :idempotency_key
        """,
        {
            "idempotency_key": (
                f"external_effect.settled:{updated.id}:succeeded:{attempt.attempt_id}"
            )
        },
    )
    assert settlement == {
        "event_type": "external_effect.settled",
        "aggregate_id": str(updated.id),
        "idempotency_key": f"external_effect.settled:{updated.id}:succeeded:{attempt.attempt_id}",
        "status": "pending",
    }


@pytest.mark.skipif(not _database_url(), reason="PostgreSQL integration database is not configured")
def test_postgres_cancel_request_uses_row_version_and_preserves_live_lease_until_settled() -> None:
    repo = SQLAlchemyExternalEffectRepository(get_session_factory(_database_url()))
    job = _plan(repo, key="r07-postgres-cancel-" + uuid4().hex)
    claimed = repo.acquire_job(job["id"], locked_by="pg-worker-cancel")
    assert claimed is not None

    stale = repo.request_cancel(
        claimed.id,
        actor="operator",
        reason="stale version",
        expected_version=claimed.row_version + 1,
    )
    requested = repo.request_cancel(
        claimed.id,
        actor="operator",
        reason="cancel before provider",
        expected_version=claimed.row_version,
    )

    assert stale is None
    assert requested is not None
    assert requested.status == "dispatching"
    assert requested.lease_token == claimed.lease_token
    assert requested.row_version == claimed.row_version + 1
    settled = repo.settle_cancel(job=requested)
    assert settled is not None
    assert settled.status == "cancelled"
    assert settled.lease_token == ""
    assert settled.row_version == requested.row_version + 1
    outbox = repo._one(
        """
        SELECT event_type, aggregate_id, idempotency_key, status, payload_json
        FROM internal_event_outbox
        WHERE idempotency_key = :idempotency_key
        """,
        {
            "idempotency_key": (
                f"external_effect.settled:{settled.id}:cancelled:row-version-{settled.row_version}"
            )
        },
    )
    assert outbox is not None
    assert outbox["event_type"] == "external_effect.settled"
    assert outbox["aggregate_id"] == str(settled.id)
    assert outbox["status"] == "pending"
    assert outbox["payload_json"]["attempt_id"] == ""
