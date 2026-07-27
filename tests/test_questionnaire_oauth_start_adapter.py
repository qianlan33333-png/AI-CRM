from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.oauth import reset_questionnaire_oauth_state


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", raising=False)
    reset_questionnaire_oauth_state()
    return TestClient(create_app())


def test_oauth_start_executes_next_adapter_and_returns_signed_state(client: TestClient) -> None:
    response = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1&scene=unit")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source_status"] == "next_oauth_adapter"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["adapter_mode"] == "fake"
    assert body["state"].count(".") == 1
    assert body["redirect_url"].startswith("/api/h5/wechat/oauth/callback?")


def test_oauth_start_options_is_next_owned(client: TestClient) -> None:
    response = client.options("/api/h5/wechat/oauth/start")

    assert response.status_code == 200
    assert response.json()["source_status"] == "next_oauth_adapter"
