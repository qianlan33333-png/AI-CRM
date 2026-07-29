from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_write import (
    get_campaign_write_audit_events,
    get_campaign_write_side_effect_plans,
    reset_campaign_write_fixture_state,
)
from aicrm_next.main import create_app


CAMPAIGN_CODE = "camp_next_read_fixture"


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_campaign_read_fixture_state()
    reset_campaign_write_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_next_command(response):
    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    body = response.json()
    assert body["ok"] is True
    assert body["command_id"]
    assert body["source_status"] == "next_command"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["campaign_execute_executed"] is False
    assert body["wecom_send_executed"] is False
    assert body["audit_event"]["command_id"] == body["command_id"]
    return body


def test_campaign_approve_reject_pause_delete_use_next_commandbus(monkeypatch):
    client = _client(monkeypatch)

    approve = _assert_next_command(
        client.post(
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/approve",
            json={},
            headers={"Idempotency-Key": "campaign-approve-command"},
        )
    )
    assert approve["command_name"] == "cloud_orchestrator.campaign.approve"
    assert approve["campaign"]["review_status"] == "approved"

    pause = _assert_next_command(
        client.post(
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/pause",
            json={"reason": "test pause"},
            headers={"Idempotency-Key": "campaign-pause-command"},
        )
    )
    assert pause["campaign"]["run_status"] == "paused"

    reject = _assert_next_command(
        client.post(
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/reject",
            json={"reason": "test reject"},
            headers={"Idempotency-Key": "campaign-reject-command"},
        )
    )
    assert reject["campaign"]["review_status"] == "rejected"

    delete = _assert_next_command(
        client.request(
            "DELETE",
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}",
            json={},
            headers={"Idempotency-Key": "campaign-delete-command"},
        )
    )
    assert delete["command_name"] == "cloud_orchestrator.campaign.delete"
    assert delete["write_model_status"] == "deleted"

    assert len(get_campaign_write_audit_events()) >= 4


def test_campaign_start_and_batch_start_return_side_effect_plan_only(monkeypatch):
    client = _client(monkeypatch)

    start = _assert_next_command(
        client.post(
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/start",
            json={},
            headers={"Idempotency-Key": "campaign-start-command"},
        )
    )
    assert start["command_name"] == "cloud_orchestrator.campaign.start"
    assert start["side_effect_plan"]["effect_type"] == "cloud_orchestrator.campaign.start"
    assert start["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert start["side_effect_plan"]["requires_approval"] is True
    assert start["side_effect_plan"]["campaign_execute_executed"] is False
    assert start["side_effect_plan"]["wecom_send_executed"] is False

    batch = _assert_next_command(
        client.post(
            "/api/admin/cloud-orchestrator/campaigns/batch-start",
            json={"campaign_codes": [CAMPAIGN_CODE]},
            headers={"Idempotency-Key": "campaign-batch-start-command"},
        )
    )
    assert batch["command_name"] == "cloud_orchestrator.campaign.batch_start"
    assert batch["started_count"] == 1
    assert batch["side_effect_plan"]["adapter_mode"] == "real_blocked"

    plans = get_campaign_write_side_effect_plans()
    assert len(plans) == 2
    assert all(plan["real_external_call_executed"] is False for plan in plans)


def test_campaign_step_mutations_use_next_commandbus(monkeypatch):
    client = _client(monkeypatch)

    added = _assert_next_command(
        client.post(
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/steps",
            json={"campaign_segment_id": 11, "message_text": "new step"},
            headers={"Idempotency-Key": "campaign-step-add-command"},
        )
    )
    step_index = added["step"]["step_index"]
    assert added["command_name"] == "cloud_orchestrator.campaign.step.add"

    updated = _assert_next_command(
        client.patch(
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/steps/{step_index}",
            json={"message_text": "updated step"},
            headers={"Idempotency-Key": "campaign-step-update-command"},
        )
    )
    assert updated["step"]["content_text"] == "updated step"

    deleted = _assert_next_command(
        client.request(
            "DELETE",
            f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/steps/{step_index}",
            json={},
            headers={"Idempotency-Key": "campaign-step-delete-command"},
        )
    )
    assert deleted["command_name"] == "cloud_orchestrator.campaign.step.delete"


def test_missing_campaign_returns_404_next_command_error(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns/missing_campaign/approve",
        json={},
        headers={"Idempotency-Key": "campaign-missing-command"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["source_status"] == "next_command"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
