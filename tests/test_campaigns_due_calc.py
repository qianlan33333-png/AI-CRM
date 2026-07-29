from __future__ import annotations

from aicrm_next.extensions.growth.cloud_orchestrator.run_due import (
    PreviewCloudCampaignRunDueCommand,
    execute_cloud_campaign_run_due_command,
    reset_run_due_fixture_state,
)


def test_campaign_due_preview_returns_plan_only_candidates() -> None:
    reset_run_due_fixture_state()

    result = execute_cloud_campaign_run_due_command(
        PreviewCloudCampaignRunDueCommand(
            batch_size=10,
            source_route="/api/admin/cloud-orchestrator/campaigns/run-due/preview",
        )
    )

    assert result["ok"] is True
    assert result["route_owner"] == "ai_crm_next"
    assert result["fallback_used"] is False
    assert result["source_status"] == "next_run_due_preview"
    assert result["real_external_call_executed"] is False
    assert result["campaign_runtime_executed"] is False
    assert result["wecom_send_executed"] is False
