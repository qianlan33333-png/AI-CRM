from __future__ import annotations

from aicrm_next.extensions.forms.questionnaire.h5_write import QuestionnaireH5SubmitCommand, execute_questionnaire_h5_submit
from aicrm_next.extensions.forms.questionnaire.domain import normalize_mobile_answer
from aicrm_next.extensions.forms.questionnaire.repo import reset_questionnaire_fixture_state


def test_questionnaire_h5_submit_normalizes_mobile_identity():
    reset_questionnaire_fixture_state()

    result = execute_questionnaire_h5_submit(
        QuestionnaireH5SubmitCommand(
            questionnaire_slug="hxc-activation-v1",
            answers={"q_activation": "activated"},
            identity={"mobile": " 138 0013 8000 "},
            source={},
            source_route="/s/hxc-activation-v1",
            idempotency_key="mobile-normalization",
        )
    )

    assert result["ok"] is True
    assert result["mobile"] == "138 0013 8000"
    assert result["binding_status"] == "bound"
    assert result["unionid"] == "unionid_001"
    assert result["fallback_used"] is False
    assert result["real_external_call_executed"] is False


def test_questionnaire_mobile_normalization_requires_11_digit_mainland_mobile():
    assert normalize_mobile_answer(" 138 0013 8000 ") == "13800138000"
    assert normalize_mobile_answer("1380013800") == ""
    assert normalize_mobile_answer("138001380001") == ""
    assert normalize_mobile_answer("12800138000") == ""
