from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aicrm_next.channels.integration_gateway.wecom_channel_entry_client import WeComApiError
from aicrm_next.platform.platform_foundation.external_effects import (
    WEBHOOK_GENERIC_PUSH,
    WECOM_CONTACT_TAG_MARK,
    ExternalEffectJob,
    ExternalEffectService,
    InMemoryExternalEffectRepository,
)
from aicrm_next.platform.platform_foundation.external_effects.adapters import DisabledAdapter, WeComContactTagAdapter
from aicrm_next.platform.platform_foundation.external_effects.retry_policy import (
    classify_error_code,
    next_retry_at,
    retry_delay_seconds,
    status_for_failure,
)
from aicrm_next.platform.shared.wecom_runtime import classify_wecom_provider_error


pytestmark = pytest.mark.high_risk


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
