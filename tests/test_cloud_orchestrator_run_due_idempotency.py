from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.run_due import get_run_due_side_effect_plans, reset_run_due_fixture_state
from aicrm_next.main import create_app


def test_repeated_idempotency_key_returns_same_command_result(monkeypatch):
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    reset_campaign_read_fixture_state()
    reset_run_due_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    first = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due",
        json={"batch_size": 10},
        headers={"Idempotency-Key": "same-run-due-key", "Authorization": "Bearer timer-token"},
    )
    second = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due",
        json={"batch_size": 10},
        headers={"Idempotency-Key": "same-run-due-key", "Authorization": "Bearer timer-token"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["command_id"] == second.json()["command_id"]
    assert first.json()["side_effect_plan"]["side_effect_plan_id"] == second.json()["side_effect_plan"]["side_effect_plan_id"]
    assert len(get_run_due_side_effect_plans()) == 1
