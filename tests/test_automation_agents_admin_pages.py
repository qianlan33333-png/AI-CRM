from __future__ import annotations

import re
from pathlib import Path

from tests.admin_auth_test_helpers import admin_session_cookies


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "aicrm_next" / "extensions" / "ai" / "automation_agents" / "templates" / "admin_console"
LIST_TEMPLATE = TEMPLATE_DIR / "automation_agent_list.html"
EDIT_TEMPLATE = TEMPLATE_DIR / "automation_agent_edit.html"


def _admin_cookies(next_client) -> dict[str, str]:
    return admin_session_cookies(next_client, "super_admin")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_automation_agent_admin_pages_require_admin_session(next_client, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "automation-agent-page-auth-test")
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "true")

    list_response = next_client.get("/admin/automation-agents", follow_redirects=False)
    edit_response = next_client.get("/admin/automation-agents/1/edit", follow_redirects=False)

    assert list_response.status_code == 302
    assert list_response.headers["location"] == "/login?next=/admin/automation-agents"
    assert edit_response.status_code == 302
    assert edit_response.headers["location"] == "/login?next=/admin/automation-agents/1/edit"


def test_automation_agent_list_page_contract(next_client, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "automation-agent-list-page-test")

    response = next_client.get("/admin/automation-agents", cookies=_admin_cookies(next_client))

    assert response.status_code == 200
    html = response.text
    for expected in (
        "自动化话术",
        "新增 Agent",
        "新增固定话术",
        "自动化名称",
        "自动化类类型",
        "固定素材",
        "状态",
        "操作",
        "编辑",
        "复制",
        "停止",
        "启用",
        "删除",
        "/api/admin/automation-agents",
        "data-agent-action-row",
        "automation-agent-list-actions",
        "automation_type",
        "fixed_script",
        "agent_name",
        "agent_code",
        "请根据{{问卷信息}}",
        "收到问卷啦～",
        "这几个问题开始聊",
    ):
        assert expected in html
    for forbidden in (
        "绑定自动化运营计划",
        "接收格式",
        "接收/发送地址",
        "字段依赖",
        "external_userid 数组",
        "Agent Webhook",
        "共 0 个",
        "共 ${items.length} 个",
    ):
        assert forbidden not in html


def test_automation_agent_list_actions_are_horizontal_on_desktop() -> None:
    source = _read(LIST_TEMPLATE)
    op_row_css = re.search(r"\.automation-agent-op-row\s*\{(?P<body>.*?)\}", source, re.S)
    create_row_css = re.search(r"\.automation-agent-list-actions\s*\{(?P<body>.*?)\}", source, re.S)

    assert op_row_css, "operation row CSS must exist"
    assert create_row_css, "create action row CSS must exist"
    body = op_row_css.group("body")
    assert "display: inline-flex" in body
    assert "flex-direction: row" in body
    assert "justify-content: flex-end" in body
    assert "gap: 10px" in body
    assert "white-space: nowrap" in body
    assert "flex-direction: column" not in body
    create_body = create_row_css.group("body")
    assert "display: flex" in create_body
    assert "justify-content: flex-end" in create_body
    assert "gap: 10px" in create_body


def test_automation_agent_list_table_uses_available_desktop_width() -> None:
    source = _read(LIST_TEMPLATE)
    table_css = re.search(r"\.automation-agent-table\s*\{(?P<body>.*?)\}", source, re.S)
    wrap_css = re.search(r"\.automation-agent-table-wrap\s*\{(?P<body>.*?)\}", source, re.S)

    assert table_css, "desktop table CSS must exist"
    assert wrap_css, "desktop table wrapper CSS must exist"
    assert "width: 100%" in table_css.group("body")
    assert "min-width" not in table_css.group("body")
    assert "overflow-x: hidden" in wrap_css.group("body")
    assert ".automation-agent-table .w-plan {\n    width: 36%;" in source
    assert ".automation-agent-table .w-type {\n    width: 14%;" in source
    assert ".automation-agent-table .w-op {\n    width: 20%;" in source


def test_automation_agent_edit_page_contract(next_client, monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "automation-agent-edit-page-test")

    response = next_client.get("/admin/automation-agents/123/edit", cookies=_admin_cookies(next_client))

    assert response.status_code == 200
    html = response.text
    for expected in (
        "编辑 Agent",
        "Agent 名称",
        "自动化类类型",
        "fixed_script",
        "当前绑定人群包",
        "绑定关系请在人群包详情的“自动化话术能力”中管理",
        "boundPackage",
        "role_prompt",
        "task_prompt",
        "插入 {{问卷信息}}",
        "插入 {{最近20条聊天信息}}",
        "插入 {{用户标签}}",
        "插入 {{激活信息}}",
        "当前字段依赖自动识别",
        "话术正文",
        "fixedScriptText",
        "renderMode",
        "保存草稿",
        "发布",
        "载入已发布版本",
        "固定素材",
        "配置固定素材",
        "暂无配置固定素材",
        "material_picker.js",
        "AICRMMaterialPicker.open",
        "/api/admin/automation-agents/123",
        "/api/admin/automation-agents/123/fixed-content",
        "`${agentApiUrl}/publish`",
        'method: "POST"',
    ):
        assert expected in html
    for forbidden in (
        "当前页面是二级配置页",
        "一对一绑定",
        "填入自动化运营计划",
        "Agent 生成后回推",
        "返回列表",
        "HTML Demo",
        "Agent Code",
        "绑定计划",
        "人群包 Key",
        "content_package 预览",
        'id="packagePreview"',
        'id="agentCode"',
        'id="planName"',
        'id="packageKey"',
        "重置 token",
        "/api/admin/automation-agents/123/reset-token",
        "接收地址",
        "发送地址",
        "receive_webhook_url",
        "send_webhook_url",
        'id="sendUrl"',
    ):
        assert forbidden not in html


def test_automation_agent_edit_bottom_bar_only_has_save_button() -> None:
    source = _read(EDIT_TEMPLATE)
    match = re.search(r'<div class="automation-agent-card automation-agent-save-bar">(?P<body>.*?)</div>', source, re.S)

    assert match, "bottom save bar must exist"
    body = match.group("body")
    assert body.count("<button") == 1
    assert ">保存<" in body
    assert "返回列表" not in body


def test_automation_agent_material_modal_and_preview_logic() -> None:
    source = _read(EDIT_TEMPLATE)

    for expected in (
        'id="materialModal"',
        "配置固定素材",
        'data-add-asset="image"',
        'data-add-asset="miniprogram"',
        'data-add-asset="attachment"',
        "+图片",
        "+小程序",
        "+附件",
        "已选素材列表",
        "保存素材",
        "openMaterial",
        'classList.add("show")',
        "const renderAssets",
        "state.contentPackage.image_library_ids",
        "state.contentPackage.miniprogram_library_ids",
        "state.contentPackage.attachment_library_ids",
        "fixedContentApiUrl",
        'method: "PUT"',
    ):
        assert expected in source


def test_automation_agent_prompt_tokens_and_dependency_detection() -> None:
    source = _read(EDIT_TEMPLATE)

    for token in ("{{问卷信息}}", "{{最近20条聊天信息}}", "{{用户标签}}", "{{激活信息}}"):
        assert source.count(token) >= 3
    assert "state.focusPrompt" in source
    assert "selectionStart" in source
    assert "selectionEnd" in source
    assert "const text = `${els.rolePrompt.value}\\n${els.taskPrompt.value}`;" in source
    assert ".filter(([token]) => text.includes(token))" in source
    assert "renderDeps()" in source
