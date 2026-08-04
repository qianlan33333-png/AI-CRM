from __future__ import annotations

import pytest


@pytest.fixture
def current_app(monkeypatch: pytest.MonkeyPatch):
    """Build the current Next app in explicit fixture mode without PostgreSQL."""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "false")
    monkeypatch.setenv("AICRM_ADMIN_AUTH_ENFORCED", "false")
    monkeypatch.setenv("SECRET_KEY", "current-contract-secret")
    monkeypatch.setenv("WECHAT_SHOP_CALLBACK_TOKEN", "current-contract-callback-token")
    from aicrm_next.main import create_app

    return create_app()
