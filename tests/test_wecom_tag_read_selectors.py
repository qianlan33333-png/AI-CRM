from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from tests.admin_auth_test_helpers import admin_session_cookies


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _admin_cookies(client: TestClient) -> dict[str, str]:
    return admin_session_cookies(client, "super_admin")


def test_questionnaire_and_adjacent_selectors_read_unified_tag_catalog_source() -> None:
    questionnaire = _read("aicrm_next/extensions/forms/questionnaire/templates/admin_questionnaires.html") + _read(
        "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.js"
    )
    tag_management_template = _read("aicrm_next/customer_tags/templates/admin_console/config_wecom_tags.html")
    tag_management = _read("aicrm_next/customer_tags/static/admin_console/wecom_tag_management.js")
    automation_picker = _read("aicrm_next/frontend_compat/static/admin_console/automation_agent_config_tag_picker.js")
    automation_channel_model = _read("aicrm_next/frontend_compat/static/admin_console/automation_agent_config_channel_model.js")
    channel_pages = _read("aicrm_next/automation_engine/static/admin_console/channel_admission_pages.js")
    channel_admin_pages = _read("aicrm_next/automation_engine/channel_admin_pages.py")

    assert "fetchJson('/api/admin/wecom/tags')" in questionnaire
    assert 'data-api-tags="/api/admin/wecom/tags"' in tag_management_template
    assert 'data-api-groups="/api/admin/wecom/tag-groups"' in tag_management_template
    assert "/api/admin/wecom/tags" in tag_management
    assert "/api/admin/wecom/tag-groups" in tag_management
    assert "apiUrls.wecom_tags" in automation_picker
    assert "AutomationAgentConfig.loadWeComTags" in automation_channel_model
    assert "(bootstrap.api_urls || {}).wecom_tags" in channel_pages
    assert '"wecom_tags": "/api/admin/wecom/tags"' in channel_admin_pages
    assert "SELECT " not in questionnaire
    assert "SELECT " not in automation_picker
    assert "SELECT " not in channel_pages


def test_questionnaire_tag_selector_treats_degraded_empty_catalog_as_warning() -> None:
    questionnaire = _read("aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.js")

    assert "data.degraded || !state.availableTags.length" in questionnaire
    assert "data.page_error || '当前未获取到企微标签，可稍后重试'" in questionnaire
    assert "tagCatalogMessageEl.className = 'inline-alert warning'" in questionnaire
    assert "tagCatalogMessageEl.className = 'inline-alert error'" in questionnaire
    assert "extractErrorMessage(data)" in questionnaire


def test_unified_business_tag_picker_visible_copy_hides_internal_fields() -> None:
    picker = _read("aicrm_next/frontend_compat/static/admin_console/wecom_tag_picker.js")
    visible_block = picker[picker.index("overlay.innerHTML = `") : picker.index("document.body.appendChild")]

    for forbidden in ["tag_id", "entry_tag_id", "et_", "使用人数", "人", "保存 tag_id", "先选择标签组"]:
        assert forbidden not in visible_block

    for expected in ["暂无标签组", "暂无标签", "没有匹配结果", "已选：", "清空", "取消", "确认选择"]:
        assert expected in picker


def test_channel_entry_tag_uses_unified_single_picker_and_hidden_save_fields() -> None:
    template = _read("aicrm_next/automation_engine/templates/admin_console/channel_code_form.html")
    script = _read("aicrm_next/automation_engine/static/admin_console/channel_admission_pages.js")

    assert "wecom_tag_picker.css" in template
    assert "wecom_tag_picker.js" in template
    assert "data-resource-picker-results" not in template
    assert "channel-resource-picker" not in script
    assert "AICRMWeComTagPicker.open" in script
    assert 'mode: "single"' in script
    assert 'name="entry_tag_id"' in template
    assert 'name="entry_tag_name"' in template
    assert 'name="entry_tag_group_name"' in template
    assert 'data-id="' not in script


def test_questionnaire_tags_use_unified_multiple_picker_without_flat_modal() -> None:
    template = _read("aicrm_next/extensions/forms/questionnaire/templates/admin_questionnaires.html")
    script = _read("aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.js")

    assert "wecom_tag_picker.css" in template
    assert "wecom_tag_picker.js" in template
    assert "AICRMWeComTagPicker.open" in script
    assert "mode: 'multiple'" in script
    assert "applyTagSelection(target" in script
    assert "tag_codes" in script
    assert "tag-modal-overlay" not in script
    assert "tag-selected-panel" not in script
    assert "tag-chip-grid" not in script
    assert "保存 tag_id" not in script
    assert "实际保存 tag_id" not in script


def test_automation_default_entry_tag_uses_unified_single_picker() -> None:
    picker = _read("aicrm_next/frontend_compat/static/admin_console/automation_agent_config_tag_picker.js")
    channel_model = _read("aicrm_next/frontend_compat/static/admin_console/automation_agent_config_channel_model.js")

    assert "AICRMWeComTagPicker.open" in picker
    assert 'mode: "single"' in picker
    assert "catalog: { items: state.availableTags }" in picker
    assert "ac-config-tag-chip" not in picker
    assert "保存时只写入 tag_id" not in picker
    assert "手工填写 tag_id" not in picker
    assert "entry_tag_id: selectedTagId" in channel_model


def test_sidebar_signup_tags_status_is_not_a_tag_catalog_selector() -> None:
    inventory = Path("docs/architecture/wecom_tag_read_route_inventory.md").read_text(encoding="utf-8")

    assert "/api/sidebar/signup-tags/status" in inventory
    assert "No separate sidebar tag catalog selector" in inventory


def test_frontend_backend_contract_matrix_documents_real_entry_points() -> None:
    inventory = Path("docs/architecture/wecom_tag_read_route_inventory.md").read_text(encoding="utf-8")

    for marker in [
        "/admin/wecom-tags",
        "/admin/questionnaires/new",
        "/admin/questionnaires/{questionnaire_id}",
        "/admin/channels",
        "/admin/channels/{channel_id}/edit",
        "/admin/automation-conversion",
        "/admin/automation-conversion/programs/{program_id}/setup",
        "removed old automation program setup page",
        "route now returns `404`",
        'data-api-tags="/api/admin/wecom/tags"',
        "fetchJson('/api/admin/wecom/tags')",
        "PostgresTagCatalogRepository",
        "wecom_tags_read_next_native",
        "wecom_tag_groups_read_next_native",
    ]:
        assert marker in inventory


def test_frontend_entry_pages_expose_tag_api_urls_without_legacy_facade(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "wecom-tag-frontend-contract")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    wecom_page = client.get("/admin/wecom-tags")
    questionnaire_new = client.get("/admin/questionnaires/new")
    channels_page = client.get("/admin/channels")
    automation_page = client.get("/admin/automation-conversion", cookies=_admin_cookies(client))

    for response in [wecom_page, questionnaire_new, channels_page, automation_page]:
        assert response.status_code != 500

    assert 'data-api-tags="/api/admin/wecom/tags"' in wecom_page.text
    assert 'data-api-groups="/api/admin/wecom/tag-groups"' in wecom_page.text
    assert "/static/questionnaire/admin_questionnaire_editor.js?v=20260715-operations-only" in questionnaire_new.text
    questionnaire_script = client.get("/static/questionnaire/admin_questionnaire_editor.js")
    assert questionnaire_script.status_code == 200
    assert "fetchJson('/api/admin/wecom/tags')" in questionnaire_script.text
    assert "/api/admin/channels?limit=300" in channels_page.text
    assert "自动化运营" in automation_page.text


def test_selector_source_route_returns_frontend_compatible_items(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "wecom-tag-selector-source")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    response = TestClient(create_app(), raise_server_exceptions=False).get("/api/admin/wecom/tags")
    payload = response.json()

    assert response.status_code == 200
    assert payload["items"]
    assert {"tag_id", "tag_name", "group_name", "group_id"}.issubset(payload["items"][0])
    assert payload["route_owner"] == "ai_crm_next"
