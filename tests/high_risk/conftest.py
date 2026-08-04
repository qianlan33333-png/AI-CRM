from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_high_risk_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every high-risk test prove behavior without production data or I/O."""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_DEPLOYMENT_PROFILE_PATH", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DATA_SOURCE", "fixture")
    monkeypatch.setenv("AICRM_WECOM_EXECUTION_MODE", "disabled")
    monkeypatch.setenv("SECRET_KEY", "current-high-risk-secret")
    monkeypatch.setenv("AICRM_NEXT_ACTION_TOKEN_SECRET", "current-high-risk-action-secret")

    def blocked_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("real network access is forbidden in high-risk tests")

    monkeypatch.setattr(socket, "create_connection", blocked_network)
    monkeypatch.setattr(socket.socket, "connect", blocked_network)
