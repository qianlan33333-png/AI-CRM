from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_FORM = ROOT / "aicrm_next/automation/automation_engine/templates/admin_console/channel_code_form.html"
CHANNEL_CENTER_TEMPLATE = ROOT / "aicrm_next/automation/automation_engine/templates/admin_console/channel_code_center.html"
CHANNEL_PAGES = ROOT / "aicrm_next/automation/automation_engine/channel_admin_pages.py"
CHANNEL_BASE = ROOT / "aicrm_next/automation/automation_engine/templates/admin_console/base.html"
CHANNEL_JS = ROOT / "aicrm_next/automation/automation_engine/static/admin_console/channel_admission_pages.js"
CHANNEL_CENTER_JS = ROOT / "aicrm_next/automation/automation_engine/static/admin_console/channel_code_center_next.js"
CHANNEL_CSS = ROOT / "aicrm_next/automation/automation_engine/static/admin_console/channel_admission_pages.css"
PICKER_JS = ROOT / "aicrm_next/app/admin_console/static/admin_console/operation_member_picker.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_channel_form_contains_multi_staff_assignment_without_demo_forbidden_surfaces() -> None:
    html = _read(CHANNEL_FORM)
    js = _read(CHANNEL_JS)
    css = _read(CHANNEL_CSS)
    combined = html + "\n" + js + "\n" + css

    assert "客服分配" in html
    assert "按比例分配" in html
    assert "满额切换" in html
    assert "平均比例" not in combined
    assert "权重" not in combined
    assert "扫码模拟" not in combined
    assert "二维码模拟" not in combined
    assert "分配日志模拟" not in combined
    assert "Payload JSON" not in combined
    assert "payload JSON" not in combined
    assert "channel-json-preview" not in combined
    assert "客服人数统计" not in combined
    assert "24h 扫码统计" not in combined
    assert "当前策略统计" not in combined
    for forbidden in [
        "仅对普通二维码生效；保存后重新生成二维码时会透传为企微 skip_verify。",
        "一个渠道最多添加 5 个客服。",
        "沿用项目内标准发送内容组件。",
        "该类型没有二维码下载动作，使用链接分享。",
        "填写原始链接和渠道参数后预览最终分享链接",
        "对应接口",
        "这里保留",
        "这里不混入",
        "external-config-blocked",
        "fail-closed",
        "企微诊断状态",
        "P1 WeCom",
        "Auth start",
        "Auth callback",
        "External contact callback",
        "Admin event visibility",
    ]:
        assert forbidden not in combined


def test_assignment_strategy_radios_are_before_titles_and_only_two_modes() -> None:
    html = _read(CHANNEL_FORM)

    ratio_block = re.search(r'<label class="strategy-card" data-strategy-card="ratio">(.*?)</label>', html, re.S)
    cap_block = re.search(r'<label class="strategy-card" data-strategy-card="cap_switch">(.*?)</label>', html, re.S)
    assert ratio_block
    assert cap_block
    assert ratio_block.group(1).index('type="radio"') < ratio_block.group(1).index("<strong>按比例分配</strong>")
    assert cap_block.group(1).index('type="radio"') < cap_block.group(1).index("<strong>满额切换</strong>")
    assert html.count('data-assignment-strategy') == 2
    assert 'value="ratio"' in html
    assert 'value="cap_switch"' in html
    assert "weighted_random" not in html
    assert "balanced" not in html
    assert "average_ratio" not in html


def test_channel_form_uses_demo_shell_dom_and_prefixed_css() -> None:
    html = _read(CHANNEL_FORM)
    js = _read(CHANNEL_JS)
    css = _read(CHANNEL_CSS)

    for class_name in [
        "channel-form-v3",
        "topbar",
        "summary-card",
        "summary-grid",
        "workspace",
        "side-nav",
        "channel-panel-card",
        "panel",
        "type-grid",
        "type-card",
        "card",
        "form-grid",
        "strategy-grid",
        "strategy-card",
        "assignee-panel",
        "assignee-head",
        "assignee-table",
        "assignee-footer",
        "summary-box",
        "summary-row",
    ]:
        assert class_name in html or class_name in js

    for selector in [
        ".channel-form-v3 .summary-card",
        ".channel-form-v3 .summary-grid",
        ".channel-form-v3 .workspace",
        ".channel-form-v3 .side-nav button.active",
        ".channel-form-v3 .panel.active",
        ".channel-form-v3 .type-card.active",
        ".channel-form-v3 .card",
        ".channel-form-v3 .strategy-card",
        ".channel-form-v3 .assignee-panel",
        ".channel-form-v3 .summary-box",
        ".channel-form-v3 .validation",
    ]:
        assert selector in css

    assert "assignee-row" in js
    assert "assignee-name" in js
    assert "assignee-row-actions" in js
    assert "channel-strategy-card" not in html
    assert "channel-assignee-panel" not in html
    assert "channel-selector-box" not in html


def test_channel_form_hides_shell_header_and_busts_static_cache() -> None:
    html = _read(CHANNEL_FORM)
    pages = _read(CHANNEL_PAGES)
    base = _read(CHANNEL_BASE)

    assert '"show_page_header": False' in pages
    assert "channel_admission_pages.css?v=group-chat-selector-20260715" in html
    assert "channel_admission_pages.js?v=channel-welcome-cursor-20260803" in html
    assert "operation_member_picker.js') }}?v=operation-member-picker-wecom-sync-20260709" in base


def test_channel_detail_clean_demo_v2_panel_and_type_contract() -> None:
    html = _read(CHANNEL_FORM)
    js = _read(CHANNEL_JS)

    basic_panel = re.search(r'data-channel-panel-content="basic"(.*?)</section>', html, re.S)
    carrier_panel = re.search(r'data-channel-panel-content="carrier"(.*?)data-channel-panel-content="assignee"', html, re.S)
    assert basic_panel
    assert carrier_panel
    assert 'data-channel-panel="basic"' in html
    assert 'data-channel-panel="carrier"' in html
    assert 'data-channel-panel="assignee"' in html
    assert 'data-channel-panel="welcome"' in html
    assert 'data-channel-panel="tag"' in html
    assert 'class="panel active" data-channel-panel-content="basic"' in html
    assert 'state = {\n    activeChannelPanel: "basic"' in js
    assert 'data-channel-panel-content' in js
    assert "setActiveChannelPanel" in js

    assert "渠道名称" in basic_panel.group(1)
    assert "渠道编码" in basic_panel.group(1)
    assert "状态" in basic_panel.group(1)
    assert "渠道类型" not in basic_panel.group(1)
    assert "扫码添加成员时自动通过好友申请" not in basic_panel.group(1)
    assert "客服分配" not in basic_panel.group(1)
    assert "欢迎语素材" not in basic_panel.group(1)
    assert "入渠标签" not in basic_panel.group(1)

    assert "普通二维码" in html
    assert "渠道获客链接" in html
    assert "基础配置" in html
    assert "渠道载体" in html
    assert html.index("基础配置") < html.index("渠道载体") < html.index('name="channel_type"')
    assert "type-card" in carrier_panel.group(1)
    assert 'data-channel-type-card="qrcode"' in carrier_panel.group(1)
    assert 'data-channel-type-card="wecom_customer_acquisition"' in carrier_panel.group(1)
    assert 'value="qrcode" {% if not is_link %}checked{% endif %}' in carrier_panel.group(1)
    assert "扫码添加成员时自动通过好友申请" in carrier_panel.group(1)
    assert "<span>渠道参数</span>" in html
    assert "<span>原始链接</span>" in html
    assert "<span>最终分享链接</span>" in html
    assert "复制链接" in carrier_panel.group(1)
    assert "分享链接" in carrier_panel.group(1)
    assert "[data-link-field], [data-link-section]" in js
    assert "node.hidden = !isLink" in js
    assert "[data-qrcode-field], [data-qrcode-section]" in js
    assert "node.hidden = isLink" in js
    assert "[data-channel-type-card]" in js
    assert "auto_accept_friend:" in js
    assert "payload.customer_channel" in js
    assert "link_url:" in js
    assert "final_url:" in js


def test_channel_center_list_has_enable_disable_and_soft_delete_actions() -> None:
    js = _read(CHANNEL_CENTER_JS)

    assert 'status === "active"' in js
    assert 'data-next-status="inactive"' in js
    assert ">下架</button>" in js
    assert 'status === "inactive"' in js
    assert 'data-next-status="active"' in js
    assert ">启用</button>" in js
    assert 'data-next-status="archived"' in js
    assert ">删除</button>" in js
    assert "删除后会归档渠道并保留历史用户、二维码、配置和入渠记录。确认删除？" in js
    assert "patchJson(`/api/admin/channels/${encodeURIComponent(channelId)}`, { status: nextStatus })" in js
    assert 'nextStatus === "archived"' in js
    assert "row.remove()" in js
    assert "row.outerHTML = renderRow(data.channel)" in js
    assert "渠道已下架" in js
    assert "渠道已启用" in js
    assert "渠道已删除" in js


def test_channel_center_list_retired_program_binding_status_and_long_channel_names() -> None:
    js = _read(CHANNEL_CENTER_JS)
    drawer_js = _read(CHANNEL_JS)
    template = _read(CHANNEL_CENTER_TEMPLATE)
    pages = _read(CHANNEL_PAGES)
    css = _read(CHANNEL_CSS)

    assert "function truncateChannelName(name, maxLength = 20)" in js
    assert "value.length > maxLength" in js
    assert "····" in js
    assert "const displayChannelName = truncateChannelName(channelName)" in js
    assert 'class="channel-name"' in js
    assert 'title="${escapeHtml(channelName)}"' in js
    assert "data-search-text" in js
    assert "searchText = String(channel.channel_name || \"\").toLowerCase()" in js
    assert '<span class="channel-pill is-bound">已绑定</span>' not in js
    assert '<span class="channel-pill is-standalone">独立使用</span>' not in js
    assert "escapeHtml(channel.bound_program_name)" not in js
    assert "绑定自动化运营" not in template
    assert "未绑定自动化运营" not in template
    assert "当前绑定自动化运营状态" not in template
    assert "绑定自动化运营" not in pages
    assert "bindings_base" not in pages
    assert "urlFromBase(urls.bindings_base" not in drawer_js
    assert "当前绑定自动化运营状态" not in drawer_js
    assert "program_name || item.program_id" not in drawer_js
    assert "apiBindings" not in drawer_js
    assert "initial_audience_code" not in drawer_js
    assert "data-bind-modal" not in drawer_js
    assert "member_stage_summary_base" not in drawer_js
    assert "renderMemberStageSummary" not in drawer_js
    assert "stageLabel" not in drawer_js
    assert "current_stage_code" not in drawer_js
    assert "pool_entered_at" not in drawer_js
    assert "stage_entered_at" not in drawer_js
    assert "池内用户" not in drawer_js
    assert "当前阶段" not in drawer_js
    assert "入池时间" not in drawer_js
    assert "阶段进入时间" not in drawer_js
    assert "已入池" not in drawer_js
    assert "旧运营池" not in drawer_js
    assert "供 AI 人群包查询" in drawer_js
    assert "is-status ${statusClass(channel.status)}" in js
    assert "is-status-inactive" in css
    assert "#b42318" in css
    assert "overflow-wrap: anywhere" in css


def test_channel_center_qrcode_generate_has_timeout_and_readable_errors() -> None:
    js = _read(CHANNEL_CENTER_JS)

    assert "function apiErrorMessage(data, fallback)" in js
    assert "window.AdminApi?.formatErrorValue" in js
    assert "detail.error_code" not in js
    assert "{ timeoutMs: 30000 }" in js
    assert "controller.abort()" in js
    assert 'error.name === "AbortError"' in js
    assert "二维码生成超时，请稍后刷新确认或重试" in js
    assert 'generateButton.textContent = "生成二维码"' in js


def test_assignment_rows_show_only_ratio_or_cap_fields_and_validate_before_save() -> None:
    js = _read(CHANNEL_JS)

    assert "分配比例" in js
    assert "24h 上限人数" in js
    assert "ratio_percent: assignmentState.strategy === \"ratio\"" in js
    assert "max_scans_24h: assignmentState.strategy === \"cap_switch\"" in js
    assert "ratio_percent: assignmentState.strategy === \"cap_switch\"" not in js
    assert "max_scans_24h: assignmentState.strategy === \"ratio\"" not in js
    assert "比例合计必须等于 100%" in js
    assert "button.disabled = assignmentState.errors.length > 0" in js
    assert "throw new Error(errors[0])" in js
    assert "weight" not in js.lower()
    assert "weighted_random" not in js
    assert "balanced" not in js
    assert "average_ratio" not in js


def test_add_assignee_uses_operation_member_picker_multiple_mode_without_fake_staff() -> None:
    js = _read(CHANNEL_JS)
    picker = _read(PICKER_JS)

    assert "OperationMemberPicker.open" in js
    assert "multiple: true" in js
    assert "max: Math.max(1, 5 - current.length)" in js
    assert "selectedMembers: []" in js
    assert "disabledUserIds: current.map((item) => item.staff_id)" in js
    assert 'scope: "channel_code"' in js
    assert "page_size: 100" in js
    assert "staffPool" not in js
    assert "Support05" not in js
    assert "selectedMembers" in picker
    assert "disabledUserIds" in picker
    assert "member-modal" in picker
    assert "member-modal__panel" in picker
    assert "member-row" in picker
    assert 'type="checkbox"' in picker


def test_standard_welcome_tag_components_and_hidden_payload_inputs_are_kept() -> None:
    html = _read(CHANNEL_FORM)
    js = _read(CHANNEL_JS)

    assert "AICRMSendContentComposer.mount" in js
    assert "AICRMWeComTagPicker.open" in js
    assert "prompt(" not in js
    assert "data-welcome-composer-inline" in html
    assert "data-open-welcome-composer" not in html
    assert "选择标签" in html
    assert 'name="welcome_message"' in html
    assert 'name="welcome_image_library_ids"' in html
    assert 'name="welcome_miniprogram_library_ids"' in html
    assert 'name="welcome_attachment_library_ids"' in html
    assert 'name="entry_tag_id"' in html
    assert 'name="entry_tag_name"' in html
    assert 'name="entry_tag_group_name"' in html
    assert "welcome_message:" in js
    assert "welcome_image_library_ids:" in js
    assert "welcome_miniprogram_library_ids:" in js
    assert "welcome_attachment_library_ids:" in js
    assert "entry_tag_id:" in js
