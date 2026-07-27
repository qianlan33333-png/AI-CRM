from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.external_effects import reset_external_effect_fixture_state


def _client() -> TestClient:
    reset_external_effect_fixture_state()
    return TestClient(create_app())


def _endpoint_module(path: str) -> str:
    app = create_app()
    for route in app.routes:
        if getattr(route, "path", "") == path and "GET" in getattr(route, "methods", set()):
            return route.endpoint.__module__
    raise AssertionError(f"missing route for {path}")


def test_user_ops_ui_compatibility_entry_redirects_to_native_workspace() -> None:
    response = _client().get("/admin/user-ops/ui", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/user-ops"
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_user_ops_workspace_page_renders_from_native_shell() -> None:
    response = _client().get("/admin/user-ops")

    assert response.status_code == 200
    assert "运营管理" in response.text
    for marker in (
        "overview-cards",
        "filter-class-term",
        "filter-keyword",
        "filter-mobile",
        "list-body",
        "batch-send-modal-backdrop",
        "send-records-backdrop",
        "customer-detail-backdrop",
        "preview-batch-send-btn",
        "execute-batch-send-btn",
        "include-dnd-toggle",
        "include-dnd-confirm-toggle",
    ):
        assert marker in response.text
    assert "X-AICRM-Compatibility-Facade" not in response.headers


def test_user_ops_workspace_api_string_contract_is_preserved() -> None:
    response = _client().get("/admin/user-ops")

    assert response.status_code == 200
    for marker in (
        "/api/admin/user-ops/overview",
        "/api/admin/user-ops/list",
        "/api/admin/user-ops/do-not-disturb",
        "/api/admin/user-ops/batch-send/preview",
        "/api/admin/user-ops/batch-send/execute",
        "/api/admin/user-ops/send-records",
        "/api/admin/user-ops/export",
        "/api/admin/miniprogram-library",
        "/api/customers/",
        "/timeline?limit=20",
    ):
        assert marker in response.text


def test_user_ops_admin_page_routes_are_owned_by_native_module() -> None:
    assert _endpoint_module("/admin/user-ops/ui") == "aicrm_next.ops_enrollment.admin_pages"
    assert _endpoint_module("/admin/user-ops") == "aicrm_next.ops_enrollment.admin_pages"


def test_user_ops_pages_removed_from_frontend_compat_inventory() -> None:
    response = _client().get("/api/frontend-compat/legacy-routes")

    assert response.status_code == 404


def test_user_ops_write_like_routes_remain_no_real_side_effect() -> None:
    client = _client()

    preview = client.post(
        "/api/admin/user-ops/batch-send/preview",
        json={"selected_ids": [1], "content": "测试消息"},
    )
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["side_effect_safety"]["real_wecom_dispatch_executed"] is False
    assert preview_body["side_effect_safety"]["side_effect_executed"] is False

    execute = client.post(
        "/api/admin/user-ops/batch-send/execute",
        json={"selected_ids": [1], "content": "测试消息", "confirm": True},
    )
    assert execute.status_code == 200
    execute_body = execute.json()
    assert execute_body["side_effect_safety"]["real_batch_send_executed"] is False
    assert execute_body["side_effect_safety"]["real_wecom_dispatch_executed"] is False
    assert execute_body["side_effect_safety"]["side_effect_executed"] is False
    assert execute_body["execution_backend"] == "external_effect_queue"
    assert execute_body["external_effect_job_ids"]
    assert execute_body["execution_summary"]["side_effect_safety"]["side_effect_executed"] is False
    assert execute_body["execution_summary"]["backend"] == "external_effect_queue"

    dnd = client.post(
        "/api/admin/user-ops/do-not-disturb",
        json={"unionid": "union_ops_001", "reason_code": "manual_set", "reason_text": "运营设置"},
    )
    assert dnd.status_code == 200
    dnd_body = dnd.json()
    assert dnd_body["side_effect_safety"]["real_dnd_write_executed"] is False
    assert dnd_body["side_effect_safety"]["side_effect_executed"] is False

    refresh = client.post(f"/api/admin/user-ops/send-records/{execute_body['record_id']}/refresh")
    assert refresh.status_code == 200
    refresh_body = refresh.json()
    assert refresh_body["refreshed"] is True
    assert refresh_body["external_effect_status_supported"] is True
    assert refresh_body["wecom_delivery_status_supported"] is False
    assert refresh_body["real_external_call_executed"] is False
