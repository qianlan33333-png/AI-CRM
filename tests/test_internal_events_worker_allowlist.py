from __future__ import annotations

from aicrm_next.internal_event_composition import register_payment_succeeded_consumers
from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import WEBHOOK_ORDER_PAID_PUSH, ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform.platform_foundation.internal_events import (
    InMemoryInternalEventRepository,
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventService,
    reset_internal_event_fixture_state,
)
from aicrm_next.platform.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.platform.platform_foundation.internal_events.config import worker_allows
from aicrm_next.platform.platform_foundation.internal_events.payment import PAYMENT_SUCCEEDED_EVENT_TYPE
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker


OTHER_EVENT_TYPE = "questionnaire.submitted"
STAGE_1_CONSUMERS = [
    "order_projection_consumer",
    "service_period_entitlement_consumer",
    "customer_business_summary_consumer",
    "dnd_policy_consumer",
    "ai_assist_notify_consumer",
]
PAYMENT_CONSUMERS = [
    "order_projection_consumer",
    "service_period_entitlement_consumer",
    "customer_business_summary_consumer",
    "dnd_policy_consumer",
    "ai_assist_notify_consumer",
    "webhook_order_paid_consumer",
]


def test_shared_worker_allowlist_intersects_event_type_and_pair_filters() -> None:
    pairs = ((PAYMENT_SUCCEEDED_EVENT_TYPE, "order_projection_consumer"), (OTHER_EVENT_TYPE, "allowed_consumer"))

    assert worker_allows(
        PAYMENT_SUCCEEDED_EVENT_TYPE,
        "order_projection_consumer",
        configured_pairs=pairs,
        configured_event_types=(PAYMENT_SUCCEEDED_EVENT_TYPE,),
    ) is True
    assert worker_allows(
        OTHER_EVENT_TYPE,
        "allowed_consumer",
        configured_pairs=pairs,
        configured_event_types=(PAYMENT_SUCCEEDED_EVENT_TYPE,),
    ) is False


def _context(trace_id: str) -> CommandContext:
    return CommandContext(
        actor_id="worker-allowlist-test",
        actor_type="system",
        request_id=f"req-{trace_id}",
        trace_id=trace_id,
        source_route="/tests/internal-event-worker-allowlist",
    )


def _enable_auto_execute(monkeypatch, *, consumers: list[str] | None = None, event_types: list[str] | None = None, max_batch_size: str = "1") -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", ",".join(event_types or [PAYMENT_SUCCEEDED_EVENT_TYPE]))
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", ",".join(consumers or STAGE_1_CONSUMERS))
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", max_batch_size)
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")


def _custom_service(registry: InternalEventConsumerRegistry, *, calls: list[str] | None = None) -> tuple[InternalEventService, InMemoryInternalEventRepository]:
    repo = InMemoryInternalEventRepository()
    calls = calls if calls is not None else []

    def handler(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
        calls.append(f"{event.event_type}:{run.consumer_name}")
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id},
            response_summary={"consumer": run.consumer_name},
            result_summary={"consumer": run.consumer_name},
        )

    registry.register(PAYMENT_SUCCEEDED_EVENT_TYPE, "allowed_consumer", handler)
    registry.register(PAYMENT_SUCCEEDED_EVENT_TYPE, "blocked_consumer", handler)
    registry.register(OTHER_EVENT_TYPE, "allowed_consumer", handler)
    return InternalEventService(repo, registry), repo


def _emit_custom(service: InternalEventService, *, event_type: str, key: str) -> dict:
    return service.emit_event(
        event_type=event_type,
        aggregate_type="test_aggregate",
        aggregate_id=key,
        subject_type="customer",
        subject_id=f"cust-{key}",
        payload={"key": key},
        payload_summary={"key": key},
        context=_context(key),
        source_module="tests.internal_events_worker_allowlist",
        idempotency_key=f"{event_type}:{key}",
    )


def _payment_service() -> tuple[InternalEventService, InMemoryInternalEventRepository, InternalEventConsumerRegistry]:
    registry = InternalEventConsumerRegistry()
    register_payment_succeeded_consumers(registry)
    repo = InMemoryInternalEventRepository()
    return InternalEventService(repo, registry), repo, registry


def _emit_payment(service: InternalEventService, *, out_trade_no: str = "WXP_WORKER_ALLOWLIST") -> dict:
    return service.emit_event(
        event_type=PAYMENT_SUCCEEDED_EVENT_TYPE,
        aggregate_type="wechat_pay_order",
        aggregate_id="7001",
        subject_type="customer",
        subject_id="wm_worker_allowlist",
        payload={
            "order": {
                "id": 7001,
                "out_trade_no": out_trade_no,
                "status": "paid",
                "trade_state": "SUCCESS",
                "product_code": "worker_allowlist",
                "product_name": "Worker Allowlist",
                "paid_at": "2026-06-14T13:00:00+08:00",
            },
            "transaction": {
                "out_trade_no": out_trade_no,
                "trade_state": "SUCCESS",
                "transaction_id": f"wx_{out_trade_no}",
                "success_time": "2026-06-14T13:00:00+08:00",
            },
        },
        payload_summary={"out_trade_no": out_trade_no, "status": "paid"},
        context=_context(out_trade_no),
        source_module="tests.internal_events_worker_allowlist",
        idempotency_key=f"payment.succeeded:{out_trade_no}",
    )


def _run_until_empty(worker: InternalEventWorker, *, limit: int = 10) -> list[dict]:
    items: list[dict] = []
    for _ in range(limit):
        result = worker.run_due(batch_size=1, dry_run=False)
        items.extend(result.get("items") or [])
        if result["counts"]["candidate_count"] == 0:
            break
    return items


def test_event_type_and_consumer_allowlists_filter_due_scan(monkeypatch) -> None:
    _enable_auto_execute(monkeypatch, consumers=["allowed_consumer"])
    registry = InternalEventConsumerRegistry()
    calls: list[str] = []
    service, repo = _custom_service(registry, calls=calls)
    _emit_custom(service, event_type=PAYMENT_SUCCEEDED_EVENT_TYPE, key="payment")
    _emit_custom(service, event_type=OTHER_EVENT_TYPE, key="questionnaire")

    preview = InternalEventWorker(repo, registry).preview_due(batch_size=10)
    result = InternalEventWorker(repo, registry).run_due(batch_size=1, dry_run=False)
    runs, _ = service.list_consumer_runs({})

    assert preview["counts"]["candidate_count"] == 1
    assert preview["items"][0]["consumer_name"] == "allowed_consumer"
    assert preview["items"][0]["would_execute"] is True
    assert result["counts"]["succeeded_count"] == 1
    assert calls == [f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:allowed_consumer"]
    assert {run.consumer_name: run.status for run in runs if run.event_id == result["processed"][0]["event_id"]}["blocked_consumer"] == "pending"
    assert all(run.status == "pending" for run in runs if run.event_id != result["processed"][0]["event_id"])


def test_requested_consumer_outside_allowlist_returns_no_candidates(monkeypatch) -> None:
    _enable_auto_execute(monkeypatch, consumers=["allowed_consumer"])
    registry = InternalEventConsumerRegistry()
    calls: list[str] = []
    service, repo = _custom_service(registry, calls=calls)
    _emit_custom(service, event_type=PAYMENT_SUCCEEDED_EVENT_TYPE, key="payment")

    preview = InternalEventWorker(repo, registry).preview_due(batch_size=10, consumer_names=["blocked_consumer"])
    result = InternalEventWorker(repo, registry).run_due(batch_size=1, dry_run=False, consumer_names=["blocked_consumer"])

    assert preview["counts"]["candidate_count"] == 0
    assert preview["consumer_names"] == []
    assert result["counts"]["processed_count"] == 0
    assert calls == []
    assert repo.list_attempts() == []


def test_auto_execute_disabled_blocks_execute_but_not_dry_run(monkeypatch) -> None:
    _enable_auto_execute(monkeypatch, consumers=["allowed_consumer"])
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "0")
    registry = InternalEventConsumerRegistry()
    calls: list[str] = []
    service, repo = _custom_service(registry, calls=calls)
    _emit_custom(service, event_type=PAYMENT_SUCCEEDED_EVENT_TYPE, key="payment")

    dry_run = InternalEventWorker(repo, registry).run_due(batch_size=1, dry_run=True)
    result = InternalEventWorker(repo, registry).run_due(batch_size=1, dry_run=False)

    assert dry_run["dry_run"] is True
    assert dry_run["counts"]["candidate_count"] == 1
    assert result["ok"] is False
    assert result["error"] == "internal_events_auto_execute_disabled"
    assert calls == []
    assert repo.list_attempts() == []


def test_batch_size_above_auto_execute_limit_is_rejected(monkeypatch) -> None:
    _enable_auto_execute(monkeypatch, consumers=["allowed_consumer"], max_batch_size="1")
    registry = InternalEventConsumerRegistry()
    calls: list[str] = []
    service, repo = _custom_service(registry, calls=calls)
    _emit_custom(service, event_type=PAYMENT_SUCCEEDED_EVENT_TYPE, key="payment-1")
    _emit_custom(service, event_type=PAYMENT_SUCCEEDED_EVENT_TYPE, key="payment-2")

    result = InternalEventWorker(repo, registry).run_due(batch_size=2, dry_run=False)

    assert result["ok"] is False
    assert result["error"] == "batch_size_exceeds_auto_execute_limit"
    assert result["counts"]["processed_count"] == 0
    assert calls == []
    assert repo.list_attempts() == []


def test_succeeded_and_skipped_consumers_are_not_reexecuted(monkeypatch) -> None:
    _enable_auto_execute(monkeypatch, consumers=["success_consumer", "skip_consumer"])
    repo = InMemoryInternalEventRepository()
    registry = InternalEventConsumerRegistry()
    calls: list[str] = []
    registry.register(PAYMENT_SUCCEEDED_EVENT_TYPE, "success_consumer", lambda event, run: calls.append(run.consumer_name) or InternalEventConsumerResult(status="succeeded"))
    registry.register(
        PAYMENT_SUCCEEDED_EVENT_TYPE,
        "skip_consumer",
        lambda event, run: calls.append(run.consumer_name) or InternalEventConsumerResult(status="skipped", response_summary={"reason": "noop"}),
    )
    service = InternalEventService(repo, registry)
    _emit_custom(service, event_type=PAYMENT_SUCCEEDED_EVENT_TYPE, key="payment")

    first = _run_until_empty(InternalEventWorker(repo, registry), limit=3)
    second = InternalEventWorker(repo, registry).run_due(batch_size=1, dry_run=False)

    assert [item["consumer_run"]["status"] for item in first] == ["succeeded", "skipped"]
    assert second["counts"]["candidate_count"] == 0
    assert calls == ["success_consumer", "skip_consumer"]
    assert len(repo.list_attempts()) == 2


def test_stage_1_payment_consumers_auto_execute_under_allowlist(monkeypatch) -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    _enable_auto_execute(monkeypatch, consumers=STAGE_1_CONSUMERS)
    service, repo, registry = _payment_service()
    emitted = _emit_payment(service)

    items = _run_until_empty(InternalEventWorker(repo, registry), limit=6)
    runs, _ = service.list_consumer_runs({"event_id": emitted["event"]["event_id"]})
    statuses = {run.consumer_name: run.status for run in runs}

    assert len(items) == 5
    assert statuses["order_projection_consumer"] == "succeeded"
    assert statuses["service_period_entitlement_consumer"] == "skipped"
    assert statuses["customer_business_summary_consumer"] == "skipped"
    assert statuses["dnd_policy_consumer"] == "skipped"
    assert statuses["ai_assist_notify_consumer"] == "skipped"
    assert statuses["webhook_order_paid_consumer"] == "pending"
    assert "automation_payment_consumer" not in statuses


def test_payment_service_period_consumer_executes_when_pair_allowlisted(monkeypatch) -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    _enable_auto_execute(monkeypatch, consumers=["service_period_entitlement_consumer"])
    monkeypatch.setenv(
        "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS",
        f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:service_period_entitlement_consumer",
    )
    service, repo, registry = _payment_service()
    emitted = _emit_payment(service)

    result = InternalEventWorker(repo, registry).run_due(batch_size=1, dry_run=False)
    runs, _ = service.list_consumer_runs({"event_id": emitted["event"]["event_id"]})
    statuses = {run.consumer_name: run.status for run in runs}

    assert result["counts"]["processed_count"] == 1
    assert result["processed"][0]["consumer_name"] == "service_period_entitlement_consumer"
    assert statuses["service_period_entitlement_consumer"] == "skipped"
    assert statuses["order_projection_consumer"] == "pending"


def test_retired_automation_payment_consumer_is_not_registered(monkeypatch) -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    _enable_auto_execute(monkeypatch, consumers=STAGE_1_CONSUMERS)
    service, repo, registry = _payment_service()
    emitted = _emit_payment(service)

    _run_until_empty(InternalEventWorker(repo, registry), limit=6)
    automation_runs, automation_total = service.list_consumer_runs({"event_id": emitted["event"]["event_id"], "consumer_name": "automation_payment_consumer"})
    assert automation_total == 0
    assert automation_runs == []

    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", ",".join([*STAGE_1_CONSUMERS, "automation_payment_consumer"]))
    result = InternalEventWorker(repo, registry).dispatch_one_consumer(emitted["event"]["event_id"], "automation_payment_consumer", dry_run=False)

    assert result["ok"] is False
    assert result["error"] == "consumer_run_not_found"


def test_webhook_payment_consumer_skips_without_configured_external_effect(monkeypatch) -> None:
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    _enable_auto_execute(monkeypatch, consumers=["webhook_order_paid_consumer"])
    monkeypatch.setattr(
        "aicrm_next.internal_event_composition._plan_order_paid_external_push_effect_from_db",
        lambda **kwargs: {"ok": True, "skipped": True, "reason": "external_push_config_unavailable"},
    )
    service, repo, registry = _payment_service()
    _emit_payment(service)

    result = InternalEventWorker(repo, registry).run_due(batch_size=1, dry_run=False)
    jobs, total = ExternalEffectService().list_jobs({"effect_type": WEBHOOK_ORDER_PAID_PUSH, "business_id": "WXP_WORKER_ALLOWLIST"})
    response_summary = result["items"][0]["attempt"]["response_summary_json"]

    assert result["processed"][0]["consumer_name"] == "webhook_order_paid_consumer"
    assert result["counts"]["succeeded_count"] == 1
    assert result["real_external_call_executed"] is False
    assert total == 0
    assert jobs == []
    assert response_summary["external_effect_job_created"] is False
    assert response_summary["skipped"] is True
    assert response_summary["reason"] == "external_push_config_unavailable"


def test_diagnostics_show_allowlists_and_due_counts(monkeypatch) -> None:
    _enable_auto_execute(monkeypatch, consumers=["allowed_consumer"])
    registry = InternalEventConsumerRegistry()
    service, repo = _custom_service(registry)
    _emit_custom(service, event_type=PAYMENT_SUCCEEDED_EVENT_TYPE, key="payment")
    _emit_custom(service, event_type=OTHER_EVENT_TYPE, key="questionnaire")

    payload = InternalEventService(repo, registry).diagnostics({})

    assert payload["auto_execute_enabled"] is True
    assert payload["shadow_only"] is True
    assert payload["allowed_event_types"] == [PAYMENT_SUCCEEDED_EVENT_TYPE]
    assert payload["allowed_consumers"] == ["allowed_consumer"]
    assert payload["worker_batch_size"] == 1
    assert payload["due_count"] == 3
    assert payload["effective_queue_metrics"]["due_count"] == 1
    assert payload["blocked_by_config_count"] == 2
    assert payload["due_count_by_event_type"][PAYMENT_SUCCEEDED_EVENT_TYPE] == 2
    assert payload["due_count_by_consumer"]["allowed_consumer"] == 2
    assert payload["real_external_call_executed"] is False
