from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aicrm_next.platform.admin_jobs.operational_inspection import (
    _normalize_external,
    build_operational_report_message,
    count_calendar_occurrences,
    inspect_systemd_timers,
    load_timer_specs,
)
from aicrm_next.platform.admin_jobs.notification_settings import (
    send_feishu_operational_webhook_message,
)
from aicrm_next.platform.shared.outbound_https.transport import HttpsTransportResponse


def _window():
    tz = ZoneInfo("Asia/Shanghai")
    return (
        datetime(2026, 7, 31, 9, 0, tzinfo=tz),
        datetime(2026, 7, 31, 10, 0, tzinfo=tz),
    )


def test_calendar_counter_supports_all_production_schedule_shapes():
    start, end = _window()

    assert count_calendar_occurrences("*-*-* *:*:40", window_start=start, window_end=end) == 60
    assert count_calendar_occurrences("*:0/15", window_start=start, window_end=end) == 4
    assert count_calendar_occurrences("*-*-* *:05:00", window_start=start, window_end=end) == 1
    assert count_calendar_occurrences("*-*-* 09,21:00:00 Asia/Shanghai", window_start=start, window_end=end) == 1


def test_every_managed_production_timer_schedule_is_countable():
    root = Path(__file__).resolve().parents[1]
    manifest_path = root / "deploy" / "production_runtime_units.json"
    start, end = _window()

    specs = load_timer_specs(manifest_path)
    counts = {}
    for spec in specs:
        source = (root / "deploy" / spec.timer).read_text(encoding="utf-8")
        schedule = next(
            line.split("=", 1)[1]
            for line in source.splitlines()
            if line.startswith("OnCalendar=")
        )
        counts[spec.timer] = count_calendar_occurrences(
            schedule,
            window_start=start,
            window_end=end,
        )

    assert len(specs) == 11
    assert counts["aicrm-job-catalog-scheduler.timer"] == 60
    assert counts["openclaw-broadcast-hourly-feishu-report.timer"] == 1


def test_systemd_timer_inspection_compares_expected_with_distinct_invocations(tmp_path):
    manifest = {
        "active_autostart": [
            {"timer": "required.timer", "service": "required.service"}
        ],
        "approval_required": [
            {"timer": "optional.timer", "service": "optional.service"}
        ],
    }
    manifest_path = tmp_path / "production_runtime_units.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "required.timer").write_text("[Timer]\nOnCalendar=*-*-* *:*:40\n", encoding="utf-8")
    (tmp_path / "optional.timer").write_text("[Timer]\nOnCalendar=*-*-* *:05:00\n", encoding="utf-8")

    def run(args):
        if args[0] == "systemctl" and args[2] == "required.timer":
            return subprocess.CompletedProcess(args, 0, "UnitFileState=enabled\nActiveState=active\n", "")
        if args[0] == "systemctl":
            return subprocess.CompletedProcess(args, 0, "UnitFileState=disabled\nActiveState=inactive\n", "")
        rows = "\n".join(
            json.dumps({"_SYSTEMD_INVOCATION_ID": f"invocation-{index}"})
            for index in range(59)
        )
        return subprocess.CompletedProcess(args, 0, rows, "")

    start, end = _window()
    report = inspect_systemd_timers(
        window_start=start,
        window_end=end,
        manifest_path=manifest_path,
        command_runner=run,
    )

    assert report["managedCount"] == 2
    assert report["monitoredCount"] == 1
    assert report["expectedExecutions"] == 60
    assert report["actualExecutions"] == 59
    assert report["issueCount"] == 1
    assert report["issues"][0]["issues"] == ["少执行 1 次"]


def test_operational_report_explains_breakpoints_in_business_language():
    start, end = _window()
    inspection = {
        "status": "异常",
        "issueCount": 5,
        "timer": {
            "managedCount": 11,
            "monitoredCount": 10,
            "expectedExecutions": 96,
            "actualExecutions": 95,
            "issueCount": 1,
            "issues": [{"timer": "campaign.timer", "issues": ["少执行 1 次"]}],
        },
        "external": {
            "created": 8,
            "succeeded": 6,
            "overdue": 1,
            "stalled": 0,
            "unknown": 1,
            "terminal": 0,
            "issueCount": 2,
            "issueTypes": [{"type": "wecom.group_message", "count": 2}],
            "businessExcluded": 3,
        },
        "internal": {
            "outboxCreated": 12,
            "outboxRelayed": 11,
            "consumerSucceeded": 20,
            "outboxBreaks": 1,
            "consumerBreaks": 1,
            "missingConsumers": 0,
            "issueCount": 2,
            "issueTypes": [{"type": "customer_projection", "count": 1}],
        },
    }

    message = build_operational_report_message(
        window_start=start,
        window_end=end,
        inspection=inspection,
    )

    assert "应执行 96 次，实际执行 95 次" in message
    assert "客户消息、群消息或其他外部动作可能没有真正到达" in message
    assert "业务数据已产生但后续自动处理可能尚未发生" in message
    assert "只读，不会自动补发、补消费" in message
    assert "webhook" not in message.lower()


def test_external_health_keeps_classified_business_outcomes_out_of_system_failures():
    _, end = _window()
    external = _normalize_external(
        {"created": 3, "succeeded": 0, "overdue": 0, "stalled": 0, "unknown": 0},
        {
            "captured_at": end,
            "evidence": {
                "recent_failed_terminal_count": 0,
                "recent_blocked_count": 0,
                "wechat_refund_not_enough_business_outcome": {"completed_count": 2},
                "external_contact_relationship_absent": {"count": 1},
            },
        },
        [],
        window_end=end,
    )

    assert external["terminal"] == 0
    assert external["businessExcluded"] == 3
    assert external["issueCount"] == 0


def test_operational_feishu_delivery_is_direct_and_uses_pinned_https_boundary():
    calls = []

    class Transport:
        def post(self, target, *, body, headers, timeout):
            calls.append(
                {
                    "hostname": target.hostname,
                    "body": body,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return HttpsTransportResponse(status_code=200, text='{"code":0}')

    result = send_feishu_operational_webhook_message(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-operational-token",
        "系统巡检正常",
        transport=Transport(),
        resolver=lambda _hostname, _port: ["8.8.8.8"],
    )

    assert result == {"ok": True, "providerStatusCode": 200}
    assert calls[0]["hostname"] == "open.feishu.cn"
    assert json.loads(calls[0]["body"])["content"]["text"] == "系统巡检正常"
