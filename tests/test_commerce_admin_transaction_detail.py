from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from starlette.routing import Match

from aicrm_next.extensions.commerce.commerce.admin_transaction_detail import PaymentProviderStatusMapper, _present
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "commerce-admin-transaction-detail")
    return TestClient(create_app(), raise_server_exceptions=False)


def _first_match(app, *, method: str, path: str):
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "headers": []}
    for route in app.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route
    raise AssertionError(f"no route matched {method} {path}")


def test_admin_transaction_pages_resolve_to_commerce_not_frontend_compat(monkeypatch) -> None:
    client = _client(monkeypatch)
    app = client.app

    for path in [
        "/admin/orders",
        "/admin/wechat-pay/transactions",
        "/admin/wechat-pay/transactions/order_masked_001",
        "/admin/alipay/transactions",
    ]:
        route = _first_match(app, method="GET", path=path)
        assert route.endpoint.__module__ == "aicrm_next.extensions.commerce.commerce.api"


def test_unified_orders_page_exposes_payment_channel_dimension(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/orders")
    html = response.text
    template = Path("aicrm_next/extensions/commerce/commerce/templates/admin_orders.html").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "交易管理" in html
    assert "支付渠道" in html
    assert "付款人 / 客户身份" in html
    assert "/api/admin/orders" in html
    assert "微信小店" in html
    assert 'colspan="8"' in template
    assert 'return row.payer_name || customer.name || "未记录付款人";' in template
    assert "row.userid || row.external_userid || row.unionid || customer.userid || customer.external_userid || customer.unionid" in template


def test_wechat_transaction_detail_page_is_next_readonly(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/wechat-pay/transactions/order_masked_001")
    html = response.text

    assert response.status_code == 200
    assert "x-aicrm-compatibility-facade" not in response.headers
    assert "商户单号" in html
    assert "微信单号" in html
    assert "回调摘要" in html
    assert "order_masked_001" in html
    assert "transaction_masked_001" in html


def test_wechat_transaction_detail_missing_returns_404(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/wechat-pay/transactions/not_found_order")

    assert response.status_code == 404
    assert "订单不存在" in response.text
    assert "x-aicrm-compatibility-facade" not in response.headers


def test_alipay_transaction_page_uses_same_next_readonly_model(monkeypatch) -> None:
    response = _client(monkeypatch).get("/admin/alipay/transactions")
    html = response.text

    assert response.status_code == 200
    assert "支付宝交易管理" in html
    assert "/api/admin/alipay/transactions" in html
    assert "支付宝交易兼容入口" not in html
    assert "x-aicrm-compatibility-facade" not in response.headers


def test_wechat_shop_transaction_page_and_detail_are_next_routes(monkeypatch) -> None:
    client = _client(monkeypatch)
    app = client.app

    for path in ["/admin/wechat-shop/transactions", "/admin/wechat-shop/transactions/shop_fixture_paid_001"]:
        route = _first_match(app, method="GET", path=path)
        assert route.endpoint.__module__ == "aicrm_next.extensions.commerce.commerce.api"


def test_admin_transaction_apis_return_unified_readonly_fields(monkeypatch) -> None:
    client = _client(monkeypatch)

    wechat = client.get("/api/admin/wechat-pay/transactions").json()
    alipay = client.get("/api/admin/alipay/transactions").json()

    for payload in [wechat, alipay]:
        assert payload["ok"] is True
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False
        assert payload["items"]
        item = payload["items"][0]
        for key in ["merchant_order_no", "platform_transaction_no", "product_name", "amount_yuan", "status_label", "callback_summary"]:
            assert key in item


def test_admin_transaction_list_repairs_wechat_payer_mojibake() -> None:
    item = _present(
        "wechat",
        {
            "id": 1,
            "out_trade_no": "WXP260609124929054C79692934",
            "transaction_id": "4200003092202606091585869259",
            "payer_name_snapshot": "å¼ ä¸‰",
            "mobile_snapshot": "18689496150",
            "userid_snapshot": "HuangYouCan",
            "product_code": "premium_monthly_trial",
            "product_name": "黄小璨月度会员私教版",
            "amount_total": 6900,
            "status": "paid",
            "trade_state": "SUCCESS",
        },
    )

    assert item["payer_name"] == "张三"
    assert item["customer"]["name"] == "张三"


def test_admin_transaction_list_repairs_emoji_payer_mojibake() -> None:
    item = _present(
        "wechat",
        {
            "id": 1,
            "out_trade_no": "WXP260609064048981593301304",
            "transaction_id": "4200003082202606093443481735",
            "payer_name_snapshot": "CarolðŸŒ¸",
            "mobile_snapshot": "18186496215",
            "external_userid": "orSqJ5sX6-GPST-E_OwJXkVOiJRw",
            "product_code": "subscription_trial_month",
            "product_name": "黄小璨首月体验",
            "amount_total": 990,
            "status": "paid",
            "trade_state": "SUCCESS",
        },
    )

    assert item["payer_name"] == "Carol🌸"


def test_payment_provider_status_mapper_covers_admin_statuses() -> None:
    mapper = PaymentProviderStatusMapper()

    cases = [
        ("wechat", {"status": "paid", "trade_state": "SUCCESS"}, "paid"),
        ("wechat", {"status": "paying", "trade_state": ""}, "pending"),
        ("wechat", {"status": "closed", "trade_state": "CLOSED"}, "closed"),
        ("wechat", {"amount_total": 100, "refunded_amount_total": 100}, "full_refunded"),
        ("alipay", {"status": "created", "trade_status": "WAIT_BUYER_PAY"}, "pending"),
        ("alipay", {"status": "paid", "trade_status": "TRADE_SUCCESS"}, "paid"),
        ("alipay", {"status": "closed", "trade_status": "TRADE_CLOSED"}, "closed"),
        ("alipay", {"amount_total": 100, "refunded_amount_total": 10}, "partial_refunded"),
    ]
    for provider, row, expected in cases:
        assert mapper.map(provider, row)["status"] == expected
