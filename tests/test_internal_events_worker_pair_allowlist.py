from __future__ import annotations

from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.internal_events import (
    InMemoryInternalEventRepository,
    InternalEventConsumerRegistry,
    InternalEventConsumerResult,
    InternalEventService,
)
from aicrm_next.platform.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.platform.platform_foundation.internal_events.payment import PAYMENT_SUCCEEDED_EVENT_TYPE
from aicrm_next.platform.platform_foundation.internal_events.shadow import CUSTOMER_TAGGED_EVENT_TYPE, tag_external_effect_shadow_consumer
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker


AI_ASSIST = "ai_assist_notify_consumer"
PAYMENT_PAIR = f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:{AI_ASSIST}"
CUSTOMER_PAIR = f"{CUSTOMER_TAGGED_EVENT_TYPE}:{AI_ASSIST}"


def _enable_pair_allowlist(monkeypatch, *, pairs: str = PAYMENT_PAIR, consumers: str = AI_ASSIST, event_types: str | None = None) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", event_types or f"{PAYMENT_SUCCEEDED_EVENT_TYPE},{CUSTOMER_TAGGED_EVENT_TYPE}")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", consumers)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", pairs)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "10")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")


def _enable_legacy_multi_event(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", f"{PAYMENT_SUCCEEDED_EVENT_TYPE},{CUSTOMER_TAGGED_EVENT_TYPE}")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", AI_ASSIST)
    monkeypatch.delenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "10")


def _registry(calls: list[str] | None = None) -> InternalEventConsumerRegistry:
    calls = calls if calls is not None else []
    registry = InternalEventConsumerRegistry()

    def handler(event: InternalEvent, run: InternalEventConsumerRun) -> InternalEventConsumerResult:
        calls.append(f"{event.event_type}:{run.consumer_name}")
        return InternalEventConsumerResult(
            status="succeeded",
            request_summary={"event_id": event.event_id, "consumer_name": run.consumer_name},
            response_summary={"processed": True},
            result_summary={"processed": True},
        )

    registry.register(PAYMENT_SUCCEEDED_EVENT_TYPE, AI_ASSIST, handler, consumer_type="orchestration")
    registry.register(CUSTOMER_TAGGED_EVENT_TYPE, AI_ASSIST, handler, consumer_type="orchestration")
    registry.register(CUSTOMER_TAGGED_EVENT_TYPE, "tag_external_effect_shadow_consumer", tag_external_effect_shadow_consumer)
    return registry


def _context(key: str) -> CommandContext:
    return CommandContext(actor_id="pair-allowlist-test", actor_type="system", trace_id=f"trace-{key}", source_route="/tests/pair-allowlist")


def _emit(service: InternalEventService, event_type: str, key: str, *, payload: dict | None = None) -> dict:
    return service.emit_event(
        event_type=event_type,
        aggregate_type="test",
        aggregate_id=key,
        subject_type="customer",
        subject_id=f"subject-{key}",
        payload=payload or {"key": key},
        payload_summary={"key": key},
        context=_context(key),
        source_module="tests.internal_events_worker_pair_allowlist",
        idempotency_key=f"{event_type}:{key}",
    )


def _service(calls: list[str] | None = None) -> tuple[InternalEventService, InMemoryInternalEventRepository, InternalEventConsumerRegistry]:
    repo = InMemoryInternalEventRepository()
    registry = _registry(calls)
    return InternalEventService(repo, registry), repo, registry


def test_pair_allowlist_allows_payment_shared_consumer_but_blocks_customer_tag_shared_consumer(monkeypatch) -> None:
    _enable_pair_allowlist(monkeypatch)
    service, repo, registry = _service()
    payment = _emit(service, PAYMENT_SUCCEEDED_EVENT_TYPE, "payment-ai")
    customer = _emit(service, CUSTOMER_TAGGED_EVENT_TYPE, "customer-ai")
    worker = InternalEventWorker(repo, registry)

    payment_preview = worker.preview_due(batch_size=10, event_types=[PAYMENT_SUCCEEDED_EVENT_TYPE], consumer_names=[AI_ASSIST])
    customer_preview = worker.preview_due(batch_size=10, event_types=[CUSTOMER_TAGGED_EVENT_TYPE], consumer_names=[AI_ASSIST])

    assert payment_preview["counts"]["candidate_count"] == 1
    assert payment_preview["items"][0]["event_id"] == payment["event"]["event_id"]
    assert payment_preview["items"][0]["event_type"] == PAYMENT_SUCCEEDED_EVENT_TYPE
    assert payment_preview["event_consumers"] == [PAYMENT_PAIR]
    assert customer_preview["counts"]["candidate_count"] == 0
    assert customer_preview["items"] == []
    assert customer_preview["event_consumers"] == []
    assert repo.get_consumer_run(customer["event"]["event_id"], AI_ASSIST).status == "pending"


def test_pair_allowlist_overrides_legacy_consumer_allowlist_for_shared_consumer(monkeypatch) -> None:
    _enable_pair_allowlist(monkeypatch, pairs=PAYMENT_PAIR, consumers=AI_ASSIST)
    service, repo, registry = _service()
    _emit(service, PAYMENT_SUCCEEDED_EVENT_TYPE, "payment-shared")
    _emit(service, CUSTOMER_TAGGED_EVENT_TYPE, "customer-shared")

    preview = InternalEventWorker(repo, registry).preview_due(
        batch_size=10,
        event_types=[PAYMENT_SUCCEEDED_EVENT_TYPE, CUSTOMER_TAGGED_EVENT_TYPE],
        consumer_names=[AI_ASSIST],
    )

    assert preview["counts"]["candidate_count"] == 1
    assert preview["items"][0]["event_type"] == PAYMENT_SUCCEEDED_EVENT_TYPE
    assert preview["items"][0]["consumer_name"] == AI_ASSIST


def test_run_due_execute_acquire_layer_respects_pair_allowlist(monkeypatch) -> None:
    calls: list[str] = []
    _enable_pair_allowlist(monkeypatch, pairs=PAYMENT_PAIR, consumers=AI_ASSIST)
    service, repo, registry = _service(calls)
    payment = _emit(service, PAYMENT_SUCCEEDED_EVENT_TYPE, "payment-execute")
    customer = _emit(service, CUSTOMER_TAGGED_EVENT_TYPE, "customer-execute")

    result = InternalEventWorker(repo, registry).run_due(
        batch_size=10,
        dry_run=False,
        event_types=[PAYMENT_SUCCEEDED_EVENT_TYPE, CUSTOMER_TAGGED_EVENT_TYPE],
        consumer_names=[AI_ASSIST],
    )

    assert result["ok"] is True
    assert result["counts"]["processed_count"] == 1
    assert result["processed"][0]["event_id"] == payment["event"]["event_id"]
    assert calls == [PAYMENT_PAIR]
    assert repo.get_consumer_run(payment["event"]["event_id"], AI_ASSIST).status == "succeeded"
    assert repo.get_consumer_run(customer["event"]["event_id"], AI_ASSIST).status == "pending"


def test_multi_event_auto_execute_without_pair_allowlist_is_blocked(monkeypatch) -> None:
    _enable_legacy_multi_event(monkeypatch)
    service, repo, registry = _service()
    _emit(service, PAYMENT_SUCCEEDED_EVENT_TYPE, "payment-legacy")
    _emit(service, CUSTOMER_TAGGED_EVENT_TYPE, "customer-legacy")

    dry_run = InternalEventWorker(repo, registry).run_due(
        batch_size=10,
        dry_run=True,
        event_types=[PAYMENT_SUCCEEDED_EVENT_TYPE, CUSTOMER_TAGGED_EVENT_TYPE],
        consumer_names=[AI_ASSIST],
    )
    execute = InternalEventWorker(repo, registry).run_due(
        batch_size=10,
        dry_run=False,
        event_types=[PAYMENT_SUCCEEDED_EVENT_TYPE, CUSTOMER_TAGGED_EVENT_TYPE],
        consumer_names=[AI_ASSIST],
    )

    assert dry_run["ok"] is True
    assert dry_run["counts"]["candidate_count"] == 2
    assert execute["ok"] is False
    assert execute["error"] == "pair_allowlist_required_for_multi_event_auto_execute"
    assert execute["counts"]["processed_count"] == 0


def test_single_consumer_endpoint_is_not_blocked_by_pair_allowlist(monkeypatch) -> None:
    calls: list[str] = []
    _enable_pair_allowlist(monkeypatch, pairs=PAYMENT_PAIR, consumers=AI_ASSIST)
    service, repo, registry = _service(calls)
    customer = _emit(service, CUSTOMER_TAGGED_EVENT_TYPE, "customer-single")

    result = InternalEventWorker(repo, registry).dispatch_one_consumer(
        customer["event"]["event_id"],
        AI_ASSIST,
        dry_run=False,
        force=False,
        reason="pair_allowlist_single_consumer_test",
    )

    assert result["ok"] is True
    assert result["consumer_run"]["status"] == "succeeded"
    assert calls == [CUSTOMER_PAIR]


def test_diagnostics_exposes_pair_allowlist_and_blocked_count(monkeypatch) -> None:
    _enable_pair_allowlist(monkeypatch, pairs=PAYMENT_PAIR, consumers=AI_ASSIST)
    service, _repo, _registry = _service()
    _emit(service, PAYMENT_SUCCEEDED_EVENT_TYPE, "payment-diagnostics")
    _emit(service, CUSTOMER_TAGGED_EVENT_TYPE, "customer-diagnostics")

    diagnostics = service.diagnostics({})

    assert diagnostics["allowed_event_consumers"] == [PAYMENT_PAIR]
    assert diagnostics["pair_allowlist_enabled"] is True
    assert diagnostics["blocked_by_pair_allowlist_count"] == 1
    assert diagnostics["effective_queue_metrics"]["due_count"] == 1
    assert diagnostics["effective_queue_metrics"]["due_count_by_event_type"] == {PAYMENT_SUCCEEDED_EVENT_TYPE: 1}


def test_config_warning_for_multi_event_auto_execute_without_pair_allowlist(monkeypatch) -> None:
    _enable_legacy_multi_event(monkeypatch)
    service, _repo, _registry = _service()

    diagnostics = service.diagnostics({})

    assert diagnostics["pair_allowlist_enabled"] is False
    assert "auto_execute_multi_event_without_pair_allowlist" in diagnostics["config_warnings"]


def test_customer_tag_q1_events_are_blocked_when_pair_allowlist_only_contains_payment_pairs(monkeypatch) -> None:
    _enable_pair_allowlist(
        monkeypatch,
        pairs=",".join(
            [
                f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:order_projection_consumer",
                f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:customer_business_summary_consumer",
                f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:dnd_policy_consumer",
                f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:{AI_ASSIST}",
                f"{PAYMENT_SUCCEEDED_EVENT_TYPE}:webhook_order_paid_consumer",
            ]
        ),
        consumers="order_projection_consumer,customer_business_summary_consumer,dnd_policy_consumer,ai_assist_notify_consumer,webhook_order_paid_consumer",
    )
    service, repo, registry = _service()
    customer = _emit(service, CUSTOMER_TAGGED_EVENT_TYPE, "customer-q1")

    preview = InternalEventWorker(repo, registry).preview_due(
        batch_size=1,
        event_types=[CUSTOMER_TAGGED_EVENT_TYPE],
        consumer_names=[AI_ASSIST, "tag_external_effect_shadow_consumer"],
    )

    assert preview["counts"]["candidate_count"] == 0
    assert preview["event_consumers"] == []
    assert repo.get_consumer_run(customer["event"]["event_id"], AI_ASSIST).status == "pending"


def test_tag_external_effect_consumer_reuses_side_effect_plan_id_when_external_job_missing(monkeypatch) -> None:
    _enable_pair_allowlist(monkeypatch, pairs=f"{CUSTOMER_TAGGED_EVENT_TYPE}:tag_external_effect_shadow_consumer")
    service, repo, registry = _service()
    customer = _emit(
        service,
        CUSTOMER_TAGGED_EVENT_TYPE,
        "customer-side-effect-plan",
        payload={
            "external_userid": "wm_side_effect_plan_only",
            "tag_ids": ["tag_a"],
            "side_effect_plan": {
                "side_effect_plan_id": "sep_123",
                "effect_type": "wecom.tag.mark",
                "status": "planned",
            },
            "external_effect_job": {},
        },
    )

    result = InternalEventWorker(repo, registry).dispatch_one_consumer(
        customer["event"]["event_id"],
        "tag_external_effect_shadow_consumer",
        dry_run=False,
        force=False,
        reason="pair_allowlist_side_effect_plan_id_test",
    )

    assert result["ok"] is True
    assert result["consumer_run"]["status"] == "succeeded"
    assert result["attempt"]["response_summary_json"]["side_effect_plan_reused"] is True
    assert result["attempt"]["response_summary_json"]["side_effect_plan_id"] == "sep_123"
