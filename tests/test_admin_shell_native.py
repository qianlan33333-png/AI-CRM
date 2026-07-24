from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.routing import Match

from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "admin-shell-native-test")
    return TestClient(create_app())


def _route_owner(client: TestClient, path: str, method: str = "GET") -> str:
    scope = {"type": "http", "path": path, "method": method, "root_path": "", "query_string": b""}
    for route in client.app.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                for candidate in getattr(route, "_effective_candidates", ()):
                    if getattr(candidate, "path", "") == path:
                        endpoint = getattr(candidate, "endpoint", None)
                        break
            return getattr(endpoint, "__module__", "")
    return ""


def test_admin_shell_family_routes_resolve_to_native_module(monkeypatch) -> None:
    client = _client(monkeypatch)

    assert _route_owner(client, "/admin") == "aicrm_next.admin_shell.routes"
    assert _route_owner(client, "/admin/logout") == "aicrm_next.admin_shell.routes"


def test_admin_dashboard_page_uses_native_shell_template(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin")
    html = response.text

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "客户管理后台" in html
    assert 'data-admin-shell-source="next_admin_shell"' in html
    assert "快捷入口" in html
    assert "业务总览" not in html
    assert "待处理事项" not in html
    assert "data-shell-context-url" not in html
    for retired_path in (
        "/admin/data-health",
        "/admin/data-quality",
        "/admin/delivery-lineage",
        "/admin/growth-orchestration",
    ):
        assert f'href="{retired_path}"' not in html
    assert 'href="/admin/automation-conversion"' in html
    assert 'href="/admin/customers"' in html


def test_duplicate_shell_status_api_is_physically_retired(monkeypatch) -> None:
    assert _client(monkeypatch).get("/api/admin/dashboard/shell-context").status_code == 404


def test_monitoring_admin_pages_are_physically_retired(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in (
        "/admin/data-health",
        "/admin/data-quality",
        "/admin/delivery-lineage",
        "/admin/growth-orchestration",
        "/admin/growth-orchestration/campaign:c-1",
    ):
        assert client.get(path).status_code == 404


def test_admin_logout_legacy_url_redirects_to_canonical_logout(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/logout"
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_frontend_compat_legacy_inventory_endpoint_removed(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/frontend-compat/legacy-routes")

    assert response.status_code == 404
