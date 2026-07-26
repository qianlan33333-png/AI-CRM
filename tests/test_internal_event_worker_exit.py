from __future__ import annotations

import pytest

from aicrm_next.platform_foundation.internal_events.repository import InMemoryInternalEventRepository
from aicrm_next.platform_foundation.internal_events.consumer_registry import InternalEventConsumerRegistry
from aicrm_next.platform_foundation.internal_events.models import InternalEventConsumerResult, InternalEventCreateRequest
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from scripts import run_internal_event_worker


class _BrokenAcquireRepository(InMemoryInternalEventRepository):
    def acquire_due_runs(self, **kwargs):
        raise RuntimeError("injected acquire failure")


class _BrokenOutboxAcquireRepository(InMemoryInternalEventRepository):
    def acquire_due_outbox(self, **kwargs):
        raise RuntimeError("injected outbox acquire failure")


class _BrokenOutboxFailurePersistenceRepository(InMemoryInternalEventRepository):
    def relay_outbox(self, outbox, consumers, *, fanout_manifest):
        raise RuntimeError("injected relay failure")

    def mark_outbox_failure(self, outbox, **kwargs):
        raise RuntimeError("injected failure persistence failure")


def _owner_registry() -> InternalEventConsumerRegistry:
    registry = InternalEventConsumerRegistry()
    registry.register(
        "test.worker.exit",
        "projection",
        lambda event, run: InternalEventConsumerResult(status="succeeded"),
    )
    registry.seal_fanout_contract()
    return registry


def test_worker_returns_failure_summary_when_consumer_acquire_raises(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")

    result = InternalEventWorker(_BrokenAcquireRepository()).run_due(batch_size=1, dry_run=False)

    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert result["error"] == "consumer_run_acquire_failed"
    assert result["error_class"] == "RuntimeError"
    assert result["counts"]["unhandled_failure_count"] == 1


def test_outbox_acquire_failure_is_reported_and_makes_worker_nonzero(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")

    result = InternalEventWorker(
        _BrokenOutboxAcquireRepository(),
        _owner_registry(),
        relay_role="owner",
    ).run_due(batch_size=1, dry_run=False)

    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert result["outbox_relay"]["error"] == "outbox_acquire_failed"
    assert result["outbox_relay"]["counts"]["unhandled_failure_count"] == 1


def test_outbox_failure_persistence_exception_is_summarized(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    repo = _BrokenOutboxFailurePersistenceRepository()
    repo.enqueue_outbox(
        InternalEventCreateRequest(
            event_type="test.worker.exit",
            aggregate_type="test",
            aggregate_id="1",
            idempotency_key="test.worker.exit:1",
        )
    )

    result = InternalEventWorker(repo, _owner_registry(), relay_role="owner").run_due(batch_size=1, dry_run=False)

    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert result["outbox_relay"]["counts"]["unhandled_failure_count"] == 1
    assert result["outbox_relay"]["items"][0]["error_code"] == "outbox_failure_persist_failed"


def test_script_main_uses_worker_exit_code(monkeypatch) -> None:
    monkeypatch.setattr(
        run_internal_event_worker,
        "run",
        lambda **kwargs: {"ok": False, "dry_run": False, "exit_code": 1, "counts": {"failed_retryable_count": 1}},
    )
    monkeypatch.setattr(run_internal_event_worker, "print_json", lambda payload: None)

    assert run_internal_event_worker.main(["--execute", "--limit", "1"]) == 1


def test_script_dry_run_remains_zero_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        run_internal_event_worker,
        "run",
        lambda **kwargs: {"ok": True, "dry_run": True, "counts": {"candidate_count": 1}},
    )
    monkeypatch.setattr(run_internal_event_worker, "print_json", lambda payload: None)

    assert run_internal_event_worker.main(["--limit", "1"]) == 0


def test_release_customer_refresh_override_requires_authorization(monkeypatch) -> None:
    monkeypatch.delenv(
        run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_AUTHORIZATION_ENV,
        raising=False,
    )

    with pytest.raises(SystemExit):
        run_internal_event_worker._parse_args(
            [
                "--execute",
                "--limit",
                "1",
                "--release-customer-read-model-refresh",
                "--event-types",
                run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_EVENT_TYPE,
                "--consumer-names",
                run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_CONSUMER,
                "--outbox-idempotency-key",
                f"{run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_EVENT_TYPE}:1",
            ]
        )


def test_release_customer_refresh_override_passes_only_the_immutable_pair(monkeypatch) -> None:
    monkeypatch.setenv(
        run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_AUTHORIZATION_ENV,
        "1",
    )
    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "dry_run": False, "exit_code": 0}

    monkeypatch.setattr(run_internal_event_worker, "run", fake_run)
    monkeypatch.setattr(run_internal_event_worker, "print_json", lambda payload: None)

    assert (
        run_internal_event_worker.main(
            [
                "--execute",
                "--limit",
                "1",
                "--release-customer-read-model-refresh",
                "--event-types",
                run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_EVENT_TYPE,
                "--consumer-names",
                run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_CONSUMER,
                "--outbox-idempotency-key",
                f"{run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_EVENT_TYPE}:12",
            ]
        )
        == 0
    )
    assert captured["exact_target_config_override"] == (
        run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_EVENT_TYPE,
        run_internal_event_worker.RELEASE_CUSTOMER_REFRESH_CONSUMER,
    )
