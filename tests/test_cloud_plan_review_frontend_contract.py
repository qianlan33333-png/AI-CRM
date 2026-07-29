from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "cloud_plan_review.html"
SCRIPT = ROOT / "aicrm_next" / "app" / "admin_console" / "static" / "admin_console" / "cloud_plan_review.js"


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "cloud-plan-review-frontend-test")
    return TestClient(create_app())


def test_plan_list_page_contract(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/admin/cloud-orchestrator/plans")

    assert response.status_code == 200
    html = response.text
    assert "AI 助手 · 运营计划审阅" in html
    assert "待审批计划" in html
    assert "今日预计触达" in html
    assert "执行中计划" in html
    assert "一级页加载人员" in html
    assert "0 人" in html
    assert "计划列表加载中" in html
    assert 'data-p1-diagnostics="ops_plan"' not in html
    assert "opsPlanP1StatusPayload" not in html
    assert "opsPlanP1StatusApp" not in html
    assert "admin_console/p1/ops_plan" not in html
    assert "计划编号" not in html
    assert "<div>已批准</div>" not in html
    assert "<div>待处理</div>" not in html
    assert "搜索计划名称、发送人" in html
    assert "查看详情" in html
    assert "data-page-mode=\"list\"" in html
    assert "cloud_plan_review.js" in html
    assert "admin_api_client.js?v=admin-error-messages-v1-20260729" in html
    assert html.index("admin_api_client.js") < html.index("cloud_plan_review.js")
    for forbidden in ["进入审批", "全部启动", "批量审批", "展开子计划", "加载子计划", "cloud-camp-group-child"]:
        assert forbidden not in html


def test_plan_detail_page_contract(monkeypatch):
    client = _client(monkeypatch)

    response = client.get("/admin/cloud-orchestrator/plans/plan_probe")

    assert response.status_code == 200
    html = response.text
    assert "AI 助手 · 计划二级明细" in html
    assert "批准并开始执行" in html
    assert "拒绝计划" in html
    assert "返回一级页" in html
    assert "目标人员" in html
    assert "话术次数" in html
    assert "批准这个人发送" in html
    assert "拒绝这个人" in html
    assert "继续加载 50 人" in html
    assert 'data-p1-diagnostics="ops_plan"' not in html
    assert "opsPlanP1StatusPayload" not in html
    assert "opsPlanP1StatusApp" not in html
    assert "admin_console/p1/ops_plan" not in html
    assert "已加载 0 / 0 人" in html
    assert "data-page-mode=\"detail\"" in html
    assert "material_picker.css" in html
    assert "send_content_composer.css" in html
    assert "material_picker.js" in html
    assert "send_content_composer.js" in html
    assert "admin_api_client.js?v=admin-error-messages-v1-20260729" in html
    assert html.index("admin_api_client.js") < html.index("cloud_plan_review.js")
    for forbidden in ["进入审批", "全部启动", "批量审批", "展开子计划", "加载子计划", "话术节奏", "cloud-camp-group-child"]:
        assert forbidden not in html


def test_plan_review_static_contract():
    template = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    combined = template + "\n" + script

    assert "params.set(\"limit\", \"20\")" in script
    assert "plan.approved_count" not in script
    assert "plan.pending_count" not in script
    assert "cloud-plan-code" not in script
    assert "limit: String(PAGE_SIZE)" in script
    assert "PAGE_SIZE = 50" in script
    assert "recipients?${params.toString()}" in script
    assert "updateRecipientInState(payload.recipient)" in script
    assert "AICRMSendContentComposer.open" in script
    assert ".cloud-plan-button--primary:hover:not([disabled])" in template
    assert ".cloud-plan-button[disabled]:hover" in template
    assert "contentPackageToTaskPayload" in script
    assert "localRequestJson" in script
    assert "JSON.stringify(finalOptions.body)" in script
    assert "data-task-material-detail" in script
    assert "/api/admin/send-content/preview" in script
    assert "小程序：" in script
    assert "已开始执行" in script
    assert "开始执行" in script
    assert "计划已批准并开始执行" in script
    assert "run === \"active\"" in script
    assert "approved ? \"开始执行\" : \"批准并开始执行\"" in script
    for forbidden in [
        "limit', '5000",
        "limit\", \"5000",
        "进入审批",
        "全部启动",
        "批量审批",
        "cloud-camp-group-child",
        "pool key",
        "profile key",
        "behavior tier",
        "opsPlanP1StatusPayload",
        "opsPlanP1StatusApp",
        "data-p1-diagnostics",
        "admin_console/p1/ops_plan",
        ".p1-",
    ]:
        assert forbidden not in combined
