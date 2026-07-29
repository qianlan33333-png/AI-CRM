from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.repo import build_questionnaire_repository, reset_questionnaire_fixture_state
from tests.admin_auth_test_helpers import access_token_headers, install_access_token
from tests.wechat_identity_test_support import authorize_wechat_client


def _client(monkeypatch, *, authorized: bool = True) -> TestClient:
    reset_questionnaire_fixture_state()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AICRM_NEXT_ENV", "test")
    monkeypatch.setenv("AICRM_NEXT_DISABLE_LEGACY_PRODUCTION_FACADE", "1")
    monkeypatch.setenv("SECRET_KEY", "external-questionnaire-api")
    monkeypatch.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
    client = TestClient(create_app(), raise_server_exceptions=False)
    token = install_access_token(
        client,
        audience="external_integration",
        capabilities=("external_read",),
        scopes=("read",),
        client_id="pytest-external-questionnaire",
        purpose="external_agent",
    )
    if authorized:
        client.headers.update(access_token_headers(token))
    return client


def _headers() -> dict[str, str]:
    return {}


def _submit_fixture(
    client: TestClient,
    *,
    mobile: str = "13800138000",
    unionid: str = "unionid_001",
    external_userid: str = "wx_ext_001",
) -> str:
    authorize_wechat_client(
        client,
        {
            "external_userid": external_userid,
            "openid": "openid_001",
            "unionid": unionid,
        },
    )
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "source": {"scene": "external-questionnaire-api-test"},
        },
        headers={"Idempotency-Key": f"external-questionnaire-{mobile}"},
    )
    assert response.status_code == 200
    return response.json()["submission_id"]


def test_external_questionnaire_submissions_requires_registered_client_access_token(monkeypatch) -> None:
    client = _client(monkeypatch, authorized=False)
    missing = client.get("/api/external/questionnaire-submissions?mobile=13800138000")
    assert missing.status_code == 401
    assert missing.json()["error"] == "access_token_required"

    invalid = client.get(
        "/api/external/questionnaire-submissions?mobile=13800138000",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"] == "invalid_access_token"


def test_external_questionnaire_submissions_requires_identity_key(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/external/questionnaire-submissions?questionnaire_id=1", headers=_headers())

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"


def test_external_questionnaire_submissions_returns_submission_and_answer_snapshots(monkeypatch) -> None:
    client = _client(monkeypatch)
    _submit_fixture(client)

    response = client.get(
        "/api/external/questionnaire-submissions?mobile=13800138000&questionnaire_id=1&limit=5",
        headers=_headers(),
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["source_status"] == "external_questionnaire_submissions"
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["read_model_status"] == "fixture"
    assert payload["has_more"] is False
    assert payload["next_cursor"] == ""
    assert payload["total"] == 1

    item = payload["items"][0]
    assert item["mobile"] == "13800138000"
    assert item["unionid"] == "unionid_001"
    assert item["external_userid"] == "wx_ext_001"
    assert item["questionnaire_id"] == 1
    assert item["questionnaire_title"] == "黄小璨激活问卷"
    assert "tag_hxc_activated" in item["final_tags"]
    assert isinstance(item["assessment_result_snapshot"], dict)
    assert item["submitted_at"]
    assert item["answers"]
    assert {
        "question_title_snapshot",
        "selected_option_texts_snapshot",
        "text_value",
        "score_contribution",
    } <= set(item["answers"][0])


def test_external_questionnaire_submissions_cursor_paginates_with_opaque_token(monkeypatch) -> None:
    client = _client(monkeypatch)
    repo = build_questionnaire_repository()
    for index in range(2):
        repo.create_submission(
            {
                "questionnaire_id": 1,
                "answers": {"q_activation": "activated"},
                "respondent_identity": {
                    "external_userid": "wx_ext_external_q_page",
                    "unionid": f"unionid_external_q_page_{index}",
                    "mobile": f"1380013800{index}",
                },
                "external_userid": "wx_ext_external_q_page",
                "unionid": f"unionid_external_q_page_{index}",
                "mobile": f"1380013800{index}",
                "score": 10,
                "final_tags": ["tag_hxc_activated"],
            }
        )

    first = client.get("/api/external/questionnaire-submissions?external_userid=wx_ext_external_q_page&limit=1", headers=_headers()).json()
    assert first["items"]
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert "offset" not in first

    second = client.get(
        f"/api/external/questionnaire-submissions?external_userid=wx_ext_external_q_page&limit=1&cursor={first['next_cursor']}",
        headers=_headers(),
    ).json()
    assert second["items"]
    assert second["items"][0]["unionid"] != first["items"][0]["unionid"]


def test_external_questionnaire_submissions_rejects_millisecond_timestamps(monkeypatch) -> None:
    response = _client(monkeypatch).get(
        "/api/external/questionnaire-submissions?mobile=13800138000&submitted_from=1779235200000",
        headers=_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_request"
