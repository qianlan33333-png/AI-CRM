from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.parity_spec import ENDPOINT_SPECS
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "checkout-orders-frontend")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_public_pay_landing_remains_display_only_and_out_of_scope(monkeypatch) -> None:
    response = _client(monkeypatch).get("/pay/test-product")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert 'data-route-owner="ai_crm_next"' in response.text
    assert 'data-fallback-used="false"' in response.text
    assert "确认报名信息" in response.text
    assert "/api/checkout/wechat" not in response.text
    assert "/api/checkout/alipay" not in response.text


def test_checkout_parity_specs_point_to_next_exact_routes() -> None:
    specs = ENDPOINT_SPECS

    assert specs["checkout_wechat.default"].method == "POST"
    assert specs["checkout_wechat.default"].path == "/api/checkout/wechat"
    assert specs["checkout_alipay.default"].method == "POST"
    assert specs["checkout_alipay.default"].path == "/api/checkout/alipay"
