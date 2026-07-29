from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.main import create_app


def test_campaign_read_routes_keep_next_owner_scope(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_campaign_read_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/admin/cloud-orchestrator/campaigns")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    body = response.json()
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["campaigns"]
