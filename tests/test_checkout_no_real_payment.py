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
    monkeypatch.setenv("AICRM_NEXT_WECHAT_PAY_MODE", "fake")
    monkeypatch.setenv("AICRM_NEXT_ALIPAY_MODE", "fake")
    monkeypatch.setenv("SECRET_KEY", "checkout-no-real-payment")
    return TestClient(create_app(), raise_server_exceptions=False)


def _checkout_payload() -> dict:
    return {
        "product_code": "test-product",
        "quantity": 1,
        "buyer_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001"},
        "return_url": "/pay/test-product",
    }


def test_checkout_responses_never_mark_real_payment_executed(monkeypatch) -> None:
    client = _client(monkeypatch)

    for path in ["/api/checkout/wechat", "/api/checkout/alipay"]:
        payload = client.post(path, json=_checkout_payload()).json()
        assert payload["payment_request_executed"] is False
        assert payload["real_external_call_executed"] is False
        assert payload["side_effect_safety"]["real_wechat_pay_executed"] is False
        assert payload["side_effect_safety"]["real_alipay_executed"] is False
        assert payload["side_effect_safety"]["real_payment_provider_called"] is False
        assert payload["adapter_contract"]["checkout"]["side_effect_executed"] is False


def test_checkout_source_does_not_enable_real_payment_by_default() -> None:
    source = "\n".join(
        [
            (ROOT / "aicrm_next/extensions/commerce/commerce/api.py").read_text(encoding="utf-8"),
            (ROOT / "aicrm_next/extensions/commerce/commerce/application.py").read_text(encoding="utf-8"),
        ]
    )

    for forbidden in (
        "forward_to_legacy_flask",
        "legacy_flask_facade",
        '"payment_request_executed": True',
        "'payment_request_executed': True",
        "payment_request_executed=True",
        '"real_wechat_pay_executed": True',
        "'real_wechat_pay_executed': True",
        '"real_alipay_executed": True',
        "'real_alipay_executed': True",
        "real_enabled default",
        "default real_enabled",
    ):
        assert forbidden not in source
