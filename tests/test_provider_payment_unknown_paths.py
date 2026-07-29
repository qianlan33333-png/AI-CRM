from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.extensions.commerce.commerce.repo import reset_commerce_fixture_state
from aicrm_next.main import create_app


def _client(monkeypatch) -> TestClient:
    reset_commerce_fixture_state()
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.delenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "provider-payment-unknown")
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_unknown_provider_path(response, *, path: str) -> None:
    payload = response.json()

    assert response.status_code == 410
    assert response.headers["X-AICRM-Route-Owner"] == "ai_crm_next"
    assert response.headers["X-AICRM-Fallback-Used"] == "false"
    assert payload["ok"] is False
    assert payload["error_code"] == "provider_payment_path_removed"
    assert payload["path"] == path
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["real_payment_notify_executed"] is False
    assert payload["provider_signature_verified"] is False


def test_unknown_wechat_pay_child_is_controlled_410_not_legacy(monkeypatch) -> None:
    _assert_unknown_provider_path(
        _client(monkeypatch).get("/api/wechat-pay/unknown-child"),
        path="/api/wechat-pay/unknown-child",
    )


def test_unknown_alipay_child_is_controlled_410_not_legacy(monkeypatch) -> None:
    _assert_unknown_provider_path(
        _client(monkeypatch).get("/api/alipay/unknown-child"),
        path="/api/alipay/unknown-child",
    )


def test_unknown_provider_options_are_controlled_410(monkeypatch) -> None:
    _assert_unknown_provider_path(
        _client(monkeypatch).options("/api/wechat-pay/unknown-child"),
        path="/api/wechat-pay/unknown-child",
    )
