from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.run_due import (
    get_run_due_audit_events,
    get_run_due_external_call_attempts,
    get_run_due_side_effect_plans,
    reset_run_due_fixture_state,
)
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_campaign_read_fixture_state()
    reset_run_due_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def _headers(idempotency_key: str) -> dict[str, str]:
    return {"Idempotency-Key": idempotency_key, "Authorization": "Bearer timer-token"}


def _assert_safe_contract(body: dict) -> None:
    assert body["ok"] is True
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["adapter_mode"] == "real_blocked"
    assert body["real_external_call_executed"] is False
    assert body["campaign_runtime_executed"] is False
    assert body["automation_runtime_executed"] is False
    assert body["wecom_send_executed"] is False
    assert body["processed_count"] == 0
    assert body["sent_count"] == 0
    assert body["failed_count"] == 0


def test_preview_command_lists_candidates_without_side_effect_plan(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due/preview",
        json={"batch_size": 10},
        headers=_headers("run-due-preview-command"),
    )

    assert response.status_code == 200
    body = response.json()
    _assert_safe_contract(body)
    assert body["source_status"] == "next_run_due_preview"
    assert body["candidate_count"] == 2
    assert body["estimated_actions"]["planned_message_count"] == 2
    assert body["planned_count"] == 0
    assert get_run_due_side_effect_plans() == []
    assert get_run_due_external_call_attempts() == []
    assert get_run_due_audit_events()


def test_run_due_command_returns_blocked_side_effect_plan(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due",
        json={"batch_size": 10},
        headers=_headers("run-due-plan-command"),
    )

    assert response.status_code == 200
    body = response.json()
    _assert_safe_contract(body)
    assert body["source_status"] == "next_run_due_plan"
    assert body["planned_count"] == 2
    assert body["skipped_count"] == 2
    assert body["side_effect_plan"]["effect_type"] == "cloud_orchestrator.campaign.run_due"
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert body["side_effect_plan"]["requires_approval"] is True
    assert body["side_effect_plan"]["status"] == "blocked"
    assert body["side_effect_plan"]["campaign_runtime_executed"] is False
    assert body["external_call_attempt"]["status"] == "blocked"
    assert body["external_call_attempt"]["adapter_mode"] == "real_blocked"
    assert len(get_run_due_side_effect_plans()) == 1
    assert len(get_run_due_external_call_attempts()) == 1
