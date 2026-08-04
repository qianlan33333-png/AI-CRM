from __future__ import annotations

import pytest

from aicrm_next.platform.platform_foundation.external_effects import (
    WEBHOOK_GENERIC_PUSH,
    ExternalEffectJob,
    ExternalEffectService,
    InMemoryExternalEffectRepository,
)
from aicrm_next.platform.platform_foundation.external_effects.adapters import DisabledAdapter


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
