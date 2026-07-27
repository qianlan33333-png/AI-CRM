from __future__ import annotations

from fastapi.testclient import TestClient

from aicrm_next.customer_tags.live_mutation import execute_wecom_tag_mutation, reset_wecom_tag_live_mutation_fixture_state
from aicrm_next.customer_tags.local_projection import get_customer_tag_local_projection_fixture_rows
from aicrm_next.customer_tags.mutation_commands import PlanCustomerTagAssignmentCommand
from aicrm_next.identity_contact.dto import IdentityResolution
from aicrm_next.integration_gateway import wecom_channel_entry_client
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform_foundation.internal_events import reset_internal_event_fixture_state
from aicrm_next.extensions.forms.questionnaire import h5_write
from tests.sidebar_auth_test_helpers import install_sidebar_auth
from tests.wechat_identity_test_support import authorize_wechat_client


def _client(monkeypatch) -> TestClient:
    h5_write.reset_questionnaire_h5_write_fixture_state()
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()
    monkeypatch.setenv("SECRET_KEY", "wecom-live-mutation-callers")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    return TestClient(create_app(), raise_server_exceptions=False)


def test_sidebar_signup_tag_mutation_remains_plan_only(monkeypatch) -> None:
    client = _client(monkeypatch)
    headers = install_sidebar_auth(
        client,
        viewer_userid="ZhaoYanFang",
        external_userid="wx_ext_001",
    )
    headers["Idempotency-Key"] = "sidebar-tag-plan-only"

    response = client.post(
        "/api/sidebar/signup-tags/mark",
        json={"external_userid": "wx_ext_001", "tag_id": "tag_fixture_active", "marked": True},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_status"] == "next_command"
    assert payload["fallback_used"] is False
    assert payload["real_external_call_executed"] is False
    assert payload["side_effect_plan"]["effect_type"] == "wecom.tag.update"
    assert payload["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert payload["side_effect_plan"]["real_external_call_executed"] is False


def test_questionnaire_submit_tag_side_effect_is_durable_and_does_not_call_wecom(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeProductionWeComAdapter:
        def __init__(self, *args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            raise AssertionError("H5 must not construct a provider adapter")

    monkeypatch.setenv("WECOM_CORP_ID", "corp-questionnaire")
    monkeypatch.setenv("WECOM_CONTACT_SECRET", "secret-questionnaire")
    monkeypatch.setattr(wecom_channel_entry_client, "ProductionWeComAdapter", FakeProductionWeComAdapter)
    client = _client(monkeypatch)
    authorize_wechat_client(
        client,
        {"external_userid": "wx_ext_001", "openid": "openid_001", "unionid": "unionid_001"},
    )

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "identity": {
                "external_userid": "wx_ext_001",
                "follow_user_userid": "owner-questionnaire",
                "openid": "openid_001",
                "unionid": "unionid_001",
            },
        },
        headers={"Idempotency-Key": "questionnaire-tag-real-mark"},
    )

    assert response.status_code == 200
    tag_plan = response.json()["side_effects"]["wecom_tag"]
    assert tag_plan["source_status"] == "durable_internal_event"
    assert tag_plan["effect_type"] == "questionnaire.tag.apply"
    assert tag_plan["fallback_used"] is False
    assert tag_plan["status"] == "queued"
    assert tag_plan["real_external_call_executed"] is False
    assert tag_plan["wecom_api_called"] is False
    assert tag_plan["mark_tag_executed"] is False
    assert tag_plan["adapter_mode"] == "durable_internal_event"
    assert tag_plan["execution_mode"] == "worker"
    assert tag_plan["requires_approval"] is False
    assert tag_plan["local_projection_updated"] is False
    assert tag_plan["local_projection_status"] == "skipped"
    assert tag_plan["external_effect_job"] is None
    assert response.json()["durable_continuation_queued"] is True
    assert response.json()["external_effect_job_status"] == "not_planned"
    assert ExternalEffectService().list_jobs({})[1] == 0
    assert get_customer_tag_local_projection_fixture_rows() == []
    assert calls == []


def test_questionnaire_submit_with_unionid_only_queues_identity_recovery_without_local_projection(monkeypatch) -> None:
    client = _client(monkeypatch)
    authorize_wechat_client(
        client,
        {"openid": "openid_union_only_001", "unionid": "unionid_union_only_001"},
    )

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "identity": {"openid": "openid_union_only_001", "unionid": "unionid_union_only_001"},
        },
        headers={"Idempotency-Key": "questionnaire-union-only-local-tags"},
    )

    assert response.status_code == 200
    tag_plan = response.json()["side_effects"]["wecom_tag"]
    assert tag_plan["adapter_mode"] == "durable_internal_event"
    assert tag_plan["status"] == "queued"
    assert tag_plan["error_code"] == ""
    assert tag_plan["retryable"] is True
    assert tag_plan["local_projection_updated"] is False
    assert tag_plan["wecom_api_called"] is False
    assert response.json()["durable_continuation_queued"] is True
    assert ExternalEffectService().list_jobs({})[1] == 0
    rows = [row for row in get_customer_tag_local_projection_fixture_rows() if row["unionid"] == "unionid_union_only_001"]
    assert rows == []


def test_questionnaire_submit_does_not_bind_untrusted_payload_mobile(monkeypatch) -> None:
    mobile_bind_calls: list[dict] = []

    class FakeResolvePersonIdentityQuery:
        def __call__(self, request):
            return IdentityResolution(
                person_id="person-questionnaire-mobile",
                external_userid=request.external_userid,
                mobile="",
                binding_status="bound",
                follow_user_userid="owner-questionnaire",
                matched_by="external_userid",
            )

    class FakeBindMobileToExternalContactCommand:
        def __call__(self, request):
            mobile_bind_calls.append(
                {
                    "external_userid": request.external_userid,
                    "mobile": request.mobile,
                    "owner_userid": request.owner_userid,
                    "bind_by_userid": request.bind_by_userid,
                }
            )
            return {
                "ok": True,
                "external_userid": request.external_userid,
                "mobile": request.mobile,
                "owner_userid": request.owner_userid,
                "person_id": "person-questionnaire-mobile",
                "binding_status": "bound",
            }

    monkeypatch.setattr(h5_write, "ResolvePersonIdentityQuery", FakeResolvePersonIdentityQuery)
    monkeypatch.setattr(h5_write, "BindMobileToExternalContactCommand", FakeBindMobileToExternalContactCommand)
    client = _client(monkeypatch)
    authorize_wechat_client(
        client,
        {
            "external_userid": "wx_questionnaire_mobile",
            "unionid": "unionid_questionnaire_mobile",
        },
    )

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "identity": {"external_userid": "wx_questionnaire_mobile", "mobile": "13800138000"},
        },
        headers={"Idempotency-Key": "questionnaire-mobile-binding"},
    )

    assert response.status_code == 200
    assert mobile_bind_calls == []


def test_customer_tag_assignment_command_is_plan_only() -> None:
    reset_wecom_tag_live_mutation_fixture_state()

    payload = execute_wecom_tag_mutation(
        PlanCustomerTagAssignmentCommand(
            idempotency_key="customer-assignment-plan",
            actor_id="customer_profile",
            actor_type="system",
            external_userid="wx_ext_001",
            tag_ids=["tag_fixture_active"],
            source_route="/api/admin/customers/profile/tags",
            source_context={"source": "customer_profile_tag_assignment"},
        )
    )

    assert payload["ok"] is True
    assert payload["command_name"] == "wecom.tag.assignment.apply"
    assert payload["effect_type"] == "wecom.tag.assignment.apply"
    assert payload["side_effect_plan"]["adapter_mode"] == "queued_external_effect"
    assert payload["side_effect_plan"]["requires_approval"] is False
    assert payload["external_effect_status"] == "queued"
    assert payload["real_external_call_executed"] is False
    assert payload["wecom_api_called"] is False
