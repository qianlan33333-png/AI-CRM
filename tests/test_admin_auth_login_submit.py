from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.platform.admin_auth.service import CSRF_COOKIE, SESSION_COOKIE
from aicrm_next.main import create_app


def test_local_password_login_route_is_absent_and_cannot_issue_session(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "wecom-only-login-test")
    app = create_app()
    post_login_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/login" and "POST" in set(getattr(route, "methods", ()) or ())
    ]

    assert post_login_routes == []
    response = TestClient(app, raise_server_exceptions=False).post(
        "/login",
        data={"username": "local-admin", "password": "must-not-work"},
        follow_redirects=False,
    )
    assert response.status_code in {401, 403, 404, 405}
    assert SESSION_COOKIE not in response.headers.get("set-cookie", "")
    assert CSRF_COOKIE not in response.headers.get("set-cookie", "")
    assert "must-not-work" not in response.text
