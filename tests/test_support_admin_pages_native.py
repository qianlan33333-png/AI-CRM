from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def _client() -> TestClient:
    return TestClient(create_app())


def _endpoint_module(path: str) -> str:
    app = create_app()
    for route in app.routes:
        if getattr(route, "path", "") == path and "GET" in getattr(route, "methods", set()):
            return route.endpoint.__module__
    raise AssertionError(f"missing route for {path}")


def test_runtime_config_page_renders_from_native_admin_config(monkeypatch) -> None:
    from aicrm_next.admin_config import api

    class FakeAdminConfigPageQuery:
        def __call__(self) -> dict:
            return {
                "ok": True,
                "source_status": "test_runtime_config",
                "cards": [{"label": "配置状态", "value": "ok", "description": "fixture"}],
                "sections": [{"title": "运行配置", "headers": ["项目", "状态"], "rows": [["database_mode", "fixture"]]}],
            }

    monkeypatch.setattr(api, "GetAdminConfigPageQuery", FakeAdminConfigPageQuery)

    response = _client().get("/admin/runtime-config")

    assert response.status_code == 200
    assert "运行配置" in response.text
    assert "查看 Next 运行时、发布和外部回调预检状态" in response.text
    assert "test_runtime_config" in response.text
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert _endpoint_module("/admin/runtime-config") == "aicrm_next.admin_config.api"


def test_api_docs_page_renders_from_native_admin_config() -> None:
    response = _client().get("/admin/api-docs")

    assert response.status_code == 200
    assert "API 文档" in response.text
    assert "AI-CRM" in response.text
    assert "接口" in response.text
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert _endpoint_module("/admin/api-docs") == "aicrm_next.admin_config.api"


def test_support_pages_removed_from_frontend_inventory() -> None:
    response = _client().get("/api/frontend-compat/legacy-routes")

    assert response.status_code == 404


def test_api_docs_view_model_import_path_is_native() -> None:
    from aicrm_next.admin_config.api_docs_view_model import build_api_docs_view_model

    assert callable(build_api_docs_view_model)
    assert not (ROOT / "aicrm_next/app/admin_console/api_docs_view_model.py").exists()


def test_api_docs_view_model_has_no_business_router_imports() -> None:
    source = (ROOT / "aicrm_next/admin_config/api_docs_view_model.py").read_text(encoding="utf-8")

    assert "def _router_sources" not in source
    assert "from aicrm_next." not in source
    assert "routes: Iterable[Any]" in source


def test_admin_config_api_has_no_admin_read_model_reverse_dependency() -> None:
    source = (ROOT / "aicrm_next/admin_config/api.py").read_text(encoding="utf-8")

    assert "aicrm_next.admin_read_model" not in source
    assert "from .runtime_view_model import GetAdminConfigPageQuery" in source


def test_admin_config_mcp_defaults_use_static_composition_catalog() -> None:
    source = (ROOT / "aicrm_next/admin_config/application_support.py").read_text(encoding="utf-8")

    assert "aicrm_next.channels.integration_gateway.mcp" not in source
    assert "from aicrm_next.mcp_tool_catalog import MCP_TOOLS" in source
