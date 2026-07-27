from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
READ_MODEL = ROOT / "aicrm_next/extensions/growth/cloud_orchestrator/campaigns_read.py"
API = ROOT / "aicrm_next/extensions/growth/cloud_orchestrator/api.py"


def test_campaign_read_model_has_no_real_send_or_http_clients():
    source = READ_MODEL.read_text(encoding="utf-8")

    forbidden = [
        "WeCom" + "Client",
        "dispatch" + "_wecom_task",
        "process" + "_due_campaign_members",
        "upload" + "_media",
        "media" + "/upload",
        "requests.",
        "http" + "x",
        "Cloud" + "Orchestrator execute",
    ]
    for marker in forbidden:
        assert marker not in source


def test_campaign_get_routes_do_not_call_legacy_forward_or_runtime(monkeypatch):
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    reset_campaign_read_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    paths = [
        "/api/admin/cloud-orchestrator/campaigns",
        "/api/admin/cloud-orchestrator/campaigns/camp_next_read_fixture",
        "/api/admin/cloud-orchestrator/campaigns/camp_next_read_fixture/members",
        "/api/admin/cloud-orchestrator/campaigns/camp_next_read_fixture/steps",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert response.headers["X-AICRM-Fallback-Used"] == "false"
        assert "X-AICRM-Compatibility-Facade" not in response.headers
        payload = response.json()
        assert payload["source_status"] == "next_cloud_orchestrator_campaign_read"
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False


def test_campaign_read_response_contract_marks_no_external_execution(monkeypatch):
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    reset_campaign_read_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/admin/cloud-orchestrator/campaigns")
    assert response.status_code == 200
    payload = response.json()
    assert payload["real_external_call_executed"] is False
    assert payload["fallback_used"] is False
    assert payload["route_owner"] == "ai_crm_next"
    assert "payment" not in API.read_text(encoding="utf-8").lower()
    assert "openclaw" not in API.read_text(encoding="utf-8").lower()
