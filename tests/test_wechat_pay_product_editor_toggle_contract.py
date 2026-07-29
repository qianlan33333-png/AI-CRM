from __future__ import annotations

from pathlib import Path


TEMPLATE = Path("aicrm_next/extensions/commerce/commerce/templates/wechat_products.html")


def test_product_editor_toggles_are_persisted_business_enablement_controls() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")

    assert "展开配置" not in text
    assert "let afterActionEnabled = Boolean((product.completion_target && product.completion_target.enabled) || product.completion_redirect_enabled || product.lead_channel_id);" in text
    assert "setAfterActionEnabled(Boolean((product.completion_target && product.completion_target.enabled) || product.completion_redirect_enabled || product.lead_channel_id));" in text
    assert 'lead_channel_id: afterActionEnabled && afterActionMode === "lead"' in text
    assert 'const enabled = afterActionEnabled && afterActionMode === "redirect" && completionTarget.enabled;' in text
    assert "completion_target: completionTarget" in text
    assert "setExternalPushActive(Boolean(externalPush.enabled));" in text
    assert "enabled: Boolean(externalPush.enabled)," in text
    assert "externalPushEnabled" not in text
    assert "支付成功外推" not in text


def test_product_editor_completion_target_ui_has_h5_and_dynamic_url_link_only() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")

    assert "H5 跳转地址" in text
    assert "动态 URL Link 接口" in text
    assert "响应字段" in text
    assert "completion_url_link_response_key" in text
    assert "打开微信小程序" not in text
    assert "completion_target_type" in text
    assert "splitMiniProgramPathInput" not in text
    assert "completion_target: completionTarget" in text

    assert "completionRedirectUrl" not in text
    assert "completion_open_strategy" not in text
    assert "data-open-strategy" not in text
    assert "target-desc" not in text
    assert "mode-note" not in text
    assert "mini_program_appid" not in text
    assert "mini_program_username" not in text
    assert "mini_program_path" not in text
    assert "completion_fallback_url" not in text
    assert "mini_program_env_version" not in text
    assert "mini_program_query" not in text
    assert "mini_program_url_link" not in text
    assert "data-h5-url-fields" in text
    assert "data-url-link-fields" in text
    assert "[data-h5-url-fields][hidden]" in text
    assert "[data-url-link-fields][hidden]" in text
    assert "打开小程序 URL Link" not in text
    assert "URL Link 兜底" not in text
