from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_write import reset_campaign_write_fixture_state
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform_foundation.internal_events.shadow import (
    AI_CAMPAIGN_APPROVED_EVENT_TYPE,
    AI_CAMPAIGN_CREATED_EVENT_TYPE,
    AI_CAMPAIGN_STARTED_EVENT_TYPE,
)
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker

AI_CAMPAIGN_CONSUMERS = [
    "ai_campaign_ai_assist_notify_consumer",
    "audit_projection_consumer",
    "broadcast_task_planner_consumer",
    "campaign_summary_consumer",
]


def _reset() -> None:
    reset_campaign_read_fixture_state()
    reset_campaign_write_fixture_state()
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _client(
    monkeypatch,
    *,
    enabled: bool = True,
    allowed_event_types: str = "ai_campaign.created,ai_campaign.approved,ai_campaign.started",
) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED", "1" if enabled else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", allowed_event_types)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")
    _reset()
    return TestClient(create_app(), raise_server_exceptions=False)


def _create(client: TestClient, *, code: str = "camp_ai_event_slice", key: str = "create") -> dict:
    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns",
        json={
            "campaign_code": code,
            "display_name": "AI Campaign Event Slice",
            "objective": "完整 prompt 不应进入 payload_summary",
            "prompt": "完整 prompt 不应进入 internal event list/detail",
            "customer_phone": "13800138000",
            "external_userids": ["wm_ai_campaign_raw_external_userid"],
            "metadata": {
                "group_code": "ai_campaign_test_group",
                "source": "ai_campaign_slice_test",
                "external_userids": ["wm_ai_campaign_raw_external_userid"],
            },
            "operator": "ai-campaign-operator",
        },
        headers={"Idempotency-Key": f"ai-campaign-{key}"},
    )
    assert response.status_code == 200
    return response.json()


def _approve(client: TestClient, *, code: str = "camp_next_read_fixture", key: str = "approve") -> dict:
    response = client.post(
        f"/api/admin/cloud-orchestrator/campaigns/{code}/approve",
        json={"operator": "ai-campaign-operator"},
        headers={"Idempotency-Key": f"ai-campaign-{key}"},
    )
    assert response.status_code == 200
    return response.json()


def _start(client: TestClient, *, code: str = "camp_next_read_fixture", key: str = "start") -> dict:
    response = client.post(
        f"/api/admin/cloud-orchestrator/campaigns/{code}/start",
        json={"operator": "ai-campaign-operator"},
        headers={"Idempotency-Key": f"ai-campaign-{key}"},
    )
    assert response.status_code == 200
    return response.json()


def _events(event_type: str):
    return InternalEventService().list_events({"event_type": event_type})


def _event(event_type: str):
    events, total = _events(event_type)
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
        reason="ai_campaign_slice_unit_test",
    )


def test_ai_campaign_flag_off_does_not_emit(monkeypatch) -> None:
    client = _client(monkeypatch, enabled=False)

    body = _create(client, code="camp_ai_event_flag_off", key="flag-off")
    events, total = _events(AI_CAMPAIGN_CREATED_EVENT_TYPE)

    assert body["ok"] is True
    assert body["internal_event_status"] == "skipped"
    assert body["internal_event_reason"] == "ai_campaign_internal_events_disabled"
    assert body["internal_event_id"] == ""
    assert body["internal_event_consumer_run_count"] == 0
    assert events == []
    assert total == 0


def test_ai_campaign_created_approved_started_emit_once_with_expected_consumers(monkeypatch) -> None:
    client = _client(monkeypatch)

    created = _create(client, code="camp_ai_event_flow", key="create-flow")
    duplicate_created = _create(client, code="camp_ai_event_flow", key="create-flow-duplicate")
    approved = _approve(client, code="camp_ai_event_flow", key="approve-flow")
    duplicate_approved = _approve(client, code="camp_ai_event_flow", key="approve-flow-duplicate")
    started = _start(client, code="camp_ai_event_flow", key="start-flow")
    duplicate_started = _start(client, code="camp_ai_event_flow", key="start-flow-duplicate")

    created_event = _event(AI_CAMPAIGN_CREATED_EVENT_TYPE)
    approved_event = _event(AI_CAMPAIGN_APPROVED_EVENT_TYPE)
    started_event = _event(AI_CAMPAIGN_STARTED_EVENT_TYPE)

    assert created["internal_event_status"] == "emitted"
    assert duplicate_created["internal_event_id"] == created_event.event_id
    assert approved["internal_event_id"] == approved_event.event_id
    assert duplicate_approved["internal_event_id"] == approved_event.event_id
    assert started["internal_event_id"] == started_event.event_id
    assert duplicate_started["internal_event_id"] == started_event.event_id

    assert created_event.aggregate_type == "ai_campaign"
    assert created_event.aggregate_id == "camp_ai_event_flow"
    assert created_event.subject_type == "ai_campaign"
    assert created_event.subject_id == "camp_ai_event_flow"
    assert created_event.idempotency_key == "ai_campaign.created:camp_ai_event_flow:created"
    assert approved_event.idempotency_key == "ai_campaign.approved:camp_ai_event_flow:approved"
    assert started_event.idempotency_key == "ai_campaign.started:camp_ai_event_flow:started"

    for event in (created_event, approved_event, started_event):
        runs, total = _runs(event.event_id)
        assert total == 4
        assert sorted(run.consumer_name for run in runs) == AI_CAMPAIGN_CONSUMERS
        assert all(run.status == "pending" for run in runs)
        assert all(run.attempt_count == 0 for run in runs)

    assert created_event.payload_summary_json["campaign_code"] == "camp_ai_event_flow"
    assert created_event.payload_summary_json["objective_present"] is True
    assert approved_event.payload_summary_json["approved"] is True
    assert started_event.payload_summary_json["started"] is True
    assert "13800138000" not in str(created_event.payload_summary_json)
    assert "wm_ai_campaign_raw_external_userid" not in str(created_event.payload_summary_json)
    assert "完整 prompt" not in str(created_event.payload_summary_json)
    assert "external_userids" not in str(created_event.payload_json["campaign"]["metadata"])


def test_ai_campaign_consumers_are_noop_or_skipped_without_external_work(monkeypatch) -> None:
    client = _client(monkeypatch)
    _create(client, code="camp_ai_event_consumers", key="consumer-create")
    event = _event(AI_CAMPAIGN_CREATED_EVENT_TYPE)
    campaign_summary = _run_consumer(event.event_id, "campaign_summary_consumer")
    ai_assist = _run_consumer(event.event_id, "ai_campaign_ai_assist_notify_consumer")
    planner = _run_consumer(event.event_id, "broadcast_task_planner_consumer")
    audit = _run_consumer(event.event_id, "audit_projection_consumer")
    _jobs, job_total = ExternalEffectService().list_jobs({})

    assert campaign_summary["consumer_run"]["status"] == "skipped"
    assert campaign_summary["attempt"]["response_summary_json"]["reason"] == "campaign_summary_not_configured"
    assert ai_assist["consumer_run"]["status"] == "skipped"
    assert ai_assist["attempt"]["response_summary_json"]["reason"] == "ai_campaign_ai_assist_notify_not_configured"
    assert planner["consumer_run"]["status"] == "skipped"
    assert planner["attempt"]["response_summary_json"]["reason"] == "missing_plan_id"
    assert audit["consumer_run"]["status"] == "succeeded"
    assert audit["attempt"]["response_summary_json"]["reason"] == "audit_projection_shadow_only"
    assert job_total == 0


def test_ai_campaign_admin_api_redacts_summary_and_hides_payload_json(monkeypatch) -> None:
    client = _client(monkeypatch)
    _create(client, code="camp_ai_event_redaction", key="redaction-create")
    event = _event(AI_CAMPAIGN_CREATED_EVENT_TYPE)

    list_payload = client.get("/api/admin/internal-events", params={"event_type": AI_CAMPAIGN_CREATED_EVENT_TYPE}).json()
    detail_payload = client.get(f"/api/admin/internal-events/{event.event_id}").json()

    assert list_payload["ok"] is True
    assert "payload_json" not in list_payload["items"][0]
    assert "payload_json" not in detail_payload
    payload_text = str(list_payload) + str(detail_payload)
    assert "13800138000" not in payload_text
    assert "wm_ai_campaign_raw_external_userid" not in payload_text
    assert "完整 prompt" not in payload_text


def test_ai_campaign_pair_allowlist_blocks_auto_execute_but_single_consumer_still_works(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        allowed_event_types="payment.succeeded,ai_campaign.created,ai_campaign.approved,ai_campaign.started",
    )
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "order_projection_consumer,campaign_summary_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS", "payment.succeeded:order_projection_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "1")
    _create(client, code="camp_ai_event_pair_block", key="pair-create")
    event = _event(AI_CAMPAIGN_CREATED_EVENT_TYPE)
    worker = InternalEventWorker()

    preview = worker.preview_due(
        batch_size=1,
        event_types=[AI_CAMPAIGN_CREATED_EVENT_TYPE],
        consumer_names=["campaign_summary_consumer", "ai_campaign_ai_assist_notify_consumer"],
    )
    execute = worker.run_due(
        batch_size=1,
        dry_run=False,
        event_types=[AI_CAMPAIGN_CREATED_EVENT_TYPE],
        consumer_names=["campaign_summary_consumer", "ai_campaign_ai_assist_notify_consumer"],
    )
    manual = worker.dispatch_one_consumer(
        event.event_id,
        "campaign_summary_consumer",
        dry_run=False,
        force=False,
        reason="ai_campaign_manual_single_consumer_test",
    )
    runs, _ = _runs(event.event_id)

    assert preview["counts"]["candidate_count"] == 0
    assert preview["event_consumers"] == []
    assert execute["counts"]["processed_count"] == 0
    assert execute["event_consumers"] == []
    assert manual["consumer_run"]["status"] == "skipped"
    assert next(run for run in runs if run.consumer_name == "broadcast_task_planner_consumer").status == "pending"


def test_diagnostics_exposes_ai_campaign_flag(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/admin/internal-events/diagnostics")

    assert response.status_code == 200
    assert response.json()["ai_campaign_internal_events_enabled"] is True
