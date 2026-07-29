from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GROUP_OPS_JS = ROOT / "aicrm_next/automation/automation_engine/group_ops/static/admin_console/group_ops.js"
GROUP_OPS_TEMPLATE = ROOT / "aicrm_next/automation/automation_engine/group_ops/templates/admin_console/group_ops.html"
PICKER_JS = ROOT / "aicrm_next/app/admin_console/static/admin_console/operation_member_picker.js"


def _source() -> str:
    return GROUP_OPS_TEMPLATE.read_text(encoding="utf-8") + "\n" + GROUP_OPS_JS.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    source = GROUP_OPS_JS.read_text(encoding="utf-8")
    start = source.index(f"function {name}")
    next_markers = [
        marker
        for marker in (
            source.find("\n  function ", start + 1),
            source.find("\n  async function ", start + 1),
        )
        if marker != -1
    ]
    next_function = min(next_markers) if next_markers else -1
    return source[start:] if next_function == -1 else source[start:next_function]


@pytest.fixture()
def group_ops_frontend_client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from aicrm_next.main import create_app

    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "0")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_group_ops_frontend_routes_are_owned_by_next(group_ops_frontend_client):
    for path in [
        "/admin/automation-conversion/group-ops/ui",
        "/admin/automation-conversion/group-ops/plans/1",
        "/admin/automation-conversion/group-ops/groups/ui",
    ]:
        response = group_ops_frontend_client.get(path)
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert 'id="group-ops-app"' in response.text


def test_group_ops_detail_html_and_js_contract_uses_second_level_dimensions(group_ops_frontend_client):
    response = group_ops_frontend_client.get("/admin/automation-conversion/group-ops/plans/7")
    html = response.text
    source = _source()

    assert response.status_code == 200
    assert 'id="group-ops-app"' in html
    assert 'data-page-mode="detail"' in html
    assert 'data-plan-id="7"' in html
    assert "/static/group-ops/admin_console/group_ops.js" in html
    assert "/static/group-ops/admin_console/group_ops.css" in html
    for label in ["基础配置", "绑定群", "Webhook", "标准编排"]:
        assert label in source
    assert 'activeDetailPanel: "basic"' in source
    assert 'data-action="switch-detail-panel"' in source
    assert 'data-action="save-active-detail-panel"' in source
    assert "function renderDetailShell" in source
    assert "function renderDetailPanels" in source
    for forbidden in [
        "配置运营成员、群包和计划内容",
        "通过弹窗选择当前运营成员名下客户群",
        "Token 状态 / 重置入口",
        "诊断状态",
        "Group Ops evidence",
        "governance",
        "preview，仅验收或管理员查看",
        "发送链路证据",
        "治理证据",
        "最终判定",
        "external_effect_job",
        "PASS_90_PLUS_CANDIDATE",
        "governance_missing",
        "evidence_incomplete",
        "对应接口",
        "这里保留",
        "这里不混入",
    ]:
        assert forbidden not in source


def test_group_ops_list_frontend_contract_has_required_actions_and_columns():
    source = _source()

    assert "查看所有群" in source
    assert "创建计划" in source
    for label in ["运营计划", "已绑定群", "今日预估", "通知排队队列"]:
        assert label in source
    assert "<th>计划名称</th><th>类型</th><th>运营成员</th><th>绑定群</th><th>今日预估</th><th>状态</th><th>操作</th>" in source
    assert "编辑" in source
    assert "停用 / 删除" not in source
    for label in ["停用", "启用", "删除"]:
        assert label in source
    for action in ['data-action="disable-plan"', 'data-action="enable-plan"', 'data-action="delete-plan"']:
        assert action in source
    assert "apiPlanEnable" in source
    assert "apiPlanDisable" in source
    assert "确认删除" in source
    assert 'name="create_plan_type"' in source
    assert "标准编排计划" in source
    assert "Webhook 接收计划" in source
    assert "create_owner_userid" in source
    assert "apiMembers" in source
    assert "/api/admin/common/operation-members?scope=group_ops" in source
    assert "OperationMemberPicker.open" in source
    assert 'scope: "group_ops"' in source
    assert "page_size: 100" in source
    assert "create_owner_userid_text" not in source
    assert '"owner_001"' not in source

    for forbidden in ["下一次动作", "计划详情", "队列策略", "可发主体", "管理员判断"]:
        assert forbidden not in source


def test_group_ops_detail_frontend_contract_matches_standard_and_webhook_requirements():
    source = _source()
    detail_source = _function_source("renderDetail")

    for label in ["返回列表", "保存当前维度", "保存基础配置", "基础配置", "运营成员", "刷新名下群聊", "绑定群", "选择群"]:
        assert label in source
    for label in ["运营成员", "绑定群", "外部联系人", "状态"]:
        assert label in source
    for label in ["第几天", "发送时间", "动作标题", "标准话术摘要", "素材标签", "操作"]:
        assert label not in detail_source
        assert label in source
    assert "添加动作" in source
    assert "open-node-modal" in source
    assert "group-ops__modal" in source
    assert "group_picker_keyword" in source
    assert "groupPickerNotice" in source
    assert "绑定中" in source
    assert 'requestErrorMessage(error, "绑定失败")' in source
    assert "配置话术和素材" in source
    assert "AICRMSendContentComposer.open" in source
    assert "配置群运营动作内容" in source
    assert "save-node" in source
    assert "delete-node" in source
    assert "node_" + "attachments" not in source
    assert "node_" + "text_content" not in source
    assert "素材 " + "JSON" not in source
    assert 'data-action="noop"' not in source
    assert "data-available-groups" not in source
    assert "renderAvailableGroups" not in source
    assert "素材 JSON" not in source
    for forbidden_time in ["入群后 10 分钟", "入群后 30 分钟", "入群后 1 小时"]:
        assert forbidden_time not in GROUP_OPS_JS.read_text(encoding="utf-8")
    for label in ["Webhook", "POST", "复制地址", "认证方式", "HTTP Message Signatures"]:
        assert label in source
    assert "历史素材已保留，保存新素材不会自动删除历史素材" in source

    for forbidden in [
        "适用场景",
        "JSON 示例",
        "请求字段说明大表",
        "请求字段说明",
        "明文 token",
        "明文 Token",
        "Token 状态 / 重置入口",
        "Token：",
        "一次性 token",
        "复制后不可再次查看",
        "接收方式",
        "默认动作",
    ]:
        assert forbidden not in source
    assert "查看所有群" not in detail_source
    assert "创建计划" not in detail_source
    assert "保存接口计划" not in detail_source
    assert "group-ops__detail-grid" not in detail_source
    assert "group-ops__stats-grid" not in detail_source


def test_group_ops_webhook_plan_uses_read_copy_and_platform_signatures_only():
    source = _source()
    load_source = _function_source("loadDetailPage")
    copy_source = _function_source("copyWebhook")

    assert "renderWebhook()" in source
    assert "copy-webhook" in source
    assert "navigator.clipboard.writeText(url)" in copy_source
    assert "routes.apiWebhook(planId)" in load_source
    assert "routes.apiPlanNodes(planId)" in load_source
    assert "PATCH" not in source
    assert "reset-webhook" not in source
    assert "apiWebhookRegenerate" not in source
    assert "/webhook/regenerate" not in source
    assert "/webhook`" in source


def test_group_ops_detail_panel_switches_without_reloading_detail_data():
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(GROUP_OPS_JS))}, "utf8");
let requestCount = 0;
const actions = [];
const app = {{
  dataset: {{ pageMode: "detail", planId: "2" }},
  _html: "",
  set innerHTML(value) {{
    this._html = String(value || "");
    actions.length = 0;
    const re = /<button([^>]*)data-action="([^"]+)"([^>]*)>/g;
    let match;
    while ((match = re.exec(this._html))) {{
      const attrs = `${{match[1]}} ${{match[3]}}`;
      const panel = (attrs.match(/data-panel="([^"]+)"/) || [null, ""])[1];
      const action = match[2];
      actions.push({{
        dataset: {{ action, panel }},
        addEventListener(eventName, handler) {{
          if (eventName === "click") this.handler = handler;
        }}
      }});
    }}
  }},
  get innerHTML() {{ return this._html; }},
  querySelectorAll(selector) {{
    if (selector === "[data-action]") return actions;
    if (selector === "[data-filter]" || selector === "[data-group-picker-search]") return [];
    return [];
  }},
  querySelector() {{ return null; }}
}};
function payloadFor(url) {{
  requestCount += 1;
  if (url.endsWith("/plans/2")) return Promise.resolve({{ item: {{ id: 2, plan_name: "Webhook 计划", plan_type: "webhook", owner_userid: "owner_001", owner_name: "黄有璨", status: "active" }} }});
  if (url.endsWith("/plans/2/groups")) return Promise.resolve({{ items: [{{ chat_id: "chat_1", group_name_snapshot: "测试群", external_member_count_snapshot: 12 }}], summary: {{ bound_group_count: 1, external_member_count: 12, internal_member_count: 1, estimated_reach: 12 }} }});
  if (String(url).includes("operation-members")) return Promise.resolve({{ items: [{{ user_id: "owner_001", display_name: "黄有璨" }}] }});
  if (String(url).includes("/group-ops/groups?")) return Promise.resolve({{ items: [] }});
  if (url.endsWith("/plans/2/webhook")) return Promise.resolve({{ webhook_url: "https://example.test/webhook", token_status: "generated" }});
  throw new Error("unexpected_url:" + url);
}}
const sandbox = {{
  window: {{
    AdminApi: {{
      escapeHtml(value) {{ return String(value || ""); }},
      requestJson: payloadFor
    }}
  }},
  document: {{ getElementById() {{ return app; }} }},
  Intl,
  navigator: {{ clipboard: {{ writeText() {{ return Promise.resolve(); }} }} }}
}};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {{
  await new Promise((resolve) => setTimeout(resolve, 0));
  const initialCount = requestCount;
  const basicActive = /id="panel-basic"[\\s\\S]*?is-active/.test(app.innerHTML) || app.innerHTML.includes('group-ops__panel is-active" id="panel-basic"');
  const activeCountBefore = (app.innerHTML.match(/group-ops__panel is-active/g) || []).length;
  const groupsButton = actions.find((item) => item.dataset.action === "switch-detail-panel" && item.dataset.panel === "groups");
  groupsButton.handler({{ currentTarget: groupsButton }});
  const groupsActive = app.innerHTML.includes('group-ops__panel is-active" id="panel-groups"');
  const afterGroupsCount = requestCount;
  const webhookButton = actions.find((item) => item.dataset.action === "switch-detail-panel" && item.dataset.panel === "webhook");
  webhookButton.handler({{ currentTarget: webhookButton }});
  const webhookActive = app.innerHTML.includes('group-ops__panel is-active" id="panel-webhook"');
  const nodesButton = actions.find((item) => item.dataset.action === "switch-detail-panel" && item.dataset.panel === "nodes");
  nodesButton.handler({{ currentTarget: nodesButton }});
  const nodesActive = app.innerHTML.includes('group-ops__panel is-active" id="panel-nodes"');
  console.log(JSON.stringify({{ basicActive, activeCountBefore, groupsActive, webhookActive, nodesActive, initialCount, afterGroupsCount, finalCount: requestCount }}));
}})().catch((error) => {{
  console.error(error && error.stack || error);
  process.exit(1);
}});
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["basicActive"] is True
    assert payload["activeCountBefore"] == 1
    assert payload["groupsActive"] is True
    assert payload["webhookActive"] is True
    assert payload["nodesActive"] is True
    assert payload["initialCount"] == payload["afterGroupsCount"] == payload["finalCount"]


def test_group_ops_standard_plan_keeps_node_actions_on_existing_interfaces():
    source = _source()
    save_source = _function_source("saveNode")
    delete_source = _function_source("deleteNode")

    assert "renderNodes()" in source
    assert "添加动作" in source
    assert "save-node" in source
    assert "delete-node" in source
    assert "routes.apiPlanNodes(state.plan.id)" in save_source
    assert "routes.apiPlanNode(state.plan.id, nodeId)" in save_source
    assert 'method: nodeId ? "PUT" : "POST"' in save_source
    assert "routes.apiPlanNode(state.plan.id, nodeId)" in delete_source
    assert 'method: "DELETE"' in delete_source


def test_group_ops_detail_refresh_owner_groups_contract_is_manual_and_owner_scoped():
    source = _source()
    refresh_source = _function_source("refreshOwnerGroups")

    assert "刷新名下群聊" in source
    assert "/api/admin/automation-conversion/group-ops/groups/sync" in source
    assert "owner_userid: owner" in refresh_source
    assert 'operator: "admin_ui"' in refresh_source
    assert "limit: 100" in refresh_source
    assert "owner_userid=${encodeURIComponent(owner)}" in refresh_source
    assert "已刷新：新增" in refresh_source
    assert "requestErrorMessage(error" in refresh_source
    assert "loadDetailPage(state.plan.id)" not in refresh_source


def test_group_ops_detail_save_plan_only_submits_base_fields():
    save_source = _function_source("savePlan")

    for field in ["plan_name", "plan_code", "plan_type", "owner_userid", "status"]:
        assert field in save_source
    for forbidden in [
        "boundGroupIds",
        "bound_group_ids",
        "webhook_url",
        "token_status",
        "nodes",
        "content_package_json",
        "attachments",
        "chat_id",
    ]:
        assert forbidden not in save_source


def test_group_ops_detail_refresh_error_uses_backend_sync_reason():
    source = _source()
    error_source = _function_source("requestErrorMessage")

    assert "payload.error_message" in error_source
    assert "detail.detail" in error_source
    assert "detail.error_code" in error_source
    assert "Conflict" not in error_source


def test_group_ops_detail_scheduled_time_options_contract():
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(GROUP_OPS_JS))}, "utf8");
const app = {{
  dataset: {{}},
  querySelectorAll() {{ return []; }},
  querySelector() {{ return null; }},
  innerHTML: ""
}};
const sandbox = {{
  window: {{}},
  document: {{ getElementById() {{ return app; }} }},
  Intl
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
console.log(JSON.stringify(sandbox.window.AICRMGroupOpsContentAdapter.scheduledTimeOptions()));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    options = json.loads(result.stdout)

    assert "08:00" in options
    assert "20:30" in options
    assert "23:30" in options
    assert "07:30" not in options
    assert "24:00" not in options


def test_group_ops_detail_imports_standard_send_content_assets():
    source = _source()

    assert "send_content_composer.js" in source
    assert "send_content_composer.css" in source
    assert "material_picker.js" in source
    assert "material_picker.css" in source
    for forbidden in [
        "/api/admin/image-" + "library",
        "/api/admin/miniprogram-" + "library",
        "/api/admin/attachment-" + "library",
        "groupOpsMaterial" + "Pick" + "er",
    ]:
        assert forbidden not in source


def test_group_ops_frontend_content_package_adapter_round_trips():
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(GROUP_OPS_JS))}, "utf8");
const app = {{
  dataset: {{}},
  querySelectorAll() {{ return []; }},
  querySelector() {{ return null; }},
  innerHTML: ""
}};
const sandbox = {{
  window: {{}},
  document: {{ getElementById() {{ return app; }} }},
  Intl
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const adapter = sandbox.window.AICRMGroupOpsContentAdapter;
const fromOld = adapter.nodeToContentPackage({{ text_content: "  老话术  ", attachments: [{{msgtype:"image"}}] }});
const fromEmptyPackage = adapter.nodeToContentPackage({{ text_content: "  老话术  ", content_package_json: {{}} }});
const fromLegacyIds = adapter.nodeToContentPackage({{
  text_content: "历史素材",
  content_package_json: {{}},
  attachments: [
    {{msgtype: "image", image: {{library_id: "12"}}}},
    {{msgtype: "miniprogram", miniprogram: {{library_id: 34}}}},
    {{msgtype: "file", file: {{library_id: "56"}}}},
    {{msgtype: "file", file: {{media_id: "legacy-file"}}}}
  ]
}});
const fromPackage = adapter.nodeToContentPackage({{
  content_package_json: {{
    content_text: "  新话术  ",
    image_library_ids: [12, "12", 34],
    miniprogram_library_ids: ["56"],
    attachment_library_ids: "78, 78, 90"
  }}
}});
const toNode = adapter.contentPackageToNodePayload({{
  content_text: "  保存话术  ",
  image_library_ids: ["101", 102],
  miniprogram_library_ids: [201],
  attachment_library_ids: ["301", "301", 302]
}});
const empty = adapter.normalizeContentPackage({{}});
console.log(JSON.stringify({{ fromOld, fromEmptyPackage, fromLegacyIds, fromPackage, toNode, empty }}));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["fromOld"] == {
        "content_text": "老话术",
        "image_library_ids": [],
        "miniprogram_library_ids": [],
        "attachment_library_ids": [],
        "group_invite_library_ids": [],
    }
    assert payload["fromEmptyPackage"]["content_text"] == "老话术"
    assert payload["fromLegacyIds"] == {
        "content_text": "历史素材",
        "image_library_ids": [12],
        "miniprogram_library_ids": [34],
        "attachment_library_ids": [56],
        "group_invite_library_ids": [],
    }
    assert payload["fromPackage"] == {
        "content_text": "新话术",
        "image_library_ids": [12, 34],
        "miniprogram_library_ids": [56],
        "attachment_library_ids": [78, 90],
        "group_invite_library_ids": [],
    }
    assert payload["toNode"] == {
        "text_content": "保存话术",
        "content_package_json": {
            "content_text": "保存话术",
            "image_library_ids": [101, 102],
            "miniprogram_library_ids": [201],
            "attachment_library_ids": [301, 302],
            "group_invite_library_ids": [],
        },
    }
    assert payload["empty"] == {
        "content_text": "",
        "image_library_ids": [],
        "miniprogram_library_ids": [],
        "attachment_library_ids": [],
        "group_invite_library_ids": [],
    }


def test_group_ops_list_create_panel_errors_and_post_failures_are_visible():
    script = f"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync({json.dumps(str(GROUP_OPS_JS))}, "utf8");
const actions = {{}};
const values = {{}};
function attr(html, name) {{
  const re = new RegExp('name="' + name + '"[^>]*value="([^"]*)"', 'm');
  const match = html.match(re);
  return match ? match[1] : "";
}}
const app = {{
  dataset: {{ pageMode: "list" }},
  _html: "",
  set innerHTML(value) {{
    this._html = String(value || "");
    for (const key of Object.keys(actions)) delete actions[key];
    const re = /data-action="([^"]+)"/g;
    let match;
    while ((match = re.exec(this._html))) {{
      const action = match[1];
      actions[action] = {{
        dataset: {{ action }},
        addEventListener(eventName, handler) {{
          if (eventName === "click") this.handler = handler;
        }}
      }};
    }}
  }},
  get innerHTML() {{ return this._html; }},
  querySelectorAll(selector) {{
    if (selector === "[data-action]") return Object.values(actions);
    return [];
  }},
  querySelector(selector) {{
    const match = selector.match(/^\\[name="([^"]+)"\\]$/);
    if (!match) return null;
    const name = match[1];
    return {{ value: values[name] ?? attr(this._html, name) }};
  }}
}};
let postFailures = 0;
const sandbox = {{
  window: {{
    AdminApi: {{
      escapeHtml(value) {{ return String(value || ""); }},
      requestJson(url, options) {{
        if (options && options.method === "POST") {{
          postFailures += 1;
          return Promise.reject({{ payload: {{ error_message: "权限不足" }} }});
        }}
        if (String(url).includes("operation-members")) return Promise.resolve({{ items: [] }});
        return Promise.resolve({{ items: [], total: 0, queue_count: 0 }});
      }}
    }},
    location: {{ assign() {{ throw new Error("should_not_redirect"); }} }}
  }},
  document: {{ getElementById() {{ return app; }} }},
  Intl
}};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {{
  await new Promise((resolve) => setTimeout(resolve, 0));
  await actions["show-create-plan"].handler({{ currentTarget: actions["show-create-plan"] }});
  const opened = app.innerHTML.includes('name="create_owner_userid"') && app.innerHTML.includes("保存计划");
  await actions["create-plan"].handler({{ currentTarget: actions["create-plan"] }});
  const missingOwner = app.innerHTML.includes("请选择运营成员");
  values.create_owner_userid = "owner_001";
  await actions["create-plan"].handler({{ currentTarget: actions["create-plan"] }});
  const postFailed = app.innerHTML.includes("创建失败") && app.innerHTML.includes("权限不足");
  console.log(JSON.stringify({{ opened, missingOwner, postFailed, postFailures }}));
}})().catch((error) => {{
  console.error(error && error.stack || error);
  process.exit(1);
}});
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload == {
        "opened": True,
        "missingOwner": True,
        "postFailed": True,
        "postFailures": 1,
    }


def test_operation_member_picker_can_scope_group_ops_without_changing_default_common_scope():
    source = PICKER_JS.read_text(encoding="utf-8")

    assert "function scopedUrl(q)" in source
    assert 'url.searchParams.set("scope", state.scope)' in source
    assert 'url.searchParams.set("page_size", state.pageSize)' in source
    assert 'state.scope = String(options.scope || "").trim();' in source
    assert 'state.pageSize = String(options.page_size || options.pageSize || "").trim();' in source


def test_group_ops_all_groups_frontend_contract_has_only_required_columns():
    source = _source()

    for label in ["群名 / 群 ID", "群主", "所属计划", "已绑定 / 未绑定"]:
        assert label in source
    assert "<th>群名</th><th>群 ID</th><th>群主</th><th>所属计划</th><th>状态</th>" in source

    for forbidden in ["群规模", "谁可发谁不可发", "下一次动作"]:
        assert forbidden not in source
