from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "public-product-no-payment-test")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_public_product_module_has_no_legacy_facade_markers() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "aicrm_next/extensions/commerce/public_product").glob("*.py"))

    for forbidden in [
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        "Alipay",
        "httpx",
        "create_h5_order",
        "create_wap_order",
    ]:
        assert forbidden not in source


def test_public_product_routes_report_no_real_payment_or_order_create(monkeypatch) -> None:
    client = _client(monkeypatch)

    responses = [
        client.get("/api/products/test-product"),
        client.get("/api/products/test-product/checkout"),
        client.post("/api/products/test-product", json={}),
    ]

    for response in responses:
        payload = response.json()
        assert payload["real_external_call_executed"] is False
        assert payload["payment_request_executed"] is False
        assert payload["order_create_executed"] is False


def test_h5_wechat_pay_create_order_requires_wechat_browser(monkeypatch) -> None:
    response = _client(monkeypatch).post("/api/h5/wechat-pay/jsapi/orders", json={"product_code": "test-product"})

    assert response.status_code == 403
    assert response.json()["error"] == "wechat_browser_required"


def test_h5_wechat_pay_create_order_requires_openid_in_wechat_browser(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/api/h5/wechat-pay/jsapi/orders",
        json={"product_code": "test-product"},
        headers={"User-Agent": "MicroMessenger"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "unionid_oauth_required"
    assert payload["oauth_start_url"].endswith("return_url=%2Fpay%2Ftest-product")
