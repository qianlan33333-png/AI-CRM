from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.admin_write import reset_questionnaire_admin_write_fixture_state
from aicrm_next.extensions.forms.questionnaire.domain import score_and_tags
from aicrm_next.extensions.forms.questionnaire.h5_write import reset_questionnaire_h5_write_fixture_state
from aicrm_next.extensions.forms.questionnaire.repo import reset_questionnaire_fixture_state
from wechat_identity_test_support import authorize_wechat_client


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_questionnaire_fixture_state()
    reset_questionnaire_admin_write_fixture_state()
    reset_questionnaire_h5_write_fixture_state()
    client = TestClient(create_app())
    authorize_wechat_client(client)
    return client


def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def _choice_questionnaire_payload(*, slug: str, question_type: str = "single_choice") -> dict:
    question_id = "q_single" if question_type == "single_choice" else "q_multi"
    return {
        "slug": slug,
        "title": f"Other option {slug}",
        "questions": [
            {
                "id": question_id,
                "type": question_type,
                "title": "来源渠道",
                "required": True,
                "options": [
                    {
                        "id": "regular",
                        "label": "公开课",
                        "value": "regular",
                        "score": 3,
                        "tag_codes": ["tag_regular"],
                    },
                    {
                        "id": "other",
                        "label": "其它渠道",
                        "value": "other",
                        "score": 5,
                        "tag_codes": ["tag_other"],
                        "is_other": True,
                        "other_placeholder": "请填写其它内容",
                        "other_max_length": 12,
                    },
                ],
            }
        ],
    }


def _create_questionnaire(client: TestClient, payload: dict) -> dict:
    response = client.post("/api/admin/questionnaires", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _submit(client: TestClient, slug: str, answers: dict, *, external_userid: str | None = None) -> dict:
    identity = {"external_userid": external_userid or f"ext_{uuid4().hex[:8]}"}
    response = client.post(f"/api/h5/questionnaires/{slug}/submit", json={"answers": answers, "identity": identity})
    assert response.status_code == 200, response.text
    return response.json()


def _result(client: TestClient, slug: str, submission_id: str) -> dict:
    del submission_id
    with pytest.MonkeyPatch.context() as route_policy:
        route_policy.setenv("AICRM_ROUTE_POLICY_ENFORCED", "true")
        response = client.get(f"/api/h5/questionnaires/{slug}/result")
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_admin_create_supports_single_choice_other_fields(client: TestClient) -> None:
    body = _create_questionnaire(client, _choice_questionnaire_payload(slug=_slug("single-other")))

    option = body["questionnaire"]["questions"][0]["options"][1]
    assert option["is_other"] is True
    assert option["other_placeholder"] == "请填写其它内容"
    assert option["other_max_length"] == 12

    detail = client.get(f"/api/admin/questionnaires/{body['questionnaire_id']}")
    assert detail.status_code == 200
    assert detail.json()["questionnaire"]["questions"][0]["options"][1]["is_other"] is True


def test_admin_create_supports_multi_choice_other_fields(client: TestClient) -> None:
    body = _create_questionnaire(client, _choice_questionnaire_payload(slug=_slug("multi-other"), question_type="multi_choice"))
    questions = client.get(f"/api/admin/questionnaires/{body['questionnaire_id']}/questions")

    assert questions.status_code == 200
    option = questions.json()["questions"][0]["options"][1]
    assert option["is_other"] is True
    assert option["other_placeholder"] == "请填写其它内容"
    assert option["other_max_length"] == 12


def test_admin_rejects_multiple_other_options_per_question(client: TestClient) -> None:
    payload = _choice_questionnaire_payload(slug=_slug("duplicate-other"))
    payload["questions"][0]["options"].append(
        {
            "id": "other_2",
            "label": "其它二",
            "value": "other_2",
            "is_other": True,
        }
    )

    response = client.post("/api/admin/questionnaires", json=payload)

    assert response.status_code == 400
    assert "other option count must be at most one" in response.json()["error"]


def test_admin_rejects_other_max_length_out_of_range(client: TestClient) -> None:
    payload = _choice_questionnaire_payload(slug=_slug("bad-other-length"))
    payload["questions"][0]["options"][1]["other_max_length"] = 201

    response = client.post("/api/admin/questionnaires", json=payload)

    assert response.status_code == 400
    assert "other_max_length must be between 1 and 200" in response.json()["error"]


def test_public_get_returns_other_metadata(client: TestClient) -> None:
    slug = _slug("public-other")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    response = client.get(f"/api/h5/questionnaires/{slug}")

    assert response.status_code == 200
    option = response.json()["questions"][0]["options"][1]
    assert option["is_other"] is True
    assert option["other_placeholder"] == "请填写其它内容"
    assert option["other_max_length"] == 12


def test_h5_submit_old_single_choice_protocol_still_succeeds(client: TestClient) -> None:
    slug = _slug("old-single")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    body = _submit(client, slug, {"q_single": "regular"})

    assert body["score"] == 3
    assert body["final_tags"] == ["tag_regular"]
    assert body["real_external_call_executed"] is False


def test_h5_submit_old_multi_choice_protocol_still_succeeds(client: TestClient) -> None:
    slug = _slug("old-multi")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug, question_type="multi_choice"))

    body = _submit(client, slug, {"q_multi": ["regular"]})

    assert body["score"] == 3
    assert body["final_tags"] == ["tag_regular"]


def test_h5_submit_single_choice_other_object_saves_text_value(client: TestClient) -> None:
    slug = _slug("single-other-submit")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    body = _submit(client, slug, {"q_single": {"selected_option_ids": ["other"], "other_text": "线下沙龙"}})
    result = _result(client, slug, body["submission_id"])

    assert body["score"] == 5
    assert body["final_tags"] == ["tag_other"]
    assert result["answers"]["q_single"] == {"selected_option_ids": ["other"], "other_text": "线下沙龙"}
    assert result["answer_snapshots"][0]["text_value"] == "线下沙龙"


def test_h5_submit_multi_choice_regular_plus_other_saves_text_value(client: TestClient) -> None:
    slug = _slug("multi-other-submit")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug, question_type="multi_choice"))

    body = _submit(
        client,
        slug,
        {"q_multi": {"selected_option_ids": ["regular", "other"], "other_text": "线下社群承接"}},
    )
    result = _result(client, slug, body["submission_id"])

    assert body["score"] == 8
    assert body["final_tags"] == ["tag_regular", "tag_other"]
    assert result["answers"]["q_multi"] == {
        "selected_option_ids": ["regular", "other"],
        "other_text": "线下社群承接",
    }
    assert result["answer_snapshots"][0]["text_value"] == "线下社群承接"


def test_h5_submit_selected_other_requires_other_text(client: TestClient) -> None:
    slug = _slug("other-empty")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    response = client.post(
        f"/api/h5/questionnaires/{slug}/submit",
        json={"answers": {"q_single": {"selected_option_ids": ["other"], "other_text": " "}}},
    )

    assert response.status_code == 400
    assert "other_text is required" in response.json()["error"]


def test_h5_submit_selected_other_rejects_too_long_other_text(client: TestClient) -> None:
    slug = _slug("other-too-long")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    response = client.post(
        f"/api/h5/questionnaires/{slug}/submit",
        json={"answers": {"q_single": {"selected_option_ids": ["other"], "other_text": "x" * 13}}},
    )

    assert response.status_code == 400
    assert "other_text length must be <= 12" in response.json()["error"]


def test_h5_submit_ignores_other_text_when_other_not_selected(client: TestClient) -> None:
    slug = _slug("other-text-ignored")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    body = _submit(client, slug, {"q_single": {"selected_option_ids": ["regular"], "other_text": "不要保存"}})
    result = _result(client, slug, body["submission_id"])

    assert result["answers"]["q_single"] == "regular"
    assert result["answer_snapshots"][0]["text_value"] == ""


def test_h5_submit_duplicate_identity_returns_409(client: TestClient) -> None:
    slug = _slug("duplicate-submit")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    _submit(client, slug, {"q_single": "regular"}, external_userid="ext_duplicate_other")
    response = client.post(
        f"/api/h5/questionnaires/{slug}/submit",
        json={"answers": {"q_single": "regular"}, "identity": {"external_userid": "ext_duplicate_other"}},
    )

    assert response.status_code == 409
    assert response.json()["error"] == "already_submitted"


def test_score_and_tags_supports_object_answer_for_other_option() -> None:
    questionnaire = _choice_questionnaire_payload(slug="domain-score", question_type="multi_choice")
    questionnaire["id"] = 1

    score, tags = score_and_tags(
        questionnaire,
        {"q_multi": {"selected_option_ids": ["regular", "other"], "other_text": "线下社群"}},
    )

    assert score == 8
    assert tags == ["tag_regular", "tag_other"]


def test_submit_response_keeps_next_no_side_effect_contract(client: TestClient) -> None:
    slug = _slug("safe-contract")
    _create_questionnaire(client, _choice_questionnaire_payload(slug=slug))

    body = _submit(client, slug, {"q_single": {"selected_option_ids": ["other"], "other_text": "线下沙龙"}})

    assert body["source_status"] == "next_command"
    assert body["route_owner"] == "ai_crm_next"
    assert body["fallback_used"] is False
    assert body["real_external_call_executed"] is False
