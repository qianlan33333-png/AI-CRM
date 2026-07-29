from __future__ import annotations

from aicrm_next.platform.platform_foundation.audit_ledger import InMemoryAuditLedger
from aicrm_next.platform.platform_foundation.command_bus import Command, CommandBus, CommandContext
from aicrm_next.platform.platform_foundation.external_calls import InMemoryExternalCallAttemptRepository
from aicrm_next.platform.platform_foundation.reconciliation import InMemoryReconciliationRunRepository
from aicrm_next.platform.platform_foundation.side_effects import InMemorySideEffectPlanRepository


def test_command_bus_executes_handler_and_records_audit_hook() -> None:
    ledger = InMemoryAuditLedger()

    def audit_hook(command, result) -> None:
        ledger.record_event(
            event_type="command.executed",
            actor_id=result.actor_id,
            actor_type=result.actor_type,
            source_route=result.source_route,
            command_id=command.command_id,
            trace_id=result.trace_id,
            payload={"status": result.status},
        )

    bus = CommandBus(audit_hook=audit_hook)
    bus.register("sample.create", lambda command: {"created": command.payload["name"]})
    command = Command(
        command_name="sample.create",
        payload={"name": "route-registry"},
        idempotency_key="sample-create-1",
        context=CommandContext(actor_id="admin", actor_type="user", source_route="/api/admin/system/routes", trace_id="trace-1"),
    )

    result = bus.execute(command)

    assert result.status == "completed"
    assert result.payload == {"created": "route-registry"}
    assert ledger.list_events()[0].command_id == command.command_id


def test_command_bus_idempotency_reuses_first_result() -> None:
    calls = {"count": 0}
    bus = CommandBus()

    def handler(command):
        calls["count"] += 1
        return {"count": calls["count"]}

    bus.register("sample.idempotent", handler)
    command = Command(command_name="sample.idempotent", idempotency_key="same-key")

    first = bus.execute(command)
    second = bus.execute(Command(command_name="sample.idempotent", idempotency_key="same-key"))

    assert first.payload == {"count": 1}
    assert second.payload == {"count": 1}
    assert calls["count"] == 1


def test_command_bus_dry_run_does_not_call_handler() -> None:
    bus = CommandBus()
    bus.register("sample.dry_run", lambda command: {"called": True})

    result = bus.execute(Command(command_name="sample.dry_run", context=CommandContext(dry_run=True)))

    assert result.status == "dry_run"
    assert result.payload == {"dry_run": True}


def test_audit_ledger_query_events() -> None:
    ledger = InMemoryAuditLedger()
    ledger.record_event(event_type="route.checked", actor_id="admin", target_type="route", target_id="/health")
    ledger.record_event(event_type="route.checked", actor_id="worker", target_type="route", target_id="/api/system/health")

    assert [event.actor_id for event in ledger.query_events(actor_id="admin")] == ["admin"]


def test_side_effect_plan_is_created_without_execution() -> None:
    repo = InMemorySideEffectPlanRepository()

    plan = repo.create_plan(
        command_id="cmd-1",
        effect_type="wecom.message",
        adapter_name="wecom",
        adapter_mode="real_blocked",
        target_type="external_user",
        target_id="wx_ext_001",
        payload={"preview": True},
        risk_level="high",
        requires_approval=True,
    )

    assert plan.status == "planned"
    assert plan.executed_at == ""
    assert repo.list_plans()[0].requires_approval is True


def test_external_call_attempt_records_success_and_failure_with_scrubbed_payload() -> None:
    repo = InMemoryExternalCallAttemptRepository()

    success = repo.record_attempt(
        adapter_name="wecom",
        adapter_mode="real_blocked",
        operation="send",
        status="success",
        request_summary={"token": "secret-token", "target": "wx_ext_001"},
        response_summary={"errcode": 0},
    )
    failure = repo.record_attempt(
        adapter_name="wecom",
        adapter_mode="real_blocked",
        operation="send",
        status="failed",
        error_code="blocked",
        error_message="external calls disabled",
    )

    assert success.request_summary["token"] == "[redacted]"
    assert failure.status == "failed"


def test_reconciliation_run_records_counts_and_sample_diffs() -> None:
    repo = InMemoryReconciliationRunRepository()

    run = repo.record_run(
        capability_owner="aicrm_next.crm.customer_read_model",
        source_name="legacy",
        target_name="next",
        source_count=10,
        target_count=9,
        diff_count=1,
        sample_diffs=[{"id": "wx_ext_001", "field": "owner"}],
    )

    assert run.status == "completed"
    assert run.diff_count == 1
    assert repo.list_runs()[0].sample_diffs[0]["field"] == "owner"
