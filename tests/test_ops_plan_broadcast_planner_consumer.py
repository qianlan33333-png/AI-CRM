from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from fastapi.testclient import TestClient

from aicrm_next.cloud_orchestrator.application import ApproveCloudPlanCommand
from aicrm_next.cloud_orchestrator.repository import build_cloud_plan_repository, reset_cloud_plan_fixture_state
from aicrm_next.main import create_app
from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.external_effects import ExternalEffectService, reset_external_effect_fixture_state
from aicrm_next.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state
from aicrm_next.internal_event_composition import register_shadow_event_consumers
from aicrm_next.platform_foundation.internal_events.shadow import OPS_PLAN_APPROVED_EVENT_TYPE
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.platform_foundation.push_center.projection import PushCenterProjectionService

PLANNER = "broadcast_task_planner_consumer"


def _reset() -> None:
    reset_cloud_plan_fixture_state()
    reset_internal_event_fixture_state()
    reset_external_effect_fixture_state()


def _configure(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENV", raising=False)
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "ops_plan.approved")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_SHADOW_ONLY", "1")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_WEBHOOK_EXECUTE", "0")
    monkeypatch.setenv("AICRM_EXTERNAL_EFFECT_ALLOWED_TYPES", "")
    _reset()
    TestClient(create_app(), raise_server_exceptions=False)


def _approve_plan() -> str:
    result = ApproveCloudPlanCommand().execute("plan_probe", operator="ops-planner-test")
    assert result["internal_event_status"] == "emitted"
    return result["internal_event_id"]


def _run_planner(event_id: str, *, force: bool = False) -> dict[str, Any]:
    return InternalEventWorker().dispatch_one_consumer(
        event_id,
        PLANNER,
        dry_run=False,
        force=force,
        reason="ops_plan_broadcast_planner_gray_test" if force else "ops_plan_broadcast_planner_unit_test",
    )


class _FixtureBroadcastAdapter:
    def __init__(self, jobs: list[dict[str, Any]]) -> None:
        self._jobs = jobs

    def list_jobs(self, filters: dict[str, Any] | None = None, *, limit: int = 1000) -> list[dict[str, Any]]:
        filters = dict(filters or {})
        business_id = str(filters.get("business_id") or "")
        rows = list(self._jobs)
        if business_id:
            rows = [
                row
                for row in rows
                if str(row.get("source_id") or "") == business_id
                or (
                    isinstance(row.get("content_payload"), dict)
                    and str(row.get("content_payload", {}).get("plan_id") or "") == business_id
                )
            ]
        return rows[: int(limit or 1000)]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        return next((row for row in self._jobs if int(row.get("id") or 0) == int(job_id)), None)


def test_planner_consumer_creates_broadcast_job_without_external_effect(monkeypatch) -> None:
    _configure(monkeypatch)
    event_id = _approve_plan()

    result = _run_planner(event_id)
    response = result["attempt"]["response_summary_json"]
    jobs, job_total = ExternalEffectService().list_jobs({})

    assert result["consumer_run"]["status"] == "succeeded"
    assert response["planner_result"] == "planner_reused_broadcast_job"
    assert response["duplicate_handling"] == "reused"
    assert response["broadcast_job_id"] > 0
    assert response["broadcast_job_count"] == 2
    assert response["push_center_job_id"] == f"broadcast_job:{response['broadcast_job_id']}"
    assert response["downstream_status"] == "broadcast_job_queued"
    assert response["real_external_call_executed"] is False
    assert response["external_effect_job_created"] is False
    assert job_total == 0
    assert jobs == []


def test_planner_consumer_force_reuses_existing_broadcast_job(monkeypatch) -> None:
    _configure(monkeypatch)
    event_id = _approve_plan()

    first = _run_planner(event_id)
    second = _run_planner(event_id, force=True)
    first_response = first["attempt"]["response_summary_json"]
    second_response = second["attempt"]["response_summary_json"]

    assert first_response["planner_result"] == "planner_reused_broadcast_job"
    assert second_response["planner_result"] == "planner_reused_broadcast_job"
    assert second_response["duplicate_handling"] == "reused"
    assert second_response["broadcast_job_id"] == first_response["broadcast_job_id"]


def test_agent_send_plan_approval_immediately_enqueues_recipient_jobs(monkeypatch) -> None:
    _configure(monkeypatch)
    repo = build_cloud_plan_repository()
    first = repo.create_or_reuse_agent_send_plan(
        external_event_id="agent_batch_for_fde",
        package_key="audience_package",
        external_userid="wm_agent_a",
        owner_userid="HuangYouCan",
        content_package={"content_text": "copy a"},
        operator="automation_agent",
    )
    second = repo.create_or_reuse_agent_send_plan(
        external_event_id="agent_batch_for_fde",
        package_key="audience_package",
        external_userid="wm_agent_b",
        owner_userid="HuangYouCan",
        content_package={"content_text": "copy b"},
        operator="automation_agent",
    )

    result = ApproveCloudPlanCommand().execute(first["plan_id"], operator="pytest")
    planner_result = repo.create_or_reuse_plan_broadcast_job(first["plan_id"], operator="pytest")
    jobs = [job for job in repo.broadcast_jobs if str(job.get("source_id") or "").startswith(f"{first['plan_id']}:")]
    recipients, recipient_total = repo.list_recipients(first["plan_id"], limit=10)
    messages = [message for recipient in recipients for message in repo.list_recipient_messages(int(recipient["recipient_id"]))]

    assert second["plan_id"] == first["plan_id"]
    assert result["broadcast_enqueue"]["status"] == "created"
    assert result["broadcast_enqueue"]["created_count"] == 2
    assert result["broadcast_enqueue"]["broadcast_job_count"] == 2
    assert planner_result["status"] == "reused"
    assert planner_result["reused_count"] == 2
    assert len(jobs) == 2
    assert {job["source_table"] for job in jobs} == {"cloud_broadcast_plan_recipients"}
    assert {job["content_payload"]["message_mode"] for job in jobs} == {"recipient_messages"}
    assert all(str(job.get("execution_id") or "").startswith("exe_broadcast_") for job in jobs)
    assert len({job["execution_id"] for job in jobs}) == 2
    assert recipient_total == 2
    assert {recipient["send_status"] for recipient in recipients} == {"queued"}
    assert {message["status"] for message in messages} == {"queued"}


def test_agent_review_plan_waits_for_admin_approval_before_enqueue(monkeypatch) -> None:
    _configure(monkeypatch)
    repo = build_cloud_plan_repository()
    created = repo.create_or_reuse_agent_send_plan(
        external_event_id="agent_review_before_enqueue",
        package_key="admin_ai_assist_review_plan",
        external_userid="wm_review_target",
        owner_userid="HuangYouCan",
        content_package={"content_text": "review me"},
        operator="pytest",
        requires_review=True,
    )

    plan = repo.get_plan(created["plan_id"])
    recipients, total = repo.list_recipients(created["plan_id"], limit=10)
    assert plan and plan["review_status"] == "pending_review"
    assert total == 1
    assert recipients[0]["approval_status"] == "pending"
    assert repo.broadcast_jobs == []

    approved = ApproveCloudPlanCommand(repo=repo).execute(created["plan_id"], operator="pytest")

    assert approved["broadcast_enqueue"]["status"] == "created"
    assert approved["broadcast_enqueue"]["created_count"] == 1
    assert len(repo.broadcast_jobs) == 1


def test_planner_missing_plan_id_skips_with_explicit_reason(monkeypatch) -> None:
    _configure(monkeypatch)
    register_shadow_event_consumers()
    service = InternalEventService()
    emitted = service.emit_event(
        event_type=OPS_PLAN_APPROVED_EVENT_TYPE,
        event_version=1,
        aggregate_type="cloud_orchestrator_plan",
        aggregate_id="",
        subject_type="ops_plan",
        subject_id="",
        idempotency_key="ops_plan.approved:missing-plan-id",
        source_module="pytest",
        source_command_id="",
        context=CommandContext(actor_id="pytest", actor_type="admin", trace_id="missing-plan-id", request_id="missing-plan-id"),
        payload={},
        payload_summary={"plan_type": "cloud_plan"},
    )

    result = _run_planner(emitted["event"]["event_id"])

    assert result["consumer_run"]["status"] == "skipped"
    assert result["attempt"]["response_summary_json"]["reason"] == "missing_plan_id"
    assert result["attempt"]["response_summary_json"]["real_external_call_executed"] is False


def test_planner_unsupported_plan_type_is_non_applicable(monkeypatch) -> None:
    _configure(monkeypatch)
    register_shadow_event_consumers()
    service = InternalEventService()
    emitted = service.emit_event(
        event_type=OPS_PLAN_APPROVED_EVENT_TYPE,
        event_version=1,
        aggregate_type="cloud_orchestrator_plan",
        aggregate_id="legacy-plan",
        subject_type="ops_plan",
        subject_id="legacy-plan",
        idempotency_key="ops_plan.approved:legacy-plan",
        source_module="pytest",
        source_command_id="legacy-plan",
        context=CommandContext(actor_id="pytest", actor_type="admin", trace_id="legacy-plan", request_id="legacy-plan"),
        payload={"plan_type": "legacy_campaign"},
        payload_summary={"plan_id": "legacy-plan", "plan_type": "legacy_campaign"},
    )

    result = _run_planner(emitted["event"]["event_id"])

    assert result["consumer_run"]["status"] == "skipped"
    assert result["attempt"]["response_summary_json"]["reason"] == "consumer_non_applicable"


def test_planner_broadcast_jobs_are_visible_as_independent_push_executions(monkeypatch) -> None:
    _configure(monkeypatch)
    event_id = _approve_plan()
    result = _run_planner(event_id)
    repo = build_cloud_plan_repository()
    fixture_jobs = getattr(repo, "broadcast_jobs", [])

    records, total = PushCenterProjectionService(
        broadcast_adapter=_FixtureBroadcastAdapter(fixture_jobs)
    ).list_projections({"business_id": "plan_probe"})

    assert total == 2
    assert f"broadcast_job:{result['attempt']['response_summary_json']['broadcast_job_id']}" in {
        record["projection_id"] for record in records
    }
    assert {record["effective_status"] for record in records} == {"pending"}
    assert {record["business_id"] for record in records} == {"plan_probe"}
    assert {record["linked_record_counts"]["broadcast_jobs"] for record in records} == {1}
    assert len({record["root_execution_id"] for record in records}) == 2


def test_planner_result_does_not_expose_sensitive_targets(monkeypatch) -> None:
    _configure(monkeypatch)
    event_id = _approve_plan()

    result = _run_planner(event_id)
    dumped = json.dumps(result, ensure_ascii=False)

    assert "wm_a" not in dumped
    assert "wm_b" not in dumped
    assert "13800138000" not in dumped
    assert "赵言方" not in dumped
    assert "黄永灿" not in dumped
