from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from aicrm_next.platform.admin_config.settings import mask_value
from aicrm_next.extensions.commerce.commerce.order_expiration import close_expired_wechat_pay_orders
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.extensions.commerce.commerce.wechat_pay_client import WeChatPayClient, WeChatPayClientConfig


def _wechat_checkout_payload(product_code: str = "test-product") -> dict:
    return {
        "product_code": product_code,
        "quantity": 1,
        "buyer_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001"},
        "return_url": f"/pay/{product_code}",
    }


def test_wechat_pay_sensitive_settings_are_masked_by_next_settings():
    assert mask_value("WECHAT_PAY_API_V3_KEY", "12345678901234567890123456789012") == "[redacted]"
    assert mask_value("WECHAT_PAY_CERT_SERIAL_NO", "serial123456") == "[redacted]"


def test_next_wechat_pay_client_sends_platform_public_key_id_header():
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    def fake_request(method, url, *, data, headers, timeout):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    pay_client = WeChatPayClient(
        WeChatPayClientConfig(
            app_id="wx-app",
            mch_id="1900000001",
            api_v3_key="12345678901234567890123456789012",
            private_key_path="",
            merchant_serial_no="merchant-serial",
            platform_serial_no="PUB_KEY_ID_0116571234562024052000123400000000",
        ),
        http_request=fake_request,
    )
    pay_client._merchant_signature = lambda message: "signed"  # type: ignore[method-assign]

    pay_client.query_order_by_out_trade_no("WXPTEST0001")

    headers = captured["headers"]
    assert captured["method"] == "GET"
    assert "/v3/pay/transactions/out-trade-no/WXPTEST0001" in captured["url"]
    assert "mchid=1900000001" in captured["url"]
    assert headers["Wechatpay-Serial"] == "PUB_KEY_ID_0116571234562024052000123400000000"


def test_next_wechat_pay_client_does_not_send_certificate_serial_header():
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    def fake_request(method, url, *, data, headers, timeout):
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    pay_client = WeChatPayClient(
        WeChatPayClientConfig(
            app_id="wx-app",
            mch_id="1900000001",
            api_v3_key="12345678901234567890123456789012",
            private_key_path="",
            merchant_serial_no="merchant-serial",
            platform_serial_no="19FBBF2A24A3F3C97F5925FA855A850D6E4624AF",
        ),
        http_request=fake_request,
    )
    pay_client._merchant_signature = lambda message: "signed"  # type: ignore[method-assign]

    pay_client.query_order_by_out_trade_no("WXPTEST0001")

    assert "Wechatpay-Serial" not in captured["headers"]


def test_next_wechat_pay_client_creates_refund_request_without_real_http():
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = '{"status":"SUCCESS","refund_id":"503000000020260516"}'

        def json(self):
            return {"status": "SUCCESS", "refund_id": "503000000020260516"}

    def fake_request(method, url, *, data, headers, timeout):
        captured["method"] = method
        captured["url"] = url
        captured["data"] = data.decode("utf-8")
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    pay_client = WeChatPayClient(
        WeChatPayClientConfig(
            app_id="wx-app",
            mch_id="1900000001",
            api_v3_key="12345678901234567890123456789012",
            private_key_path="",
            merchant_serial_no="merchant-serial",
        ),
        http_request=fake_request,
    )
    pay_client._merchant_signature = lambda message: "signed"  # type: ignore[method-assign]

    result = pay_client.create_refund(
        {
            "transaction_id": "420000REALREFUND",
            "out_refund_no": "WXRTEST0001",
            "reason": "客户主动申请退款",
            "amount": {"refund": 1000, "total": 9900, "currency": "CNY"},
        }
    )

    assert result["status"] == "SUCCESS"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v3/refund/domestic/refunds")
    assert json.loads(captured["data"])["out_refund_no"] == "WXRTEST0001"


def test_next_wechat_pay_client_verifies_notify_signature_with_platform_certificate(tmp_path):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "WeChat Pay Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, "wechatpay.test"),
        ]
    )
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(1234567890)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(private_key, hashes.SHA256())
    )
    certificate_path = tmp_path / "wechatpay_platform_cert.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    body = '{"id":"notify-id"}'
    timestamp = "1778888888"
    nonce = "notify-nonce"
    message = f"{timestamp}\n{nonce}\n{body}\n".encode("utf-8")
    signature = base64.b64encode(private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")

    pay_client = WeChatPayClient(
        WeChatPayClientConfig(
            app_id="wx-app",
            mch_id="1900000001",
            api_v3_key="12345678901234567890123456789012",
            private_key_path="",
            merchant_serial_no="merchant-serial",
            platform_public_key_path=str(certificate_path),
            platform_serial_no="1234567890",
        )
    )

    pay_client.verify_notification_signature(
        body=body,
        headers={
            "Wechatpay-Timestamp": timestamp,
            "Wechatpay-Nonce": nonce,
            "Wechatpay-Signature": signature,
            "Wechatpay-Serial": "1234567890",
        },
    )


def test_commerce_wechat_pay_client_facade_has_no_direct_http_call() -> None:
    source = Path("aicrm_next/extensions/commerce/commerce/wechat_pay_client.py").read_text(encoding="utf-8")

    assert "requests.request" not in source
    assert "import requests" not in source


def test_next_wechat_checkout_and_notify_keep_h5_payment_contract(next_client):
    reset_commerce_fixture_state()

    checkout = next_client.post("/api/checkout/wechat", json=_wechat_checkout_payload())
    checkout_payload = checkout.json()

    assert checkout.status_code == 200
    assert checkout.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert checkout.headers["X-AICRM-Fallback-Used"] == "false"
    assert checkout.headers["X-AICRM-Payment-Request-Executed"] == "false"
    assert checkout_payload["ok"] is True
    assert checkout_payload["payment_provider"] == "wechat"
    assert checkout_payload["fake_payment"] is True
    assert checkout_payload["order_create_executed"] == "local_only"
    assert checkout_payload["real_external_call_executed"] is False

    notify = next_client.post(
        "/api/wechat-pay/notify",
        json={
            "order_no": checkout_payload["order_no"],
            "payment_status": "paid",
            "transaction_id": "fake_tx_h5_001",
            "provider_payload": {"notify_id": "notify_h5_001"},
        },
    )
    notify_payload = notify.json()

    assert notify.status_code == 200
    assert notify.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert notify.headers["X-AICRM-Fallback-Used"] == "false"
    assert notify_payload["payment_notify_executed"] == "local_only"
    assert notify_payload["real_payment_notify_executed"] is False
    assert notify_payload["provider_signature_verified"] is False


def test_h5_wechat_pay_order_expiry_defaults_to_two_hours() -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(h5_wechat_pay._expires_at().replace("Z", "+00:00"))

    assert timedelta(minutes=119) <= expires_at - now <= timedelta(minutes=121)


def test_close_expired_wechat_pay_orders_only_targets_unpaid_pending_orders() -> None:
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class FakeConn:
        def __init__(self):
            self.query = ""
            self.params = ()

        def execute(self, query, params=()):
            self.query = query
            self.params = params
            return Cursor(
                [
                    {
                        "id": 1,
                        "out_trade_no": "WXP_EXPIRED",
                        "status": "closed",
                        "trade_state": "CLOSED",
                        "created_at": "2026-06-30T18:00:00Z",
                        "updated_at": "2026-06-30T20:01:00Z",
                    }
                ]
            )

    conn = FakeConn()
    result = close_expired_wechat_pay_orders(
        conn=conn,
        now=datetime(2026, 6, 30, 22, 0, 0, tzinfo=timezone.utc),
        ttl_hours=2,
        limit=20,
    )

    assert result["closed_count"] == 1
    assert result["real_external_call_executed"] is False
    assert "COALESCE(status, '') IN ('created', 'paying', 'pending', '')" in conn.query
    assert "COALESCE(trade_state, '') <> 'SUCCESS'" in conn.query
    assert "paid_at IS NULL" in conn.query
    assert "FOR UPDATE SKIP LOCKED" in conn.query
    assert conn.params[0] == datetime(2026, 6, 30, 20, 0, 0, tzinfo=timezone.utc)


def test_h5_order_status_closes_expired_pending_order_before_return(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    calls: list[dict] = []

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
            calls.append({"commit": True})

        def rollback(self):
            calls.append({"rollback": True})

        def execute(self, query, params=()):
            if "SELECT * FROM wechat_pay_orders" in query:
                return Cursor(
                    {
                        "id": 1,
                        "out_trade_no": "WXP_EXPIRED",
                        "product_code": "course_masked_001",
                        "product_name": "课程商品样例",
                        "amount_total": 9900,
                        "currency": "CNY",
                        "unionid": "union-expired-owner",
                        "coupon_snapshot_json": {
                            "claim_no": "CLM_INTERNAL_SECRET",
                            "coupon_name": "测试券",
                            "discount_amount_total": 100,
                        },
                        "status": "closed",
                        "trade_state": "CLOSED",
                        "refund_status": "",
                        "refunded_amount_total": 0,
                        "created_at": "2026-06-30T18:00:00Z",
                    }
                )
            if "SELECT lead_channel_id, lead_program_id" in query:
                return Cursor({"lead_channel_id": None, "lead_program_id": None})
            return Cursor(None)

    def fake_close(*, conn, out_trade_no="", limit=200, **kwargs):
        calls.append({"out_trade_no": out_trade_no, "limit": limit})
        return {"ok": True, "closed_count": 1}

    monkeypatch.setattr(h5_wechat_pay, "production_data_ready", lambda: True)
    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: FakeConn())
    monkeypatch.setattr(h5_wechat_pay, "close_expired_wechat_pay_orders", fake_close)
    monkeypatch.setattr(
        h5_wechat_pay,
        "_identity_from_request",
        lambda _request: {"openid": "openid-expired-owner", "unionid": "union-expired-owner"},
    )
    monkeypatch.setattr(h5_wechat_pay, "_resolve_payment_identity", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(h5_wechat_pay, "resolved_unionid", lambda _result: "union-expired-owner")

    response = h5_wechat_pay.order_status_response(
        "WXP_EXPIRED",
        type("Request", (), {"query_params": {}, "cookies": {}})(),
    )
    payload = response.body.decode("utf-8")

    assert calls[0] == {"out_trade_no": "WXP_EXPIRED", "limit": 1}
    assert any(call.get("commit") for call in calls)
    assert '"status":"closed"' in payload
    assert "CLM_INTERNAL_SECRET" not in payload
    assert '"coupon_name":"测试券"' in payload


def test_h5_order_status_requires_signed_payment_identity_before_db_access(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    monkeypatch.setattr(h5_wechat_pay, "production_data_ready", lambda: True)
    monkeypatch.setattr(h5_wechat_pay, "_identity_from_request", lambda _request: {})
    monkeypatch.setattr(
        h5_wechat_pay,
        "_connect",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be queried anonymously")),
    )

    response = h5_wechat_pay.order_status_response(
        "WXP_PRIVATE_ORDER",
        type("Request", (), {"query_params": {}, "cookies": {}, "headers": {"User-Agent": "MicroMessenger"}})(),
    )

    assert response.status_code == 401
    assert b"unionid_oauth_required" in response.body


def test_h5_order_status_hides_orders_owned_by_another_unionid(monkeypatch) -> None:
    from aicrm_next.extensions.commerce.public_product import h5_wechat_pay

    calls: list[str] = []

    class Cursor:
        def fetchone(self):
            return {
                "id": 9,
                "out_trade_no": "WXP_OTHER_OWNER",
                "unionid": "union-other-owner",
            }

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query, _params=()):
            return Cursor()

        def rollback(self):
            calls.append("rollback")

    monkeypatch.setattr(h5_wechat_pay, "production_data_ready", lambda: True)
    monkeypatch.setattr(h5_wechat_pay, "_connect", lambda: FakeConn())
    monkeypatch.setattr(
        h5_wechat_pay,
        "_identity_from_request",
        lambda _request: {"openid": "openid-requester", "unionid": "union-requester"},
    )
    monkeypatch.setattr(h5_wechat_pay, "_resolve_payment_identity", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(h5_wechat_pay, "resolved_unionid", lambda _result: "union-requester")
    monkeypatch.setattr(
        h5_wechat_pay,
        "close_expired_wechat_pay_orders",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("foreign order must not be mutated")),
    )

    response = h5_wechat_pay.order_status_response(
        "WXP_OTHER_OWNER",
        type("Request", (), {"query_params": {"refresh": "1"}, "cookies": {}})(),
    )

    assert response.status_code == 404
    assert b"order_not_found" in response.body
    assert calls == ["rollback"]


def test_legacy_h5_wechat_pay_paths_are_retired_under_next(next_client):
    for method, path in [
        ("get", "/api/h5/wechat-pay/oauth/start?return_url=/pay/test-product"),
        ("get", "/api/h5/wechat-pay/oauth/callback"),
        ("post", "/api/h5/wechat-pay/jsapi/orders"),
        ("post", "/api/h5/wechat-pay/notify"),
        ("post", "/api/h5/wechat-pay/status/refresh"),
    ]:
        if method == "post":
            response = next_client.post(path, json={})
        else:
            response = next_client.get(path)
        payload = response.json()
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert response.headers.get("X-AICRM-Fallback-Used", "false") == "false"
        assert response.status_code >= 400
        assert payload.get("fallback_used", False) is False
        assert payload.get("real_external_call_executed", False) is False
