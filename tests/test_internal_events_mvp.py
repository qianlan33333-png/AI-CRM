from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.execution_runtime.commands import QueueRuntimeCommandService
from aicrm_next.platform_foundation.internal_events import (
    InMemoryInternalEventRepository,
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventService,
)
from aicrm_next.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from tests.admin_auth_test_helpers import install_admin_action_tokens


EVENT_TYPE = "test.customer.activated"


def _context(trace_id: str = "trace-internal-event") -> CommandContext:
    return CommandContext(
        actor_id="tester",
        actor_type="system",
        request_id="req-internal-event",
        trace_id=trace_id,
        source_route="/tests/internal-events",
    )


def _service(repo: InMemoryInternalEventRepository, registry: InternalEventConsumerRegistry) -> InternalEventService:
    return InternalEventService(repo, registry)


def _emit(service: InternalEventService, *, idempotency_key: str = "event-same-key") -> dict:
    return service.emit_event(
        event_type=EVENT_TYPE,
        event_version=1,
        aggregate_type="customer",
        aggregate_id="cust-1",
        subject_type="external_user",
        subject_id="wx-ext-1",
        payload={"token": "secret", "score": 9},
        context=_context(),
        source_module="tests",
        source_command_id="cmd-1",
        correlation_id="corr-1",
        idempotency_key=idempotency_key,
    )


def test_internal_event_migration_contract_uses_current_head_and_required_tables() -> None:
    source = Path("migrations/versions/0043_internal_event_queue.py").read_text(encoding="utf-8")
    r06_source = Path("migrations/versions/0099_internal_event_outbox_and_consumer_lease.py").read_text(encoding="utf-8")

    assert 'down_revision = "0042_legacy_webhook_deprecation_registry"' in source
    for table in [
        "CREATE TABLE IF NOT EXISTS internal_event",
        "CREATE TABLE IF NOT EXISTS internal_event_consumer_run",
        "CREATE TABLE IF NOT EXISTS internal_event_consumer_attempt",
    ]:
        assert table in source
    assert "CONSTRAINT uq_internal_event_tenant_idempotency UNIQUE (tenant_id, idempotency_key)" in source
    assert "UNIQUE (tenant_id, event_id, consumer_name)" in source
    assert "REFERENCES internal_event(event_id) ON DELETE CASCADE" in source
    assert "REFERENCES internal_event_consumer_run(id) ON DELETE CASCADE" in source
    assert 'down_revision = "0098_admin_session_revocation"' in r06_source
    assert "CREATE TABLE IF NOT EXISTS internal_event_outbox" in r06_source
    assert "ADD COLUMN IF NOT EXISTS lease_token" in r06_source
    assert "'manual_retry'" in r06_source


def test_emit_event_is_idempotent_and_creates_multiple_consumer_runs_once() -> None:
    repo = InMemoryInternalEventRepository()
    registry = InternalEventConsumerRegistry()
    registry.register(EVENT_TYPE, "project_customer_state", lambda event, run: InternalEventConsumerResult(status="succeeded"))
    registry.register(EVENT_TYPE, "plan_external_push", lambda event, run: InternalEventConsumerResult(status="succeeded"), consumer_type="external_effect_planner")
    service = _service(repo, registry)

    first = _emit(service)
    duplicate = _emit(service)
    events, event_total = service.list_events({"event_type": EVENT_TYPE})
    runs, run_total = service.list_consumer_runs({"event_id": first["event"]["event_id"]})

    assert first["event"]["event_id"] == duplicate["event"]["event_id"]
    assert first["event"]["payload_summary_json"]["token"] == "[redacted]"
    assert event_total == 1
    assert len(events) == 1
    assert run_total == 2
    assert sorted(run.consumer_name for run in runs) == ["plan_external_push", "project_customer_state"]
    assert len(duplicate["consumer_runs"]) == 2
    assert {run["id"] for run in first["consumer_runs"]} == {run["id"] for run in duplicate["consumer_runs"]}


def test_worker_preview_does_not_execute_handler() -> None:
    repo = InMemoryInternalEventRepository()
    registry = InternalEventConsumerRegistry()
    calls: list[str] = []

    def handler(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
        calls.append(event.event_id)
        return InternalEventConsumerResult(status="succeeded")

    registry.register(EVENT_TYPE, "projection", handler)
    service = _service(repo, registry)
    _emit(service, idempotency_key="preview-only")

    preview = InternalEventWorker(repo, registry).preview_due(batch_size=10)
    dry_run = InternalEventWorker(repo, registry).run_due(batch_size=10)

    assert preview["counts"]["candidate_count"] == 1
    assert dry_run["dry_run"] is True
    assert dry_run["real_external_call_executed"] is False
    assert calls == []
    assert repo.list_attempts() == []


def test_run_due_records_success_retryable_and_skipped_attempts() -> None:
    repo = InMemoryInternalEventRepository()
    registry = InternalEventConsumerRegistry()
    registry.register(
        EVENT_TYPE,
        "success_consumer",
        lambda event, run: InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id},
            response_summary={"projected": True},
            result_summary={"projection": "updated"},
        ),
    )
    registry.register(
        EVENT_TYPE,
        "retryable_consumer",
        lambda event, run: InternalEventConsumerResult(
            status="failed_retryable",
            request_summary={"event_id": event.event_id},
            response_summary={"retry": True},
            error_code="temporary_dependency_unavailable",
            error_message="try later",
            retry_after_seconds=30,
        ),
    )
    registry.register(
        EVENT_TYPE,
        "skipped_consumer",
        lambda event, run: InternalEventConsumerResult(
            status="skipped",
            request_summary={"event_id": event.event_id},
            response_summary={"skipped": True},
            result_summary={"reason": "no-op"},
        ),
    )
    service = _service(repo, registry)
    emitted = _emit(service, idempotency_key="run-due-three-statuses")

    result = InternalEventWorker(repo, registry).run_due(batch_size=10, dry_run=False)
    runs, total = service.list_consumer_runs({"event_id": emitted["event"]["event_id"]})
    attempts = service.list_attempts(event_id=emitted["event"]["event_id"])

    assert result["counts"]["candidate_count"] == 3
    assert result["counts"]["succeeded_count"] == 1
    assert result["counts"]["failed_retryable_count"] == 1
    assert result["counts"]["skipped_count"] == 1
    assert result["real_external_call_executed"] is False
    assert total == 3
    assert {run.consumer_name: run.status for run in runs} == {
        "success_consumer": "succeeded",
        "retryable_consumer": "failed_retryable",
        "skipped_consumer": "skipped",
    }
    assert len(attempts) == 3
    assert sorted(attempt.status for attempt in attempts) == ["failed_retryable", "skipped", "succeeded"]
    assert all(attempt.response_summary_json["real_external_call_executed"] is False for attempt in attempts)


def test_retry_consumer_run_only_allows_failed_retryable_terminal_and_blocked() -> None:
    repo = InMemoryInternalEventRepository()
    registry = InternalEventConsumerRegistry()
    registry.register(EVENT_TYPE, "ok", lambda event, run: InternalEventConsumerResult(status="succeeded"))
    registry.register(EVENT_TYPE, "retryable", lambda event, run: InternalEventConsumerResult(status="failed_retryable", error_code="timeout"))
    registry.register(EVENT_TYPE, "terminal", lambda event, run: InternalEventConsumerResult(status="succeeded"))
    registry.register(EVENT_TYPE, "blocked", lambda event, run: InternalEventConsumerResult(status="succeeded"))
    service = _service(repo, registry)
    emitted = _emit(service, idempotency_key="retry-boundary")
    runs, _ = service.list_consumer_runs({"event_id": emitted["event"]["event_id"]})
    by_name = {run.consumer_name: run for run in runs}

    assert service.retry_consumer_run(
        emitted["event"]["event_id"],
        "ok",
        actor_id="operator-1",
        actor_type="test",
        reason="not retryable",
    ) is None
    InternalEventWorker(repo, registry).dispatch_one(by_name["ok"])
    InternalEventWorker(repo, registry).dispatch_one(by_name["retryable"])
    repo.mark_result(by_name["terminal"].id, status="failed_terminal", attempt_id="manual-terminal", error_code="bad_payload")
    repo.mark_result(by_name["blocked"].id, status="blocked", attempt_id="manual-blocked", error_code="missing_handler")

    assert service.retry_consumer_run(
        emitted["event"]["event_id"],
        "ok",
        actor_id="operator-1",
        actor_type="test",
        reason="not retryable",
    ) is None
    for name in ["retryable", "terminal", "blocked"]:
        retried = service.retry_consumer_run(
            emitted["event"]["event_id"],
            name,
            actor_id="operator-1",
            actor_type="test",
            reason=f"approved retry for {name}",
        )
        assert retried is not None
        run, audit = retried
        assert run.status == "pending"
        assert audit.status == "manual_retry"


def test_diagnostics_and_admin_api_return_internal_event_metrics(
    next_client: TestClient,
    next_pg_schema,
    monkeypatch,
) -> None:
    del next_pg_schema, monkeypatch
    registry = next_client.app.state.internal_event_consumer_registry
    registry.clear()
    tokens = install_admin_action_tokens(
        next_client,
        ("POST", "/api/admin/internal-events/run-due/preview"),
        ("POST", "/api/admin/internal-events/run-due"),
        ("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry"),
        ("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip"),
    )
    calls: list[str] = []

    def handler(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
        calls.append(run.consumer_name)
        return InternalEventConsumerResult(status="failed_retryable", error_code="retry_me")

    try:
        registry.register(EVENT_TYPE, "api_consumer", handler)
        service = InternalEventService(consumer_registry=registry)
        emitted = _emit(service, idempotency_key="api-diagnostics")

        listed = next_client.get("/api/admin/internal-events", params={"event_type": EVENT_TYPE})
        detail = next_client.get(f"/api/admin/internal-events/{emitted['event']['event_id']}")
        diagnostics_before = next_client.get("/api/admin/internal-events/diagnostics")
        unauthorized_preview = next_client.post("/api/admin/internal-events/run-due/preview", json={"batch_size": 10})
        preview = next_client.post(
            "/api/admin/internal-events/run-due/preview",
            headers={"X-Admin-Action-Token": tokens[("POST", "/api/admin/internal-events/run-due/preview")]},
            json={"batch_size": 10, "event_types": [EVENT_TYPE]},
        )
        run_due = next_client.post(
            "/api/admin/internal-events/run-due",
            headers={"X-Admin-Action-Token": tokens[("POST", "/api/admin/internal-events/run-due")]},
            json={"batch_size": 10, "dry_run": False, "event_types": [EVENT_TYPE]},
        )
        diagnostics_after = next_client.get("/api/admin/internal-events/diagnostics")
        command_target = QueueRuntimeCommandService().read_internal_consumer_target(
            emitted["event"]["event_id"],
            "api_consumer",
        )
        assert command_target is not None
        retry = next_client.post(
            f"/api/admin/internal-events/{emitted['event']['event_id']}/consumers/api_consumer/retry",
            json={
                    "admin_action_token": tokens[("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/retry")],
                    "actor": "pytest-operator",
                    "reason": "approved operator retry",
                    "expected_version": command_target.version_token,
                },
        )
        skip = next_client.post(
            f"/api/admin/internal-events/{emitted['event']['event_id']}/consumers/api_consumer/skip",
            json={
                    "admin_action_token": tokens[("POST", "/api/admin/internal-events/{event_id}/consumers/{consumer_name}/skip")],
                    "actor": "pytest-operator",
                    "reason": "operator decided no-op",
                    "expected_version": command_target.version_token,
                },
        )

        assert listed.status_code == 200
        assert listed.json()["route_owner"] == "ai_crm_next"
        assert listed.json()["total"] == 1
        assert detail.status_code == 200
        assert detail.json()["event"]["event_id"] == emitted["event"]["event_id"]
        assert diagnostics_before.json()["due_count"] == 1
        assert diagnostics_before.json()["failed_retryable_count"] == 0
        assert diagnostics_before.json()["failed_terminal_count"] == 0
        assert "oldest_pending_age_seconds" in diagnostics_before.json()
        assert unauthorized_preview.status_code == 401
        assert preview.status_code == 200
        assert preview.json()["dry_run"] is True
        assert preview.json()["counts"]["candidate_count"] == 1
        assert run_due.status_code == 422
        assert set(run_due.json()["missing_fields"]) == {
            "actor",
            "reason",
            "expected_version",
        }
        assert calls == []
        assert diagnostics_after.json()["due_count"] == 1
        assert diagnostics_after.json()["failed_retryable_count"] == 0
        assert retry.status_code == 409
        assert retry.json()["error"] == "queue_command_cas_conflict"
        assert skip.status_code == 202
        assert skip.json()["action"] == "skip"
        assert skip.json()["status"] == "skipped"
    finally:
        registry.clear()
