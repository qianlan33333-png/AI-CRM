from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.growth.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.main import create_app


CAMPAIGN_CODE = "camp_next_read_fixture"


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_campaign_read_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_contract(payload: dict):
    assert payload["ok"] is True
    assert payload["source_status"] == "next_cloud_orchestrator_campaign_read"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def _assert_headers(response):
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert response.headers["X-AICRM-Real-External-Call-Executed"] == "false"
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_campaign_list_read_route_is_next_contract(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/api/admin/cloud-orchestrator/campaigns")
    assert response.status_code == 200
    _assert_headers(response)
    payload = response.json()
    _assert_contract(payload)

    assert payload["count"] == 1
    assert payload["total"] == 1
    assert payload["items"][0]["campaign_code"] == CAMPAIGN_CODE
    assert payload["campaigns"][0]["source_type"] == "campaign_read_model"


def test_campaign_detail_members_and_steps_are_next_read_routes(monkeypatch):
    client = _client(monkeypatch)

    detail = client.get(f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}")
    assert detail.status_code == 200
    _assert_headers(detail)
    detail_payload = detail.json()
    _assert_contract(detail_payload)
    assert detail_payload["campaign"]["campaign"]["campaign_code"] == CAMPAIGN_CODE
    assert detail_payload["campaign"]["segments"][0]["steps"][0]["content_text"] == "fixture hello"

    members = client.get(f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/members")
    assert members.status_code == 200
    _assert_headers(members)
    members_payload = members.json()
    _assert_contract(members_payload)
    assert members_payload["total"] == 2
    assert len(members_payload["members"]) == 2

    steps = client.get(f"/api/admin/cloud-orchestrator/campaigns/{CAMPAIGN_CODE}/steps")
    assert steps.status_code == 200
    _assert_headers(steps)
    steps_payload = steps.json()
    _assert_contract(steps_payload)
    assert steps_payload["count"] == 1
    assert steps_payload["steps"][0]["segment_code"] == "seg_fixture"


def test_missing_campaign_returns_controlled_error_without_fallback(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/api/admin/cloud-orchestrator/campaigns/missing_campaign")
    assert response.status_code == 404
    _assert_headers(response)
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"] == "campaign_not_found"
    assert payload["source_status"] == "next_cloud_orchestrator_campaign_read"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_campaign_page_is_non_empty_and_references_next_read_api(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/admin/cloud-orchestrator/campaigns")
    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    html = response.text
    assert "运营计划审阅" in html
    assert "Campaign" in html
    assert "/api/admin/cloud-orchestrator/campaigns?" in html
    assert "CAMPAIGN_WRITE_DISABLED = false" in html
    assert "Next CommandBus" in html
    assert "production_compat" not in html
