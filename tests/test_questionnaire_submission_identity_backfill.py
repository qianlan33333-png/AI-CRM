from __future__ import annotations

from aicrm_next.identity_contact.dto import IdentityResolution
from aicrm_next.extensions.forms.questionnaire.application import ListQuestionnaireSubmissionsQuery
from aicrm_next.extensions.forms.questionnaire.h5_write import QuestionnaireH5SubmitCommand, execute_questionnaire_h5_submit
from aicrm_next.extensions.forms.questionnaire.repo import reset_questionnaire_fixture_state


def test_questionnaire_submission_identity_is_written_by_next_submit_command():
    reset_questionnaire_fixture_state()

    result = execute_questionnaire_h5_submit(
        QuestionnaireH5SubmitCommand(
            questionnaire_slug="hxc-activation-v1",
            answers={"q_activation": "activated"},
            identity={"mobile": "13800138000", "external_userid": "wx_ext_001", "person_id": "person_001"},
            source={"source": "identity-backfill-test"},
            source_route="/s/hxc-activation-v1",
            idempotency_key="identity-backfill-next",
        )
    )

    assert result["ok"] is True
    submissions = ListQuestionnaireSubmissionsQuery().execute(1, limit=10)
    created = [item for item in submissions["submissions"] if item["submission_id"] == result["submission_id"]][0]
    assert created["external_userid"] == "wx_ext_001"
    assert created["mobile"] == "13800138000"
    assert created["person_id"] == "person_001"


def test_questionnaire_submission_persists_identity_map_resolution(monkeypatch):
    reset_questionnaire_fixture_state()

    class FakeResolvePersonIdentityQuery:
        def __call__(self, request):
            assert request.unionid == "unionid_live_001"
            return IdentityResolution(
                person_id=None,
                external_userid="wm_live_001",
                mobile=None,
                openid="openid_live_001",
                unionid="unionid_live_001",
                binding_status="bound",
                owner_userid="owner_live_001",
                identity_map_id=75550,
                follow_user_userid="owner_live_001",
                matched_by="unionid",
            )

    monkeypatch.setattr(
        "aicrm_next.extensions.forms.questionnaire.h5_write.ResolvePersonIdentityQuery",
        FakeResolvePersonIdentityQuery,
    )

    result = execute_questionnaire_h5_submit(
        QuestionnaireH5SubmitCommand(
            questionnaire_slug="hxc-activation-v1",
            answers={"q_activation": "activated"},
            identity={"openid": "openid_live_001", "unionid": "unionid_live_001"},
            source={"source": "identity-map-resolution-test"},
            source_route="/s/hxc-activation-v1",
            idempotency_key="identity-map-resolution-next",
        )
    )

    assert result["ok"] is True
    assert result["external_userid"] == "wm_live_001"
    submissions = ListQuestionnaireSubmissionsQuery().execute(1, limit=10)
    created = [item for item in submissions["submissions"] if item["submission_id"] == result["submission_id"]][0]
    assert created["identity_map_id"] == 75550
    assert created["external_userid"] == "wm_live_001"
    assert created["follow_user_userid"] == "owner_live_001"
    assert created["matched_by"] == "unionid"
