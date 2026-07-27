from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _canonical_identity_row(params) -> dict:
    external_userid, unionid, openid, mobile = tuple(params)
    return {
        "unionid": unionid,
        "external_userid": external_userid,
        "openid": openid,
        "mobile": mobile,
        "mobile_normalized": mobile,
        "status": "active",
        "matched_unionid": bool(unionid),
        "matched_external_userid": bool(external_userid),
        "matched_openid": bool(openid),
        "matched_mobile": bool(mobile),
    }


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "public-product-frontend-contract-test")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_public_product_frontend_redirects_empty_material_and_keeps_checkout_contract(monkeypatch) -> None:
    client = _client(monkeypatch)
    product = client.get("/p/test-product", follow_redirects=False)
    pay = client.get("/pay/test-product")

    assert product.status_code == 302
    assert product.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert product.headers["X-AICRM-Fallback-Used"] == "false"
    assert product.headers["location"] == "/pay/test-product"

    assert 'data-route-owner="ai_crm_next"' in pay.text
    assert "确认报名信息" in pay.text
    assert "请在微信中打开" in pay.text
    assert "/api/h5/wechat-pay/jsapi/orders" in pay.text
    assert "WeixinJSBridge.invoke" in pay.text
    assert 'id="leadQrModal"' in pay.text
    assert 'id="showLeadQrButton"' in pay.text
    assert "leadQrFromOrder" in pay.text
    assert "showLeadQr(order" in pay.text
    assert "支付暂不可用" not in pay.text
    assert "不会创建订单" not in pay.text
    assert "微信身份正在同步，请稍后重试。" in pay.text
    assert "当前微信身份存在冲突，请联系客服处理。" in pay.text


def test_payment_identity_accepts_canonical_unionid_before_openid_projection() -> None:
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _resolve_payment_identity

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConn:
        def execute(self, query, params=()):
            if "FROM crm_user_identity identity" in query:
                external_userid, unionid, openid, mobile = tuple(params)
                if unionid == "un_canonical" and not openid:
                    return Cursor(
                        [
                            {
                                "unionid": "un_canonical",
                                "external_userid": "ext_canonical",
                                "openid": "",
                                "mobile": "",
                                "mobile_normalized": "",
                                "status": "active",
                                "matched_unionid": True,
                                "matched_external_userid": False,
                                "matched_openid": False,
                                "matched_mobile": False,
                            }
                        ]
                    )
                return Cursor([])
            if "FROM crm_user_identity_resolution_queue" in query:
                return Cursor([{"pending_count": 1}])
            raise AssertionError(query)

    result = _resolve_payment_identity(
        FakeConn(),
        {"unionid": "un_canonical", "openid": "op_not_projected_yet"},
        for_update=True,
    )

    assert result.status == "resolved"
    assert result.identity is not None
    assert result.identity.unionid == "un_canonical"


def test_payment_identity_blocks_openid_resolved_to_another_unionid() -> None:
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _resolve_payment_identity

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConn:
        def execute(self, query, params=()):
            if "FROM crm_user_identity identity" not in query:
                raise AssertionError(query)
            _external_userid, unionid, openid, _mobile = tuple(params)
            if unionid:
                resolved_unionid = unionid
                matched_unionid = True
                matched_openid = False
            else:
                resolved_unionid = "un_other"
                matched_unionid = False
                matched_openid = bool(openid)
            return Cursor(
                [
                    {
                        "unionid": resolved_unionid,
                        "external_userid": "",
                        "openid": openid,
                        "mobile": "",
                        "mobile_normalized": "",
                        "status": "active",
                        "matched_unionid": matched_unionid,
                        "matched_external_userid": False,
                        "matched_openid": matched_openid,
                        "matched_mobile": False,
                    }
                ]
            )

    result = _resolve_payment_identity(
        FakeConn(),
        {"unionid": "un_canonical", "openid": "op_other"},
        for_update=True,
    )

    assert result.status == "conflict"
    assert result.reason == "identity_inputs_disagree"


def test_public_pay_landing_hides_mobile_before_oauth_for_mobile_required_product(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_PAY_ENABLED", "1")
    reset_commerce_fixture_state()
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "oauth-mobile-required",
            "title": "需手机号授权商品",
            "price_cents": 990,
            "enabled": True,
            "status": "active",
            "require_mobile": True,
            "buy_button_text": "立即报名",
        },
    )
    assert created.status_code == 200

    response = client.get("/pay/oauth-mobile-required")

    assert response.status_code == 200
    assert "请在微信中打开" in response.text
    assert "请在微信中打开后完成授权。" in response.text
    assert 'id="mobileInput"' not in response.text
    assert 'id="payButton"' not in response.text
    assert "/api/h5/wechat-pay/oauth/start" in response.text


def test_public_pay_landing_shows_mobile_after_oauth_for_mobile_required_product(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    monkeypatch.setenv("WECHAT_PAY_ENABLED", "1")
    reset_commerce_fixture_state()
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "authed-mobile-required",
            "title": "已授权手机号商品",
            "price_cents": 990,
            "enabled": True,
            "status": "active",
            "require_mobile": True,
            "buy_button_text": "立即报名",
        },
    )
    assert created.status_code == 200
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob({"openid": "op_authed", "unionid": "un_authed", "payer_name": "已授权用户"}),
    )

    response = client.get("/pay/authed-mobile-required")

    assert response.status_code == 200
    assert 'id="mobileInput"' in response.text
    assert "!/^1[3-9]\\d{9}$/.test(value)" in response.text
    assert "立即报名" in response.text
    assert "已就绪。" in response.text
    assert "微信授权后继续" not in response.text
    assert 'id="payButton"' in response.text


def test_public_product_frontend_material_page_contains_detail_images_and_cta(monkeypatch) -> None:
    reset_commerce_fixture_state()
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "frontend-material-product",
            "title": "前台带素材商品",
            "price_cents": 100,
            "enabled": True,
            "status": "active",
            "buy_button_text": "立即报名",
            "slices": [
                {"image_library_id": 1, "image_url": "data:image/png;base64,YQ==", "sort_order": 1},
                {"image_library_id": 2, "image_url": "data:image/png;base64,Yg==", "sort_order": 2},
            ],
        },
    )
    assert created.status_code == 200

    product = client.get("/p/frontend-material-product")

    assert product.status_code == 200
    assert 'data-route-owner="ai_crm_next"' in product.text
    assert 'data-fallback-used="false"' in product.text
    assert 'class="sticky-buy"' in product.text
    assert 'class="slice-img"' in product.text
    assert product.text.index('class="sticky-buy"') < product.text.index('class="slice-img"')
    assert "data:image" not in product.text
    assert (
        '<img class="slice-img" src="/api/h5/product-images/frontend-material-product/1/variants/original" '
        'loading="eager" decoding="async" fetchpriority="high" alt="">'
    ) in product.text
    assert (
        '<img class="slice-img" src="/api/h5/product-images/frontend-material-product/2/variants/original" '
        'loading="lazy" decoding="async" fetchpriority="low" alt="">'
    ) in product.text
    assert "/pay/frontend-material-product" in product.text
    assert 'class="hero-panel"' not in product.text
    assert 'class="detail-card"' not in product.text
    assert "当前页面只展示商品信息" not in product.text


def test_public_product_frontend_restores_slice_image_layout() -> None:
    from aicrm_next.extensions.commerce.public_product.service import render_product_page

    html = render_product_page(
        {
            "product_code": "subscription_trial_month",
            "title": "黄小璨首月体验",
            "description": "首月体验商品",
            "price_cents": 990,
            "currency": "CNY",
            "enabled": True,
            "slices": [
                {
                    "image_library_id": 1,
                    "image_url": "data:image/png;base64,YWFhYWFh",
                    "sort_order": 1,
                    "width": 750,
                    "height": 2400,
                },
                {
                    "image_library_id": 2,
                    "image_url": "data:image/png;base64,YmJiYmJi",
                    "sort_order": 2,
                }
            ],
            "detail_sections": [{"title": "服务说明", "body": "体验权益说明"}],
            "buy_button_text": "立即报名",
        }
    )

    assert 'class="detail-media"' in html
    assert 'class="slice-img"' in html
    assert "data:image" not in html
    assert html.index('class="sticky-buy"') < html.index('class="slice-img"')
    assert (
        '<img class="slice-img" src="/api/h5/product-images/subscription_trial_month/1/variants/original" '
        'loading="eager" decoding="async" fetchpriority="high" width="750" height="2400" alt="">'
    ) in html
    assert (
        '<img class="slice-img" src="/api/h5/product-images/subscription_trial_month/2/variants/original" '
        'loading="lazy" decoding="async" fetchpriority="low" alt="">'
    ) in html
    assert '<section class="hero-panel">' not in html
    assert 'class="detail-card"' not in html
    assert 'class="sticky-buy"' in html
    assert "立即报名" in html


def test_public_product_image_route_serves_only_bound_enabled_product_images(monkeypatch) -> None:
    from aicrm_next.platform.shared.errors import NotFoundError

    reset_commerce_fixture_state()
    calls: list[tuple[str, str]] = []

    class FakeGetImageVariantQuery:
        def __call__(self, image_id: str, variant_key: str) -> dict:
            calls.append((image_id, variant_key))
            if image_id != "1" or variant_key != "original":
                raise NotFoundError("image variant not found")
            return {
                "ok": True,
                "variant": {
                    "bytes": b"public-product-image",
                    "mime_type": "image/png",
                    "etag": '"public-product-image-v1"',
                },
            }

    monkeypatch.setattr("aicrm_next.extensions.commerce.public_product.service.GetImageVariantQuery", lambda: FakeGetImageVariantQuery())
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "public-image-product",
            "title": "公开图片商品",
            "price_cents": 100,
            "enabled": True,
            "status": "active",
            "slices": [{"image_library_id": 1, "image_url": "data:image/png;base64,YQ==", "sort_order": 1}],
        },
    )
    assert created.status_code == 200

    response = client.get("/api/h5/product-images/public-image-product/1/variants/original")

    assert response.status_code == 200
    assert response.content == b"public-product-image"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["etag"] == '"public-product-image-v1"'
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert calls == [("1", "original")]

    cached = client.get(
        "/api/h5/product-images/public-image-product/1/variants/original",
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304
    assert cached.headers["etag"] == '"public-product-image-v1"'

    calls.clear()
    unbound = client.get("/api/h5/product-images/public-image-product/2/variants/original")
    assert unbound.status_code == 404
    assert calls == []

    unknown = client.get("/api/h5/product-images/unknown-product/1/variants/original")
    assert unknown.status_code == 404

    disabled_created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "disabled-image-product",
            "title": "下架图片商品",
            "price_cents": 100,
            "enabled": False,
            "status": "disabled",
            "slices": [{"image_library_id": 1, "image_url": "data:image/png;base64,YQ==", "sort_order": 1}],
        },
    )
    assert disabled_created.status_code == 200
    disabled = client.get("/api/h5/product-images/disabled-image-product/1/variants/original")
    assert disabled.status_code == 404


def test_public_h5_order_payload_adds_lead_qr_only_after_paid() -> None:
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _order_payload

    row = {
        "out_trade_no": "WXP_PAID",
        "product_code": "subscription_trial_month",
        "product_name": "黄小璨首月体验",
        "amount_total": 990,
        "currency": "CNY",
        "status": "paid",
        "trade_state": "SUCCESS",
    }

    payload = _order_payload(
        row,
        completion_redirect={
            "completion_redirect_enabled": False,
            "completion_redirect_url": "",
            "completion_redirect": {"enabled": False, "url": ""},
            "completion_action": {"type": "default", "redirect_url": ""},
        },
        lead_qr={"channel_id": 7, "channel_name": "首月体验", "qr_url": "https://example.com/lead.png", "status": "active"},
    )

    assert payload["completion_action"] == {"type": "lead_qr", "redirect_url": ""}
    assert payload["lead_qr"]["qr_url"] == "https://example.com/lead.png"


def test_public_h5_order_payload_hides_lead_qr_before_paid_or_when_redirecting() -> None:
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _order_payload

    base_row = {
        "out_trade_no": "WXP_UNPAID",
        "product_code": "subscription_trial_month",
        "product_name": "黄小璨首月体验",
        "amount_total": 990,
        "currency": "CNY",
        "status": "paying",
        "trade_state": "",
    }
    lead_qr = {"channel_id": 7, "channel_name": "首月体验", "qr_url": "https://example.com/lead.png", "status": "active"}

    unpaid = _order_payload(base_row, lead_qr=lead_qr)
    assert "lead_qr" not in unpaid

    redirecting = _order_payload(
        {**base_row, "status": "paid", "trade_state": "SUCCESS"},
        completion_redirect={
            "completion_redirect_enabled": True,
            "completion_redirect_url": "/welcome",
            "completion_redirect": {"enabled": True, "url": "/welcome"},
            "completion_action": {"type": "redirect", "redirect_url": "/welcome"},
        },
        lead_qr=lead_qr,
    )
    assert redirecting["completion_action"] == {"type": "redirect", "redirect_url": "/welcome"}
    assert "lead_qr" not in redirecting


def test_public_lead_qr_resolver_reuses_configured_channel_asset(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    queries: list[tuple[str, tuple]] = []

    class Cursor:
        def fetchone(self):
            return {
                "channel_id": 7,
                "channel_name": "报名后企微",
                "qr_url": "https://example.com/current-channel.png",
                "status": "active",
            }

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            queries.append((query, params))
            return Cursor()

    monkeypatch.setattr(h5_wechat_pay, "production_data_ready", lambda: True)
    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: FakeConn())

    lead_qr = h5_wechat_pay.resolve_product_lead_qr(
        {
            "product_code": "sp_public_lead_qr",
            "lead_channel_id": 7,
            "completion_redirect_enabled": False,
            "completion_target": {},
        }
    )

    assert lead_qr == {
        "channel_id": 7,
        "channel_name": "报名后企微",
        "qr_url": "https://example.com/current-channel.png",
        "status": "active",
    }
    assert len(queries) == 1
    assert "FROM automation_channel c" in queries[0][0]
    assert queries[0][1] == (7,)


def test_public_lead_qr_resolver_skips_redirect_products(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    monkeypatch.setattr(h5_wechat_pay, "production_data_ready", lambda: True)
    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: (_ for _ in ()).throw(AssertionError("database should not be read")))

    assert h5_wechat_pay.resolve_product_lead_qr(
        {
            "product_code": "sp_redirect",
            "lead_channel_id": 7,
            "completion_redirect_enabled": True,
            "completion_redirect_url": "/welcome",
        }
    ) == {}


def test_public_pay_landing_reopens_existing_paid_order(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            if "FROM crm_user_identity identity" in query:
                return Cursor(_canonical_identity_row(params))
            if "FROM wechat_pay_orders" in query:
                return Cursor(
                    {
                        "id": 9,
                        "out_trade_no": "WXP_ALREADY_PAID",
                        "product_code": "test-product",
                        "product_name": "测试商品",
                        "amount_total": 990,
                        "currency": "CNY",
                        "status": "paid",
                        "trade_state": "SUCCESS",
                        "refund_status": "",
                        "refunded_amount_total": 0,
                        "unionid": "un_paid",
                    }
                )
            if "SELECT lead_channel_id" in query:
                return Cursor({"lead_channel_id": 7})
            if "FROM automation_channel c" in query:
                return Cursor({"channel_id": 7, "channel_name": "已购引流", "qr_url": "https://example.com/paid-qr.png", "status": "active"})
            return Cursor(None)

    monkeypatch.setattr(h5_wechat_pay, "production_data_ready", lambda: True)
    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: FakeConn())
    monkeypatch.setenv("WECHAT_PAY_ENABLED", "1")
    reset_commerce_fixture_state()
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "paid-mobile-required",
            "title": "已购手机号商品",
            "price_cents": 990,
            "enabled": True,
            "status": "active",
            "require_mobile": True,
            "buy_button_text": "立即报名",
        },
    )
    assert created.status_code == 200
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob({"openid": "op_paid", "unionid": "un_paid", "payer_name": "已购用户"}),
    )

    response = client.get("/pay/paid-mobile-required")

    assert response.status_code == 200
    assert '"paid_order": null' not in response.text
    assert "WXP_ALREADY_PAID" in response.text
    assert "https://example.com/paid-qr.png" in response.text
    assert 'id="mobileInput"' not in response.text
    assert 'id="payButton"' not in response.text
    assert "已报名，正在打开报名成功页。" in response.text
    assert "showPaid(state.paid_order, { autoShowQr: false })" in response.text


def test_public_h5_create_order_returns_existing_paid_order(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
    from aicrm_next.extensions.commerce.commerce.wechat_pay_client import WeChatPayClientConfig

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params=()):
            if "FROM crm_user_identity identity" in query:
                return Cursor(_canonical_identity_row(params))
            if "FROM wechat_pay_orders" in query:
                return Cursor(
                    {
                        "id": 9,
                        "out_trade_no": "WXP_ALREADY_PAID",
                        "product_code": "test-product",
                        "product_name": "测试商品",
                        "amount_total": 990,
                        "currency": "CNY",
                        "status": "paid",
                        "trade_state": "SUCCESS",
                        "refund_status": "",
                        "refunded_amount_total": 0,
                        "unionid": "un_paid",
                    }
                )
            if "SELECT lead_channel_id" in query:
                return Cursor({"lead_channel_id": None})
            return Cursor(None)

    class FailingClient:
        def __init__(self, config):
            raise AssertionError("wechat pay client should not be created for already paid orders")

    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: FakeConn())
    monkeypatch.setattr(
        h5_wechat_pay,
        "_require_payment_ready",
        lambda: WeChatPayClientConfig(
            app_id="app",
            mch_id="mch",
            api_v3_key="api-v3-key",
            private_key_path="/tmp/key.pem",
            merchant_serial_no="serial",
        ),
    )
    monkeypatch.setattr(h5_wechat_pay, "WeChatPayClient", FailingClient)
    client = _client(monkeypatch)
    client.cookies.set(h5_wechat_pay.COOKIE_NAME, h5_wechat_pay._signed_blob({"openid": "op_paid", "unionid": "un_paid"}))

    response = client.post(
        "/api/h5/wechat-pay/jsapi/orders",
        json={"product_code": "test-product"},
        headers={"User-Agent": "MicroMessenger"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["already_paid"] is True
    assert payload["order"]["out_trade_no"] == "WXP_ALREADY_PAID"
    assert payload["order"]["status"] == "paid"


def test_public_h5_create_order_does_not_reuse_paid_order_from_mismatched_sidebar_context(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
    from aicrm_next.extensions.commerce.commerce.wechat_pay_client import WeChatPayClientConfig

    queries: list[tuple[str, tuple]] = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def commit(self):
            return None

        def execute(self, query, params=()):
            queries.append((query, tuple(params)))
            if "FROM crm_user_identity identity" in query:
                return Cursor(_canonical_identity_row(params))
            if "FROM wechat_pay_orders" in query:
                assert "un_current" in params
                assert "ext_already_paid" not in params
                return Cursor(None)
            if "INSERT INTO wechat_pay_orders" in query:
                return Cursor(
                    {
                        "id": 10,
                        "out_trade_no": params[0],
                        "product_code": "test-product",
                        "product_name": "测试商品",
                        "amount_total": 990,
                        "currency": "CNY",
                        "status": "created",
                        "trade_state": "",
                        "unionid": "un_current",
                    }
                )
            if "UPDATE wechat_pay_orders" in query and "prepay_id" in query:
                return Cursor(
                    {
                        "id": 10,
                        "out_trade_no": params[-1],
                        "product_code": "test-product",
                        "product_name": "测试商品",
                        "amount_total": 990,
                        "currency": "CNY",
                        "status": "paying",
                        "trade_state": "",
                        "unionid": "un_current",
                    }
                )
            return Cursor(None)

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def create_jsapi_transaction(self, payload):
            return {"prepay_id": "wx_prepay_123"}

        def build_jsapi_pay_params(self, prepay_id):
            return {"package": f"prepay_id={prepay_id}"}

    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: FakeConn())
    monkeypatch.setattr(
        h5_wechat_pay,
        "_require_payment_ready",
        lambda: WeChatPayClientConfig(
            app_id="app",
            mch_id="mch",
            api_v3_key="api-v3-key",
            private_key_path="/tmp/key.pem",
            merchant_serial_no="serial",
        ),
    )
    monkeypatch.setattr(
        h5_wechat_pay,
        "resolve_sidebar_order_context",
        lambda **kwargs: {
            "context_status": "valid",
            "context_source": "signed_sidebar_product_link",
            "external_userid": "ext_already_paid",
            "owner_userid": "HuangYouCan",
            "mobile_source": "",
            "mobile": "",
        },
    )
    monkeypatch.setattr(h5_wechat_pay, "WeChatPayClient", FakeClient)
    client = _client(monkeypatch)
    client.cookies.set(h5_wechat_pay.COOKIE_NAME, h5_wechat_pay._signed_blob({"openid": "op_current", "unionid": "un_current"}))

    response = client.post(
        "/api/h5/wechat-pay/jsapi/orders",
        json={"product_code": "test-product", "ctx": "ctx-for-other-external"},
        headers={"User-Agent": "MicroMessenger"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "already_paid" not in payload
    assert payload["order"]["status"] == "paying"
    assert payload["pay_params"]["package"] == "prepay_id=wx_prepay_123"
    assert any("INSERT INTO wechat_pay_orders" in query for query, _ in queries)


@pytest.mark.parametrize(
    ("identity_status", "expected_error", "retryable"),
    [
        ("conflict", "identity_conflict", False),
        ("pending", "identity_resolution_required", True),
    ],
)
def test_public_h5_create_order_blocks_unresolved_payment_identity_before_order_or_wechat_call(
    monkeypatch,
    identity_status: str,
    expected_error: str,
    retryable: bool,
) -> None:
    from aicrm_next.crm.identity_contact.dto import IdentityResolveResult
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
    from aicrm_next.extensions.commerce.commerce.wechat_pay_client import WeChatPayClientConfig

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: FakeConn())
    monkeypatch.setattr(
        h5_wechat_pay,
        "_require_payment_ready",
        lambda: WeChatPayClientConfig(
            app_id="app",
            mch_id="mch",
            api_v3_key="api-v3-key",
            private_key_path="/tmp/key.pem",
            merchant_serial_no="serial",
        ),
    )
    monkeypatch.setattr(
        h5_wechat_pay,
        "resolve_identity_with_dbapi",
        lambda *_args, **_kwargs: IdentityResolveResult(status=identity_status, reason="test_identity_boundary"),
    )
    monkeypatch.setattr(
        h5_wechat_pay,
        "_insert_order",
        lambda *_args, **_kwargs: pytest.fail("unresolved payer must not create an order"),
    )
    monkeypatch.setattr(
        h5_wechat_pay,
        "WeChatPayClient",
        lambda *_args, **_kwargs: pytest.fail("unresolved payer must not call WeChat Pay"),
    )
    client = _client(monkeypatch)
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob({"openid": "op_unresolved", "unionid": "un_unresolved"}),
    )

    response = client.post(
        "/api/h5/wechat-pay/jsapi/orders",
        json={"product_code": "test-product"},
        headers={"User-Agent": "MicroMessenger"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "ok": False,
        "error": expected_error,
        "identity_status": identity_status,
        "retryable": retryable,
    }


def test_public_h5_paid_order_lookup_accepts_product_code_alias() -> None:
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _paid_order_for_product_identity

    captured = {}

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def execute(self, query, params=()):
            if "FROM crm_user_identity identity" in query:
                return Cursor(_canonical_identity_row(params))
            captured["query"] = query
            captured["params"] = tuple(params)
            return Cursor()

    _paid_order_for_product_identity(
        FakeConn(),
        product={"product_code": "subscription_trial_month"},
        identity={"openid": "op_alias", "unionid": "un_alias"},
    )

    assert "product_code IN" in captured["query"]
    assert "payer_openid = %s" not in captured["query"]
    assert "unionid = %s" in captured["query"]
    assert "subscription_trial_month" in captured["params"]
    assert "prd_20260518095708_9f77db" in captured["params"]
    assert "un_alias" in captured["params"]


def test_public_h5_paid_order_lookup_prefers_payment_identity_over_sidebar_external_userid() -> None:
    from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _paid_order_for_product_identity

    captured = {}

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConn:
        def execute(self, query, params=()):
            if "FROM crm_user_identity identity" in query:
                return Cursor(_canonical_identity_row(params))
            captured["query"] = query
            captured["params"] = tuple(params)
            return Cursor()

    _paid_order_for_product_identity(
        FakeConn(),
        product={"product_code": "premium_monthly_trial"},
        identity={"openid": "op_current", "unionid": "un_current", "external_userid": "ext_from_shared_card"},
    )

    assert "unionid = %s" in captured["query"]
    assert "external_userid = %s" not in captured["query"]
    assert "un_current" in captured["params"]
    assert "ext_from_shared_card" not in captured["params"]
