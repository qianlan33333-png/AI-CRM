from __future__ import annotations

from aicrm_next.extensions.growth.cloud_orchestrator.run_due import (
    PlanCloudCampaignRunDueCommand,
    execute_cloud_campaign_run_due_command,
    reset_run_due_fixture_state,
)


def test_cloud_orchestrator_run_due_blocks_external_agent_execution() -> None:
    reset_run_due_fixture_state()

    result = execute_cloud_campaign_run_due_command(
        PlanCloudCampaignRunDueCommand(
            batch_size=10,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due",
            idempotency_key="external-agent-run-due-blocked",
        )
    )

    assert result["ok"] is True
    assert result["source_status"] == "next_run_due_plan"
    assert result["route_owner"] == "ai_crm_next"
    assert result["fallback_used"] is False
    assert result["adapter_mode"] == "real_blocked"
    assert result["real_external_call_executed"] is False
    assert result["campaign_runtime_executed"] is False
    assert result["side_effect_plan"]["status"] == "blocked"
