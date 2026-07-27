from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.platform.admin_auth.service import CSRF_COOKIE, SESSION_COOKIE
from aicrm_next.main import create_app
from tests.admin_auth_test_helpers import install_admin_session


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "admin-auth-logout-test")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_logout_redirects_to_login_and_clears_next_cookie(monkeypatch) -> None:
    client = _client(monkeypatch)
    issued = install_admin_session(client, "super_admin", subject="admin:break-glass", principal_id="admin-break-glass")

    response = client.get("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert SESSION_COOKIE in response.headers["set-cookie"]
    assert CSRF_COOKIE in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"] or "max-age=0" in response.headers["set-cookie"].lower()
    assert not client.app.state.auth_session_service.introspect(issued.session_cookie).active


def test_logout_options_is_next_diagnostics(monkeypatch) -> None:
    response = _client(monkeypatch).options("/logout")

    assert response.status_code == 200
    assert response.json()["route"] == "/logout"
    assert response.json()["fallback_used"] is False
    assert response.json()["real_external_call_executed"] is False
