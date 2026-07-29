from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.hxc.hxc_dashboard.safe_mode import reset_hxc_safe_mode_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "hxc-dashboard-api-contract-test")
    reset_hxc_safe_mode_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_next_safe(body: dict) -> None:
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False


def test_dashboard_read_contract(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/admin/hxc-dashboard")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    body = response.json()
    _assert_next_safe(body)
    assert body["source_status"] == "next_hxc_dashboard"
    assert body["dashboard_summary"]["total"] == 1
    assert body["rows"]


def test_refresh_and_directory_sync_are_plan_only(monkeypatch) -> None:
    client = _client(monkeypatch)

    refresh = client.post("/api/admin/hxc-dashboard/refresh", json={"trigger_source": "pytest"})
    directory = client.post("/api/admin/hxc-dashboard/refresh-directory", json={})

    for response, source_status, effect_type in (
        (refresh, "next_hxc_refresh_plan", "hxc.refresh"),
        (directory, "next_hxc_directory_sync_plan", "hxc.directory_sync"),
    ):
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        body = response.json()
        _assert_next_safe(body)
        assert body["source_status"] == source_status
        assert body["status"] == "planned_blocked"
        assert body["side_effect_plan"]["effect_type"] == effect_type
        assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
        assert body["external_call_attempt"]["status"] == "blocked"
        assert body["hxc_refresh_executed"] is False
        assert body["directory_sync_executed"] is False
        assert body["wecom_api_called"] is False


def test_options_and_unknown_paths_are_controlled_next_responses(monkeypatch) -> None:
    client = _client(monkeypatch)

    options = client.options("/api/admin/hxc-dashboard/refresh")
    unknown_get = client.get("/api/admin/hxc-dashboard/unlisted")
    unknown_post = client.post("/api/admin/hxc-dashboard/unlisted", json={})

    assert options.status_code == 200
    assert options.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert options.json()["source_status"] == "next_hxc_refresh_plan"
    for response in (unknown_get, unknown_post):
        assert response.status_code == 404
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        body = response.json()
        assert body["source_status"] == "next_hxc_unknown_path"
        assert body["fallback_used"] is False
        assert body["error"] == "not_found"
