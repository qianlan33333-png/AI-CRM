from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("AICRM_NEXT_PAYMENT_NOTIFY_MODE", "fake")
    monkeypatch.setenv("SECRET_KEY", "provider-payment-no-real")
    return TestClient(create_app(), raise_server_exceptions=False)


def test_provider_notify_and_return_never_mark_real_execution(monkeypatch) -> None:
    client = _client(monkeypatch)
    responses = [
        client.post(
            "/api/wechat-pay/notify",
            json={
                "order_no": "order_fake_0002",
                "payment_status": "paid",
                "transaction_id": "fake_tx_001",
                "provider_payload": {"notify_id": "fake_notify_001"},
            },
        ),
        client.post(
            "/api/alipay/notify",
            json={
                "order_no": "order_fake_0003",
                "payment_status": "paid",
                "transaction_id": "fake_alipay_tx_001",
                "provider_payload": {"notify_id": "fake_notify_002"},
            },
        ),
        client.get("/api/alipay/return?order_no=order_fake_0003&status=paid"),
    ]

    for response in responses:
        payload = response.json()
        assert response.status_code == 200
        assert payload["fallback_used"] is False
        assert payload["real_external_call_executed"] is False
        assert payload["provider_signature_verified"] is False
        assert payload["side_effect_safety"]["real_payment_notify_executed"] is False
        assert payload["side_effect_safety"]["real_wechat_pay_executed"] is False
        assert payload["side_effect_safety"]["real_alipay_executed"] is False


def test_provider_payment_source_does_not_enable_real_provider_defaults() -> None:
    source = "\n".join(
        [
            (ROOT / "aicrm_next/extensions/commerce/commerce/api.py").read_text(encoding="utf-8"),
            (ROOT / "aicrm_next/extensions/commerce/commerce/application.py").read_text(encoding="utf-8"),
        ]
    )

    for forbidden in (
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        '"real_payment_notify_executed": True',
        "'real_payment_notify_executed': True",
        "real_payment_notify_executed=True",
        '"provider_signature_verified": True',
        "'provider_signature_verified': True",
        "provider_signature_verified=True",
        '"real_wechat_pay_executed": True',
        "'real_wechat_pay_executed': True",
        '"real_alipay_executed": True',
        "'real_alipay_executed': True",
        "real_enabled default",
        "default real_enabled",
    ):
        assert forbidden not in source
