from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.run_due import reset_run_due_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    reset_campaign_read_fixture_state()
    reset_run_due_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_preview_accepts_query_params_and_does_not_execute_runtime(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due/preview?batch_size=1&dry_run=false",
        headers={"Authorization": "Bearer timer-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "next_run_due_preview"
    assert body["candidate_count"] == 1
    assert body["estimated_actions"]["wecom_send_count"] == 0
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["campaign_runtime_executed"] is False
    assert body["automation_runtime_executed"] is False
    assert body["wecom_send_executed"] is False


def test_run_due_missing_body_uses_controlled_default(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due",
        headers={"Authorization": "Bearer timer-token", "Idempotency-Key": "run-due-default-body"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_status"] == "next_run_due_plan"
    assert body["candidate_count"] == 2
    assert body["side_effect_plan"]["payload_summary"]["batch_size"] == 200


def test_invalid_batch_size_returns_400_input_error(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due",
        json={"batch_size": 0},
        headers={"Authorization": "Bearer timer-token"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert "batch_size" in body["error"]
