from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]
RETIRED_RUNTIME_FILES = (
    ROOT / "aicrm_next/app/admin_console/templates/admin_shell/p1_group_ops_workspace.html",
    ROOT / "aicrm_next/automation/automation_engine/group_ops/draft_api.py",
    ROOT / "aicrm_next/automation/automation_engine/group_ops/draft_repository.py",
    ROOT / "aicrm_next/automation/automation_engine/group_ops/draft_service.py",
    ROOT / "aicrm_next/automation/automation_engine/group_ops/governance_api.py",
    ROOT / "aicrm_next/automation/automation_engine/group_ops/governance_repository.py",
    ROOT / "aicrm_next/automation/automation_engine/group_ops/governance_service.py",
    ROOT / "aicrm_next/app/admin_console/static/admin_console/p1/p1_group_ops_workspace",
    ROOT / "scripts/diagnose_p1_group_ops_workspace_bridge_acceptance.py",
)

ALLOWED_INTEGRATION_ROUTES = {
    "/api/automation/group-ops/webhooks/{webhook_key}",
    "/api/automation/group-ops/broadcast",
}

REQUIRED_FORMAL_ROUTES = {
    ("GET", "/api/admin/automation-conversion/group-ops/plans"),
    ("GET", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/members"),
    ("POST", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/members/import"),
    ("POST", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/members/refresh-from-groups"),
    ("GET", "/api/admin/automation-conversion/group-ops/audience-rules"),
    ("POST", "/api/admin/automation-conversion/group-ops/audience-rules"),
    ("POST", "/api/admin/automation-conversion/group-ops/audience-rules/{rule_key}/versions"),
    ("POST", "/api/admin/automation-conversion/group-ops/audience-rules/{rule_key}/preview"),
    ("POST", "/api/admin/automation-conversion/group-ops/audience-rules/{rule_key}/refresh"),
    ("GET", "/api/admin/automation-conversion/group-ops/audience-rules/{rule_key}/results"),
    ("PUT", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/segmentation"),
    ("POST", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/segmentation/preview"),
    ("GET", "/api/admin/automation-conversion/group-ops/plans/{plan_id}/executions"),
}


def _route_contracts() -> set[tuple[str, str]]:
    contracts: set[tuple[str, str]] = set()
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            contracts.add((method, route.path))
    return contracts


def test_only_purpose_bound_group_ops_integrations_remain_under_compatibility_prefix() -> None:
    contracts = _route_contracts()
    compatibility_paths = {
        path
        for _, path in contracts
        if path.startswith("/api/automation/group-ops")
    }

    assert compatibility_paths == ALLOWED_INTEGRATION_ROUTES


def test_group_ops_management_capabilities_use_only_formal_admin_routes() -> None:
    contracts = _route_contracts()

    assert REQUIRED_FORMAL_ROUTES <= contracts
    assert not any(path.startswith("/api/admin/p1/group-ops-workspace") for _, path in contracts)
    assert not any(path == "/admin/p1/group-ops-workspace" for _, path in contracts)


def test_retired_group_ops_control_plane_returns_not_found(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    assert client.get("/admin/p1/group-ops-workspace").status_code == 404
    assert client.get("/api/admin/p1/group-ops-workspace/drafts").status_code == 404
    assert client.get("/api/automation/group-ops/plans").status_code == 404
    assert client.get("/api/automation/group-ops/plans/1/executions").status_code == 404


def test_p1_group_ops_runtime_files_are_physically_retired() -> None:
    for path in RETIRED_RUNTIME_FILES:
        if path.is_dir():
            assert not any(path.rglob("*")), f"retired P1 Group Ops runtime directory is not empty: {path}"
        else:
            assert not path.exists(), f"retired P1 Group Ops runtime still exists: {path}"


def test_historical_p1_group_ops_reports_and_migrations_remain() -> None:
    assert (ROOT / "docs/reports/p1_group_ops_workspace_final_closeout_20260624.md").exists()
    assert (ROOT / "migrations/versions/0047_group_ops_workspace_drafts.py").exists()
    assert (ROOT / "migrations/versions/0049_group_ops_workspace_governance.py").exists()
