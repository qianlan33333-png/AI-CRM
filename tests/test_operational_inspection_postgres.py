from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aicrm_next.platform.admin_jobs.operational_inspection import inspect_durable_queues


def test_operational_queue_inspection_runs_read_only_against_current_schema(next_pg_schema):
    tz = ZoneInfo("Asia/Shanghai")
    report = inspect_durable_queues(
        window_start=datetime(2026, 7, 31, 9, 0, tzinfo=tz),
        window_end=datetime(2026, 7, 31, 10, 0, tzinfo=tz),
    )

    assert report["issueCount"] == 1
    assert report["external"]["overdue"] == 0
    assert report["external"]["healthSnapshotFresh"] is False
    assert report["internal"]["missingConsumers"] == 0
