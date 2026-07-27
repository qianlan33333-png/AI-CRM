from __future__ import annotations

from aicrm_next.extensions.growth.cloud_orchestrator.time_helpers import campaign_step_due_iso


def test_campaign_step_due_iso_uses_campaign_timezone() -> None:
    due = campaign_step_due_iso(anchor_date="2026-06-01", day_offset=2, send_time="09:30", step_timezone="Asia/Shanghai")

    assert due == "2026-06-03T09:30:00+08:00"


def test_campaign_step_due_iso_falls_back_for_invalid_timezone() -> None:
    due = campaign_step_due_iso(anchor_date="2026-06-01", day_offset=0, send_time="08:15", step_timezone="Not/AZone")

    assert due == "2026-06-01T08:15:00+08:00"
