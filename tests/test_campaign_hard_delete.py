from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_write import reset_campaign_write_fixture_state
from aicrm_next.main import create_app


CAMPAIGN_CODE = "camp_next_read_fixture"


def test_campaign_delete_is_next_commandbus_contract(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_campaign_read_fixture_state()
    reset_campaign_write_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.request(
        "DELETE",
        f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}",
        json={},
        headers={"Idempotency-Key": "campaign-delete-next-native"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["command_name"] == "cloud_orchestrator.campaign.delete"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["write_model_status"] == "deleted"
