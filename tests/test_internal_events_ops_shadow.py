from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.usefixtures("composed_internal_event_registry")

from aicrm_next.extensions.growth.cloud_orchestrator.application import ApproveCloudPlanCommand, ApproveCloudPlanRecipientCommand
from aicrm_next.extensions.growth.cloud_orchestrator.repository import reset_cloud_plan_fixture_state
from aicrm_next.crm.owner_migration.application import OwnerMigrationCommand, OwnerMigrationService
from aicrm_next.platform.platform_foundation.internal_events import InternalEventService, reset_internal_event_fixture_state


class MinimalOwnerMigrationRepo:
    source_status = "unit_test"

    def preview_owner_migration(self, *, source_owner_userid: str, target_owner_userid: str, external_userids: list[str] | None = None) -> dict:
        del source_owner_userid, target_owner_userid
        candidates = list(external_userids or ["wm_owner_a", "wm_owner_b"])
        return {
            "source_status": self.source_status,
            "candidate_count": len(candidates),
            "all_external_userids": candidates,
            "sample_external_userids": candidates[:2],
            "surface_counts": {"contacts": len(candidates)},
            "pending_review": {},
        }

    def execute_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        operator: str,
        external_userids: list[str] | None = None,
        target_owner_display_name: str | None = None,
    ) -> dict:
        del source_owner_userid, target_owner_userid, operator, target_owner_display_name
        touched = list(external_userids or ["wm_owner_a", "wm_owner_b"])
        return {
            "executed": True,
            "touched_count": len(touched),
            "update_counts": {"contacts": len(touched)},
            "touched_external_userids": touched,
        }

    def resolve_operation_members(self, userids: list[str]) -> dict:
        return {userid: {"user_id": userid, "display_name": userid, "status": "active"} for userid in userids}

    def lookup_customer_owners(self, external_userids: list[str]) -> dict:
        return {external_userid: {"owner_userids": ["owner_a"], "customer_name": external_userid} for external_userid in external_userids}

    def save_import_session(self, session: dict) -> None:
        del session

    def get_import_session(self, session_id: str) -> dict | None:
        del session_id
        return None

    def save_preview(self, preview: dict) -> None:
        del preview

    def get_preview(self, preview_token: str) -> dict | None:
        del preview_token
        return None

    def get_latest_preview_by_session(self, session_id: str) -> dict | None:
        del session_id
        return None

    def mark_preview_executed(self, preview_token: str, result_id: str) -> None:
        del preview_token, result_id

    def save_result(self, result: dict) -> None:
        del result

    def get_result(self, result_id: str) -> dict | None:
        del result_id
        return None

    def audit_owner_migration_event(self, event_type: str, payload: dict) -> None:
        del event_type, payload


def test_cloud_plan_approval_shadow_emits_ops_plan_approved(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_OPS_PLAN_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "ops_plan.approved")
    reset_internal_event_fixture_state()
    reset_cloud_plan_fixture_state()

    result = ApproveCloudPlanCommand().execute("plan_probe", operator="pytest")
    events, total = InternalEventService().list_events({"event_type": "ops_plan.approved", "aggregate_id": "plan_probe"})
    trace_events, trace_total = InternalEventService().list_events({"event_type": "ops_plan.approved", "trace_id": "plan_probe"})
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": events[0].event_id})

    assert result["ok"] is True
    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == events[0].event_id
    assert total == 1
    assert trace_total == 1
    assert trace_events[0].event_id == events[0].event_id
    assert events[0].aggregate_type == "cloud_orchestrator_plan"
    assert events[0].payload_summary_json == {
        "plan_id": "plan_probe",
        "source": "cloud_plan",
        "operator": "pytest",
        "target_count": 2,
        "campaign_code": "",
        "approved": True,
        "plan_type": "cloud_plan",
        "stage": "approved",
        "status": "approved",
    }
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == [
        "audit_projection_consumer",
        "automation_schedule_refresh_consumer",
        "broadcast_task_planner_consumer",
        "ops_plan_ai_assist_notify_consumer",
    ]


def test_cloud_plan_recipient_approval_shadow_emits_broadcast_task_created(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_BROADCAST_TASK_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "broadcast_task.created")
    reset_internal_event_fixture_state()
    reset_cloud_plan_fixture_state()
    plan_id = "plan_probe"
    plan_hash = hashlib.sha256(plan_id.encode("utf-8")).hexdigest()[:16]
    plan_ref = f"ops_plan_ref:{plan_hash}"
    approval = ApproveCloudPlanCommand().execute(plan_id, operator="pytest")

    result = ApproveCloudPlanRecipientCommand().execute(plan_id, 1, operator="pytest")
    events, total = InternalEventService().list_events({"event_type": "broadcast_task.created", "aggregate_id": str(result["job_id"])})
    trace_events, trace_total = InternalEventService().list_events({"event_type": "broadcast_task.created", "original_trace_hash": "plan_probe"})
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": events[0].event_id})

    assert result["ok"] is True
    assert approval["broadcast_task_events"] == {"emitted": 2}
    assert result["status"] == "already_approved"
    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == events[0].event_id
    assert total == 1
    assert trace_total == 2
    assert events[0].event_id in {event.event_id for event in trace_events}
    assert events[0].trace_id == f"broadcast_task.created:{result['job_id']}"
    assert events[0].aggregate_type == "broadcast_task"
    assert events[0].payload_summary_json == {
        "task_id": str(result["job_id"]),
        "task_type": "cloud_plan",
        "send_channel": "wecom_private",
        "source": "cloud_plan_approval",
        "campaign_code": "",
        "ops_plan_id": plan_ref,
        "ops_plan_ref": plan_ref,
        "ops_plan_hash": plan_hash,
        "ops_plan_present": True,
        "target_count": 1,
        "status": "created",
        "scheduled": False,
    }
    assert "external_userids" not in events[0].payload_summary_json
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == [
        "audit_projection_consumer",
        "broadcast_queue_projection_consumer",
        "broadcast_task_ai_assist_notify_consumer",
        "push_center_link_consumer",
    ]


def test_owner_migration_execute_shadow_emits_owner_migration_executed(monkeypatch) -> None:
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_OWNER_MIGRATION_ENABLED", "1")
    monkeypatch.setenv("AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_TYPES", "owner_migration.executed")
    reset_internal_event_fixture_state()

    result = OwnerMigrationService(MinimalOwnerMigrationRepo()).run(
        OwnerMigrationCommand(
            source_owner_userid="owner_a",
            target_owner_userid="owner_b",
            operator="pytest",
            execute=True,
            confirm=True,
            perform_wecom_transfer=False,
        )
    )
    aggregate_id = f"legacy:{hashlib.sha256('owner_a:owner_b:pytest'.encode('utf-8')).hexdigest()[:16]}"
    trace_id = f"owner_migration.executed:{aggregate_id}"
    events, total = InternalEventService().list_events({"event_type": "owner_migration.executed", "aggregate_id": aggregate_id})
    trace_events, trace_total = InternalEventService().list_events({"event_type": "owner_migration.executed", "trace_id": trace_id})
    runs, run_total = InternalEventService().list_consumer_runs({"event_id": events[0].event_id})

    assert result["ok"] is True
    assert result["mode"] == "execute"
    assert result["internal_event_status"] == "emitted"
    assert result["internal_event_id"] == events[0].event_id
    assert total == 1
    assert trace_total == 1
    assert trace_events[0].event_id == events[0].event_id
    assert events[0].aggregate_type == "owner_migration"
    assert events[0].payload_summary_json["migration_id"] == aggregate_id
    assert events[0].payload_summary_json["customer_count"] == 2
    assert events[0].payload_summary_json["success_count"] == 2
    assert events[0].payload_summary_json["operator"] == "pytest"
    assert events[0].payload_summary_json["source"] == "owner_migration"
    assert events[0].payload_summary_json["executed"] is True
    assert "wm_owner_a" not in str(events[0].payload_summary_json)
    assert run_total == 4
    assert sorted(run.consumer_name for run in runs) == [
        "customer_owner_projection_consumer",
        "customer_summary_mark_dirty_consumer",
        "owner_migration_ai_assist_notify_consumer",
        "webhook_owner_migration_consumer",
    ]
