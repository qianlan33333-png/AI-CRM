from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_NEXT_PAYMENT_NOTIFY_MODE", "fake")
    monkeypatch.setenv("SECRET_KEY", "provider-payment-return")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_alipay_return_runs_next_fake_contract(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/alipay/return?order_no=order_fake_0003&status=paid")
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert response.headers["X-AICRM-Provider-Signature-Verified"] == "false"
    assert payload["ok"] is True
    assert payload["source_status"] == "fake_return_received"
    assert payload["order_no"] == "order_fake_0003"
    assert payload["payment_provider"] == "alipay"
    assert payload["payment_status"] == "paid"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["payment_return_executed"] == "fake"
    assert payload["provider_signature_verified"] is False
    assert payload["side_effect_safety"]["payment_return_executed"] == "fake"
    assert payload["adapter_contract"]["return"]["side_effect_executed"] is False


def test_alipay_return_without_order_is_fake_no_order_contract(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/alipay/return?status=paid")
    payload = response.json()

    assert response.status_code == 200
    assert payload["source_status"] == "fake_return_no_order"
    assert payload["order_no"] == ""
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False


def test_alipay_return_options_are_next_diagnostics(monkeypatch) -> None:
    response = _client(monkeypatch).options("/api/alipay/return")
    payload = response.json()

    assert response.status_code == 200
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["source_status"] == "next_payment_return"
    assert payload["fallback_used"] is False
    assert payload["provider_signature_verified"] is False
