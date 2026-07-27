from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "payment-wildcard-final-alipay")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_admin_alipay_known_transaction_route_is_next_owned(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/admin/alipay/transactions")
    payload = response.json()

    assert response.status_code == 200
    assert "x-aicrm-compatibility-facade" not in response.headers
    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["real_alipay_executed"] is False
    assert payload["provider_signature_verified"] is False


def test_admin_alipay_options_and_unknown_are_next_controlled(monkeypatch) -> None:
    client = _client(monkeypatch)

    options = client.options("/api/admin/alipay/transactions")
    assert options.status_code == 200
    assert options.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert options.json()["fallback_used"] is False

    unknown = client.get("/api/admin/alipay/unknown-child")
    payload = unknown.json()
    assert unknown.status_code == 410
    assert payload["error_code"] == "admin_alipay_path_removed"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False


def test_admin_alipay_legacy_order_paths_are_deprecated_410(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in ["/api/admin/alipay/orders", "/api/admin/alipay/order-export.csv"]:
        response = client.get(path)
        payload = response.json()
        assert response.status_code == 410
        assert payload["error_code"] == "admin_alipay_path_removed"
        assert payload["replacement"] == "/api/admin/alipay/transactions"
        assert payload["real_external_call_executed"] is False
