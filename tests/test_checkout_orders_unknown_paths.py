from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "checkout-orders-unknown")
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_unknown_contract(response, *, error_code: str) -> None:
    payload = response.json()

    assert response.status_code == 410
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert payload["ok"] is False
    assert payload["error_code"] == error_code
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["payment_request_executed"] is False
    assert payload["real_external_call_executed"] is False


def test_unknown_checkout_child_is_controlled_410_not_legacy(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/checkout/unknown-child")

    _assert_unknown_contract(response, error_code="checkout_path_removed")
    assert response.json()["source_status"] == "next_checkout_not_found"


def test_unknown_order_child_is_controlled_410_not_legacy(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/orders/smoke/legacy-child")

    _assert_unknown_contract(response, error_code="order_child_path_removed")
    assert response.json()["source_status"] == "next_order_child_not_found"


def test_unknown_checkout_options_is_controlled_not_legacy(monkeypatch) -> None:
    response = _client(monkeypatch).options("/api/checkout/unknown-child")

    _assert_unknown_contract(response, error_code="checkout_path_removed")
