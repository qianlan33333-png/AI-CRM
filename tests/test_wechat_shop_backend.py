from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.extensions.commerce.commerce.wechat_shop_client import WeChatShopClient, WeChatShopClientConfig, WeChatShopClientError
from aicrm_next.extensions.commerce.commerce.wechat_shop_service import (
    fixture_wechat_shop_order,
    fixture_wechat_shop_refunds,
    sync_wechat_shop_orders_backfill,
    sync_wechat_shop_orders_incremental,
    sync_wechat_shop_orders_window,
)
from aicrm_next.main import create_app
from aicrm_next.extensions.commerce.commerce.wechat_shop_signature import verify_signature


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "wechat-shop-backend")
    monkeypatch.setenv("WECHAT_SHOP_APPID", "wx_shop_test")
    monkeypatch.setenv("WECHAT_SHOP_APPSECRET", "shop-secret-value")
    monkeypatch.delenv("WECHAT_SHOP_CALLBACK_TOKEN", raising=False)
    return TestClient(create_app(), raise_server_exceptions=False)


def _mock_order(
    monkeypatch,
    *,
    order_id: str,
    status: int = 20,
    create_time: int = 1759900000,
    pay_time: int = 1760000000,
    transaction_id: str = "shop_tx_001",
    finish_aftersale_sku_cnt: int = 0,
    deliver_method: int = 3,
    buyer_mobile: str = "13520436848",
    product_title: str = "微信小店虚拟课程",
    sku_id: str = "sku_shop_001",
) -> None:
    monkeypatch.setattr(WeChatShopClient, "get_stable_access_token", lambda self, force_refresh=False: {"access_token": "shop-access-token", "expires_in": 7200})

    def fake_get_order(self, requested_order_id: str, access_token: str) -> dict:
        assert requested_order_id == order_id
        return {
            "errcode": 0,
            "order": {
                "order_id": requested_order_id,
                "status": status,
                "create_time": create_time,
                "update_time": 1760000300,
                "order_detail": {
                    "pay_info": {"pay_time": pay_time, "transaction_id": transaction_id, "pay_method": 1},
                    "price_info": {"order_price": 12900, "freight": 0},
                    "delivery_info": {
                        "deliver_method": deliver_method,
                        "address_info": {
                            "tel_number": "",
                            "purchaser_tel_number": "",
                            "virtual_order_tel_number": buyer_mobile,
                        },
                        "recharge_info": {"account_no": "virtual-account-001", "account_type": "member_id"},
                    },
                    "product_infos": [
                        {
                            "title": product_title,
                            "sku_id": sku_id,
                            "product_id": sku_id,
                            "sku_cnt": 1,
                            "finish_aftersale_sku_cnt": finish_aftersale_sku_cnt,
                        }
                    ],
                    "refund_info": {"refund_freight": 0},
                },
                "aftersale_detail": {"aftersale_order_list": []},
            },
        }

    monkeypatch.setattr(WeChatShopClient, "get_order", fake_get_order)


def test_commerce_wechat_shop_client_facade_has_no_direct_http_call() -> None:
    source = Path("aicrm_next/extensions/commerce/commerce/wechat_shop_client.py").read_text(encoding="utf-8")

    assert "requests.post" not in source
    assert "import requests" not in source


def test_wechat_shop_client_uses_injected_http_post_without_real_http() -> None:
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200
        text = '{"errcode":0,"order":{"order_id":"shop-order-001"}}'

        def json(self):
            return {"errcode": 0, "order": {"order_id": "shop-order-001"}}

    def fake_post(url, *, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    client = WeChatShopClient(
        WeChatShopClientConfig(appid="wx-shop", appsecret="secret", api_base="https://api.weixin.qq.com"),
        http_post=fake_post,
    )

    result = client.get_order("shop-order-001", "access-token")

    assert result["order"]["order_id"] == "shop-order-001"
    assert calls[0]["url"].endswith("/channels/ec/order/get?access_token=access-token")
    assert calls[0]["json"] == {"order_id": "shop-order-001"}


def test_wechat_shop_provider_is_accepted_by_unified_orders(monkeypatch) -> None:
    client = _client(monkeypatch)

    shop = client.get("/api/admin/orders?provider=wechat_shop").json()
    merged = client.get("/api/admin/orders?provider=all").json()

    assert shop["ok"] is True
    assert shop["providers"] == ["wechat_shop"]
    assert merged["ok"] is True
    assert merged["providers"] == ["wechat", "alipay", "wechat_shop"]


def test_wechat_shop_notify_records_large_order_id_as_string(monkeypatch) -> None:
    order_id = "370511505847120892812345678901"
    _mock_order(monkeypatch, order_id=order_id)
    client = _client(monkeypatch)

    response = client.post(
        "/api/wechat-shop/notify",
        json={
            "ToUserName": "gh_test",
            "FromUserName": "OPENID",
            "CreateTime": 1662480000,
            "MsgType": "event",
            "Event": "channels_ec_order_new",
            "order_info": {"order_id": int(order_id)},
        },
    )

    assert response.status_code == 200
    assert response.text == "success"
    events = client.get(f"/api/admin/wechat-shop/events?order_id={order_id}").json()
    assert events["events"][0]["order_id"] == order_id
    assert "e+" not in events["events"][0]["order_id"].lower()


def test_wechat_shop_notify_rejects_bad_signature_in_production(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "wechat-shop-prod-signature-test")
    monkeypatch.setenv("WECHAT_SHOP_CALLBACK_TOKEN", "wechat-shop-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/wechat-shop/notify?signature=bad&timestamp=1777777777&nonce=nonce",
        json={"order_info": {"order_id": "3705115058471208928"}},
    )

    assert response.status_code == 403


def test_wechat_shop_notify_insert_failure_returns_retryable_status(monkeypatch) -> None:
    client = _client(monkeypatch)
    monkeypatch.setenv("WECHAT_SHOP_CALLBACK_TOKEN", "wechat-shop-token")
    monkeypatch.setattr("aicrm_next.extensions.commerce.commerce.wechat_shop_service._insert_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))

    timestamp = "1777777777"
    nonce = "nonce"
    parts = ["wechat-shop-token", timestamp, nonce]
    import hashlib

    signature = hashlib.sha1("".join(sorted(parts)).encode("utf-8")).hexdigest()

    response = client.post(
        f"/api/wechat-shop/notify?signature={signature}&timestamp={timestamp}&nonce={nonce}",
        json={"order_info": {"order_id": "3705115058471208928"}},
    )

    assert response.status_code == 500
    assert response.text != "success"
    assert verify_signature("wechat-shop-token", timestamp, nonce, signature)


def test_wechat_shop_paid_order_maps_to_unified_paid_status(monkeypatch) -> None:
    order_id = "3705115058471208928"
    _mock_order(monkeypatch, order_id=order_id, status=20, transaction_id="shop_tx_paid")
    client = _client(monkeypatch)

    sync = client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync").json()
    detail = client.get(f"/api/admin/orders/{order_id}?provider=wechat_shop").json()

    assert sync["ok"] is True
    assert sync["provider"] == "wechat_shop"
    assert sync["provider_label"] == "微信小店"
    assert detail["order"]["status"] == "paid"
    assert detail["order"]["status_label"] in {"成交", "已支付"}
    assert detail["order"]["provider_label"] == "微信小店"
    assert detail["order"]["transaction_id"] == "shop_tx_paid"


def test_wechat_shop_order_created_at_uses_source_create_time(monkeypatch) -> None:
    order_id = "3705115058471208125"
    _mock_order(monkeypatch, order_id=order_id, create_time=1700000000)
    client = _client(monkeypatch)

    client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")
    saved = fixture_wechat_shop_order(order_id)

    assert saved is not None
    assert saved["created_at"] == "2023-11-14T22:13:20Z"


def test_wechat_shop_subscription_product_is_canonicalized(monkeypatch) -> None:
    order_id = "3705115058471208124"
    _mock_order(
        monkeypatch,
        order_id=order_id,
        product_title="老黄的一人公司实践与思考.订阅会员",
        sku_id="15383271146",
    )
    client = _client(monkeypatch)

    client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")
    detail = client.get(f"/api/admin/orders/{order_id}?provider=wechat_shop").json()
    items = client.get("/api/admin/orders?provider=all").json()["items"]

    assert detail["order"]["product_code"] == "subscription_trial_month"
    assert detail["order"]["product_name"] == "订阅会员"
    assert any(item["provider_label"] == "微信小店" for item in items)


def test_wechat_shop_buyer_mobile_is_available_for_filtering_and_export(monkeypatch) -> None:
    order_id = "3705115058471208123"
    mobile = "13520436848"
    _mock_order(monkeypatch, order_id=order_id, buyer_mobile=mobile)
    client = _client(monkeypatch)

    client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")
    detail = client.get(f"/api/admin/orders/{order_id}?provider=wechat_shop").json()
    filtered = client.get(f"/api/admin/orders?provider=wechat_shop&mobile={mobile[-4:]}").json()
    export = client.post(
        "/api/admin/exports",
        json={"resource": "orders", "format": "csv", "filters": {"provider": "wechat_shop", "order_no": order_id}},
    ).json()
    exported = client.get(export["job"]["download_url"]).json()

    assert detail["order"]["mobile"] == mobile
    assert detail["order"]["customer"]["mobile"] == mobile
    assert filtered["total"] == 1
    assert filtered["items"][0]["order_no"] == order_id
    assert "mobile" in exported["content_text"].splitlines()[0]
    assert mobile in exported["content_text"]


def test_wechat_shop_returned_order_maps_to_refunded_without_refund_ability(monkeypatch) -> None:
    order_id = "3705115058471208999"
    _mock_order(monkeypatch, order_id=order_id, status=200, finish_aftersale_sku_cnt=1)
    client = _client(monkeypatch)

    client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")
    detail = client.get(f"/api/admin/orders/{order_id}?provider=wechat_shop").json()

    assert detail["order"]["returned_recorded"] is True
    assert detail["order"]["status"] in {"full_refunded", "partial_refunded"}
    assert detail["order"]["can_refund"] is False


def test_wechat_shop_refund_request_uses_after_sale_api(monkeypatch) -> None:
    order_id = "3705115058471208555"
    _mock_order(monkeypatch, order_id=order_id, status=20, transaction_id="shop_tx_refund")
    aftersale_calls = []

    def fake_gen_after_sale_order(self, payload: dict, access_token: str) -> dict:
        aftersale_calls.append(payload)
        assert payload["order_id"] == order_id
        assert payload["request_id"].startswith("WSR")
        assert payload["amount"] == 12900
        return {"errcode": 0, "aftersale_id": "after_sale_001"}

    monkeypatch.setattr(WeChatShopClient, "gen_after_sale_order", fake_gen_after_sale_order)
    client = _client(monkeypatch)
    client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")

    response = client.post(
        "/api/admin/refunds",
        json={
            "provider": "wechat_shop",
            "order_no": order_id,
            "reason": "客户主动申请退款",
            "order_no_confirmation": order_id,
            "checked": True,
            "operator": "admin",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["provider"] == "wechat_shop"
    assert body["refund"]["status"] == "PROCESSING"
    assert body["refund"]["aftersale_id"] == "after_sale_001"
    assert body["order"]["status"] == "refund_processing"
    assert aftersale_calls
    refunds = fixture_wechat_shop_refunds()
    assert refunds[0]["order_id"] == order_id
    assert refunds[0]["status"] == "PROCESSING"


def test_wechat_shop_detail_page_shows_refund_form(monkeypatch) -> None:
    order_id = "3705115058471208557"
    _mock_order(monkeypatch, order_id=order_id, status=20, transaction_id="shop_tx_refund_page")
    client = _client(monkeypatch)

    client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")
    response = client.get(f"/admin/wechat-shop/transactions/{order_id}")

    assert response.status_code == 200
    assert "申请退款" in response.text
    assert "微信小店订单号" in response.text
    assert "/api/admin/refunds" in response.text


def test_wechat_shop_refund_failure_is_structured_and_redacted(monkeypatch) -> None:
    order_id = "3705115058471208556"
    _mock_order(monkeypatch, order_id=order_id, status=20, transaction_id="shop_tx_refund_fail")

    def fail_gen_after_sale_order(self, payload: dict, access_token: str) -> dict:
        raise WeChatShopClientError("bad access_token=shop-access-token shop-secret-value")

    monkeypatch.setattr(WeChatShopClient, "gen_after_sale_order", fail_gen_after_sale_order)
    client = _client(monkeypatch)
    client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")

    response = client.post(
        "/api/admin/refunds",
        json={
            "provider": "wechat_shop",
            "order_no": order_id,
            "reason": "客户主动申请退款",
            "order_no_confirmation": order_id,
            "checked": True,
        },
    )
    body = response.json()

    assert response.status_code == 400
    assert body["ok"] is False
    assert body["error_code"] == "invalid_refund_request"
    assert "shop-access-token" not in body["message"]
    assert "shop-secret-value" not in body["message"]


def test_wechat_shop_virtual_delivery_is_recorded_without_delivery_api(monkeypatch) -> None:
    order_id = "3705115058471208777"
    _mock_order(monkeypatch, order_id=order_id, deliver_method=3)
    client = _client(monkeypatch)

    response = client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")
    saved = fixture_wechat_shop_order(order_id)

    assert response.status_code == 200
    assert saved is not None
    assert saved["is_virtual_delivery"] is True
    assert saved["virtual_account_no"] == "virtual-account-001"
    assert saved["virtual_account_type"] == "member_id"


def test_wechat_shop_manual_sync_error_is_structured_and_redacted(monkeypatch) -> None:
    order_id = "3705115058471208666"
    monkeypatch.setattr(WeChatShopClient, "get_stable_access_token", lambda self, force_refresh=False: {"access_token": "shop-access-token", "expires_in": 7200})

    def fail_get_order(self, requested_order_id: str, access_token: str) -> dict:
        raise WeChatShopClientError("bad access_token=shop-access-token shop-secret-value")

    monkeypatch.setattr(WeChatShopClient, "get_order", fail_get_order)
    client = _client(monkeypatch)

    response = client.post(f"/api/admin/wechat-shop/orders/{order_id}/sync")
    body = response.json()

    assert response.status_code == 400
    assert body["ok"] is False
    assert body["error_code"] == "wechat_shop_order_sync_failed"
    assert "shop-access-token" not in body["message"]
    assert "shop-secret-value" not in body["message"]


def test_wechat_shop_window_sync_paginates_and_keeps_order_ids_as_strings(monkeypatch) -> None:
    client = _client(monkeypatch)
    order_ids = ["370511505847120892812345678901", "370511505847120892812345678902"]
    list_calls = []
    get_calls = []
    monkeypatch.setattr(WeChatShopClient, "get_stable_access_token", lambda self, force_refresh=False: {"access_token": "shop-access-token", "expires_in": 7200})

    def fake_list_orders(self, *, start_time: int, end_time: int, access_token: str, time_mode: str = "update_time", page_size: int = 100, next_key: str = "") -> dict:
        list_calls.append({"start_time": start_time, "end_time": end_time, "time_mode": time_mode, "page_size": page_size, "next_key": next_key})
        if not next_key:
            return {"errcode": 0, "order_id_list": [int(order_ids[0])], "has_more": True, "next_key": "page-2"}
        return {"errcode": 0, "order_id_list": [order_ids[1]], "has_more": False, "next_key": ""}

    def fake_get_order(self, requested_order_id: str, access_token: str) -> dict:
        get_calls.append(requested_order_id)
        return {
            "errcode": 0,
            "order": {
                "order_id": requested_order_id,
                "status": 20,
                "create_time": 1700000000,
                "order_detail": {
                    "pay_info": {"pay_time": 1700000100, "transaction_id": f"tx_{requested_order_id[-4:]}"},
                    "price_info": {"order_price": 9900},
                    "delivery_info": {"deliver_method": 3},
                    "product_infos": [{"title": "微信小店虚拟课程", "sku_id": "sku_shop_001", "sku_cnt": 1}],
                },
            },
        }

    monkeypatch.setattr(WeChatShopClient, "list_orders", fake_list_orders)
    monkeypatch.setattr(WeChatShopClient, "get_order", fake_get_order)

    result = sync_wechat_shop_orders_window(1700000000, 1700003600, mode="update_time", page_size=100, operator="pytest")
    runs = client.get("/api/admin/wechat-shop/sync-runs").json()

    assert result["ok"] is True
    assert result["sync_run"]["status"] == "success"
    assert result["sync_run"]["scanned_count"] == 2
    assert result["sync_run"]["synced_count"] == 2
    assert list_calls[0]["time_mode"] == "update_time"
    assert list_calls[1]["next_key"] == "page-2"
    assert get_calls == order_ids
    assert fixture_wechat_shop_order(order_ids[0])["order_id"] == order_ids[0]
    assert "e+" not in fixture_wechat_shop_order(order_ids[0])["order_id"].lower()
    assert runs["ok"] is True
    assert runs["sync_runs"][0]["status"] == "success"
    assert runs["sync_runs"][0]["scanned_count"] == 2


def test_wechat_shop_incremental_sync_continues_when_one_order_fails(monkeypatch) -> None:
    _client(monkeypatch)
    monkeypatch.setattr(WeChatShopClient, "get_stable_access_token", lambda self, force_refresh=False: {"access_token": "shop-access-token", "expires_in": 7200})

    def fake_list_orders(self, *, start_time: int, end_time: int, access_token: str, time_mode: str = "update_time", page_size: int = 100, next_key: str = "") -> dict:
        return {"errcode": 0, "order_id_list": ["ok-order-001", "fail-order-002"], "has_more": False, "next_key": ""}

    def fake_get_order(self, requested_order_id: str, access_token: str) -> dict:
        if requested_order_id == "fail-order-002":
            raise WeChatShopClientError("bad access_token=shop-access-token shop-secret-value")
        return {
            "errcode": 0,
            "order": {
                "order_id": requested_order_id,
                "status": 20,
                "create_time": 1700000000,
                "order_detail": {
                    "pay_info": {"pay_time": 1700000100, "transaction_id": "tx_ok"},
                    "price_info": {"order_price": 9900},
                    "delivery_info": {"deliver_method": 3},
                    "product_infos": [{"title": "微信小店虚拟课程", "sku_id": "sku_shop_001", "sku_cnt": 1}],
                },
            },
        }

    monkeypatch.setattr(WeChatShopClient, "list_orders", fake_list_orders)
    monkeypatch.setattr(WeChatShopClient, "get_order", fake_get_order)

    result = sync_wechat_shop_orders_incremental(lookback_minutes=120, overlap_minutes=15, operator="pytest")

    assert result["ok"] is True
    assert result["sync_run"]["status"] == "partial"
    assert result["sync_run"]["scanned_count"] == 2
    assert result["sync_run"]["synced_count"] == 1
    assert result["sync_run"]["failed_count"] == 1
    assert "shop-access-token" not in result["failures"][0]["error_message"]
    assert "shop-secret-value" not in result["failures"][0]["error_message"]
    assert fixture_wechat_shop_order("ok-order-001")["status_code"] == 20
    assert fixture_wechat_shop_order("fail-order-002")["sync_status"] == "failed"


def test_wechat_shop_backfill_uses_create_time_windows_in_dry_run(monkeypatch) -> None:
    _client(monkeypatch)
    list_calls = []
    monkeypatch.setattr(WeChatShopClient, "get_stable_access_token", lambda self, force_refresh=False: {"access_token": "shop-access-token", "expires_in": 7200})

    def fake_list_orders(self, *, start_time: int, end_time: int, access_token: str, time_mode: str = "update_time", page_size: int = 100, next_key: str = "") -> dict:
        list_calls.append({"start_time": start_time, "end_time": end_time, "time_mode": time_mode})
        return {"errcode": 0, "order_id_list": ["dry-run-order"], "has_more": False, "next_key": ""}

    monkeypatch.setattr(WeChatShopClient, "list_orders", fake_list_orders)

    result = sync_wechat_shop_orders_backfill(start_time=1700000000, end_time=1700172799, window_days=1, dry_run=True, operator="pytest")

    assert result["ok"] is True
    assert result["window_count"] == 2
    assert result["scanned_count"] == 2
    assert result["synced_count"] == 0
    assert {call["time_mode"] for call in list_calls} == {"create_time"}


def test_wechat_shop_does_not_change_existing_refund_routes(monkeypatch) -> None:
    client = _client(monkeypatch)

    alipay_refund = client.post("/api/admin/refunds", json={"provider": "alipay", "order_no": "order_fake_0003"})
    wechat_pay_refunds = client.get("/api/admin/wechat-pay/orders/1/refunds")

    assert alipay_refund.status_code == 400
    assert alipay_refund.json()["error_code"] == "provider_refund_not_supported"
    assert wechat_pay_refunds.status_code == 410
    assert wechat_pay_refunds.json()["error_code"] == "admin_wechat_pay_path_removed"
