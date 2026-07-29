from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "aicrm_next" / "app" / "admin_console" / "static" / "admin_console"
AUTOMATION_STATIC = ROOT / "aicrm_next" / "automation" / "automation_engine" / "static" / "admin_console"
AUTOMATION_TEMPLATES = ROOT / "aicrm_next" / "automation" / "automation_engine" / "templates" / "admin_console"
RETIRED_OPERATION_TEMPLATE = AUTOMATION_TEMPLATES / "_automation_operation_orchestration_panel.html"
HXC_TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "hxc_dashboard.html"
AI_AUDIENCE_TEMPLATE = ROOT / "aicrm_next" / "extensions" / "ai" / "ai_audience_ops" / "templates" / "admin_console" / "ai_audience_package_list.html"
AUTOMATION_AGENT_TEMPLATE = ROOT / "aicrm_next" / "extensions" / "ai" / "automation_agents" / "templates" / "admin_console" / "automation_agent_edit.html"
CHANNEL_FORM_TEMPLATE = AUTOMATION_TEMPLATES / "channel_code_form.html"
GROUP_OPS_TEMPLATE = ROOT / "aicrm_next" / "automation" / "automation_engine" / "group_ops" / "templates" / "admin_console" / "group_ops.html"
CLOUD_CAMPAIGNS_TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "cloud_campaigns_workspace.html"
CLOUD_PLAN_TEMPLATE = ROOT / "aicrm_next" / "app" / "admin_console" / "templates" / "admin_console" / "cloud_plan_review.html"
RETIRED_OPERATION_JS = AUTOMATION_STATIC / "automation_operation_orchestration_panel.js"
RETIRED_AGENT_TEMPLATE_JS = STATIC / "automation_agent_config_templates.js"
MATERIAL_PICKER_CSS = STATIC / "material_picker.css"
SEND_CONTENT_ASSET_VERSION = "group-chat-selector-20260715"
GROUP_CHAT_ASSET_VERSION = "group-chat-direct-select-20260715"
CHANNEL_INLINE_ASSET_VERSION = "inline-channel-welcome-20260729"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_send_content_composer_exists_and_exposes_global_api() -> None:
    source = _read(STATIC / "send_content_composer.js")

    assert "window.AICRMSendContentComposer" in source
    assert "{ open, mount }" in source


def test_send_content_composer_inline_mode_auto_grows_and_emits_changes() -> None:
    source = _read(STATIC / "send_content_composer.js")
    css = _read(STATIC / "send_content_composer.css")

    assert "function mount(container, options)" in source
    assert "function autoGrowTextarea()" in source
    assert 'textField.style.height = "auto"' in source
    assert "textField.scrollHeight" in source
    assert 'typeof options.onChange === "function"' in source
    assert "countMaterials(currentPackage()) >= maxTotal" in source
    assert ".aicrm-send-composer--inline" in css
    assert "overflow-y: hidden" in css
    assert "grid-template-columns: 1fr" in css


def test_material_picker_exists_and_exposes_global_api() -> None:
    source = _read(STATIC / "material_picker.js")

    assert "window.AICRMMaterialPicker" in source
    assert ".open" in source or "{ open }" in source


def test_material_picker_hidden_empty_state_overrides_grid_display() -> None:
    source = _read(MATERIAL_PICKER_CSS)

    assert ".aicrm-material-picker__empty[hidden]" in source
    assert "display: none !important" in source


def test_standard_send_content_assets_are_cache_busted_on_migrated_surfaces() -> None:
    templates = [
        AI_AUDIENCE_TEMPLATE,
        CHANNEL_FORM_TEMPLATE,
        GROUP_OPS_TEMPLATE,
        CLOUD_CAMPAIGNS_TEMPLATE,
        CLOUD_PLAN_TEMPLATE,
        HXC_TEMPLATE,
    ]

    for path in templates:
        source = _read(path)
        assert "material_picker.css') }}?v=" in source
        assert "send_content_composer.css') }}?v=" in source
        assert "material_picker.js') }}?v=" in source
        assert "send_content_composer.js') }}?v=" in source
        if path == CHANNEL_FORM_TEMPLATE:
            assert source.count(f"?v={CHANNEL_INLINE_ASSET_VERSION}") >= 3
        else:
            assert source.count(f"?v={GROUP_CHAT_ASSET_VERSION}") >= 4

    automation_agent_source = _read(AUTOMATION_AGENT_TEMPLATE)
    assert automation_agent_source.count(f"?v={GROUP_CHAT_ASSET_VERSION}") >= 4
    assert f"channel_admission_pages.js?v={CHANNEL_INLINE_ASSET_VERSION}" in _read(CHANNEL_FORM_TEMPLATE)
    assert f"group_ops.js?v={SEND_CONTENT_ASSET_VERSION}" in _read(GROUP_OPS_TEMPLATE)
    assert "cloud_plan_review.js') }}?v=" + SEND_CONTENT_ASSET_VERSION in _read(CLOUD_PLAN_TEMPLATE)
    assert "user_ops_batch_send_modal.js') }}?v=" + SEND_CONTENT_ASSET_VERSION in _read(AI_AUDIENCE_TEMPLATE)


def test_send_content_composer_excludes_non_standard_controls() -> None:
    source = _read(STATIC / "send_content_composer.js")

    for forbidden in ["插入班期", "插入顾问名", "AI 改写", "保存为话术模板"]:
        assert forbidden not in source


def test_send_content_composer_supports_text_disabled_agent_mode() -> None:
    source = _read(STATIC / "send_content_composer.js")

    assert "textEnabled" in source
    assert "textEnabled=false" not in source
    assert "Agent 将为每个客户生成个性化话术" in source
    assert 'content_text: textEnabled ? normalized.content_text : ""' in source


def test_send_content_composer_uses_direct_material_add_buttons() -> None:
    source = _read(STATIC / "send_content_composer.js")

    assert 'data-add-material="image"' in source
    assert 'data-add-material="miniprogram"' in source
    assert 'data-add-material="attachment"' in source
    assert "+图片" in source
    assert "+小程序" in source
    assert "+附件" in source
    assert "data-composer-type" not in source
    assert "activeType" not in source


def test_send_content_composer_preview_renders_visual_material_cards() -> None:
    source = _read(STATIC / "send_content_composer.js")
    css = _read(STATIC / "send_content_composer.css")

    assert "function materialPreviewCard" in source
    assert "item.thumbnail_url" in source
    assert '<img src="${escapeHtml(item.thumbnail_url)}"' in source
    assert "aicrm-send-composer__preview-material" in source
    assert "aicrm-send-composer__preview-thumb" in css
    assert "object-fit: cover" in css


def test_send_content_composer_modal_fits_without_horizontal_scroll() -> None:
    css = _read(STATIC / "send_content_composer.css")

    assert "width: min(1180px, calc(100vw - 32px))" in css
    assert "overflow-x: hidden" in css
    assert "grid-template-columns: minmax(420px, 1fr) minmax(300px, 380px)" in css


def test_retired_operation_task_panel_assets_stay_removed() -> None:
    assert not RETIRED_OPERATION_TEMPLATE.exists()
    assert not RETIRED_OPERATION_JS.exists()


def test_material_selection_only_uses_material_picker_contract() -> None:
    composer = _read(STATIC / "send_content_composer.js")
    picker = _read(STATIC / "material_picker.js")

    assert "AICRMMaterialPicker.open" in composer
    assert "内容选择器未加载，请刷新页面后重试" in composer
    assert "/api/admin/material-picker/items" in picker
    for source in [composer, picker]:
        assert "/api/admin/image-library" not in source
        assert "/api/admin/miniprogram-library" not in source
        assert "/api/admin/attachment-library" not in source
        assert "hxc-" + "asset-grid" not in source
        assert "hxc-" + "img-grid" not in source
        assert "hxc-" + "mp-grid" not in source


def test_retired_profile_and_behavior_segmentation_ui_contracts_do_not_reappear() -> None:
    checked_paths = [
        STATIC / "automation_agent_config_core.js",
        STATIC / "automation_agent_config_boot.js",
        STATIC / "send_content_composer.js",
        STATIC / "material_picker.js",
        CHANNEL_FORM_TEMPLATE,
        GROUP_OPS_TEMPLATE,
        CLOUD_CAMPAIGNS_TEMPLATE,
        HXC_TEMPLATE,
    ]
    forbidden = [
        "profile-segment-templates",
        "behavior-segment-rules",
        "data-profile-template-select",
        "data-behavior-rule-select",
        "profile_segment_template_id",
        "segmentationQuestionId",
        "templateFields",
        "initializeTemplates",
        "refreshTemplates",
        "bindTemplateInteractions",
        "agent-materials",
        "data-config-unified",
    ]

    assert not RETIRED_AGENT_TEMPLATE_JS.exists()
    for path in checked_paths:
        source = _read(path)
        for marker in forbidden:
            assert marker not in source, f"{marker} leaked in {path}"


def test_hxc_dashboard_uses_standard_composer_without_legacy_broadcast() -> None:
    source = _read(HXC_TEMPLATE)

    assert "AICRMSendContentComposer.open" in source
    assert "send_content_composer.js" in source
    assert "/api/admin/hxc-dashboard/broadcast-tasks" in source
    assert not re.search(r"fetch\([\"']/api/admin/hxc-dashboard/broadcast[\"']", source)
    assert "/api/admin/image-library" not in source
    assert "/api/admin/miniprogram-library" not in source
    assert "hxc-" + "asset-grid" not in source
    assert "hxc-" + "img-grid" not in source
    assert "hxc-" + "mp-grid" not in source
