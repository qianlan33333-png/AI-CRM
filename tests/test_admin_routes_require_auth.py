from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.platform.admin_auth.service import CSRF_COOKIE
from aicrm_next.main import create_app
from tests.admin_auth_test_helpers import install_admin_session
from tests.sidebar_auth_test_helpers import install_sidebar_auth
from tools.check_admin_route_auth import check_admin_route_auth_gate


def test_admin_api_routes_require_session_when_enforced(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/admin/channels")

    assert response.status_code == 401
    assert response.json()["error"] == "admin_auth_required"


def test_admin_pages_redirect_to_login_when_enforced(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/admin/api-docs", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=/admin/api-docs"


def test_production_data_mode_enforces_all_admin_pages_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_ADMIN_AUTH_ENFORCED", raising=False)
    monkeypatch.setattr("aicrm_next.platform.admin_auth.guards.production_data_ready", lambda: True)
    client = TestClient(create_app(), raise_server_exceptions=False)

    admin_pages = [
        "/admin/automation-conversion",
        "/admin/automation-agents",
        "/admin/customers",
        "/admin/questionnaires",
        "/admin/wechat-pay/products",
        "/admin/api-docs",
    ]
    for path in admin_pages:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == f"/login?next={path}"


def test_production_data_mode_enforces_all_admin_apis_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_ADMIN_AUTH_ENFORCED", raising=False)
    monkeypatch.setattr("aicrm_next.platform.admin_auth.guards.production_data_ready", lambda: True)
    client = TestClient(create_app(), raise_server_exceptions=False)

    for path in ["/api/admin/ai-audience/packages", "/api/admin/automation-agents", "/api/admin/channels"]:
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["error"] == "admin_auth_required"


def test_setup_wizard_redirects_to_login_when_enforced(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/setup/wizard", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=/setup/wizard"


def test_admin_session_allows_protected_route_when_enforced(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_session(client, "super_admin")

    response = client.get("/api/admin/channels")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_sidebar_routes_do_not_use_admin_session_when_enforced(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)
    install_admin_session(client, "super_admin")

    admin_only = client.get("/api/sidebar/profile?external_userid=wx_ext_001")
    headers = install_sidebar_auth(
        client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
    )
    response = client.get("/api/sidebar/profile?external_userid=wx_ext_001", headers=headers)

    assert admin_only.status_code == 401
    assert admin_only.json()["error"] == "sidebar_context_required"
    assert response.status_code == 200
    assert response.json()["route_owner"] == "ai_crm_next"


def test_public_routes_remain_public_when_admin_auth_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)

    assert client.get("/health").status_code == 200
    assert client.get("/api/system/health").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/static/admin_console/admin_console.css").status_code == 200
    assert client.get("/api/sidebar/jssdk-config").status_code == 400
    assert client.get("/sidebar/bind-mobile").status_code == 200
    assert client.get("/api/h5/questionnaires/slug").status_code != 401
    assert client.get("/api/wecom/events").status_code != 401
    assert client.get("/wecom/external-contact/callback").status_code != 401


def test_admin_write_routes_require_session_bound_csrf_when_enforced(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)
    issued = install_admin_session(client, "super_admin")
    del client.headers["X-CSRF-Token"]
    client.cookies.delete(CSRF_COOKIE)

    missing = client.post("/api/admin/jobs/order-identity-repair/run", json={})
    assert missing.status_code == 403
    assert missing.json()["error"] == "admin_csrf_required"

    client.cookies.set(CSRF_COOKIE, issued.csrf_token)
    cookie_only = client.post("/api/admin/jobs/order-identity-repair/run", json={})
    assert cookie_only.status_code == 403
    assert cookie_only.json()["error"] == "admin_csrf_required"

    passed_csrf = client.post(
        "/api/admin/jobs/order-identity-repair/run",
        json={},
        headers={"X-CSRF-Token": issued.csrf_token},
    )
    assert passed_csrf.status_code == 401
    assert passed_csrf.json()["error"] != "admin_csrf_required"


def test_admin_route_auth_checker_passes() -> None:
    assert check_admin_route_auth_gate() == []
