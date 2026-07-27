from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.hxc.hxc_dashboard.safe_mode import reset_hxc_safe_mode_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "hxc-dashboard-pages-test")
    reset_hxc_safe_mode_fixture_state()
    return TestClient(create_app(), raise_server_exceptions=False)


def test_hxc_dashboard_page_is_next_owned_and_non_empty(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/hxc-dashboard")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "用户激活漏斗看板" in response.text
    assert "Safe Mode Customer" in response.text
    assert "/api/admin/hxc-dashboard/refresh" in response.text
    assert "/api/admin/hxc-dashboard/broadcast-tasks" in response.text
    assert "/admin/hxc-send-config" in response.text


def test_hxc_send_config_page_is_next_owned_and_non_empty(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/hxc-send-config")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "群发发送人管理" in response.text
    assert "HXC Safe Sender" in response.text
    assert "/api/admin/hxc-dashboard/send-config" in response.text
    assert "/api/admin/hxc-dashboard/refresh-directory" in response.text
