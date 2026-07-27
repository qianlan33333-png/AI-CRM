from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.wechat_pay_client import WeChatPayClientConfig
from aicrm_next.internal_event_composition import register_payment_succeeded_consumers
from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.internal_events.consumer_registry import InternalEventConsumerRegistry
from aicrm_next.platform.platform_foundation.internal_events.models import InternalEvent, InternalEventConsumerRun
from aicrm_next.platform.platform_foundation.internal_events.payment import PAYMENT_SUCCEEDED_EVENT_TYPE
from aicrm_next.extensions.commerce.public_product import h5_wechat_pay
from aicrm_next.extensions.commerce.service_period.application import CreateServicePeriodProductCommand
from aicrm_next.extensions.commerce.service_period.dto import ServicePeriodProductCreateRequest
from aicrm_next.extensions.commerce.service_period.payment_consumer import service_period_entitlement_consumer
from aicrm_next.extensions.commerce.service_period.repo import reset_service_period_fixture_state
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("SECRET_KEY", "service-period-h5-test-secret")
    return TestClient(create_app(), raise_server_exceptions=False)


def _create_service_product(product_code: str = "sp_h5_001") -> dict:
    return CreateServicePeriodProductCommand()(
        ServicePeriodProductCreateRequest(
            product_code=product_code,
            title="H5 周期服务",
            price_cents=9900,
            status="active",
            duration_days=30,
        )
    )["product"]


def test_service_period_h5_order_allows_repeat_paid_order(monkeypatch) -> None:
    reset_commerce_fixture_state()
    reset_service_period_fixture_state()
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
                external_userid, unionid, openid, mobile = tuple(params)
                canonical_unionid = unionid or ("union_h5_repeat" if openid == "op_h5" else "")
                return Cursor(
                    {
                        "unionid": canonical_unionid,
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
                )
            if "INSERT INTO wechat_pay_orders" in query:
                assert params[1] == "service_period_checkout"
                return Cursor(
                    {
                        "id": 20,
                        "out_trade_no": params[0],
                        "product_code": "sp_h5_001",
                        "product_name": "H5 周期服务",
                        "amount_total": 9900,
                        "currency": "CNY",
                        "status": "created",
                        "trade_state": "",
                        "unionid": "union_h5_repeat",
                    }
                )
            if "UPDATE wechat_pay_orders" in query and "prepay_id" in query:
                return Cursor(
                    {
                        "id": 20,
                        "out_trade_no": params[-1],
                        "product_code": "sp_h5_001",
                        "product_name": "H5 周期服务",
                        "amount_total": 9900,
                        "currency": "CNY",
                        "status": "paying",
                        "trade_state": "",
                        "unionid": "union_h5_repeat",
                    }
                )
            raise AssertionError(query)

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def create_jsapi_transaction(self, payload):
            assert payload["attach"].find("sp_h5_001") >= 0
            return {"prepay_id": "wx_sp_prepay"}

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
    monkeypatch.setattr(h5_wechat_pay, "WeChatPayClient", FakeClient)
    client = _client(monkeypatch)
    _create_service_product()
    client.cookies.set(h5_wechat_pay.COOKIE_NAME, h5_wechat_pay._signed_blob({"openid": "op_h5", "unionid": "union_h5_repeat"}))

    response = client.post(
        "/api/h5/service-period-products/sp_h5_001/wechat-pay/jsapi/orders",
        json={},
        headers={"User-Agent": "MicroMessenger"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "already_paid" not in payload
    assert payload["order"]["status"] == "paying"
    assert payload["pay_params"]["package"] == "prepay_id=wx_sp_prepay"
    assert any("INSERT INTO wechat_pay_orders" in query for query, _ in queries)
    assert not any("FROM wechat_pay_orders" in query for query, _ in queries)


def test_service_period_h5_order_requires_mobile_when_configured(monkeypatch) -> None:
    reset_commerce_fixture_state()
    reset_service_period_fixture_state()

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
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/service-period-products",
        json={
            "product_code": "sp_h5_mobile_required",
            "title": "H5 周期服务",
            "price_cents": 9900,
            "status": "active",
            "duration_days": 30,
            "require_mobile": True,
        },
    )
    assert created.status_code == 201
    client.cookies.set(h5_wechat_pay.COOKIE_NAME, h5_wechat_pay._signed_blob({"openid": "op_h5_mobile", "unionid": "union_h5_mobile"}))

    response = client.post(
        "/api/h5/service-period-products/sp_h5_mobile_required/wechat-pay/jsapi/orders",
        json={},
        headers={"User-Agent": "MicroMessenger"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "mobile_required"


def test_service_period_h5_order_rejects_12_digit_mobile_when_configured(monkeypatch) -> None:
    reset_commerce_fixture_state()
    reset_service_period_fixture_state()

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
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/service-period-products",
        json={
            "product_code": "sp_h5_invalid_mobile",
            "title": "H5 周期服务",
            "price_cents": 9900,
            "status": "active",
            "duration_days": 30,
            "require_mobile": True,
        },
    )
    assert created.status_code == 201
    client.cookies.set(
        h5_wechat_pay.COOKIE_NAME,
        h5_wechat_pay._signed_blob({"openid": "op_h5_invalid", "unionid": "union_h5_invalid"}),
    )

    response = client.post(
        "/api/h5/service-period-products/sp_h5_invalid_mobile/wechat-pay/jsapi/orders",
        json={"mobile": "186109474111"},
        headers={"User-Agent": "MicroMessenger"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "mobile_invalid"


def test_payment_succeeded_consumer_registers_and_skips_non_service_product() -> None:
    registry = InternalEventConsumerRegistry()
    register_payment_succeeded_consumers(registry)
    names = {consumer.consumer_name for consumer in registry.list_for_event_type(PAYMENT_SUCCEEDED_EVENT_TYPE)}
    assert "service_period_entitlement_consumer" in names

    result = service_period_entitlement_consumer(
        InternalEvent(
            event_type=PAYMENT_SUCCEEDED_EVENT_TYPE,
            aggregate_id="NON_SERVICE_ORDER",
            payload_json={
                "order": {
                    "out_trade_no": "NON_SERVICE_ORDER",
                    "product_code": "ordinary_product",
                    "status": "paid",
                    "trade_state": "SUCCESS",
                    "unionid": "union_non_service",
                }
            },
        ),
        InternalEventConsumerRun(consumer_name="service_period_entitlement_consumer"),
    )
    assert result.status == "skipped"
    assert result.response_summary["reason"] == "not_service_period_product"
