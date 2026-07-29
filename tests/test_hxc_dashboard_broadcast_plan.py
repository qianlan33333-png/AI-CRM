from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.hxc.hxc_dashboard.safe_mode import reset_hxc_safe_mode_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "hxc-dashboard-broadcast-plan-test")
    reset_hxc_safe_mode_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_broadcast_plan_requires_targets_and_content(monkeypatch) -> None:
    client = _client(monkeypatch)

    no_targets = client.post("/api/admin/hxc-dashboard/broadcast", json={"content": "hello"})
    no_content = client.post("/api/admin/hxc-dashboard/broadcast", json={"external_userids": ["wx_ext_001"]})

    assert no_targets.status_code == 400
    assert no_targets.json()["error"] == "no targets"
    assert no_content.status_code == 400
    assert no_content.json()["error"] == "empty content"
    assert no_targets.json()["fallback_used"] is False
    assert no_content.json()["fallback_used"] is False


def test_broadcast_plan_creates_blocked_side_effect_plan_only(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/api/admin/hxc-dashboard/broadcast",
        json={"external_userids": ["wx_ext_001", "wx_ext_002"], "content": "Next safe broadcast plan"},
    )

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    body = response.json()
    assert body["source_status"] == "next_hxc_broadcast_plan"
    assert body["status"] == "planned_blocked"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["hxc_broadcast_executed"] is False
    assert body["wecom_send_executed"] is False
    assert body["wecom_api_called"] is False
    assert body["side_effect_plan"]["effect_type"] == "hxc.broadcast"
    assert body["side_effect_plan"]["adapter_name"] == "wecom_broadcast"
    assert body["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert body["side_effect_plan"]["status"] == "blocked"
    assert body["side_effect_plan"]["requires_approval"] is True
    assert body["external_call_attempt"]["status"] == "blocked"
