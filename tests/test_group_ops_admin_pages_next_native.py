from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_COMPAT = ROOT / "aicrm_next" / "app" / "admin_console"
GROUP_OPS_BUNDLE = ROOT / "aicrm_next" / "automation_engine" / "group_ops"


def test_group_ops_admin_pages_render_from_next_native_bundle(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app())

    list_response = client.get("/admin/automation-conversion/group-ops/ui")
    detail_response = client.get("/admin/automation-conversion/group-ops/plans/7")
    groups_response = client.get("/admin/automation-conversion/group-ops/groups/ui")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert groups_response.status_code == 200
    assert list_response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert detail_response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert groups_response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert 'id="group-ops-app"' in list_response.text
    assert 'data-page-mode="list"' in list_response.text
    assert 'data-page-mode="detail"' in detail_response.text
    assert 'data-plan-id="7"' in detail_response.text
    assert 'data-page-mode="groups"' in groups_response.text
    for label in ["基础配置", "绑定群", "Webhook", "标准编排"]:
        assert label in detail_response.text
    for forbidden in [
        "p1-diagnostics-toggle",
        "groupOpsP1StatusApp",
        "groupOpsP1StatusPayload",
        "data-p1-diagnostics",
        "Group Ops evidence",
        "governance_missing",
        "evidence_incomplete",
        "PASS_90_PLUS_CANDIDATE",
        "通过弹窗选择当前运营成员名下客户群",
        "配置运营成员、群包和计划内容",
    ]:
        assert forbidden not in detail_response.text
    assert "/static/group-ops/admin_console/group_ops.css" in list_response.text
    assert "/static/group-ops/admin_console/group_ops.js" in list_response.text
    assert "/static/group-ops/admin_console/group_ops.css" in detail_response.text
    assert "/static/group-ops/admin_console/group_ops.js" in detail_response.text
    assert "admin_console/material_picker.js" in list_response.text
    assert "admin_console/send_content_composer.js" in list_response.text


def test_group_ops_admin_routes_are_removed_from_frontend_compat() -> None:
    assert not (FRONTEND_COMPAT / "legacy_routes.py").exists()
    assert (GROUP_OPS_BUNDLE / "admin_pages.py").exists()
    assert (GROUP_OPS_BUNDLE / "templates/admin_console/group_ops.html").exists()
    assert (GROUP_OPS_BUNDLE / "static/admin_console/group_ops.css").exists()
    assert (GROUP_OPS_BUNDLE / "static/admin_console/group_ops.js").exists()
    assert not (FRONTEND_COMPAT / "templates/admin_console/group_ops.html").exists()
    assert not (FRONTEND_COMPAT / "static/admin_console/group_ops.css").exists()
    assert not (FRONTEND_COMPAT / "static/admin_console/group_ops.js").exists()
