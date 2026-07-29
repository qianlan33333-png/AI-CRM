from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.h5_write import get_questionnaire_h5_write_audit_events
from wechat_identity_test_support import authorize_wechat_client


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    client = TestClient(create_app())
    authorize_wechat_client(client, {"openid": "openid_001", "unionid": "unionid_001", "external_userid": "wx_ext_001"})
    return client


def _assert_next_command(body: dict, command_name: str) -> None:
    assert body["ok"] is True
    assert body["command_name"] == command_name
    assert body["source_status"] == "next_command"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
    assert body["audit_recorded"] is True
    assert body["command_id"]


def test_h5_submit_executes_next_commandbus_and_writes_submission_projection(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "identity": {
                "external_userid": "wx_ext_001",
                "openid": "openid_001",
                "unionid": "unionid_001",
                "mobile": "13800138000",
            },
            "source": {"scene": "unit"},
        },
        headers={"Idempotency-Key": "h5-submit-command"},
    )

    assert response.status_code == 200
    body = response.json()
    _assert_next_command(body, "questionnaire.h5.submit")
    assert body["success"] is True
    assert body["submission_id"]
    assert body["questionnaire_id"] == 1
    assert body["slug"] == "hxc-activation-v1"
    assert body["write_model_status"] == "submitted"
    assert body["result"]["score"] == 13
    assert "tag_hxc_activated" in body["result"]["final_tags"]
    assert "tag_interest_ai_tools" in body["result"]["final_tags"]
    assert body["identity"]["anonymous"] is False
    assert body["external_userid"] == "wx_ext_001"
    assert body["mobile"] == "13800138000"
    assert body["mobile_binding"]["ok"] is True
    assert body["mobile_binding"]["real_external_call_executed"] is False
    assert "result_access_token" not in body

    sequential = client.get(f"/api/h5/questionnaires/hxc-activation-v1/result/{body['submission_id']}")
    assert sequential.status_code == 404

    result = client.get("/api/h5/questionnaires/hxc-activation-v1/result")
    assert result.status_code == 200
    assert result.json()["result"]["answers"]["q_activation"] == "activated"
    assert "result_token" not in result.json()["result"]

    copied_link_client = TestClient(client.app, raise_server_exceptions=False)
    copied_link = copied_link_client.get("/api/h5/questionnaires/hxc-activation-v1/result")
    assert copied_link.status_code == 403
    assert copied_link.json()["error"] == "questionnaire_result_access_forbidden"
    assert "result" not in copied_link.json()

    audit_events = get_questionnaire_h5_write_audit_events()
    assert body["command_id"] in {event["command_id"] for event in audit_events}


def test_h5_submit_without_payload_identity_uses_signed_session(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={"answers": {"q_activation": "not_activated"}},
    )

    assert response.status_code == 200
    body = response.json()
    _assert_next_command(body, "questionnaire.h5.submit")
    assert body["identity"]["anonymous"] is False
    assert body["identity"]["unionid"] == "unionid_001"
    assert body["binding_status"] == "bound"
    assert body["result"]["score"] == 0


def test_h5_submit_uses_signed_payment_identity_when_payload_has_no_identity(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={"answers": {"q_activation": "not_activated"}},
    )

    assert response.status_code == 200
    body = response.json()
    _assert_next_command(body, "questionnaire.h5.submit")
    assert body["identity"]["anonymous"] is False
    assert body["identity"]["openid"] == "openid_001"
    assert body["identity"]["unionid"] == "unionid_001"
    assert body["external_userid"] == "wx_ext_001"


def test_h5_submit_fails_closed_when_identity_resolution_is_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingIdentityQuery:
        def __call__(self, query):
            raise RuntimeError("column b.id does not exist")

    monkeypatch.setattr("aicrm_next.extensions.forms.questionnaire.h5_write.ResolvePersonIdentityQuery", lambda: FailingIdentityQuery())

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated"},
            "identity": {
                "external_userid": "wm_identity_resolution_down_001",
                "openid": "openid-identity-resolution-down-001",
                "unionid": "unionid-identity-resolution-down-001",
                "mobile": "13800138099",
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_h5_submit_syncs_mobile_binding_without_external_webhook_switch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    class FakeBindMobileCommand:
        def __call__(self, request):
            calls.append(request)
            return {
                "ok": True,
                "source_status": "fixture_identity_binding",
                "external_userid": request.external_userid,
                "mobile": request.mobile,
                "owner_userid": request.owner_userid or "HuangYouCan",
                "person_id": "fixture_person_questionnaire",
                "binding_status": "bound",
                "side_effect_executed": False,
            }

    monkeypatch.setattr(
        "aicrm_next.extensions.forms.questionnaire.h5_write.BindMobileToExternalContactCommand",
        lambda: FakeBindMobileCommand(),
    )

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated"},
            "identity": {
                "external_userid": "wx_ext_001",
                "mobile": "13800138000",
            },
        },
        headers={"Idempotency-Key": "h5-submit-mobile-binding"},
    )

    assert response.status_code == 200
    body = response.json()
    _assert_next_command(body, "questionnaire.h5.submit")
    assert body["external_push_mode"] == "queue"
    assert body["external_push"]["attempted"] is False
    assert body["mobile_binding"]["ok"] is True
    assert body["mobile_binding"]["source_status"] == "fixture_identity_binding"
    assert body["mobile_binding"]["real_external_call_executed"] is False
    assert body["binding_status"] == "bound"
    assert body["person_id"] == "fixture_person_questionnaire"
    assert body["side_effects"]["mobile_binding"]["owner_userid"] == "ZhaoYanFang"
    assert calls
    assert calls[0].external_userid == "wx_ext_001"
    assert calls[0].mobile == "13800138000"
    assert calls[0].bind_by_userid == "questionnaire_h5_submit"
    assert calls[0].force_rebind is True


def test_h5_submit_rejects_repeated_identity(client: TestClient) -> None:
    payload = {
        "answers": {"q_activation": "activated"},
        "identity": {
            "external_userid": "wx_ext_001",
            "openid": "openid_001",
            "unionid": "unionid_001",
        },
    }
    first = client.post("/api/h5/questionnaires/hxc-activation-v1/submit", json=payload)
    assert first.status_code == 200

    second = client.post("/api/h5/questionnaires/hxc-activation-v1/submit", json=payload)
    assert second.status_code == 409
    body = second.json()
    assert body["error"] == "already_submitted"
    assert body["source_status"] == "already_submitted"
    assert body["write_model_status"] == "already_submitted"


def test_h5_submit_validates_required_answers(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={"answers": {"q_interest": ["ai_tools"]}},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["source_status"] == "input_error"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False


def test_h5_submit_rejects_disabled_questionnaire(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/disabled-demo/submit",
        json={"answers": {"q_disabled": "yes"}},
    )

    assert response.status_code == 404
    assert response.json()["source_status"] == "not_found"


def test_h5_submit_and_diagnostics_options_are_next_owned(client: TestClient) -> None:
    for path in [
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        "/api/h5/questionnaires/hxc-activation-v1/client-diagnostics",
    ]:
        response = client.options(path)
        assert response.status_code == 200
        body = response.json()
        assert body["source_status"] == "next_command"
        assert body["route_owner"] == "ai_crm_next"
        assert body["fallback_used"] is False
        assert body["real_external_call_executed"] is False
