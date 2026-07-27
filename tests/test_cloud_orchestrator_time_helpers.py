from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aicrm_next.extensions.growth.cloud_orchestrator.time_helpers import campaign_step_due_iso


def test_campaign_step_due_iso_basic_asia_shanghai() -> None:
    assert (
        campaign_step_due_iso(
            anchor_date="2026-05-30",
            day_offset=0,
            send_time="09:00",
            step_timezone="Asia/Shanghai",
        )
        == "2026-05-30T09:00:00+08:00"
    )


def test_campaign_step_due_iso_day_offset() -> None:
    assert (
        campaign_step_due_iso(
            anchor_date="2026-05-30",
            day_offset=2,
            send_time="10:30",
            step_timezone="Asia/Shanghai",
        )
        == "2026-06-01T10:30:00+08:00"
    )


def test_campaign_step_due_iso_utc_timezone() -> None:
    due_at = campaign_step_due_iso(
        anchor_date="2026-05-30",
        day_offset=0,
        send_time="09:00",
        step_timezone="UTC",
    )

    assert due_at == "2026-05-30T09:00:00+00:00"


def test_campaign_step_due_iso_invalid_timezone_falls_back_to_shanghai() -> None:
    due_at = campaign_step_due_iso(
        anchor_date="2026-05-30",
        day_offset=0,
        send_time="09:00",
        step_timezone="Invalid/Timezone",
    )

    assert due_at == "2026-05-30T09:00:00+08:00"


def test_campaign_step_due_iso_invalid_anchor_without_today_fallback_is_stable() -> None:
    due_at = campaign_step_due_iso(
        anchor_date="bad-date",
        day_offset=0,
        send_time="10:15",
        step_timezone="Asia/Shanghai",
        fallback_to_timezone_today=False,
    )
    parsed = datetime.fromisoformat(due_at)

    assert parsed.hour == 10
    assert parsed.minute == 15
    assert parsed.tzinfo is not None


def test_campaign_step_due_iso_invalid_send_time_does_not_raise() -> None:
    due_at = campaign_step_due_iso(
        anchor_date="2026-05-30",
        day_offset=0,
        send_time="bad",
        step_timezone="Asia/Shanghai",
    )
    parsed = datetime.fromisoformat(due_at)

    assert parsed.hour == 0
    assert parsed.minute == 0
    assert parsed.tzinfo is not None


def test_cloud_repository_imports_next_time_helper() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "aicrm_next/extensions/growth/cloud_orchestrator/repository.py").read_text(encoding="utf-8")

    assert "wecom_ability" + "_service.domains.campaigns.time_helpers" not in source
    assert "from .time_helpers import" in source
    assert "campaign_step_due_iso" in source
