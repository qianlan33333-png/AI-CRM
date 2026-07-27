from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _endpoint_module(path: str) -> str:
    app = create_app()
    for route in app.routes:
        if getattr(route, "path", "") == path and "GET" in getattr(route, "methods", set()):
            return route.endpoint.__module__
    raise AssertionError(f"missing route for {path}")


def test_cloud_root_redirect_is_owned_by_native_cloud_module() -> None:
    response = _client().get("/admin/cloud-orchestrator", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/cloud-orchestrator/plans"
    assert _endpoint_module("/admin/cloud-orchestrator") == "aicrm_next.extensions.growth.cloud_orchestrator.api"
    assert _endpoint_module("/admin/cloud-orchestrator") != "aicrm_next.frontend_compat.legacy_routes"


def test_cloud_observability_page_renders_from_native_cloud_module() -> None:
    response = _client().get("/admin/cloud-orchestrator/observability")

    assert response.status_code == 200
    assert "Cloud Orchestrator · 可观察性" in response.text
    assert "工单、审计、漏斗与 Tool 调用统计" in response.text
    assert "返回助手" in response.text
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert _endpoint_module("/admin/cloud-orchestrator/observability") == "aicrm_next.extensions.growth.cloud_orchestrator.api"
    assert _endpoint_module("/admin/cloud-orchestrator/observability") != "aicrm_next.frontend_compat.legacy_routes"


def test_cloud_root_and_observability_removed_from_frontend_inventory() -> None:
    response = _client().get("/api/frontend-compat/legacy-routes")

    assert response.status_code == 404
