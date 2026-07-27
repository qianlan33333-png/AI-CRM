from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REMOVED_FILES = [
    ROOT / "aicrm_next/app/admin_console/business_closure.py",
    ROOT / "aicrm_next/app/admin_console/templates/admin_shell/business_closure.html",
    ROOT / "frontend/admin/business_closure/status_model.ts",
    ROOT / "frontend/admin/business_closure/business_closure_overview.ts",
    ROOT / "aicrm_next/app/admin_console/static/admin_console/p1/business_closure/status_model.js",
    ROOT / "aicrm_next/app/admin_console/static/admin_console/p1/business_closure/status_model.d.ts",
    ROOT / "aicrm_next/app/admin_console/static/admin_console/p1/business_closure/business_closure_overview.js",
    ROOT / "aicrm_next/app/admin_console/static/admin_console/p1/business_closure/business_closure_overview.d.ts",
    ROOT / "tests/frontend/p1_business_closure_status.test.mjs",
]


@pytest.fixture()
def frontend_client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_p1_diagnostics_page_route_is_removed(frontend_client) -> None:
    response = frontend_client.get("/admin/business-closure")

    assert response.status_code == 404
    assert "Business Closure / P1 Readiness" not in response.text
    assert "P1 诊断状态" not in response.text


def test_p1_diagnostics_nav_entry_is_removed(frontend_client) -> None:
    response = frontend_client.get("/admin")

    assert response.status_code == 200
    assert "P1 诊断状态" not in response.text
    assert "/admin/business-closure" not in response.text


def test_p1_diagnostics_files_are_physically_removed() -> None:
    for path in REMOVED_FILES:
        assert not path.exists(), f"retired P1 diagnostics artifact still exists: {path}"


def test_p1_diagnostics_route_manifest_entry_is_removed() -> None:
    manifest = (ROOT / "docs/architecture/route_ownership_manifest.yml").read_text(encoding="utf-8")
    routes = (ROOT / "aicrm_next/app/admin_console/routes.py").read_text(encoding="utf-8")
    navigation = (ROOT / "aicrm_next/app/admin_console/navigation.py").read_text(encoding="utf-8")

    assert "/admin/business-closure" not in manifest
    assert "admin_business_closure_page" not in routes
    assert "business_closure_payload" not in routes
    assert "api.admin_business_closure_page" not in navigation
    assert "P1 诊断状态" not in navigation


def test_p1_diagnostics_frontend_test_entry_is_removed_from_package_json() -> None:
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    frontend_test_command = package_json["scripts"]["test:frontend"]

    assert "p1_business_closure_status.test.mjs" not in frontend_test_command
    assert "business_closure" not in frontend_test_command
