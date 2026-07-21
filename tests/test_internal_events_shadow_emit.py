from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.cloud_orchestrator.campaigns_write import (
    ApproveCloudCampaignCommand,
    CreateCloudCampaignCommand,
    StartCloudCampaignCommand,
    execute_cloud_campaign_command,
    reset_campaign_write_fixture_state,
)
from aicrm_next.customer_tags.live_mutation import execute_wecom_tag_mutation, reset_wecom_tag_live_mutation_fixture_state
from aicrm_next.customer_tags.mutation_commands import PlanWeComTagMarkCommand, PlanWeComTagUnmarkCommand
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.questionnaire.h5_write import reset_questionnaire_h5_write_fixture_state
from aicrm_next.questionnaire.repo import reset_questionnaire_fixture_state
from tests.wechat_identity_test_support import authorize_wechat_client


QUESTIONNAIRE_CONSUMERS = frozenset({
    "ai_audience_source_poke_consumer",
    "automation_questionnaire_consumer",
    "customer_read_model_dirty_consumer",
    "customer_timeline_projection_consumer",
    "customer_summary_consumer",
    "questionnaire_projection_consumer",
    "questionnaire_tag_consumer",
    "questionnaire_webhook_consumer",
})


def _client(monkeypatch) -> TestClient:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    reset_questionnaire_h5_write_fixture_state()
    reset_questionnaire_fixture_state()
    reset_internal_event_fixture_state()
    return TestClient(create_app())


def test_questionnaire_submit_emits_durable_event_without_request_path_side_effect(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_QUESTIONNAIRE_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "questionnaire.submitted")
    client = _client(monkeypatch)
    authorize_wechat_client(
        client,
        {
            "external_userid": "wm_shadow_questionnaire",
            "openid": "openid_shadow_questionnaire",
            "unionid": "unionid_shadow_questionnaire",
        },
    )

    response = client.post(
        "/api/h5/questionnaires/hxc-activation-v1/submit",
        json={
            "answers": {"q_activation": "activated", "q_interest": ["ai_tools"]},
            "identity": {"external_userid": "wm_shadow_questionnaire", "mobile": "13800138000"},
            "source": {"scene": "p0-2d"},
        },
        headers={"Idempotency-Key": "p0-2d-questionnaire-submit"},
    )
    body = response.json()
    events, total = InternalEventService().list_events({"event_type": "questionnaire.submitted"})
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": events[0].event_id})

    assert response.status_code == 200
    assert body["success"] is True
    assert body["external_effect_job_id"] is None
    assert body["external_effect_job_status"] == "not_planned"
    assert body["side_effects"]["wecom_tag"]["status"] == "queued"
    assert body["side_effects"]["wecom_tag"]["error_code"] == ""
    assert body["side_effects"]["wecom_tag"]["external_effect_job_id"] is None
    assert body["side_effects"]["wecom_tag"]["wecom_api_called"] is False
    assert body["durable_continuation_queued"] is True
    assert body["internal_event_status"] == "emitted"
    assert body["internal_event_id"] == events[0].event_id
    assert total == 1
    assert events[0].aggregate_type == "questionnaire_submission"
    assert events[0].aggregate_id == body["submission_id"]
    assert events[0].idempotency_key == f"questionnaire.submitted:{body['submission_id']}"
    assert events[0].payload_summary_json == {
        "answer_count": 2,
        "external_push_configured": False,
        "final_tag_count": 2,
        "questionnaire_id": 1,
        "slug": "hxc-activation-v1",
        "submission_id": body["submission_id"],
        "unionid_present": True,
    }
    assert run_total == len(QUESTIONNAIRE_CONSUMERS)
    assert {run.consumer_name for run in runs} == QUESTIONNAIRE_CONSUMERS


def test_customer_tag_mark_and_unmark_shadow_emit_internal_events(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "customer.tagged,customer.untagged")
    reset_internal_event_fixture_state()
    reset_wecom_tag_live_mutation_fixture_state()

    mark = execute_wecom_tag_mutation(
        PlanWeComTagMarkCommand(
            idempotency_key="p0-2d-tag-mark",
            external_userid="wx_ext_tag_shadow_001",
            tag_ids=["tag_a", "tag_b"],
            source_route="/api/admin/customer-tags/live/mark",
            source_context={"source": "unit_test"},
        )
    )
    unmark = execute_wecom_tag_mutation(
        PlanWeComTagUnmarkCommand(
            idempotency_key="p0-2d-tag-unmark",
            external_userid="wx_ext_tag_shadow_001",
            tag_ids=["tag_a"],
            source_route="/api/admin/customer-tags/live/unmark",
            source_context={"source": "unit_test"},
        )
    )
    tagged_events, tagged_total = InternalEventService().list_events({"event_type": "customer.tagged"})
    untagged_events, untagged_total = InternalEventService().list_events({"event_type": "customer.untagged"})

    assert mark["ok"] is True
    assert mark["external_effect_job_id"]
    assert mark["internal_event_status"] == "emitted"
    assert mark["internal_event_id"] == tagged_events[0].event_id
    assert unmark["internal_event_id"] == untagged_events[0].event_id
    assert tagged_total == 1
    assert untagged_total == 1
    assert tagged_events[0].idempotency_key == "customer.tagged:p0-2d-tag-mark"
    assert tagged_events[0].payload_summary_json["external_userid_redacted"] == "wx_e..._001"
    assert tagged_events[0].payload_summary_json["tag_count"] == 2
    assert tagged_events[0].payload_summary_json["source"] == "unit_test"
    assert InternalEventService().list_consumer_runs({"event_id": tagged_events[0].event_id})[1] == 3


def test_ai_campaign_created_approve_and_start_shadow_emit_internal_events(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AI_CAMPAIGN_ENABLED", "1")
    monkeypatch.setenv(
        "AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES",
        "ai_campaign.created,ai_campaign.approved,ai_campaign.started",
    )
    reset_internal_event_fixture_state()
    reset_campaign_read_fixture_state()
    reset_campaign_write_fixture_state()

    created = execute_cloud_campaign_command(
        CreateCloudCampaignCommand(
            campaign_code="camp_next_shadow_created",
            idempotency_key="p0-2d-campaign-create",
            source_route="/api/admin/cloud-orchestrator/campaigns",
            payload={"display_name": "Shadow Created Campaign", "objective": "shadow created"},
        )
    )
    approve = execute_cloud_campaign_command(
        ApproveCloudCampaignCommand(
            campaign_code="camp_next_shadow_created",
            idempotency_key="p0-2d-campaign-approve",
            source_route="/api/admin/cloud-orchestrator/campaigns/camp_next_shadow_created/approve",
        )
    )
    start = execute_cloud_campaign_command(
        StartCloudCampaignCommand(
            campaign_code="camp_next_shadow_created",
            idempotency_key="p0-2d-campaign-start",
            source_route="/api/admin/cloud-orchestrator/campaigns/camp_next_shadow_created/start",
        )
    )
    created_events, created_total = InternalEventService().list_events({"event_type": "ai_campaign.created"})
    approved_events, approved_total = InternalEventService().list_events({"event_type": "ai_campaign.approved"})
    started_events, started_total = InternalEventService().list_events({"event_type": "ai_campaign.started"})

    assert created["ok"] is True
    assert created["internal_event_status"] == "emitted"
    assert created["internal_event_id"] == created_events[0].event_id
    assert approve["ok"] is True
    assert approve["internal_event_status"] == "emitted"
    assert approve["internal_event_id"] == approved_events[0].event_id
    assert start["internal_event_id"] == started_events[0].event_id
    assert created_total == 1
    assert approved_total == 1
    assert started_total == 1
    assert created_events[0].idempotency_key == "ai_campaign.created:camp_next_shadow_created:created"
    assert approved_events[0].aggregate_type == "ai_campaign"
    assert approved_events[0].aggregate_id == "camp_next_shadow_created"
    assert approved_events[0].idempotency_key == "ai_campaign.approved:camp_next_shadow_created:approved"
    assert started_events[0].idempotency_key == "ai_campaign.started:camp_next_shadow_created:started"
    assert approved_events[0].payload_summary_json["campaign_code"] == "camp_next_shadow_created"
    assert approved_events[0].payload_summary_json["review_status"] == "approved"
    assert started_events[0].payload_summary_json["run_status"] == "active"
    assert InternalEventService().list_consumer_runs({"event_id": created_events[0].event_id})[1] == 4
    assert InternalEventService().list_consumer_runs({"event_id": approved_events[0].event_id})[1] == 4
    assert InternalEventService().list_consumer_runs({"event_id": started_events[0].event_id})[1] == 4


def test_shadow_emit_failure_does_not_break_original_tag_write(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "customer.tagged,customer.untagged")
    reset_internal_event_fixture_state()
    reset_wecom_tag_live_mutation_fixture_state()

    class BrokenInternalEventService:
        def emit_event(self, **_kwargs):
            raise RuntimeError("internal event unavailable")

    monkeypatch.setattr("aicrm_next.platform_foundation.internal_events.shadow.InternalEventService", BrokenInternalEventService)

    result = execute_wecom_tag_mutation(
        PlanWeComTagMarkCommand(
            idempotency_key="p0-2d-tag-emit-failure",
            external_userid="wx_ext_tag_shadow_failure",
            tag_ids=["tag_a"],
            source_route="/api/admin/customer-tags/live/mark",
        )
    )

    assert result["ok"] is True
    assert result["external_effect_job_id"]
    assert result["internal_event_status"] == "failed"
    assert result["real_external_call_executed"] is False
