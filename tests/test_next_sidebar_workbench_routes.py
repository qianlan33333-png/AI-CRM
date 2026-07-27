from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


NEXT_SIDEBAR_WORKBENCH_CSS = Path("aicrm_next/app/admin_console/static/sidebar_workbench/sidebar_workbench.css")


def _production_client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://probe:probe@127.0.0.1:1/aicrm_probe")
    monkeypatch.setenv("SECRET_KEY", "next-sidebar-workbench-test")
    return TestClient(create_app())


def test_next_sidebar_bind_mobile_page_renders_v2_workbench(monkeypatch):
    client = _production_client(monkeypatch)

    response = client.get("/sidebar/bind-mobile")
    html = response.text

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "Not Found" not in html
    assert "客户侧边栏 V2 工作台" in html
    assert 'data-workbench-url="/api/sidebar/v2/workbench"' in html
    assert 'data-material-send-url="/api/sidebar/v2/materials/send"' in html
    assert 'data-periodic-orders-url="/api/sidebar/v2/periodic-orders"' in html
    assert 'data-periodic-order-remark-url="/api/sidebar/v2/periodic-orders"' in html
    assert "/api/sidebar/bind-mobile" in html
    assert "sidebar_workbench/sidebar_workbench.css" in html
    assert "sidebar_workbench/sidebar_workbench.js" in html
    assert "加载中..." in html
    assert "客户档案绑定" not in html
    assert "只读客户上下文" not in html
    assert "写入仍受保护" not in html
    assert "/api/sidebar/customer-context" not in html
    assert "/api/admin/automation-conversion/member" not in html


def test_next_sidebar_workbench_static_assets_are_served(monkeypatch):
    client = _production_client(monkeypatch)

    css_response = client.get("/static/sidebar_workbench/sidebar_workbench.css")
    js_response = client.get("/static/sidebar_workbench/sidebar_workbench.js")

    assert css_response.status_code == 200
    assert ".profile-card" in css_response.text
    assert js_response.status_code == 200
    assert "other_staff_messages" in js_response.text
    assert "periodic_orders" in js_response.text
    assert "service_period_products" in js_response.text
    assert "data-product-type" in js_response.text
    assert "savePeriodicOrderRemark" in js_response.text


def test_next_sidebar_workbench_css_keeps_dense_three_column_tabs():
    css = NEXT_SIDEBAR_WORKBENCH_CSS.read_text(encoding="utf-8")

    assert css.count("grid-template-columns: repeat(3, minmax(0, 1fr));") >= 2
    assert ".product-seg" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert "font-size: 22px" not in css
    assert "min-height: 50px" not in css
    assert "font-size: 19px" not in css
    assert "min-height: 42px" not in css


def test_next_sidebar_read_apis_return_input_errors_without_404(monkeypatch):
    client = _production_client(monkeypatch)

    status_response = client.get("/api/sidebar/contact-binding-status")
    jssdk_response = client.get("/api/sidebar/jssdk-config")

    assert status_response.status_code == 401
    assert status_response.json()["detail"] == "sidebar context required"
    assert jssdk_response.status_code == 400
    assert jssdk_response.json()["error"] == "url is required"


def test_next_sidebar_jssdk_adapter_returns_blocked_signature_payload(monkeypatch):
    client = _production_client(monkeypatch)

    response = client.get(
        "/api/sidebar/jssdk-config",
        params={"url": "https://www.youcangogogo.com/sidebar/bind-mobile"},
    )

    assert response.status_code == 200
    assert response.json()["source_status"] == "next_jssdk_adapter"
    assert response.json()["adapter_mode"] == "real_blocked"
    assert response.json()["fallback_used"] is False
    assert response.json()["real_external_call_executed"] is False
    assert response.json()["config"]["signature"]
    assert response.json()["agent_config"]["signature"]


def test_next_sidebar_detail_dependencies_exclude_retired_automation_member(monkeypatch):
    client = _production_client(monkeypatch)

    member_response = client.get("/api/admin/automation-conversion/member")
    tags_response = client.get("/api/admin/customers/profile/tags")

    assert member_response.status_code == 404
    assert tags_response.status_code == 400
    assert tags_response.json()["error"] == "unionid is required"


def test_next_owns_sidebar_customer_context_and_profile_readonly_routes(monkeypatch):
    client = _production_client(monkeypatch)

    context_response = client.get("/api/sidebar/customer-context")
    profile_response = client.get("/api/admin/customers/profile")

    assert context_response.status_code == 400
    assert context_response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert context_response.json()["source_status"] == "input_error"
    assert profile_response.status_code == 400
    assert profile_response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert profile_response.json()["source_status"] == "input_error"
