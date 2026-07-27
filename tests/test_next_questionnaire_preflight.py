from __future__ import annotations

from aicrm_next.extensions.forms.questionnaire.application import GetQuestionnairePreflightQuery


def test_next_questionnaire_preflight_reads_runtime_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "runtime-secret")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-runtime-app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "wx-runtime-secret")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-runtime")
    monkeypatch.setenv("WECOM_CONTACT_SECRET", "contact-runtime-secret")
    monkeypatch.setenv("WECOM_SECRET", "runtime-secret")
    monkeypatch.setenv("WECOM_API_BASE", "https://qyapi.example.test")

    payload = GetQuestionnairePreflightQuery()()

    assert payload["status"] == "ok"
    assert payload["checks"]["wechat_oauth_configured"] is True
    assert payload["checks"]["wecom_contact_configured"] is True
    assert payload["checks"]["wecom_tags_api_available"] is True


def test_next_questionnaire_preflight_uses_next_query_when_production_ready(monkeypatch):
    import pytest

    testclient = pytest.importorskip("fastapi.testclient")

    from aicrm_next.main import create_app
    import aicrm_next.extensions.forms.questionnaire.api as questionnaire_api

    monkeypatch.setenv("SECRET_KEY", "questionnaire-preflight-forwarding-test")
    monkeypatch.setenv("WECHAT_MP_APP_ID", "wx-runtime-app")
    monkeypatch.setenv("WECHAT_MP_APP_SECRET", "wx-runtime-secret")
    monkeypatch.setenv("WECOM_CORP_ID", "ww-runtime")
    monkeypatch.setenv("WECOM_CONTACT_SECRET", "contact-runtime-secret")
    monkeypatch.setenv("WECOM_SECRET", "runtime-secret")
    monkeypatch.setattr(questionnaire_api, "production_data_ready", lambda: True)

    response = testclient.TestClient(create_app()).get("/api/admin/questionnaires/preflight")

    assert response.status_code == 200
    assert "x-aicrm-compatibility-facade" not in response.headers
    body = response.json()
    assert body["checks"]["wechat_oauth_configured"] is True
    assert body["checks"]["wecom_contact_configured"] is True
    assert body["checks"]["wecom_tags_api_available"] is True
