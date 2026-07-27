from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce import api as commerce_api
from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "payment-wildcard-final-wechat")
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_admin_contract(payload: dict) -> None:
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["payment_request_executed"] is False
    assert payload["provider_signature_verified"] is False
    assert payload["real_refund_executed"] is False


def test_admin_wechat_known_routes_are_next_owned(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in [
        "/api/admin/wechat-pay/products",
        "/api/admin/wechat-pay/orders",
        "/api/admin/wechat-pay/transactions",
    ]:
        response = client.get(path)
        payload = response.json()
        assert response.status_code == 200
        assert "x-aicrm-compatibility-facade" not in response.headers
        assert payload["ok"] is True
        _assert_admin_contract(payload)


def test_admin_wechat_exact_options_are_next_diagnostics(monkeypatch) -> None:
    response = _client(monkeypatch).options("/api/admin/wechat-pay/products")
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["source_status"] == "next_payment_admin"


def test_admin_wechat_refund_payload_can_mark_real_provider_execution() -> None:
    payload = commerce_api._payment_final_payload(
        {"ok": True},
        real_external_call_executed=bool(1),
        real_refund_executed=bool(1),
    )
    headers = commerce_api._payment_final_headers(
        real_external_call_executed=bool(1),
        real_refund_executed=bool(1),
    )

    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["payment_request_executed"] is False
    assert payload["provider_signature_verified"] is False
    assert payload["real_external_call_executed"] is True
    assert payload["real_refund_executed"] is True
    assert headers["X-AICRM-Real-External-Call-Executed"] == "true"
    assert headers["X-AICRM-Real-Refund-Executed"] == "true"


def test_admin_wechat_unknown_and_deprecated_paths_are_controlled(monkeypatch) -> None:
    client = _client(monkeypatch)

    expectations = {
        "/api/admin/wechat-pay/unknown-child": "admin_wechat_pay_path_removed",
        "/api/admin/wechat-pay/products/lead-plans": "admin_wechat_pay_lead_plans_removed",
        "/api/admin/wechat-pay/order-exports/job_legacy/download": "admin_wechat_pay_export_job_removed",
    }
    for path, error_code in expectations.items():
        response = client.get(path)
        payload = response.json()
        assert response.status_code == 410
        assert payload["error_code"] == error_code
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["fallback_used"] is False
        assert payload["real_refund_executed"] is False
