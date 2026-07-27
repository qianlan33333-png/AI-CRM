from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.oauth import reset_questionnaire_oauth_state


def test_production_default_oauth_adapter_is_real_blocked_without_external_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_NEXT_ENV", "production")
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", raising=False)
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ENABLE_REAL", raising=False)
    reset_questionnaire_oauth_state()
    client = TestClient(create_app())

    response = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1&redirect=/s/hxc-activation-v1")

    assert response.status_code == 200
    body = response.json()
    assert body["adapter_mode"] == "real_blocked"
    assert body["external_call_blocked"] is True
    assert body["real_external_call_executed"] is False


def test_real_enabled_mode_requires_explicit_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AICRM_QUESTIONNAIRE_OAUTH_ADAPTER_MODE", "real_enabled")
    monkeypatch.delenv("AICRM_QUESTIONNAIRE_OAUTH_ENABLE_REAL", raising=False)
    reset_questionnaire_oauth_state()
    client = TestClient(create_app())

    response = client.get("/api/h5/wechat/oauth/start?slug=hxc-activation-v1")

    assert response.status_code == 200
    assert response.json()["adapter_mode"] == "real_blocked"
    assert response.json()["real_external_call_executed"] is False
