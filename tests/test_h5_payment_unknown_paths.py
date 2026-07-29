from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "payment-wildcard-final-h5")
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_h5_closed(response, *, error_code: str, replacement: str) -> None:
    payload = response.json()

    assert response.status_code == 410
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert payload["ok"] is False
    assert payload["error_code"] == error_code
    assert payload["replacement"] == replacement
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["payment_request_executed"] is False
    assert payload["provider_signature_verified"] is False


def test_h5_wechat_pay_paths_are_controlled_410_not_legacy(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in [
        "/api/h5/wechat-pay/unknown-child",
        "/api/h5/wechat-pay/jsapi/orders",
        "/api/h5/wechat-pay/notify",
    ]:
        _assert_h5_closed(
            client.get(path),
            error_code="h5_wechat_pay_path_removed",
            replacement="/api/checkout/wechat",
        )


def test_h5_alipay_paths_are_controlled_410_not_legacy(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in [
        "/api/h5/alipay/unknown-child",
        "/api/h5/alipay/wap/orders",
        "/api/h5/alipay/notify",
    ]:
        _assert_h5_closed(
            client.get(path),
            error_code="h5_alipay_path_removed",
            replacement="/api/checkout/alipay",
        )


def test_h5_payment_options_are_next_diagnostics(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in ["/api/h5/wechat-pay/unknown-child", "/api/h5/alipay/unknown-child"]:
        response = client.options(path)
        payload = response.json()
        assert response.status_code == 200
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["source_status"] == "next_h5_payment_blocked"
