from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import build_commerce_repository, reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_NEXT_WECHAT_PAY_MODE", "fake")
    monkeypatch.setenv("AICRM_NEXT_ALIPAY_MODE", "fake")
    monkeypatch.setenv("SECRET_KEY", "checkout-api-contract")
    return TestClient(create_app(), raise_server_exceptions=False)


def _checkout_payload() -> dict:
    return {
        "product_code": "test-product",
        "quantity": 1,
        "buyer_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001"},
        "return_url": "/pay/test-product",
    }


def _assert_checkout_contract(response) -> dict:
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert payload["ok"] is True
    assert payload["order_no"].startswith("order_fake_")
    assert payload["amount_cents"] == 12900
    assert payload["fake_payment"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["source_status"] == "next_checkout"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["payment_request_executed"] is False
    assert payload["order_create_executed"] == "local_only"
    assert payload["side_effect_safety"]["payment_request_executed"] is False
    assert payload["side_effect_safety"]["real_external_call_executed"] is False
    assert payload["side_effect_safety"]["order_create_executed"] == "local_only"
    return payload


def test_checkout_wechat_returns_next_fake_payment_contract(monkeypatch) -> None:
    response = _client(monkeypatch).post("/api/checkout/wechat", json=_checkout_payload())
    payload = _assert_checkout_contract(response)

    assert payload["payment_provider"] == "wechat"
    assert payload["adapter_mode"] == "fake"
    assert payload["provider_payload"]["provider"] == "wechat"


def test_checkout_alipay_returns_next_fake_payment_contract(monkeypatch) -> None:
    response = _client(monkeypatch).post("/api/checkout/alipay", json=_checkout_payload())
    payload = _assert_checkout_contract(response)

    assert payload["payment_provider"] == "alipay"
    assert payload["adapter_mode"] == "fake"
    assert payload["provider_payload"]["provider"] == "alipay"


def test_checkout_options_are_next_diagnostics(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in ["/api/checkout/wechat", "/api/checkout/alipay"]:
        response = client.options(path)
        payload = response.json()
        assert response.status_code == 200
        assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["side_effect_safety"]["payment_request_executed"] is False


def test_required_mobile_checkout_rejects_invalid_mobile_before_order_creation(monkeypatch) -> None:
    client = _client(monkeypatch)
    repo = build_commerce_repository()
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "checkout-required-mobile",
            "title": "需手机号商品",
            "price_cents": 990,
            "enabled": True,
            "status": "active",
            "require_mobile": True,
        },
    )
    assert created.status_code == 200
    payload = {
        "product_code": "checkout-required-mobile",
        "quantity": 1,
        "buyer_identity": {"mobile": "186109474111", "external_userid": "wx_invalid_mobile"},
    }

    for path in ["/api/checkout/wechat", "/api/checkout/alipay"]:
        provider = path.rsplit("/", 1)[-1]
        before = repo.list_transactions(provider, {}, limit=100, offset=0)["total"]
        response = client.post(path, json=payload)

        assert response.status_code == 400
        assert response.json()["detail"] == "手机号必须为11位有效的中国大陆手机号"
        assert repo.list_transactions(provider, {}, limit=100, offset=0)["total"] == before


def test_required_mobile_checkout_accepts_valid_11_digit_mobile(monkeypatch) -> None:
    client = _client(monkeypatch)
    created = client.post(
        "/api/admin/wechat-pay/products",
        json={
            "product_code": "checkout-valid-mobile",
            "title": "需手机号商品",
            "price_cents": 990,
            "enabled": True,
            "status": "active",
            "require_mobile": True,
        },
    )
    assert created.status_code == 200

    response = client.post(
        "/api/checkout/wechat",
        json={
            "product_code": "checkout-valid-mobile",
            "quantity": 1,
            "buyer_identity": {"mobile": "18610947411", "external_userid": "wx_valid_mobile"},
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
