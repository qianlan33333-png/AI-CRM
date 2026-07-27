from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_write import get_campaign_write_audit_events, reset_campaign_write_fixture_state
from aicrm_next.main import create_app


def test_campaign_write_idempotency_replays_cached_command_result(monkeypatch):
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    reset_campaign_read_fixture_state()
    reset_campaign_write_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    headers = {"Idempotency-Key": "campaign-write-idempotency"}
    first = client.post("/api/admin/cloud-orchestrator/campaigns/camp_next_read_fixture/start", json={}, headers=headers)
    second = client.post("/api/admin/cloud-orchestrator/campaigns/camp_next_read_fixture/start", json={}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["command_id"] == second_body["command_id"]
    assert first_body["side_effect_plan"]["side_effect_plan_id"] == second_body["side_effect_plan"]["side_effect_plan_id"]

    matching_events = [event for event in get_campaign_write_audit_events() if event["command_id"] == first_body["command_id"]]
    assert len(matching_events) == 1
