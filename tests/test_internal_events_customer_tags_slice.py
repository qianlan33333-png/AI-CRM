from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.crm.customer_tags.live_mutation import execute_wecom_tag_mutation, reset_wecom_tag_live_mutation_fixture_state
from aicrm_next.crm.customer_tags.mutation_commands import PlanWeComTagMarkCommand, PlanWeComTagUnmarkCommand
from aicrm_next.main import create_app
from aicrm_next.platform.platform_foundation.external_effects import (
    WECOM_CONTACT_TAG_MARK,
    WECOM_CONTACT_TAG_UNMARK,
    ExternalEffectService,
    reset_external_effect_fixture_state,
)
from aicrm_next.platform.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.platform.platform_foundation.internal_events.shadow import CUSTOMER_TAGGED_EVENT_TYPE, CUSTOMER_UNTAGGED_EVENT_TYPE
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker

CUSTOMER_TAG_CONSUMERS = [
    "ai_assist_notify_consumer",
    "tag_external_effect_shadow_consumer",
    "tag_summary_consumer",
]


def _reset() -> None:
    reset_wecom_tag_live_mutation_fixture_state()
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _enable_customer_tag_events(monkeypatch, *, enabled: bool = True, allowed: str = "customer.tagged,customer.untagged") -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_CUSTOMER_TAGS_ENABLED", "1" if enabled else "0")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", allowed)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")


def _mark_command(*, key: str, external_userid: str = "wm_customer_tag_slice_001") -> PlanWeComTagMarkCommand:
    return PlanWeComTagMarkCommand(
        command_id=f"cmd-{key}",
        idempotency_key=f"idem-{key}",
        actor_id="tag-operator",
        external_userid=external_userid,
        tag_ids=["tag_alpha", "tag_beta"],
        source_route="/tests/customer-tags/mark",
        source_context={"source": "customer_tag_slice_test"},
        trace_id=f"trace-{key}",
    )


def _unmark_command(*, key: str, external_userid: str = "wm_customer_tag_slice_002") -> PlanWeComTagUnmarkCommand:
    return PlanWeComTagUnmarkCommand(
        command_id=f"cmd-{key}",
        idempotency_key=f"idem-{key}",
        actor_id="tag-operator",
        external_userid=external_userid,
        tag_ids=["tag_alpha"],
        source_route="/tests/customer-tags/unmark",
        source_context={"source": "customer_tag_slice_test"},
        trace_id=f"trace-{key}",
    )


def _event(event_type: str):
    events, total = InternalEventService().list_events({"event_type": event_type})
    assert total == 1
    return events[0]


def _run_consumer(event_id: str, consumer_name: str) -> dict:
    return InternalEventWorker().dispatch_one_consumer(
        event_id,
        consumer_name,
        dry_run=False,
        force=False,
        reason="customer_tag_slice_unit_test",
    )


def test_customer_tags_flag_off_does_not_emit(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch, enabled=False)
    _reset()

    result = execute_wecom_tag_mutation(_mark_command(key="flag-off"))
    events, total = InternalEventService().list_events({"event_type": CUSTOMER_TAGGED_EVENT_TYPE})

    assert result["ok"] is True
    assert result["internal_event_status"] == "skipped"
    assert result["internal_event_id"] == ""
    assert events == []
    assert total == 0


def test_customer_tagged_emits_single_event_and_expected_consumers(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch)
    _reset()

    result = execute_wecom_tag_mutation(_mark_command(key="tagged"))
    duplicate = execute_wecom_tag_mutation(_mark_command(key="tagged"))
    event = _event(CUSTOMER_TAGGED_EVENT_TYPE)
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": event.event_id})

    assert result["internal_event_status"] == "emitted"
    assert duplicate["internal_event_id"] == event.event_id
    assert event.event_type == CUSTOMER_TAGGED_EVENT_TYPE
    assert event.aggregate_type == "customer"
    assert event.aggregate_id == "wm_customer_tag_slice_001"
    assert event.subject_type == "customer"
    assert event.subject_id == "wm_c..._001"
    assert event.idempotency_key == "customer.tagged:idem-tagged"
    assert event.payload_json["external_userid"] == "wm_customer_tag_slice_001"
    assert event.payload_summary_json["external_userid_redacted"] == "wm_c..._001"
    assert event.payload_summary_json["tag_count"] == 2
    assert event.payload_summary_json["tag_ids_count"] == 2
    assert event.payload_summary_json["effect_type"] == "wecom.tag.mark"
    assert "wm_customer_tag_slice_001" not in str(event.payload_summary_json)
    assert run_total == 3
    assert sorted(run.consumer_name for run in runs) == CUSTOMER_TAG_CONSUMERS


def test_customer_untagged_emits_single_event(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch)
    _reset()

    result = execute_wecom_tag_mutation(_unmark_command(key="untagged"))
    event = _event(CUSTOMER_UNTAGGED_EVENT_TYPE)
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": event.event_id})

    assert result["internal_event_status"] == "emitted"
    assert event.event_type == CUSTOMER_UNTAGGED_EVENT_TYPE
    assert event.aggregate_id == "wm_customer_tag_slice_002"
    assert event.idempotency_key == "customer.untagged:idem-untagged"
    assert event.payload_summary_json["effect_type"] == "wecom.tag.unmark"
    assert event.payload_summary_json["tag_count"] == 1
    assert run_total == 3
    assert sorted(run.consumer_name for run in runs) == CUSTOMER_TAG_CONSUMERS


def test_tag_external_effect_shadow_consumer_reuses_planned_shadow_job_without_attempt(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch)
    _reset()

    execute_wecom_tag_mutation(_mark_command(key="consumer-reuse"))
    event = _event(CUSTOMER_TAGGED_EVENT_TYPE)
    jobs, job_total_before = ExternalEffectService().list_jobs(
        {
            "effect_type": WECOM_CONTACT_TAG_MARK,
            "target_type": "external_user",
            "target_id": "wm_customer_tag_slice_001",
            "business_type": "wecom_tag",
            "business_id": "wm_customer_tag_slice_001",
        }
    )
    assert job_total_before == 1
    assert jobs[0].execution_mode == "execute"
    assert jobs[0].status == "queued"
    assert ExternalEffectService().list_attempts(jobs[0].id) == []

    result = _run_consumer(event.event_id, "tag_external_effect_shadow_consumer")
    jobs_after, job_total_after = ExternalEffectService().list_jobs(
        {
            "effect_type": WECOM_CONTACT_TAG_MARK,
            "target_type": "external_user",
            "target_id": "wm_customer_tag_slice_001",
            "business_type": "wecom_tag",
            "business_id": "wm_customer_tag_slice_001",
        }
    )
    runs, _ = InternalEventService().list_consumer_runs({"event_id": event.event_id})
    run = next(item for item in runs if item.consumer_name == "tag_external_effect_shadow_consumer")

    assert result["ok"] is True
    assert result["consumer_run"]["status"] == "succeeded"
    assert result["attempt"]["response_summary_json"]["external_effect_job_reused"] is True
    assert result["attempt"]["response_summary_json"]["real_external_call_executed"] is False
    assert result["attempt"]["response_summary_json"]["wecom_api_called"] is False
    assert run.attempt_count == 1
    assert job_total_after == 1
    assert jobs_after[0].id == jobs[0].id
    assert ExternalEffectService().list_attempts(jobs[0].id) == []


def test_untag_external_effect_shadow_consumer_reuses_shadow_unmark_job_without_attempt(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch)
    _reset()

    execute_wecom_tag_mutation(_unmark_command(key="consumer-unmark"))
    event = _event(CUSTOMER_UNTAGGED_EVENT_TYPE)
    jobs, job_total = ExternalEffectService().list_jobs({"effect_type": WECOM_CONTACT_TAG_UNMARK})

    assert job_total == 1
    assert jobs[0].execution_mode == "execute"

    result = _run_consumer(event.event_id, "tag_external_effect_shadow_consumer")

    assert result["consumer_run"]["status"] == "succeeded"
    assert result["attempt"]["response_summary_json"]["external_effect_job_reused"] is True
    assert ExternalEffectService().list_attempts(jobs[0].id) == []


def test_tag_summary_and_ai_assist_consumers_are_skipped_with_clear_reasons(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch)
    _reset()

    execute_wecom_tag_mutation(_mark_command(key="skipped-consumers"))
    event = _event(CUSTOMER_TAGGED_EVENT_TYPE)

    summary = _run_consumer(event.event_id, "tag_summary_consumer")
    ai_assist = _run_consumer(event.event_id, "ai_assist_notify_consumer")

    assert summary["consumer_run"]["status"] == "skipped"
    assert summary["attempt"]["response_summary_json"]["reason"] == "customer_tag_summary_not_configured"
    assert ai_assist["consumer_run"]["status"] == "skipped"
    assert ai_assist["attempt"]["response_summary_json"]["reason"] == "ai_assist_notify_not_configured"


def test_payment_worker_allowlist_does_not_pick_customer_tag_consumers(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch)
    _reset()
    execute_wecom_tag_mutation(_mark_command(key="allowlist"))

    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_PAYMENT_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "payment.succeeded")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_CONSUMERS", "order_projection_consumer,webhook_order_paid_consumer")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE", "1")
    worker = InternalEventWorker()

    payment_preview = worker.preview_due(
        batch_size=1,
        event_types=["payment.succeeded"],
        consumer_names=["order_projection_consumer", "webhook_order_paid_consumer"],
    )
    customer_preview = worker.preview_due(
        batch_size=1,
        event_types=[CUSTOMER_TAGGED_EVENT_TYPE],
        consumer_names=["tag_external_effect_shadow_consumer", "tag_summary_consumer"],
    )

    assert payment_preview["counts"]["candidate_count"] == 0
    assert all(item.get("event_type") != CUSTOMER_TAGGED_EVENT_TYPE for item in payment_preview["items"])
    assert customer_preview["counts"]["candidate_count"] == 0
    assert customer_preview["event_types"] == []
    assert customer_preview["consumer_names"] == []


def test_diagnostics_exposes_customer_tags_flag(monkeypatch) -> None:
    _enable_customer_tag_events(monkeypatch)
    _reset()

    response = TestClient(create_app()).get("/api/admin/internal-events/diagnostics")

    assert response.status_code == 200
    assert response.json()["customer_tags_internal_events_enabled"] is True
