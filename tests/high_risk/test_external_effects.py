from __future__ import annotations

from datetime import datetime, timedelta, timezone
from inspect import getsource

import pytest

from aicrm_next.channels.channel_entry import identity_external_effect as identity_effect
from aicrm_next.channels.channel_entry.identity_external_effect import plan_profile_description_update
from aicrm_next.channels.integration_gateway.wecom_channel_entry_client import WeComApiError
from aicrm_next.platform.platform_foundation.external_effects import (
    WEBHOOK_GENERIC_PUSH,
    WECOM_CONTACT_TAG_MARK,
    WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
    WECOM_PROFILE_UPDATE,
    ExternalEffectJob,
    ExternalEffectService,
    InMemoryExternalEffectRepository,
)
from aicrm_next.platform.platform_foundation.external_effects.adapters import DisabledAdapter, WeComContactTagAdapter
from aicrm_next.platform.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository
from aicrm_next.platform.platform_foundation.external_effects.retry_policy import (
    classify_error_code,
    next_retry_at,
    retry_delay_seconds,
    status_for_failure,
)
from aicrm_next.platform.shared.wecom_runtime import classify_wecom_provider_error


pytestmark = pytest.mark.high_risk


def test_identity_detail_plans_idempotent_profile_update_without_overwriting_description() -> None:
    repository = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repository)
    parent = ExternalEffectJob(
        id=501,
        effect_type=WECOM_EXTERNAL_CONTACT_DETAIL_FETCH,
        business_type="identity_resolution_queue",
        business_id="701",
        execution_id="exe-parent-501",
        trace_id="trace-parent-501",
        request_id="request-parent-501",
    )
    provider_detail = {
        "external_contact": {"external_userid": "wm_profile_001"},
        "follow_user": [
            {
                "userid": "owner-profile",
                "description": "已有业务描述",
            }
        ],
    }

    first = plan_profile_description_update(
        parent_job=parent,
        queue_id=701,
        event_log_id=801,
        provider_detail=provider_detail,
        external_userid="wm_profile_001",
        owner_userid="owner-profile",
        service=service,
    )
    duplicate = plan_profile_description_update(
        parent_job=parent,
        queue_id=701,
        event_log_id=801,
        provider_detail=provider_detail,
        external_userid="wm_profile_001",
        owner_userid="owner-profile",
        service=service,
    )
    jobs, total = service.list_jobs(limit=10)

    assert first["status"] == "queued"
    assert first["external_effect_job_id"] == duplicate["external_effect_job_id"]
    assert first["created"] is True
    assert duplicate["created"] is False
    assert total == 1
    assert jobs[0].effect_type == WECOM_PROFILE_UPDATE
    assert jobs[0].adapter_name == "wecom_profile"
    assert jobs[0].operation == "update_description"
    assert jobs[0].payload_json == {
        "external_userid": "wm_profile_001",
        "follow_user_userid": "owner-profile",
        "description": "已有业务描述\nwm_profile_001",
    }
    assert jobs[0].parent_execution_id == "exe-parent-501"
    assert jobs[0].ordering_key == "external_user:wm_profile_001"
    already_present = plan_profile_description_update(
        parent_job=ExternalEffectJob(id=502, execution_id="exe-parent-502"),
        queue_id=702,
        event_log_id=802,
        provider_detail={
            "external_contact": {"external_userid": "wm_profile_002"},
            "follow_user": [
                {
                    "userid": "owner-profile",
                    "description": "已有业务描述\nwm_profile_002",
                }
            ],
        },
        external_userid="wm_profile_002",
        owner_userid="owner-profile",
        service=service,
    )
    assert already_present == {
        "status": "skipped",
        "reason": "external_userid_already_present",
        "description_source": "external_userid",
    }
    assert service.list_jobs(limit=10)[1] == 1
    assert "plan_profile_description_update(" in getsource(identity_effect._run_private)


def test_effect_planning_is_idempotent_and_approval_gated() -> None:
    repository = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repository)
    request = {
        "effect_type": WEBHOOK_GENERIC_PUSH,
        "adapter_name": "outbound_webhook",
        "operation": "post",
        "target_type": "customer",
        "target_id": "customer-current",
        "business_type": "customer_followup",
        "business_id": "followup-current",
        "payload": {"message": "fixture only"},
        "requires_approval": True,
        "idempotency_key": "effect-current-1",
    }
    first = service.plan_effect(**request)
    second = service.plan_effect(**request)
    approved = service.approve(int(first["id"]))
    jobs, total = service.list_jobs(limit=10)
    assert first["created_on_plan"] is True
    assert second["created_on_plan"] is False
    assert first["id"] == second["id"]
    assert first["status"] == "planned"
    assert approved is not None and approved.status == "queued"
    assert total == 1 and len(jobs) == 1


def test_record_only_reconciliation_never_completes_a_planned_execute_job() -> None:
    repository = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repository)
    planned = service.plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="customer",
        target_id="customer-awaiting-material",
        payload={"message": "wait for material dependency"},
        business_type="broadcast_job",
        business_id="broadcast-awaiting-material",
        execution_mode="execute",
        status="planned",
        idempotency_key="planned-execute-must-not-be-record-only",
    )

    result = service.complete_record_only(dry_run=False, limit=10, operator="pytest")
    unchanged = service.get(int(planned["id"]))

    assert result["candidate_count"] == 0
    assert result["completed_count"] == 0
    assert unchanged is not None
    assert unchanged.status == "planned"
    assert unchanged.execution_mode == "execute"
    assert unchanged.attempt_count == 0


def test_record_only_reconciliation_still_completes_shadow_jobs() -> None:
    repository = InMemoryExternalEffectRepository()
    service = ExternalEffectService(repository)
    shadow = service.plan_effect(
        effect_type=WEBHOOK_GENERIC_PUSH,
        adapter_name="outbound_webhook",
        operation="post",
        target_type="customer",
        target_id="customer-shadow",
        payload={"message": "record only"},
        execution_mode="shadow",
        status="planned",
        idempotency_key="shadow-record-only",
    )

    result = service.complete_record_only(dry_run=False, limit=10, operator="pytest")
    completed = service.get(int(shadow["id"]))

    assert result["candidate_count"] == 1
    assert result["completed_count"] == 1
    assert completed is not None
    assert completed.status == "simulated"
    assert completed.attempt_count == 1


def test_postgres_record_only_query_excludes_planned_execute_jobs() -> None:
    repository = SQLAlchemyExternalEffectRepository(lambda: None)
    captured: dict[str, str] = {}

    def capture(statement: str, _params: dict[str, object] | None = None) -> list[dict[str, object]]:
        captured["statement"] = " ".join(statement.split())
        return []

    repository._all = capture  # type: ignore[method-assign]

    assert repository.list_record_only_jobs(limit=10) == []
    assert "execution_mode IN ('shadow', 'plan_only', 'disabled', 'execute_dryrun')" in captured["statement"]
    assert "OR status = 'planned'" not in captured["statement"]


def test_unregistered_adapter_records_a_terminal_non_execution() -> None:
    result = DisabledAdapter().dispatch(
        ExternalEffectJob(
            effect_type=WEBHOOK_GENERIC_PUSH,
            adapter_name="missing",
            operation="post",
            target_type="customer",
            target_id="customer-current",
            execution_mode="disabled",
        )
    )
    assert result.status == "failed_terminal"
    assert result.real_external_call_executed is False
    assert result.response_summary["blocked"] is True


def test_wecom_tag_operation_conflict_uses_seconds_scale_retry_without_losing_failure_truth() -> None:
    error_code, classification = classify_wecom_provider_error(provider_errcode=45035)
    assert (error_code, classification) == ("operation_conflict", "retryable")
    assert classify_error_code(error_code) == "retryable"
    assert retry_delay_seconds(0, error_code=error_code) == 5
    assert retry_delay_seconds(3, error_code=error_code) == 40
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    assert next_retry_at(0, now=now, jitter_ratio=0, error_code=error_code) == now + timedelta(seconds=5)
    assert status_for_failure(error_code=error_code, attempt_count=1, max_attempts=5) == "failed_retryable"
    assert status_for_failure(error_code=error_code, attempt_count=5, max_attempts=5) == "failed_terminal"

    class ConflictAdapter:
        def mark_external_contact_tags(self, **_payload):
            raise WeComApiError(
                "operation conflict",
                payload={"errcode": 45035, "errmsg": "operation conflict"},
            )

    result = WeComContactTagAdapter(adapter_factory=ConflictAdapter).dispatch(
        ExternalEffectJob(
            effect_type=WECOM_CONTACT_TAG_MARK,
            adapter_name="wecom_tag",
            operation="mark",
            target_type="external_user",
            target_id="wm_conflict",
            payload_json={
                "external_userid": "wm_conflict",
                "follow_user_userid": "owner_conflict",
                "add_tags": ["tag_conflict"],
            },
        )
    )
    assert result.status == "failed_retryable"
    assert result.error_code == "operation_conflict"
    assert result.response_summary["errcode"] == 45035
