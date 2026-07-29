from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "public-pay-landing-test")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_public_pay_landing_renders_wechat_jsapi_checkout(monkeypatch) -> None:
    response = _client(monkeypatch).get("/pay/test-product")

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert "测试商品" in response.text
    assert "确认报名信息" in response.text
    assert "请在微信中打开" in response.text
    assert "/api/h5/wechat-pay/jsapi/orders" in response.text
    assert "/api/h5/wechat-pay/orders/{out_trade_no}" in response.text
    assert "WeixinJSBridge.invoke" in response.text
    assert 'id="leadQrModal"' in response.text
    assert 'id="showLeadQrButton"' in response.text
    assert "leadQrFromOrder" in response.text
    assert "支付/下单动作已受控阻断" not in response.text


def test_public_pay_landing_unknown_path_is_controlled_404(monkeypatch) -> None:
    response = _client(monkeypatch).get("/pay/unknown-path-for-smoke")

    assert response.status_code == 404
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert "商品不存在" in response.text


def test_public_pay_landing_options_is_next_owned(monkeypatch) -> None:
    response = _client(monkeypatch).options("/pay/test-product")

    assert response.status_code == 200
    assert response.json()["route_owner"] == "ai_crm_next"
    assert response.json()["payment_request_executed"] is False
