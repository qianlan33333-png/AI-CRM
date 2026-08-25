from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aicrm_next.platform.admin_jobs import operational_inspection


pytestmark = pytest.mark.unit


def test_internal_flow_uses_report_window_before_stale_cutoffs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_read_operational_inspection_rows(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "external_flow": {},
            "external_health_snapshot": {},
            "external_types": [],
            "internal": {},
            "internal_types": [],
        }

    monkeypatch.setattr(
        operational_inspection,
        "read_operational_inspection_rows",
        fake_read_operational_inspection_rows,
    )
    zone = ZoneInfo("Asia/Shanghai")
    window_start = datetime(2026, 8, 25, 21, 0, tzinfo=zone)
    window_end = datetime(2026, 8, 25, 22, 0, tzinfo=zone)
    stale_before = window_end - timedelta(minutes=10)

    operational_inspection.inspect_durable_queues(
        window_start=window_start,
        window_end=window_end,
    )

    assert captured["internal_flow_params"] == (
        window_start,
        window_end,
        window_start,
        window_end,
        stale_before,
        stale_before,
        window_start,
        window_end,
        stale_before,
        stale_before,
    )


def test_archive_sync_timer_matches_production_three_minute_cadence() -> None:
    root = Path(__file__).resolve().parents[2]
    timer_text = (root / "deploy" / "aicrm-archive-sync.timer").read_text(encoding="utf-8")
    schedule = next(
        line.split("=", 1)[1].strip()
        for line in timer_text.splitlines()
        if line.startswith("OnCalendar=")
    )
    zone = ZoneInfo("Asia/Shanghai")

    assert schedule == "*-*-* *:00/3:00"
    assert operational_inspection.count_calendar_occurrences(
        schedule,
        window_start=datetime(2026, 8, 25, 21, 0, tzinfo=zone),
        window_end=datetime(2026, 8, 25, 22, 0, tzinfo=zone),
    ) == 20
