from __future__ import annotations

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from aicrm_next.main import create_app


def _iter_api_routes(routes):
    for route in routes:
        original_router = getattr(route, "original_router", None)
        nested = getattr(route, "routes", None) or getattr(original_router, "routes", None)
        if nested:
            yield from _iter_api_routes(nested)
        elif isinstance(route, APIRoute):
            yield route


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "sidebar-bind-mobile-native-page-test")
    return TestClient(create_app())


def _endpoint_module(path: str) -> str:
    app = create_app()
    for route in _iter_api_routes(app.routes):
        if getattr(route, "path", "") == path and "GET" in getattr(route, "methods", set()):
            return route.endpoint.__module__
    raise AssertionError(f"missing route for {path}")


def test_sidebar_bind_mobile_page_renders_from_native_shell(monkeypatch) -> None:
    response = _client(monkeypatch).get("/sidebar/bind-mobile")
    html = response.text

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "客户侧边栏 V2 工作台" in html
    for marker in (
        'id="sidebar-workbench-root"',
        'data-debug-enabled="false"',
        'data-workbench-url="/api/sidebar/v2/workbench"',
        'data-profile-url="/api/sidebar/v2/profile"',
        'data-questionnaires-url="/api/sidebar/v2/questionnaires"',
        'data-products-url="/api/sidebar/v2/products"',
        'data-orders-url="/api/sidebar/v2/orders"',
        'data-materials-url="/api/sidebar/v2/materials"',
        'data-material-send-url="/api/sidebar/v2/materials/send"',
        'data-other-staff-messages-url="/api/sidebar/v2/other-staff-messages"',
        'data-bind-mobile-url="/api/sidebar/bind-mobile"',
        'data-jssdk-config-url="/api/sidebar/jssdk-config"',
        "sidebar_workbench/sidebar_workbench.css",
        "sidebar_workbench/sidebar_workbench.js",
        "https://res.wx.qq.com/open/js/jweixin-1.6.0.js",
    ):
        assert marker in html


def test_sidebar_bind_mobile_page_route_owner_is_native() -> None:
    assert _endpoint_module("/sidebar/bind-mobile") == "aicrm_next.crm.identity_contact.admin_pages"
    assert _endpoint_module("/sidebar/bind-mobile") != "aicrm_next.frontend_compat.legacy_routes"


def test_sidebar_workbench_static_assets_are_still_served(monkeypatch) -> None:
    client = _client(monkeypatch)

    css_response = client.get("/static/sidebar_workbench/sidebar_workbench.css")
    js_response = client.get("/static/sidebar_workbench/sidebar_workbench.js")

    assert css_response.status_code == 200
    assert ".profile-card" in css_response.text
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in css_response.text
    assert js_response.status_code == 200
    assert "other_staff_messages" in js_response.text
    assert "/static/sidebar_workbench/product-card-cover.png" in js_response.text


def test_sidebar_bind_mobile_removed_from_frontend_inventory(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/frontend-compat/legacy-routes")

    assert response.status_code == 404


def test_sidebar_api_routes_stay_next_native(monkeypatch) -> None:
    client = _client(monkeypatch)

    cases = (
        ("GET", "/api/sidebar/contact-binding-status", 401),
        ("GET", "/api/sidebar/binding-status", 401),
        ("GET", "/api/sidebar/jssdk-config", 400),
        ("GET", "/api/sidebar/customer-context", 400),
        ("GET", "/api/sidebar/v2/workbench", 400),
        ("PUT", "/api/sidebar/v2/profile", 403),
        ("GET", "/api/sidebar/v2/materials", 403),
        ("POST", "/api/sidebar/v2/materials/send", 403),
        ("GET", "/api/admin/customers/profile", 400),
    )
    for method, path, expected_status in cases:
        response = client.request(method, path, json={} if method == "POST" else None)

        assert response.status_code == expected_status, path
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_retired_automation_member_route_is_removed(monkeypatch) -> None:
    client = _client(monkeypatch)

    response = client.get("/api/admin/automation-conversion/member")

    assert response.status_code == 404
