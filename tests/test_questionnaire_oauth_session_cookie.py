from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.oauth import _load_signed_blob, reset_questionnaire_oauth_state


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    reset_questionnaire_oauth_state()
    return TestClient(create_app())


def test_oauth_session_cookie_is_signed_and_contains_identity(client: TestClient) -> None:
    state = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1").json()["state"]

    response = client.get(f"/api/h5/wechat/oauth/callback?code=fake-code&state={state}")

    assert response.status_code == 200
    cookie_header = response.headers["set-cookie"]
    cookie_value = cookie_header.split("questionnaire_h5_identity=", 1)[1].split(";", 1)[0]
    payload = _load_signed_blob(cookie_value)
    assert payload["slug"] == "hxc-activation-v1"
    assert payload["openid"].startswith("openid_fake_")
    assert payload["respondent_key"]
    assert "HttpOnly" in cookie_header
