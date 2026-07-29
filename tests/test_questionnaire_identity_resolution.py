from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.repo import reset_questionnaire_fixture_state
from wechat_identity_test_support import authorize_wechat_client


def _client() -> TestClient:
    reset_questionnaire_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)
    authorize_wechat_client(client, {"openid": "openid_001", "unionid": "unionid_001", "external_userid": "wx_ext_001"})
    return client


def test_questionnaire_h5_submit_resolves_identity_into_result_contract(monkeypatch):
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    client = _client()

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"], "q_note": "Next fixture"},
            "respondent_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001"},
            "meta": {"source": "next-test"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["external_userid"] == "wx_ext_001"
    assert payload["mobile"] == "13800138000"
    assert "result_access_token" not in payload

    result = client.get("/api/h5/questionnaires/hxc-activation-v1/result")
    assert result.status_code == 200
    assert result.json()["result"]["submission_id"] == payload["submission_id"]


def test_questionnaire_repeat_submission_is_blocked_by_identity():
    client = _client()
    body = {
        "answers": {"q_activation": "activated"},
        "respondent_identity": {"mobile": "13800138000", "external_userid": "wx_ext_001"},
    }

    assert client.post("/api/h5/questionnaires/hxc-activation-v1/submit", json=body).status_code == 200
    duplicate = client.post("/api/h5/questionnaires/hxc-activation-v1/submit", json=body)

    assert duplicate.status_code == 409
    assert duplicate.json()["fallback_used"] is False
