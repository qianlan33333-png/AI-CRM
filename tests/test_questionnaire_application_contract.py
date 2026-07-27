from __future__ import annotations

from aicrm_next.extensions.forms.questionnaire.application import (
    GetQuestionnaireDetailQuery,
    ListQuestionnairesQuery,
    SubmitQuestionnaireCommand,
    build_questionnaire_share_payload,
)
from aicrm_next.extensions.forms.questionnaire.dto import QuestionnaireSubmitRequest
from aicrm_next.extensions.forms.questionnaire.repo import InMemoryQuestionnaireRepository, reset_questionnaire_fixture_state


class _NullIdentityQuery:
    def __call__(self, query):
        self.query = query
        return None


class _SpySideEffectGateway:
    def __init__(self) -> None:
        self.mobile_binding_calls = []

    def bind_mobile(self, *, submission, questionnaire):
        self.mobile_binding_calls.append({"submission": submission, "questionnaire": questionnaire})
        return {"ok": True, "operation": "bind_mobile", "side_effect_executed": False}

    def apply_tags(self, **kwargs):
        return {"ok": True, "operation": "apply_tags", "side_effect_executed": False, "target": kwargs}

    def emit_external_push(self, **kwargs):
        return {"ok": True, "operation": "emit_external_push", "side_effect_executed": False, "target": kwargs}

    def emit_automation_questionnaire_result(self, **kwargs):
        return {"ok": True, "operation": "emit_automation_questionnaire_result", "side_effect_executed": False, "result": {}}

    def side_effect_safety(self):
        return {"real_mobile_binding_executed": False, "side_effect_executed": False}


def test_next_questionnaire_application_lists_and_reads_fixture_contract():
    reset_questionnaire_fixture_state()

    payload = ListQuestionnairesQuery().execute()
    detail = GetQuestionnaireDetailQuery().execute(1)

    assert payload["ok"] is True
    assert payload["route_owner"] == "ai_crm_next"
    assert payload["fallback_used"] is False
    assert payload["questionnaires"][0]["slug"] == "hxc-activation-v1"
    assert detail["ok"] is True
    assert detail["questionnaire"]["slug"] == "hxc-activation-v1"
    assert detail["questions"]


def test_next_questionnaire_share_payload_keeps_public_paths():
    reset_questionnaire_fixture_state()
    questionnaire = GetQuestionnaireDetailQuery().execute(1)["questionnaire"]

    share = build_questionnaire_share_payload(questionnaire, share_url="https://crm.example.test/s/hxc-activation-v1")

    assert share["questionnaire_id"] == 1
    assert share["slug"] == "hxc-activation-v1"
    assert share["public_path"] == "/s/hxc-activation-v1"
    assert share["url"] == "https://crm.example.test/s/hxc-activation-v1"
    assert share["qr_data_url"].startswith("data:image/svg+xml")


def test_questionnaire_submit_extracts_mobile_answer_and_runs_binding_boundary():
    repo = InMemoryQuestionnaireRepository()
    questionnaire = repo.get_questionnaire_by_slug("hxc-activation-v1")
    questionnaire["questions"].append(
        {
            "id": "q_mobile",
            "type": "mobile",
            "title": "请填写你要激活的手机号",
            "required": False,
            "sidebar_profile_field": "mobile",
            "options": [],
        }
    )
    repo.save_questionnaire(questionnaire, questionnaire_id=questionnaire["id"])
    identity_query = _NullIdentityQuery()
    gateway = _SpySideEffectGateway()

    result = SubmitQuestionnaireCommand(
        repo=repo,
        identity_query=identity_query,
        side_effect_gateway=gateway,
    ).execute(
        "hxc-activation-v1",
        QuestionnaireSubmitRequest(
                answers={"q_activation": "activated", "q_mobile": "138-3560-4611"},
            respondent_identity={"external_userid": "wmbNXyCwAAmPAoMr3Yz015qYew8ADdMA"},
        ),
    )

    assert result["ok"] is True
    assert identity_query.query.mobile == "13835604611"
    assert result["mobile"] == "13835604611"
    assert result["side_effects"]["mobile_binding"]["ok"] is True
    assert gateway.mobile_binding_calls[0]["submission"]["mobile"] == "13835604611"
    assert gateway.mobile_binding_calls[0]["submission"]["external_userid"] == "wmbNXyCwAAmPAoMr3Yz015qYew8ADdMA"
