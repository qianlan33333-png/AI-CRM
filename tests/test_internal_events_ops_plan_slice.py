from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.application import ApproveCloudPlanCommand
from aicrm_next.extensions.growth.cloud_orchestrator.repository import reset_cloud_plan_fixture_state
from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.command_bus import CommandContext
from aicrm_next.platform.platform_foundation.external_effects import ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform.platform_foundation.internal_events.repository import build_internal_event_repository
from aicrm_next.internal_event_composition import register_shadow_event_consumers
from aicrm_next.platform.platform_foundation.internal_events.shadow import OPS_PLAN_APPROVED_EVENT_TYPE
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker

OPS_PLAN_CONSUMERS = [
    "audit_projection_consumer",
    "automation_schedule_refresh_consumer",
    "broadcast_task_planner_consumer",
    "ops_plan_ai_assist_notify_consumer",
]


def _reset() -> None:
    reset_cloud_plan_fixture_state()
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _configure(
    monkeypatch,
    *,
    enabled: bool = True,
    allowed_event_types: str = "ops_plan.approved",
) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED", "1" if enabled else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", allowed_event_types)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")
    _reset()
    return TestClient(create_app(), raise_server_exceptions=False)


def _approve(*, operator: str = "pytest") -> dict:
    return ApproveCloudPlanCommand().execute("plan_probe", operator=operator)


def _events():
    return InternalEventService().list_events({"event_type": OPS_PLAN_APPROVED_EVENT_TYPE})


def _event():
    events, total = _events()
    assert total == 1
    return events[0]


def _runs(event_id: str):
    return InternalEventService().list_consumer_runs({"event_id": event_id})


def _run_consumer(event_id: str, consumer_name: str) -> dict:
    return InternalEventWorker().dispatch_one_consumer(
        event_id,
        consumer_name,
        dry_run=False,
        force=False,
        reason="ops_plan_slice_unit_test",
    )


def test_ops_plan_flag_off_does_not_emit(monkeypatch) -> None:
    _configure(monkeypatch, enabled=False)

    result = _approve()
    events, total = _events()

    assert result["ok"] is True
    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "ops_plan_internal_events_disabled"
    assert result["internal_event_id"] == ""
    assert result["internal_event_consumer_run_count"] == 0
    assert events == []
    assert total == 0


def test_ops_plan_approved_emits_once_with_expected_consumers(monkeypatch) -> None:
    _configure(monkeypatch)
    register_shadow_event_consumers()

    result = _approve(operator="ops-operator")
    duplicate = _approve(operator="ops-operator")
    event = _event()
    trace_events, trace_total = InternalEventService().list_events(
        {"event_type": OPS_PLAN_APPROVED_EVENT_TYPE, "trace_id": "plan_probe"}
    )
    runs, run_total = _runs(event.event_id)

    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == event.event_id
    assert result["internal_event_consumer_run_count"] == 4
    assert duplicate["internal_event_id"] == event.event_id
    assert trace_total == 1
    assert trace_events[0].event_id == event.event_id
    assert event.aggregate_type == "cloud_orchestrator_plan"
    assert event.aggregate_id == "plan_probe"
    assert event.subject_type == "ops_plan"
    assert event.subject_id == "plan_probe"
    assert event.idempotency_key == "ops_plan.approved:cloud_orchestrator_plan:plan_probe:approved"
    assert event.payload_summary_json == {
        "plan_id": "plan_probe",
        "source": "cloud_plan",
        "operator": "ops-operator",
        "target_count": 2,
        "campaign_code": "",
        "approved": True,
        "plan_type": "cloud_plan",
        "stage": "approved",
        "status": "approved",
    }
    assert "wm_a" not in str(event.payload_summary_json)
    assert "wm_b" not in str(event.payload_summary_json)
    assert "13800138000" not in str(event.payload_summary_json)
    assert "prompt" not in str(event.payload_summary_json).lower()
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == OPS_PLAN_CONSUMERS
    assert "ai_assist_notify_consumer" not in [run.consumer_name for run in runs]
    assert all(run.status == "pending" for run in runs)
    assert all(run.attempt_count == 0 for run in runs)


def test_ops_plan_requires_explicit_event_type_allowlist(monkeypatch) -> None:
    _configure(monkeypatch, allowed_event_types="")

    result = _approve()
    events, total = _events()

    assert result["ok"] is True
    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_reason"] == "ops_plan_event_type_not_explicitly_allowed"
    assert result["internal_event_id"] == ""
    assert events == []
    assert total == 0


def test_ops_plan_reuses_legacy_idempotency_key(monkeypatch) -> None:
    _configure(monkeypatch)
    register_shadow_event_consumers()
    service = InternalEventService()
    legacy = service.emit_event(
        event_type=OPS_PLAN_APPROVED_EVENT_TYPE,
        event_version=1,
        aggregate_type="cloud_orchestrator_plan",
        aggregate_id="plan_probe",
        subject_type="ops_plan",
        subject_id="plan_probe",
        idempotency_key="ops_plan.approved:cloud_orchestrator_plan:plan_probe",
        source_module="legacy.ops_plan",
        source_command_id="plan_probe",
        correlation_id="plan_probe",
        context=CommandContext(actor_id="legacy", actor_type="admin", trace_id="plan_probe", request_id="legacy-plan-probe"),
        payload={"legacy": True},
        payload_summary={"plan_id": "plan_probe", "legacy": True},
    )

    result = _approve()
    events, total = _events()
    new_key_events, new_key_total = service.list_events(
        {"idempotency_key": "ops_plan.approved:cloud_orchestrator_plan:plan_probe:approved"}
    )

    assert total == 1
    assert events[0].event_id == legacy["event"]["event_id"]
    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == legacy["event"]["event_id"]
    assert result["internal_event_reason"] == "ops_plan_legacy_idempotency_key_reused"
    assert new_key_events == []
    assert new_key_total == 0


def test_ops_plan_legacy_ai_assist_consumer_run_has_dispatch_handler(monkeypatch) -> None:
    _configure(monkeypatch)
    _approve()
    event = _event()
    repo = build_internal_event_repository()
    legacy_run = repo.create_consumer_run(
        event=event,
        consumer_name="ai_assist_notify_consumer",
        consumer_type="orchestration",
    )

    result = InternalEventWorker().dispatch_one_consumer(
        event.event_id,
        "ai_assist_notify_consumer",
        dry_run=False,
        force=False,
        reason="ops_plan_legacy_consumer_compat_test",
    )
    runs, run_total = _runs(event.event_id)

    assert legacy_run.consumer_name == "ai_assist_notify_consumer"
    assert result["consumer_run"]["status"] == "skipped"
    assert result["attempt"]["response_summary_json"]["reason"] == "ops_plan_legacy_ai_assist_notify_not_configured"
    assert run_total == 5
    assert sorted(run.consumer_name for run in runs) == sorted([*OPS_PLAN_CONSUMERS, "ai_assist_notify_consumer"])


def test_ops_plan_consumers_are_noop_or_skipped_without_external_work(monkeypatch) -> None:
    _configure(monkeypatch)
    _approve()
    event = _event()

    schedule = _run_consumer(event.event_id, "automation_schedule_refresh_consumer")
    ai_assist = _run_consumer(event.event_id, "ops_plan_ai_assist_notify_consumer")
    audit = _run_consumer(event.event_id, "audit_projection_consumer")
    planner = _run_consumer(event.event_id, "broadcast_task_planner_consumer")
    _jobs, job_total = ExternalEffectService().list_jobs({})

    assert schedule["consumer_run"]["status"] == "succeeded"
    assert schedule["attempt"]["response_summary_json"]["reason"] == "automation_schedule_refresh_shadow_only"
    assert ai_assist["consumer_run"]["status"] == "skipped"
    assert ai_assist["attempt"]["response_summary_json"]["reason"] == "ops_plan_ai_assist_notify_not_configured"
    assert audit["consumer_run"]["status"] == "succeeded"
    assert audit["attempt"]["response_summary_json"]["audit_projection"] == "ops_plan_approved_recorded"
    assert planner["consumer_run"]["status"] == "succeeded"
    assert planner["attempt"]["response_summary_json"]["planner_result"] == "planner_reused_broadcast_job"
    assert planner["attempt"]["response_summary_json"]["broadcast_job_count"] == 2
    assert planner["attempt"]["response_summary_json"]["real_external_call_executed"] is False
    assert job_total == 0


def test_ops_plan_admin_api_redacts_summary_and_hides_payload_json(monkeypatch) -> None:
    client = _configure(monkeypatch)
    _approve()
    event = _event()

    list_payload = client.get("/api/admin/internal-events", params={"event_type": OPS_PLAN_APPROVED_EVENT_TYPE}).json()
    detail_payload = client.get(f"/api/admin/internal-events/{event.event_id}").json()

    assert list_payload["ok"] is True
    assert "payload_json" not in list_payload["items"][0]
    assert "payload_json" not in detail_payload
    payload_text = str(list_payload) + str(detail_payload)
    assert "wm_a" not in payload_text
    assert "wm_b" not in payload_text
    assert "13800138000" not in payload_text
    assert "完整 prompt" not in payload_text


def test_ops_plan_pair_allowlist_blocks_auto_execute_but_single_consumer_still_works(monkeypatch) -> None:
    _configure(monkeypatch, allowed_event_types="payment.succeeded,ops_plan.approved")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "order_projection_consumer,audit_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", "payment.succeeded:order_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "1")
    _approve()
    event = _event()
    worker = InternalEventWorker()

    preview = worker.preview_due(
        batch_size=1,
        event_types=[OPS_PLAN_APPROVED_EVENT_TYPE],
        consumer_names=["audit_projection_consumer", "automation_schedule_refresh_consumer"],
    )
    execute = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[OPS_PLAN_APPROVED_EVENT_TYPE],
        consumer_names=["audit_projection_consumer", "automation_schedule_refresh_consumer"],
    )
    manual = worker.dispatch_one_consumer(
        event.event_id,
        "audit_projection_consumer",
        dry_run=False,
        force=False,
        reason="ops_plan_manual_single_consumer_test",
    )
    runs, _ = _runs(event.event_id)

    assert preview["counts"]["candidate_count"] == 0
    assert preview["event_consumers"] == []
    assert execute["counts"]["processed_count"] == 0
    assert execute["event_consumers"] == []
    assert manual["consumer_run"]["status"] == "succeeded"
    assert next(run for run in runs if run.consumer_name == "broadcast_task_planner_consumer").status == "pending"


def test_diagnostics_exposes_ops_plan_flag(monkeypatch) -> None:
    client = _configure(monkeypatch)

    response = client.get("/api/admin/internal-events/diagnostics")

    assert response.status_code == 200
    assert response.json()["ops_plan_internal_events_enabled"] is True
