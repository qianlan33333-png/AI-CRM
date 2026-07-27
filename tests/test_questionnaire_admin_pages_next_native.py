from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


def test_questionnaire_admin_list_page_exposes_next_read_status_without_facade() -> None:
    response = TestClient(create_app()).get("/admin/questionnaires")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    html = response.text
    assert "data-route-owner=\"ai_crm_next\"" in html
    assert "data-source-status=\"local_contract_probe\"" in html
    assert "data-read-model-status=\"fixture\"" in html
    assert "data-fallback-used=\"false\"" in html
    assert "hxc-activation-v1" in html
    assert "data-action=\"duplicate\"" in html
    assert "downloadQuestionnaireData" in html
    assert ".questionnaire-toast.is-busy" in html
    assert "导出中，请稍候..." in html
    assert "导出中..." in html
    assert "function downloadQuestionnaireData(questionnaireId, button)" in html
    assert "downloadQuestionnaireData(item.id, button)" in html
    assert "data-admin-shell-source=\"next_admin_shell\"" in html


def test_questionnaire_admin_ui_alias_redirects_to_canonical_route() -> None:
    response = TestClient(create_app()).get("/admin/questionnaires/ui", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/questionnaires"


def test_questionnaire_admin_new_page_is_readonly_shell_without_write_execution() -> None:
    response = TestClient(create_app()).get("/admin/questionnaires/new")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "新建问卷" in response.text
    assert '"initialQuestionnaireId": null' in response.text
    assert "/static/questionnaire/admin_questionnaire_editor.js?v=20260715-operations-only" in response.text


def test_questionnaire_admin_editor_exposes_other_option_controls() -> None:
    response = TestClient(create_app()).get("/admin/questionnaires/new")

    assert response.status_code == 200
    script = TestClient(create_app()).get("/static/questionnaire/admin_questionnaire_editor.js")
    assert script.status_code == 200
    assert "设为其它选项" in script.text
    assert 'data-option-field="is_other"' in script.text
    assert 'data-option-field="other_placeholder"' in script.text
    assert 'data-option-field="other_max_length"' in script.text
    assert "validateOtherOptionsBeforeSave" in script.text


def test_questionnaire_admin_detail_page_uses_next_read_model_editor_payload() -> None:
    response = TestClient(create_app()).get("/admin/questionnaires/1")

    assert response.status_code == 200
    assert "X-AICRM-Compatibility-Facade" not in response.headers
    assert "黄小璨激活问卷" in response.text
    assert "hxc-activation-v1" in response.text
    assert "/static/questionnaire/admin_questionnaire_editor.js?v=20260715-operations-only" in response.text
    script = TestClient(create_app()).get("/static/questionnaire/admin_questionnaire_editor.js")
    assert script.status_code == 200
    assert "复制问卷" in script.text


def test_questionnaire_admin_pages_are_removed_from_frontend_compat_routes() -> None:
    root = Path(__file__).resolve().parents[1]

    assert not (root / "aicrm_next/frontend_compat/legacy_routes.py").exists()


def test_questionnaire_admin_templates_live_in_questionnaire_bundle() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "aicrm_next/extensions/forms/questionnaire/templates/admin_console/questionnaires.html").exists()
    assert (root / "aicrm_next/extensions/forms/questionnaire/templates/admin_questionnaires.html").exists()
    assert (root / "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.css").exists()
    assert (root / "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.js").exists()
    assert not (root / "aicrm_next/frontend_compat/templates/admin_console/questionnaires.html").exists()
    assert not (root / "aicrm_next/frontend_compat/templates/admin_questionnaires.html").exists()


def test_questionnaire_operations_page_owns_completion_and_external_push_ui() -> None:
    root = Path(__file__).resolve().parents[1]
    client = TestClient(create_app())
    page = client.get("/admin/questionnaires/1/operations")

    assert page.status_code == 200
    assert page.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert page.headers["X-AICRM-Fallback-Used"] == "false"
    assert page.text.count('id="qo-summary-title"') == 1
    assert 'class="admin-page-title"' not in page.text
    assert "提交后动作" in page.text
    assert "展示渠道二维码" in page.text
    assert "直接跳转" in page.text
    assert "H5 跳转地址" in page.text
    assert "动态 URL Link 接口" in page.text
    assert 'id="qo-legacy-appid"' in page.text
    assert 'id="qo-legacy-path"' in page.text
    assert "外部推送" in page.text
    assert "测试推送" in page.text
    assert "/static/navigation-target/completion_target_config.js" in page.text

    operations_script = (root / "aicrm_next/extensions/forms/questionnaire/static/questionnaire_operations.js").read_text(encoding="utf-8")
    assert 'document.addEventListener("DOMContentLoaded", initialize' in operations_script
    assert 'document.readyState === "loading" || !window.AdminApi' in operations_script
    assert "const api = window.AdminApi;" in operations_script
    assert '$("qo-legacy-appid").textContent' in operations_script
    assert '$("qo-legacy-path").textContent' in operations_script

    editor_script = (root / "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.js").read_text(encoding="utf-8")
    assert "completion_target" not in editor_script
    assert "external_push" not in editor_script
    assert "field-external-push" not in editor_script
    assert "editor-global-external-push-logs-btn" not in editor_script
    assert "editor-external-push-logs-btn" not in editor_script
    assert "/admin/questionnaires/external-push-logs" not in editor_script

    product_template = (root / "aicrm_next/extensions/commerce/commerce/templates/wechat_products.html").read_text(encoding="utf-8")
    assert "/static/navigation-target/completion_target_config.js" in product_template
    assert "window.AICRMCompletionTargetConfig.mount" in product_template

    public_template = (root / "aicrm_next/frontend_compat/templates/questionnaire_h5_page.html").read_text(encoding="utf-8")
    public_completion_script = (root / "aicrm_next/extensions/forms/questionnaire/static/questionnaire_completion_action.js").read_text(encoding="utf-8")
    assert "/static/questionnaire/questionnaire_completion_action.js" in public_template
    assert "AICRMQuestionnaireCompletionAction.create" in public_template
    assert 'action.type === "lead_qr"' in public_completion_script


def test_questionnaire_list_exposes_operations_only_for_regular_questionnaires() -> None:
    response = TestClient(create_app()).get("/admin/questionnaires")

    assert response.status_code == 200
    assert 'href="/admin/questionnaires/${item.id}/operations">运营配置</a>' in response.text
    assert "item.is_assessment_template_asset ? ''" in response.text
