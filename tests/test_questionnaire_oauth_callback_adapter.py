from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.oauth import get_questionnaire_oauth_audit_events, reset_questionnaire_oauth_state


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", raising=False)
    reset_questionnaire_oauth_state()
    return TestClient(create_app())


def test_oauth_callback_creates_identity_session_and_audit(client: TestClient) -> None:
    state = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1").json()["state"]

    response = client.get(f"/api/h5/wechat/oauth/callback?code=fake-code&state={state}")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source_status"] == "next_oauth_adapter"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["identity"]["openid"].startswith("openid_fake_")
    assert "questionnaire_h5_identity=" in response.headers["set-cookie"]
    assert "session_cookie" not in body
    assert {event["event_type"] for event in get_questionnaire_oauth_audit_events()} >= {
        "questionnaire.oauth.start",
        "questionnaire.oauth.callback",
    }


def test_oauth_callback_options_is_next_owned(client: TestClient) -> None:
    response = client.options("/api/h5/wechat/oauth/callback")

    assert response.status_code == 200
    assert response.json()["fallback_used"] is False
