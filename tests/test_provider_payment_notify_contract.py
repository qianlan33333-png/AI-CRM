from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_NEXT_PAYMENT_NOTIFY_MODE", "fake")
    monkeypatch.setenv("SECRET_KEY", "provider-payment-notify")
    return TestClient(create_app(), raise_server_exceptions=False)


def _notify_payload(order_no: str, transaction_id: str) -> dict:
    return {
        "order_no": order_no,
        "payment_status": "paid",
        "transaction_id": transaction_id,
        "provider_payload": {"notify_id": f"notify_{transaction_id}"},
    }


def _assert_notify_contract(response, *, provider: str, order_no: str, transaction_id: str) -> dict:
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert response.headers["X-AICRM-Provider-Signature-Verified"] == "false"
    assert payload["ok"] is True
    assert payload["order_no"] == order_no
    assert payload["payment_provider"] == provider
    assert payload["payment_status"] == "paid"
    assert payload["transaction_id"] == transaction_id
    assert payload["source_status"] == "fake_signature_not_verified"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["payment_notify_executed"] == "local_only"
    assert payload["real_payment_notify_executed"] is False
    assert payload["provider_signature_verified"] is False
    assert payload["event_stub"]["external_side_effect"] is False
    assert payload["side_effect_safety"]["payment_notify_executed"] == "local_only"
    assert payload["side_effect_safety"]["real_payment_notify_executed"] is False
    assert payload["side_effect_safety"]["provider_signature_verified"] is False
    assert payload["adapter_contract"]["notify"]["side_effect_executed"] is False
    assert payload["adapter_contract"]["notify"]["result"]["signature_verified"] is False
    return payload


def test_wechat_notify_runs_next_fake_contract(monkeypatch) -> None:
    response = _client(monkeypatch).post("/api/wechat-pay/notify", json=_notify_payload("order_fake_0002", "fake_tx_001"))

    _assert_notify_contract(response, provider="wechat", order_no="order_fake_0002", transaction_id="fake_tx_001")


def test_alipay_notify_runs_next_fake_contract(monkeypatch) -> None:
    response = _client(monkeypatch).post("/api/alipay/notify", json=_notify_payload("order_fake_0003", "fake_alipay_tx_001"))

    _assert_notify_contract(response, provider="alipay", order_no="order_fake_0003", transaction_id="fake_alipay_tx_001")


def test_notify_options_are_next_diagnostics(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in ["/api/wechat-pay/notify", "/api/alipay/notify"]:
        response = client.options(path)
        payload = response.json()
        assert response.status_code == 200
        assert payload["route_owner"] == "ai_crm_next"
        assert payload["source_status"] == "next_payment_notify"
        assert payload["fallback_used"] is False
        assert payload["provider_signature_verified"] is False
