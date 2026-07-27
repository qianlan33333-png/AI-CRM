from __future__ import annotations

import pytest

from aicrm_next.main import create_app
from aicrm_next.platform.shared.runtime import assert_required_runtime_secrets, require_signing_secret, runtime_health_state


def test_create_app_requires_secret_key_in_production(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("WECHAT_SHOP_CALLBACK_TOKEN", "wechat-shop-token")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app()


def test_create_app_requires_wechat_shop_callback_token_in_production(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "runtime-production-secret")
    monkeypatch.delenv("WECHAT_SHOP_CALLBACK_TOKEN", raising=False)
    monkeypatch.delenv("AICRM_ALLOW_MISSING_WECHAT_SHOP_CALLBACK_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="WECHAT_SHOP_CALLBACK_TOKEN"):
        create_app()


def test_production_can_explicitly_allow_optional_wechat_shop_callback_token(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "runtime-production-secret")
    monkeypatch.delenv("WECHAT_SHOP_CALLBACK_TOKEN", raising=False)
    monkeypatch.setenv("AICRM_ALLOW_MISSING_WECHAT_SHOP_CALLBACK_TOKEN", "1")

    assert_required_runtime_secrets()
    health = runtime_health_state()

    assert health["wechat_shop_callback_token_present"] is False
    assert health["wechat_shop_callback_token_required"] is False


def test_local_signing_secret_fallback_is_non_production_only(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    assert require_signing_secret("SECRET_KEY", local_fallback="local-secret") == b"local-secret"
    assert runtime_health_state()["secret_key_present"] is False
