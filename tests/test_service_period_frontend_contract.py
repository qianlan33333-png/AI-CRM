from __future__ import annotations

import json
from pathlib import Path

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.extensions.commerce.service_period.application import GrantOrRenewEntitlementCommand
from aicrm_next.extensions.commerce.service_period.repo import reset_service_period_fixture_state


def _reset() -> None:
    reset_commerce_fixture_state()
    reset_service_period_fixture_state()


def _payload(**overrides) -> dict:
    payload = {
        "product_code": "sp_frontend_001",
        "title": "前端周期服务",
        "description": "周期商品前端契约",
        "price_cents": 99900,
        "currency": "CNY",
        "status": "active",
        "duration_days": 90,
        "membership_config_id": "frontend_vip_90d",
        "membership_config_name": "测试会员设置",
    }
    payload.update(overrides)
    return payload


def _create(next_client, **overrides) -> dict:
    response = next_client.post("/api/admin/service-period-products", json=_payload(**overrides))
    assert response.status_code == 201
    return response.json()["product"]


def _paid_order(out_trade_no: str, *, product_code: str, unionid: str, paid_at: str) -> dict:
    return {
        "id": abs(hash(out_trade_no)) % 100000,
        "out_trade_no": out_trade_no,
        "product_code": product_code,
        "product_name": "前端周期服务",
        "amount_total": 99900,
        "currency": "CNY",
        "unionid": unionid,
        "payer_name_snapshot": "前端用户",
        "status": "paid",
        "trade_state": "SUCCESS",
        "paid_at": paid_at,
    }


def test_service_period_admin_list_page_matches_table_contract(next_client) -> None:
    _reset()
    product = _create(next_client)

    response = next_client.get("/admin/service-period-products")
    assert response.status_code == 200
    text = response.text

    assert "周期商品管理" in text
    assert "创建、编辑和上下架周期商品。" in text
    assert "创建周期商品" in text
    for header in ("商品编码", "商品名称", "价格", "状态", "已售卖数量", "更新时间", "操作"):
        assert f"<th>{header}</th>" in text
    assert f"/admin/service-period-products/{product['id']}/data" in text
    assert "999.00 CNY / 90 天" in text
    operation_order = [">编辑<", ">数据<", ">分享<", ">复制<", ">下架<", ">删除<"]
    positions = [text.index(item) for item in operation_order]
    assert positions == sorted(positions)
    for forbidden in ("会员列表", "数据概览", "报名链接", "续费规则", "交易商品信息"):
        assert forbidden not in text


def test_service_period_edit_page_keeps_four_existing_dimensions_only(next_client) -> None:
    _reset()
    product = _create(next_client)

    response = next_client.get(f"/admin/service-period-products/{product['id']}/edit")
    assert response.status_code == 200
    text = response.text

    assert f"编辑周期商品 {product['product_code']}" in text
    for panel in ("sale", "media", "after", "push"):
        assert f'data-service-period-panel="{panel}"' in text
        assert f'data-service-period-panel-content="{panel}"' in text
    for label in ("售卖信息", "页面素材", "购买后动作", "外部推送"):
        assert label in text
    for field in ("商品名称", "商品编码", "价格", "有效期", "绑定会员设置", "商品状态", "手机号要求", "商品描述"):
        assert field in text
    assert "保存售卖信息" in text
    assert "保存页面素材" in text
    assert "保存购买后动作" in text
    assert "保存外部推送" in text
    assert "/api/admin/service-period-products/membership-configs" in text
    assert "/api/admin/wechat-pay/products/${encodeURIComponent(tradeId)}/external-push" in text
    assert "image_upload_client.js" in text
    assert "prepareImageForUpload(file)" in text
    assert 'if (mode === "new") body.product_code = productCodeValue;' in text
    assert "formatApiError(payload.detail || payload.error)" in text
    assert 'return window.AdminApi?.formatErrorValue(value) || "";' in text
    assert "JSON.stringify(value)" not in text
    assert 'require_mobile: $("requireMobile").value === "true"' in text
    assert "product_code: productCodeValue" not in text
    for forbidden in (
        "购买按钮文案",
        "周期设置",
        "会员设置</span>",
        "未过期续费规则",
        "已过期续费规则",
        "到期后处理",
        "礼包重发",
        "自动续费",
        "多规格",
        "用户端设置",
        "数据分析",
        "开通成功弹窗",
        "续费成功弹窗",
        "重新开通成功文案",
    ):
        assert forbidden not in text


def test_service_period_admin_payloads_omit_inline_slice_image_data(next_client) -> None:
    _reset()
    product = _create(
        next_client,
        product_code="sp_light_slices",
        slices=[
            {"image_library_id": 21, "image_url": "data:image/png;base64,YQ==", "sort_order": 1},
            {"image_library_id": 22, "data_url": "data:image/png;base64,Yg==", "sort_order": 2},
        ],
    )

    listed = next_client.get("/api/admin/service-period-products?limit=100")
    assert listed.status_code == 200
    listed_payload = listed.json()
    listed_text = json.dumps(listed_payload, ensure_ascii=False)
    assert "data:image" not in listed_text
    assert "data_base64" not in listed_text
    listed_product = next(item for item in listed_payload["items"] if item["id"] == product["id"])
    assert "slices" not in (listed_product.get("trade_product") or {})

    detail = next_client.get(f"/api/admin/service-period-products/{product['id']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    detail_text = json.dumps(detail_payload, ensure_ascii=False)
    assert "data:image" not in detail_text
    assert "data_base64" not in detail_text
    slices = detail_payload["product"]["trade_product"]["slices"]
    assert [item["image_library_id"] for item in slices] == [21, 22]
    assert [item["sort_order"] for item in slices] == [1, 2]
    assert all(not str(item.get("image_url") or "").startswith("data:") for item in slices)

    edit_page = next_client.get(f"/admin/service-period-products/{product['id']}/edit")
    assert edit_page.status_code == 200
    assert "data:image" not in edit_page.text
    assert "data_base64" not in edit_page.text


def test_service_period_data_page_has_only_data_contract(next_client) -> None:
    _reset()
    product = _create(next_client)

    response = next_client.get(f"/admin/service-period-products/{product['id']}/data")
    assert response.status_code == 200
    text = response.text

    assert "前端周期服务数据" in text
    assert "按视图筛选、排序和分组周期商品会员数据。" in text
    assert 'id="spMemberGrid"' in text
    assert 'id="spViewTabs"' in text
    assert 'id="spAddView"' in text
    assert 'id="spFilterButton"' in text
    assert 'id="spGroupButton"' in text
    assert 'id="spSortButton"' in text
    assert 'id="spSaveView"' in text
    assert 'id="spSaveAsView"' in text
    assert 'id="spGridScroll"' in text
    assert 'id="spUnsavedDialog"' in text
    assert 'id="spConflictDialog"' in text
    assert 'id="spShareButton"' in text
    assert 'id="spShareDialog"' in text
    assert 'id="spInviteCollaborator"' in text
    assert 'id="spExternalShareToggle"' in text
    assert 'id="spCopyExternalShareUrl"' in text
    assert "/static/service-period/admin_console/member_grid.css" in text
    assert "/static/service-period/admin_console/member_grid_share.js" in text
    assert "/static/service-period/admin_console/member_grid.js" in text

    schema = next_client.get(f"/api/admin/service-period-products/{product['id']}/member-grid/schema")
    assert schema.status_code == 200
    fields = schema.json()["schema"]["fields"]
    assert [field["label"] for field in fields] == [
        "会员",
        "剩余有效期",
        "正式登录",
        "token 消耗",
        "学习计划进度",
        "近 7 天打开次数",
        "最后打开时间",
        "续费次数",
        "备注",
        "联盟",
    ]
    assert [field["id"] for field in fields] == [
        "member",
        "remaining_days",
        "formally_logged_in",
        "token_usage",
        "learning_plan_progress",
        "open_count_7d",
        "last_open_at",
        "renewal_count",
        "remark",
        "alliance",
    ]
    assert [field["id"] for field in fields if field["editable"]] == ["remark", "alliance"]

    script = (
        Path(__file__).resolve().parents[1]
        / "aicrm_next/extensions/commerce/service_period/static/admin_console/member_grid.js"
    ).read_text(encoding="utf-8")
    assert "/member-grid/query" in script
    assert "/member-views" in script
    assert "/members/${encodeURIComponent(row.unionid)}/${encodeURIComponent(fieldId)}" in script
    assert '["remark", "alliance"]' in script
    assert 'editableTextCell("remark")' in script
    assert 'editableTextCell("alliance")' in script
    assert "sp-col-renewal_count" in script
    assert "IntersectionObserver" in script
    assert "beforeunload" in script
    assert 'event.key === "Enter" && !event.shiftKey' in script
    assert 'event.key === "Escape"' in script
    assert "window.sessionStorage" in script
    assert 'String(root.dataset.mode || "internal") === "public"' in script
    assert '"X-AICRM-Grid-Share-Token": shareToken' in script
    assert 'credentials: "omit"' in script
    assert 'referrerPolicy: "no-referrer"' in script

    stylesheet = (
        Path(__file__).resolve().parents[1]
        / "aicrm_next/extensions/commerce/service_period/static/admin_console/member_grid.css"
    ).read_text(encoding="utf-8")
    assert "max-height: 780px" not in stylesheet
    assert "height: calc(100vh - 286px)" in stylesheet
    assert ".sp-member-table .sp-col-renewal_count" in stylesheet
    assert ".sp-member-table .sp-col-alliance" in stylesheet

    for forbidden in (
        "导出数据",
        "有效用户",
        "7 天内到期",
        "续费订单",
        "累计金额",
        "会员列表",
        "添加记录",
        "字段配置",
        "填色",
        "行高",
        "操作</th>",
        "报名链接",
        "续费规则",
        "交易商品卡片",
        "交易商品信息",
        "用户报名页",
        "用户续费页",
        "管理配置页",
        "管理详情页",
    ):
        assert forbidden not in text


def test_service_period_public_page_renders_none_active_and_expired_ctas(next_client, monkeypatch) -> None:
    _reset()
    monkeypatch.setattr(
        h5_wechat_pay,
        "resolve_product_lead_qr",
        lambda _product: {
            "channel_id": 7,
            "channel_name": "报名后企微",
            "qr_url": "https://example.com/service-period-lead.png",
            "status": "active",
        },
        raising=False,
    )
    _create(next_client, product_code="sp_public_none")

    none_page = next_client.get("/s/sp_public_none")
    none_state = next_client.get("/api/h5/service-period-products/sp_public_none")
    assert none_page.status_code == 200
    assert none_state.status_code == 403
    assert none_state.json()["error"] == "wechat_browser_required"
    assert "立即报名" in none_page.text
    assert "开通后获得" not in none_page.text
    assert "测试会员设置" not in none_page.text
    assert "90 天有效期" not in none_page.text
    assert 'window.location.href = state.checkout_url' in none_page.text
    assert 'WeixinJSBridge.invoke("getBrandWCPayRequest"' not in none_page.text
    assert "商品编码" not in none_page.text
    assert "webhook" not in none_page.text.lower()
    assert '<div class="service-period-wecom-action" id="servicePeriodWecomAction" hidden>' in none_page.text

    _create(next_client, product_code="sp_public_active")
    GrantOrRenewEntitlementCommand()(
        order=_paid_order("SP_PUBLIC_ACTIVE", product_code="sp_public_active", unionid="union_public_active", paid_at="2099-01-01T00:00:00+00:00")
    )
    next_client.cookies.set(h5_wechat_pay.COOKIE_NAME, h5_wechat_pay._signed_blob({"openid": "op_active", "unionid": "union_public_active"}))
    active_page = next_client.get("/s/sp_public_active")
    active_state = next_client.get("/api/h5/service-period-products/sp_public_active")
    assert active_page.status_code == 200
    assert active_state.status_code == 200
    assert active_state.json()["lead_qr"]["qr_url"] == "https://example.com/service-period-lead.png"
    assert active_state.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "立即续费" in active_page.text
    assert "使用中" not in active_page.text
    assert "当前服务仍在有效期内" not in active_page.text
    assert "续费后有效期将继续顺延" not in active_page.text
    assert '<div class="service-period-wecom-action" id="servicePeriodWecomAction">' in active_page.text
    assert 'id="servicePeriodAddWecomButton"' in active_page.text
    assert "添加企微账号" in active_page.text
    assert "width: min(100%, 280px);" in active_page.text
    assert "min-height: 44px;" in active_page.text
    assert "font-size: 16px;" in active_page.text
    assert 'id="leadQrModal"' in active_page.text
    assert "扫码添加企微领取后续资料" in active_page.text
    assert "https://example.com/service-period-lead.png" in active_page.text

    _create(next_client, product_code="sp_public_expired")
    GrantOrRenewEntitlementCommand()(
        order=_paid_order("SP_PUBLIC_EXPIRED", product_code="sp_public_expired", unionid="union_public_expired", paid_at="2000-01-01T00:00:00+00:00")
    )
    next_client.cookies.set(h5_wechat_pay.COOKIE_NAME, h5_wechat_pay._signed_blob({"openid": "op_expired", "unionid": "union_public_expired"}))
    expired_page = next_client.get("/s/sp_public_expired")
    expired_state = next_client.get("/api/h5/service-period-products/sp_public_expired")
    assert expired_page.status_code == 200
    assert expired_state.status_code == 200
    assert expired_state.json()["lead_qr"] == {}
    assert "重新开通" in expired_page.text
    assert "上次到期日" in expired_page.text
    assert '<div class="service-period-wecom-action" id="servicePeriodWecomAction" hidden>' in expired_page.text


def test_service_period_active_page_hides_wecom_entry_without_channel_qr(next_client, monkeypatch) -> None:
    _reset()
    monkeypatch.setattr(h5_wechat_pay, "resolve_product_lead_qr", lambda _product: {}, raising=False)
    _create(next_client, product_code="sp_public_active_without_qr")
    GrantOrRenewEntitlementCommand()(
        order=_paid_order(
            "SP_PUBLIC_ACTIVE_WITHOUT_QR",
            product_code="sp_public_active_without_qr",
            unionid="union_public_active_without_qr",
            paid_at="2099-01-01T00:00:00+00:00",
        )
    )
    next_client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob({"openid": "op_active_without_qr", "unionid": "union_public_active_without_qr"}),
    )

    page = next_client.get("/s/sp_public_active_without_qr")
    state = next_client.get("/api/h5/service-period-products/sp_public_active_without_qr")

    assert page.status_code == 200
    assert state.status_code == 200
    assert state.json()["lead_qr"] == {}
    assert '<div class="service-period-wecom-action" id="servicePeriodWecomAction" hidden>' in page.text
    assert "wecomAction.hidden = !(status === \"active\" && activeLeadQr.qr_url);" in page.text
    assert "leadQrController.clear();" in page.text


def test_service_period_public_page_starts_oauth_before_state_in_wechat(next_client) -> None:
    _reset()
    _create(next_client, product_code="sp_public_auth_gate")

    response = next_client.get(
        "/s/sp_public_auth_gate",
        headers={"User-Agent": "Mozilla/5.0 MicroMessenger"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/api/h5/wechat-pay/oauth/start?return_url=%2Fs%2Fsp_public_auth_gate"


def test_service_period_pay_page_reuses_public_pay_confirmation_contract(next_client, monkeypatch) -> None:
    _reset()
    _create(next_client, product_code="sp_public_pay_mobile", require_mobile=True)
    monkeypatch.setenv("WECHAT_PAY_ENABLED", "1")

    before_auth = next_client.get("/s/sp_public_pay_mobile/pay")
    assert before_auth.status_code == 200
    assert "确认报名信息" in before_auth.text
    assert "请在微信中打开" in before_auth.text
    assert "请在微信中打开后完成授权。" in before_auth.text
    assert 'id="mobileInput"' not in before_auth.text

    next_client.cookies.set(h5_wechat_pay.COOKIE_NAME, h5_wechat_pay._signed_blob({"openid": "op_sp_pay", "unionid": "union_sp_pay"}))
    after_auth = next_client.get("/s/sp_public_pay_mobile/pay")
    assert after_auth.status_code == 200
    assert "授权登录" not in after_auth.text
    assert 'id="mobileInput"' in after_auth.text
    assert "/api/h5/service-period-products/sp_public_pay_mobile/wechat-pay/jsapi/orders" in after_auth.text
    assert '"post_paid_redirect_url": "/s/sp_public_pay_mobile"' in after_auth.text
    assert "支付成功，正在刷新服务期..." in after_auth.text
    assert "servicePeriodRedirect && !leadQrFromOrder(paidOrder).qr_url" in after_auth.text
    assert 'WeixinJSBridge.invoke("getBrandWCPayRequest"' in after_auth.text
    assert "请填写 11 位手机号后再继续。" in after_auth.text


def test_service_period_public_page_keeps_draft_slug_in_service_period_context(next_client) -> None:
    _reset()
    _create(next_client, product_code="sp_public_draft", status="draft")

    page = next_client.get("/s/sp_public_draft")
    assert page.status_code == 200
    assert "暂未开放" in page.text
    assert 'button.disabled = true;' in page.text
    assert "payload && payload.ok !== false" in page.text
    assert "questionnaire not found" not in page.text


def test_service_period_membership_configs_contract(next_client) -> None:
    _reset()

    response = next_client.get("/api/admin/service-period-products/membership-configs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["items"] == []
