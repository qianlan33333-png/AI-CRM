from __future__ import annotations

from aicrm_next.automation.ops_enrollment.application import (
    PreviewUserOpsBroadcastCommand,
    get_user_ops_side_effect_plans,
    reset_user_ops_fixture_state,
)
from aicrm_next.automation.ops_enrollment.dto import BroadcastPreviewRequest


def test_marketing_router_preview_dispatch_is_plan_only() -> None:
    reset_user_ops_fixture_state()

    result = PreviewUserOpsBroadcastCommand()(
        BroadcastPreviewRequest(
            selection_mode="manual",
            selected_ids=[1],
            message={"text": "hello"},
        )
    )

    assert result["ok"] is True
    assert result["fallback_used"] is False
    assert result["real_external_call_executed"] is False
    assert result["side_effect_plan"]["real_external_call_executed"] is False
    assert result["side_effect_plan"]["adapter_mode"] == "real_blocked"
    assert get_user_ops_side_effect_plans()
