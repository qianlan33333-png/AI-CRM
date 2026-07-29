from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire import oauth as oauth_module
from aicrm_next.extensions.forms.questionnaire.oauth import reset_questionnaire_oauth_state


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", raising=False)
    reset_questionnaire_oauth_state()
    return TestClient(create_app())


def test_oauth_state_tamper_is_rejected(client: TestClient) -> None:
    state = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1").json()["state"]

    response = client.get(f"/api/h5/wechat/oauth/callback?code=fake-code&state={state}tampered")

    assert response.status_code == 400
    assert response.json()["error"] == "state_invalid"


def test_oauth_state_expiry_is_rejected(client: TestClient) -> None:
    expired = oauth_module._signed_blob(
        {"slug": "hxc-activation-v1", "redirect": "/s/hxc-activation-v1", "nonce": "expired-nonce", "iat": 1, "exp": 1}
    )

    response = client.get(f"/api/h5/wechat/oauth/callback?code=fake-code&state={expired}")

    assert response.status_code == 400
    assert response.json()["error"] == "state_expired"


def test_oauth_state_replay_is_rejected(client: TestClient) -> None:
    state = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1").json()["state"]

    first = client.get(f"/api/h5/wechat/oauth/callback?code=fake-code&state={state}")
    second = client.get(f"/api/h5/wechat/oauth/callback?code=fake-code&state={state}")

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "state_replayed"


def test_oauth_redirect_allowlist_rejects_untrusted_absolute_redirect(client: TestClient) -> None:
    response = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=https://evil.example/s")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"] == "redirect_not_allowed"
