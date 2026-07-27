from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aicrm_next.main import create_app
from aicrm_next.extensions.forms.questionnaire.h5_write import get_questionnaire_h5_side_effect_plans, reset_questionnaire_h5_write_fixture_state
from wechat_identity_test_support import authorize_wechat_client


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    reset_questionnaire_h5_write_fixture_state()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    client = TestClient(create_app())
    authorize_wechat_client(client, {"openid": "openid_001", "unionid": "unionid_001", "external_userid": "wx_ext_001"})
    return client


def test_h5_submit_reports_only_the_durable_continuation_and_never_checks_provider_config(client: TestClient) -> None:
    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={"answers": {"q_activation": "activated"}, "identity": {"external_userid": "wx_ext_001"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["real_external_call_executed"] is False
    assert body["tag_apply"]["status"] == "queued"
    assert body["tag_apply"]["durable_continuation_queued"] is True
    assert body["tag_apply"]["wecom_api_called"] is False
    assert body["tag_apply"]["local_projection_updated"] is False
    plan = body["side_effect_plan"]
    assert plan["adapter_mode"] == "durable_internal_event"
    assert plan["requires_approval"] is False
    assert plan["real_external_call_executed"] is False
    assert plan["payload"]["real_external_call_executed"] is False
    assert "wecom.tag.contact_tags_mirror.skipped" in plan["payload"]["planned_effects"]
    assert "wecom.tag.mark_tag.queued" in plan["payload"]["planned_effects"]

    plans = get_questionnaire_h5_side_effect_plans()
    assert all(item["real_external_call_executed"] is False for item in plans)


def test_h5_write_source_only_persists_durable_continuation_without_provider_or_direct_planner() -> None:
    text = Path("aicrm_next/extensions/forms/questionnaire/h5_write.py").read_text(encoding="utf-8")
    assert "ProductionWeComAdapter" not in text
    assert ".mark_external_contact_tags(" not in text
    assert "plan_questionnaire_external_push_effect" not in text
    assert "enqueue_transactional_internal_event_outbox" not in text
    assert "execute_wecom_tag_mutation" not in text
    assert "PlanQuestionnaireTagSideEffectCommand" not in text
    assert "X-AICRM-Compatibility-Facade" not in text


def test_public_h5_read_source_has_no_legacy_public_identity_helpers() -> None:
    source_paths = [
        Path("aicrm_next/extensions/forms/questionnaire/api.py"),
        Path("aicrm_next/extensions/forms/questionnaire/application.py"),
        Path("aicrm_next/extensions/forms/questionnaire/public_access.py"),
    ]
    removed_facade = Path("aicrm_next/integration_gateway/legacy_flask_facade.py")
    assert not removed_facade.exists()
    combined = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)
    assert not (Path("aicrm_next/integration_gateway") / "legacy_questionnaire_facade.py").exists()
    for marker in [
        "get_public_questionnaire_from_legacy",
        "get_public_questionnaire_submission_status_from_legacy",
        "legacy_questionnaire_session_identity",
        "legacy_questionnaire_oauth_is_configured",
    ]:
        assert marker not in combined


def test_production_compat_no_longer_registers_h5_submit_or_diagnostics_exact_routes() -> None:
    assert not Path("aicrm_next/production_compat").exists()
