from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "orders-api-contract")
    return TestClient(create_app(), raise_server_exceptions=False)


def _create_order(client: TestClient) -> str:
    response = client.post(
        "/api/checkout/wechat",
        json={
            "product_code": "test-product",
            "quantity": 1,
            "buyer_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001"},
            "return_url": "/pay/test-product",
        },
    )
    assert response.status_code == 200
    return str(response.json()["order_no"])


def _assert_order_read_contract(response, order_no: str) -> None:
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert payload["ok"] is True
    assert payload["order"]["order_no"] == order_no
    assert payload["payment_status"] == "pending"
    assert payload["source_status"] == "next_order_read"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_order_read_and_status_are_next_contracts(monkeypatch) -> None:
    client = _client(monkeypatch)
    order_no = _create_order(client)

    _assert_order_read_contract(client.get(f"/api/orders/{order_no}"), order_no)
    _assert_order_read_contract(client.get(f"/api/orders/{order_no}/status"), order_no)


def test_order_options_are_next_diagnostics(monkeypatch) -> None:
    client = _client(monkeypatch)
    order_no = _create_order(client)

    for path in [f"/api/orders/{order_no}", f"/api/orders/{order_no}/status"]:
        response = client.options(path)
        payload = response.json()
        assert response.status_code == 200
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["source_status"] == "next_order_read"
        assert payload["fallback_used"] is False


def test_unknown_order_number_is_controlled_404(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/orders/unknown-order-for-smoke")
    payload = response.json()

    assert response.status_code == 404
    assert payload["error_code"] == "order_not_found"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
