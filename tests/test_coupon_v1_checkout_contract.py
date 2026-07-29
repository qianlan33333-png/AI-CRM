from __future__ import annotations

import inspect
import json
from typing import Any

import pytest
from fastapi import Request

from aicrm_next.extensions.commerce.commerce import admin_transactions
from aicrm_next.extensions.commerce.commerce.coupons import application as coupon_application
from aicrm_next.extensions.commerce.commerce.coupons import public_api as coupon_public_api
from aicrm_next.channels.integration_gateway.wechat_pay_client import WeChatPayClientConfig
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.extensions.commerce.public_product.service import render_pay_landing


def _public_coupon_state() -> dict[str, Any]:
    return {
        "ok": True,
        "coupon": {
            "name": "微信领取券",
            "discount_amount_total": 1_000,
            "instructions": "每单限用一张",
        },
        "products": [],
        "claimable": True,
        "claimed": False,
        "identity_ready": False,
        "user_limit_reached": False,
        "user_claim_count": 0,
        "display_state": "active",
        "validity_text": "领取后 2 个自然日内有效",
    }


def test_public_coupon_page_and_claim_enforce_wechat_oauth_and_idempotency(
    next_client,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    class FakePublicApplication:
        def get_coupon(self, public_slug: str, *, identity: dict[str, str]) -> dict[str, Any]:
            calls.append(("get", public_slug, identity.get("unionid", "")))
            return _public_coupon_state()

        def claim_coupon(
            self,
            public_slug: str,
            *,
            identity: dict[str, str],
            idempotency_key: str,
        ) -> dict[str, Any]:
            calls.append(("claim", public_slug, idempotency_key))
            return {"ok": True, "claim": {"claim_no": "CLM_public_opaque"}, "idempotent": False}

    monkeypatch.setattr(coupon_public_api, "CouponPublicApplication", FakePublicApplication)
    monkeypatch.setattr(coupon_public_api, "_identity", lambda _request: {})

    oauth_redirect = next_client.get(
        "/c/cpn_public_test",
        headers={"User-Agent": "Mozilla/5.0 MicroMessenger"},
        follow_redirects=False,
    )
    oauth_required = next_client.post(
        "/api/h5/coupons/cpn_public_test/claim",
        headers={"User-Agent": "Mozilla/5.0 MicroMessenger"},
    )

    assert oauth_redirect.status_code == 302
    assert oauth_redirect.headers["location"] == (
        "/api/h5/wechat-pay/oauth/start?return_url=%2Fc%2Fcpn_public_test"
    )
    assert oauth_required.status_code == 401
    assert oauth_required.json() == {
        "ok": False,
        "identity_ready": False,
        "error": "unionid_oauth_required",
        "message": "请先完成微信授权，获取稳定身份后继续。",
        "oauth_start_url": "/api/h5/wechat-pay/oauth/start?return_url=%2Fc%2Fcpn_public_test",
    }

    monkeypatch.setattr(
        coupon_public_api,
        "_identity",
        lambda _request: {"openid": "openid_coupon", "unionid": "union_coupon"},
    )
    missing_key = next_client.post(
        "/api/h5/coupons/cpn_public_test/claim",
        headers={"User-Agent": "MicroMessenger"},
    )
    claimed = next_client.post(
        "/api/h5/coupons/cpn_public_test/claim",
        headers={"User-Agent": "MicroMessenger", "Idempotency-Key": "claim-click-001"},
    )
    outside_wechat = next_client.post(
        "/api/h5/coupons/cpn_public_test/claim",
        headers={"User-Agent": "Safari", "Idempotency-Key": "claim-click-002"},
    )

    assert missing_key.status_code == 400
    assert missing_key.json()["error"] == "idempotency_key_required"
    assert claimed.status_code == 201
    assert claimed.json()["claim"]["claim_no"] == "CLM_public_opaque"
    assert outside_wechat.status_code == 403
    assert outside_wechat.json()["error"] == "wechat_browser_required"
    assert ("claim", "cpn_public_test", "claim-click-001") in calls
    assert not any(call[-1] == "claim-click-002" for call in calls)


@pytest.mark.parametrize(
    "payment_create_url",
    [
        "/api/h5/wechat-pay/jsapi/orders",
        "/api/h5/service-period-products/quarter/wechat-pay/jsapi/orders",
    ],
)
def test_ordinary_and_service_period_pay_pages_explicitly_submit_auto_or_none_coupon_choice(
    payment_create_url: str,
) -> None:
    product = {
        "product_code": "quarter" if "service-period" in payment_create_url else "ordinary",
        "title": "周期商品" if "service-period" in payment_create_url else "普通商品",
        "amount_total": 10_000,
        "price_cents": 10_000,
        "currency": "CNY",
    }
    html = render_pay_landing(
        product,
        {
            "product": product,
            "identity_ready": True,
            "paid_order": None,
            "require_mobile": False,
            "cta_text": "立即支付",
            "enabled": True,
            "payment_create_url": payment_create_url,
            "payment_status_url_template": "/api/h5/wechat-pay/orders/{out_trade_no}",
            "available_coupon_url": "/api/h5/coupons/available?target_ref=opaque",
        },
    )

    assert payment_create_url in html
    assert "coupon_choice: couponChoice()" in html
    assert 'if (value === "auto") return { mode: "auto" };' in html
    assert 'return { mode: "none" };' in html
    assert 'noneOption.textContent = "不使用优惠券"' in html
    assert "loadCoupons();" in html


class _PhaseConnection:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def __enter__(self):
        self.events.append(f"{self.name}:enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append(f"{self.name}:exit")
        return False

    def commit(self) -> None:
        self.events.append(f"{self.name}:commit")


def _request(path: str = "/api/h5/wechat-pay/jsapi/orders") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [(b"user-agent", b"MicroMessenger")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


def test_payment_create_commits_order_and_coupon_before_provider_and_retains_unknown_reservation(
    monkeypatch,
) -> None:
    events: list[str] = []
    connections = iter(
        [
            _PhaseConnection("phase1", events),
            _PhaseConnection("unknown-repair", events),
        ]
    )
    product = {
        "id": 101,
        "product_code": "ordinary",
        "title": "普通商品",
        "description": "普通商品",
        "price_cents": 10_000,
        "amount_total": 10_000,
        "currency": "CNY",
        "require_mobile": False,
    }

    monkeypatch.setattr(h5_wechat_pay, "_is_wechat_browser", lambda _request: True)
    monkeypatch.setattr(h5_wechat_pay, "get_public_product", lambda _code: dict(product))
    monkeypatch.setattr(
        h5_wechat_pay,
        "_identity_from_request",
        lambda _request: {"openid": "openid_pay", "unionid": "union_pay", "payer_name": "测试用户"},
    )
    monkeypatch.setattr(
        h5_wechat_pay,
        "_require_payment_ready",
        lambda: WeChatPayClientConfig(
            app_id="app",
            mch_id="mch",
            api_v3_key="x" * 32,
            private_key_path="/tmp/not-used.pem",
            merchant_serial_no="serial",
        ),
    )
    monkeypatch.setattr(
        h5_wechat_pay,
        "resolve_sidebar_order_context",
        lambda **_kwargs: {
            "mobile": "",
            "external_userid": "",
            "owner_userid": "",
            "context_status": "missing",
            "context_source": "",
            "mobile_source": "",
        },
    )
    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: next(connections))
    monkeypatch.setattr(h5_wechat_pay, "_resolve_payment_identity", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(h5_wechat_pay, "resolved_unionid", lambda _result: "union_pay")
    monkeypatch.setattr(h5_wechat_pay, "_paid_order_payload_for_product_identity", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(h5_wechat_pay, "_active_order_for_client_reference", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(h5_wechat_pay, "_out_trade_no", lambda: "WXP_COUPON_UNKNOWN")

    def insert_order(*_args, **_kwargs):
        events.append("order:inserted")
        return {
            "id": 88,
            "out_trade_no": "WXP_COUPON_UNKNOWN",
            "amount_total": 10_000,
            "subtotal_amount_total": 10_000,
            "discount_amount_total": 0,
            "currency": "CNY",
            "status": "created",
        }

    def reserve_coupon(*_args, **_kwargs):
        events.append("coupon:reserved")
        return {
            **insert_order(),
            "amount_total": 8_500,
            "discount_amount_total": 1_500,
            "coupon_claim_id": 7,
        }

    monkeypatch.setattr(h5_wechat_pay, "_insert_order", insert_order)
    monkeypatch.setattr(coupon_application, "reserve_coupon_for_order", reserve_coupon)

    class UnknownProviderClient:
        def __init__(self, _config):
            pass

        def create_jsapi_transaction(self, payload):
            events.append(f"provider:called:{payload['amount']['total']}")
            raise TimeoutError("provider response lost")

    monkeypatch.setattr(h5_wechat_pay, "WeChatPayClient", UnknownProviderClient)
    monkeypatch.setattr(
        h5_wechat_pay,
        "_mark_order_provider_unknown",
        lambda _conn, out_trade_no, _error: events.append(f"provider:unknown:{out_trade_no}"),
    )
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda *_args, **_kwargs: pytest.fail("unknown provider outcome must retain the coupon reservation"),
    )

    response = h5_wechat_pay.create_jsapi_order_response(
        _request(),
        {
            "product_code": "ordinary",
            "client_order_ref": "browser-order-001",
            "coupon_choice": {"mode": "auto"},
        },
    )
    payload = json.loads(response.body)

    assert response.status_code == 502
    assert payload == {
        "ok": False,
        "error": "wechat_pay_provider_outcome_unknown",
        "retryable": True,
        "out_trade_no": "WXP_COUPON_UNKNOWN",
    }
    assert response.headers["X-AICRM-Real-External-Call-Executed"] == "true"
    assert response.headers["X-AICRM-Payment-Request-Executed"] == "true"
    assert response.headers["X-AICRM-Order-Create-Executed"] == "true"
    assert events.index("coupon:reserved") < events.index("phase1:commit")
    assert events.index("phase1:commit") < events.index("provider:called:8500")
    assert "provider:unknown:WXP_COUPON_UNKNOWN" in events


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    def fetchone(self):
        return self.row


class _CallbackConnection:
    def __init__(self, order: dict[str, Any]) -> None:
        self.order = dict(order)
        self.order_updates = 0

    def execute(self, sql: str, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT * FROM wechat_pay_orders"):
            return _Result(dict(self.order))
        if normalized.startswith("UPDATE wechat_pay_orders"):
            self.order_updates += 1
            self.order.update(
                {
                    "status": params[0],
                    "trade_state": params[1],
                    "transaction_id": params[2],
                }
            )
            return _Result(dict(self.order))
        raise AssertionError(f"unexpected callback SQL: {normalized}")


def _transaction(*, trade_state: str = "SUCCESS", total: int = 8_500) -> dict[str, Any]:
    return {
        "out_trade_no": "WXP_CALLBACK_COUPON",
        "trade_state": trade_state,
        "transaction_id": "wx-transaction-001",
        "success_time": "2026-07-14T12:00:00+08:00",
        "amount": {"total": total, "payer_total": total, "currency": "CNY"},
    }


def test_coupon_payment_callback_rejects_amount_mismatch_before_order_or_coupon_mutation(monkeypatch) -> None:
    conn = _CallbackConnection(
        {
            "id": 88,
            "out_trade_no": "WXP_CALLBACK_COUPON",
            "amount_total": 8_500,
            "currency": "CNY",
            "coupon_claim_id": 7,
            "status": "paying",
            "trade_state": "",
        }
    )
    monkeypatch.setattr(
        coupon_application,
        "consume_coupon_for_paid_order",
        lambda *_args, **_kwargs: pytest.fail("amount mismatch must not consume a coupon"),
    )

    with pytest.raises(RuntimeError, match="amount_mismatch"):
        h5_wechat_pay._apply_transaction(conn, _transaction(total=8_501))

    assert conn.order_updates == 0


def test_coupon_payment_callback_is_idempotent_and_closed_callback_releases_once(monkeypatch) -> None:
    paid_conn = _CallbackConnection(
        {
            "id": 88,
            "out_trade_no": "WXP_CALLBACK_COUPON",
            "amount_total": 8_500,
            "currency": "CNY",
            "coupon_claim_id": 7,
            "status": "paying",
            "trade_state": "",
        }
    )
    lifecycle = {"redemption": "reserved", "effective_consumes": 0, "calls": 0}

    def idempotent_consume(*_args, **_kwargs):
        lifecycle["calls"] += 1
        if lifecycle["redemption"] == "reserved":
            lifecycle["redemption"] = "consumed"
            lifecycle["effective_consumes"] += 1
        return dict(paid_conn.order)

    monkeypatch.setattr(coupon_application, "consume_coupon_for_paid_order", idempotent_consume)
    monkeypatch.setattr(h5_wechat_pay, "_safe_project_order_mobile_to_identity", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(h5_wechat_pay, "_enqueue_payment_succeeded_internal_event_outbox", lambda *_args, **_kwargs: {})

    h5_wechat_pay._apply_transaction(paid_conn, _transaction())
    h5_wechat_pay._apply_transaction(paid_conn, _transaction())

    assert lifecycle == {"redemption": "consumed", "effective_consumes": 1, "calls": 2}

    closed_conn = _CallbackConnection(
        {
            "id": 89,
            "out_trade_no": "WXP_CALLBACK_COUPON",
            "amount_total": 8_500,
            "currency": "CNY",
            "coupon_claim_id": 8,
            "status": "paying",
            "trade_state": "",
        }
    )
    releases: list[tuple[str, str]] = []
    monkeypatch.setattr(
        coupon_application,
        "release_coupon_for_order",
        lambda _conn, *, out_trade_no, reason: releases.append((out_trade_no, reason)) or {},
    )

    h5_wechat_pay._apply_transaction(closed_conn, _transaction(trade_state="CLOSED", total=0))

    assert releases == [("WXP_CALLBACK_COUPON", "wechat_pay_closed")]


def test_partial_and_full_refund_paths_never_return_consumed_coupon() -> None:
    refund_functions = (
        admin_transactions.create_wechat_refund_request,
        admin_transactions.apply_wechat_refund_result,
        admin_transactions.handle_wechat_refund_notify,
    )

    for refund_function in refund_functions:
        source = inspect.getsource(refund_function)
        assert "release_coupon_for_order" not in source
        assert "commerce_coupon_claims" not in source
        assert "commerce_coupon_redemptions" not in source

    release_source = inspect.getsource(coupon_application.release_coupon_for_order)
    assert "refunds never call this path" in release_source
