from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from .repository import read_operational_inspection_rows


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_RUNTIME_MANIFEST = ROOT / "deploy" / "production_runtime_units.json"
TIMER_STALE_AFTER = timedelta(minutes=10)

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class TimerSpec:
    timer: str
    service: str
    required: bool
    allowed_missed_executions: int = 0
    allow_additional_invocations: bool = False


def collect_operational_inspection(
    *,
    window_start: datetime,
    window_end: datetime,
    manifest_path: Path = PRODUCTION_RUNTIME_MANIFEST,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Collect read-only timer and durable-queue evidence for one report window."""

    timer_report = inspect_systemd_timers(
        window_start=window_start,
        window_end=window_end,
        manifest_path=manifest_path,
        command_runner=command_runner,
    )
    queue_report = inspect_durable_queues(
        window_start=window_start,
        window_end=window_end,
    )
    issue_count = int(timer_report["issueCount"]) + int(queue_report["issueCount"])
    return {
        "status": "异常" if issue_count else "正常",
        "issueCount": issue_count,
        "readOnly": True,
        "timer": timer_report,
        "external": queue_report["external"],
        "internal": queue_report["internal"],
    }


def inspect_systemd_timers(
    *,
    window_start: datetime,
    window_end: datetime,
    manifest_path: Path = PRODUCTION_RUNTIME_MANIFEST,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    run = command_runner or _run_command
    timer_specs = load_timer_specs(manifest_path)
    details: list[dict[str, Any]] = []
    expected_total = 0
    actual_total = 0

    for spec in timer_specs:
        state = _systemd_unit_state(spec.timer, run=run)
        monitored = spec.required or state["enabled"]
        schedule = _read_on_calendar(manifest_path.parent / spec.timer)
        expected: int | None = None
        actual = 0
        reason = ""
        if monitored:
            try:
                expected = count_calendar_occurrences(
                    schedule,
                    window_start=window_start,
                    window_end=window_end,
                )
            except ValueError:
                reason = "无法识别定时表达式"
            actual = count_service_invocations(
                spec.service,
                window_start=window_start,
                window_end=window_end,
                run=run,
            )
            if expected is not None:
                expected_total += expected
            actual_total += actual

        issues: list[str] = []
        if spec.required and not state["enabled"]:
            issues.append("生产必需 timer 未启用")
        if spec.required and not state["active"]:
            issues.append("生产必需 timer 未运行")
        if monitored and reason:
            issues.append(reason)
        if (
            monitored
            and expected is not None
            and actual < max(0, expected - spec.allowed_missed_executions)
        ):
            issues.append(f"少执行 {expected - actual} 次")
        if (
            monitored
            and expected is not None
            and actual > expected
            and not spec.allow_additional_invocations
        ):
            issues.append(f"多执行 {actual - expected} 次")
        details.append(
            {
                "timer": spec.timer,
                "service": spec.service,
                "required": spec.required,
                "monitored": monitored,
                "enabled": state["enabled"],
                "active": state["active"],
                "schedule": schedule,
                "expected": expected,
                "actual": actual,
                "allowedMissedExecutions": spec.allowed_missed_executions,
                "allowAdditionalInvocations": spec.allow_additional_invocations,
                "issues": issues,
            }
        )

    issue_details = [item for item in details if item["issues"]]
    calibrated_differences = [
        item
        for item in details
        if item["monitored"]
        and item["expected"] is not None
        and item["actual"] != item["expected"]
        and not item["issues"]
    ]
    return {
        "managedCount": len(timer_specs),
        "monitoredCount": sum(1 for item in details if item["monitored"]),
        "expectedExecutions": expected_total,
        "actualExecutions": actual_total,
        "issueCount": len(issue_details),
        "calibratedDifferenceCount": len(calibrated_differences),
        "issues": issue_details,
        "details": details,
    }


def load_timer_specs(manifest_path: Path = PRODUCTION_RUNTIME_MANIFEST) -> list[TimerSpec]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    specs: list[TimerSpec] = []
    seen: set[str] = set()
    for section_name, required in (("active_autostart", True), ("approval_required", False)):
        for item in manifest.get(section_name) or []:
            timer = str(item.get("timer") or "").strip()
            service = str(item.get("service") or "").strip()
            if not timer or not service or timer in seen:
                continue
            seen.add(timer)
            specs.append(
                TimerSpec(
                    timer=timer,
                    service=service,
                    required=required,
                    allowed_missed_executions=max(
                        0,
                        int(item.get("inspection_allowed_missed_executions") or 0),
                    ),
                    allow_additional_invocations=bool(
                        item.get("inspection_allow_additional_invocations", False)
                    ),
                )
            )
    return specs


def count_calendar_occurrences(
    expression: str,
    *,
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Count repo-supported systemd calendar slots in the half-open window."""

    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("calendar window must be timezone-aware")
    tokens = expression.split()
    if not tokens:
        raise ValueError("OnCalendar is missing")
    schedule_zone = window_start.tzinfo
    if "/" in tokens[-1] and ":" not in tokens[-1]:
        schedule_zone = ZoneInfo(tokens.pop())
    if len(tokens) == 2:
        date_part, time_part = tokens
        if date_part not in {"*", "*-*-*"}:
            raise ValueError("unsupported calendar date")
    elif len(tokens) == 1:
        time_part = tokens[0]
    else:
        raise ValueError("unsupported calendar expression")
    fields = time_part.split(":")
    if len(fields) == 2:
        fields.append("0")
    if len(fields) != 3:
        raise ValueError("unsupported calendar time")

    start_utc = window_start.astimezone(timezone.utc).replace(microsecond=0)
    end_utc = window_end.astimezone(timezone.utc).replace(microsecond=0)
    count = 0
    cursor = start_utc
    while cursor < end_utc:
        local = cursor.astimezone(schedule_zone)
        if (
            _calendar_field_matches(fields[0], local.hour, 23)
            and _calendar_field_matches(fields[1], local.minute, 59)
            and _calendar_field_matches(fields[2], local.second, 59)
        ):
            count += 1
        cursor += timedelta(seconds=1)
    return count


def count_service_invocations(
    service: str,
    *,
    window_start: datetime,
    window_end: datetime,
    run: CommandRunner | None = None,
) -> int:
    runner = run or _run_command
    completed = runner(
        [
            "journalctl",
            "-u",
            service,
            "--since",
            f"@{int(window_start.timestamp())}",
            "--until",
            f"@{int(window_end.timestamp())}",
            "-o",
            "json",
            "--no-pager",
        ]
    )
    if completed.returncode != 0:
        return 0
    invocation_ids: set[str] = set()
    for line in completed.stdout.splitlines():
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        invocation_id = str(
            row.get("_SYSTEMD_INVOCATION_ID") or row.get("INVOCATION_ID") or ""
        ).strip()
        if invocation_id:
            invocation_ids.add(invocation_id)
    return len(invocation_ids)


def inspect_durable_queues(
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    stale_before = window_end - TIMER_STALE_AFTER
    rows = read_operational_inspection_rows(
        external_flow_query=_external_flow_sql(),
        external_flow_params=(
            window_start,
            window_end,
            window_start,
            window_end,
            stale_before,
            stale_before,
        ),
        external_health_query=_external_health_snapshot_sql(),
        internal_flow_query=_internal_flow_sql(),
        internal_flow_params=(
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
        ),
        external_issue_types_query=_external_issue_types_sql(),
        internal_issue_types_query=_internal_issue_types_sql(),
    )

    external = _normalize_external(
        rows["external_flow"],
        rows["external_health_snapshot"],
        rows["external_types"],
        window_end=window_end,
    )
    internal_summary = _normalize_internal(rows["internal"], rows["internal_types"])
    return {
        "issueCount": int(external["issueCount"]) + int(internal_summary["issueCount"]),
        "external": external,
        "internal": internal_summary,
    }


def build_operational_report_message(
    *,
    window_start: datetime,
    window_end: datetime,
    inspection: dict[str, Any],
) -> str:
    timer = inspection["timer"]
    external = inspection["external"]
    internal = inspection["internal"]
    status_icon = "🔴" if inspection.get("issueCount") else "🟢"
    timer_summary = (
        f"应执行 {timer['expectedExecutions']} 次，实际执行 {timer['actualExecutions']} 次；"
        f"监控 {timer['monitoredCount']}/{timer['managedCount']} 个 timer，异常 {timer['issueCount']} 个。"
    )
    calibrated_timer_count = int(timer.get("calibratedDifferenceCount") or 0)
    if calibrated_timer_count:
        timer_summary += (
            f"其中 {calibrated_timer_count} 个执行次数差异属于发布停顿或上线主动补跑，"
            "保留原始次数但不计为业务断点。"
        )
    lines = [
        f"【系统运营巡检小时报】{status_icon} {inspection.get('status', '未知')}",
        f"统计窗口：{window_start.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M} - {window_end.astimezone(ZoneInfo('Asia/Shanghai')):%H:%M}",
        "",
        "1. 定时任务",
        timer_summary,
        "",
        "2. 外部推送",
        (
            f"本小时入队 {external['created']}，完成 {external['succeeded']}；"
            f"到点未推送 {external['overdue']}，执行卡住 {external['stalled']}，"
            f"结果未知 {external['unknown']}，近24小时系统终止 {external['terminal']}。"
        ),
        "",
        "3. 内部消费",
        (
            f"本小时 outbox 写入 {internal['outboxCreated']}、已转事件 {internal['outboxRelayed']}，"
            f"消费者完成 {internal['consumerSucceeded']}；outbox 断点 {internal['outboxBreaks']}，"
            f"消费者断点 {internal['consumerBreaks']}，缺失消费者 {internal['missingConsumers']}。"
        ),
    ]
    issue_lines = _business_issue_lines(inspection)
    if issue_lines:
        lines.extend(["", "需要处理的断点", *issue_lines])
    else:
        lines.extend(["", "业务影响：当前未发现应执行未执行、应推送未推送或应消费未消费。"])
    lines.extend(["", "说明：本巡检只读，不会自动补发、补消费；已确认的业务拒绝和历史不重放记录不计入系统故障。"])
    return "\n".join(lines)


def _external_flow_sql() -> str:
    return """
        SELECT
          COUNT(*) FILTER (WHERE created_at >= %s AND created_at < %s) AS created,
          COUNT(*) FILTER (WHERE status = 'succeeded' AND completed_at >= %s AND completed_at < %s) AS succeeded,
          COUNT(*) FILTER (
            WHERE hold_reason = '' AND (
              status IN ('queued', 'failed_retryable', 'approved')
              OR (status = 'planned' AND requires_approval IS FALSE)
            ) AND COALESCE(available_at, next_retry_at, scheduled_at, created_at) < %s
          ) AS overdue,
          COUNT(*) FILTER (
            WHERE status = 'dispatching' AND lease_expires_at < %s
          ) AS stalled,
          COUNT(*) FILTER (
            WHERE status = 'unknown_after_dispatch' OR reconciliation_required IS TRUE
          ) AS unknown
        FROM external_effect_job
        WHERE execution_mode = 'execute'
    """


def _external_health_snapshot_sql() -> str:
    return """
        SELECT jsonb_object_agg(
                   item.check_item ->> 'check_id',
                   item.check_item -> 'evidence'
               ) AS evidence_by_check,
               snapshot.captured_at
        FROM data_health_snapshot snapshot
        CROSS JOIN LATERAL jsonb_array_elements(snapshot.checks_json) AS item(check_item)
        WHERE snapshot.singleton IS TRUE
          AND item.check_item ->> 'check_id' IN (
              'external_effect_due_retryable_backlog',
              'external_effect_unclassified_terminal_recent',
              'external_effect_unclassified_blocked_recent'
          )
        GROUP BY snapshot.captured_at
        LIMIT 1
    """


def _internal_flow_sql() -> str:
    return """
        WITH event_gaps AS (
          SELECT event.event_id,
                 GREATEST(event.expected_consumer_count - COUNT(run.id), 0) AS missing_count
          FROM internal_event event
          LEFT JOIN internal_event_consumer_run run ON run.event_id = event.event_id
          WHERE event.created_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
          GROUP BY event.event_id, event.expected_consumer_count
        )
        SELECT
          (SELECT COUNT(*) FROM internal_event_outbox WHERE created_at >= %s AND created_at < %s) AS outbox_created,
          (SELECT COUNT(*) FROM internal_event_outbox WHERE status = 'relayed' AND relayed_at >= %s AND relayed_at < %s) AS outbox_relayed,
          (SELECT COUNT(*) FROM internal_event_outbox
             WHERE status IN ('pending', 'failed_retryable') AND hold_reason = ''
               AND COALESCE(available_at, next_retry_at, occurred_at, created_at) < %s) AS outbox_overdue,
          (SELECT COUNT(*) FROM internal_event_outbox
             WHERE status = 'running' AND lease_expires_at < %s) AS outbox_stalled,
          (SELECT COUNT(*) FROM internal_event_outbox
             WHERE status = 'failed_terminal' AND hold_reason = ''
               AND updated_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours') AS outbox_terminal,
          (SELECT COUNT(*) FROM internal_event_consumer_run WHERE status = 'succeeded' AND finished_at >= %s AND finished_at < %s) AS consumer_succeeded,
          (SELECT COUNT(*) FROM internal_event_consumer_run
             WHERE status IN ('pending', 'failed_retryable') AND hold_reason = ''
               AND COALESCE(available_at, next_retry_at, created_at) < %s) AS consumer_overdue,
          (SELECT COUNT(*) FROM internal_event_consumer_run
             WHERE status = 'running' AND lease_expires_at < %s) AS consumer_stalled,
          (SELECT COUNT(*) FROM internal_event_consumer_run
             WHERE status IN ('failed_terminal', 'blocked') AND hold_reason = ''
               AND updated_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours') AS consumer_terminal,
          COALESCE((SELECT SUM(missing_count) FROM event_gaps), 0) AS missing_consumers
    """


def _external_issue_types_sql() -> str:
    return """
        SELECT COALESCE(NULLIF(effect_type, ''), 'unknown') AS issue_type,
               COUNT(*)::BIGINT AS issue_count
        FROM external_effect_job
        WHERE execution_mode = 'execute' AND hold_reason = '' AND (
          ((status IN ('queued', 'failed_retryable', 'approved')
             OR (status = 'planned' AND requires_approval IS FALSE))
            AND COALESCE(available_at, next_retry_at, scheduled_at, created_at) < CURRENT_TIMESTAMP - INTERVAL '10 minutes')
          OR (status = 'dispatching' AND lease_expires_at < CURRENT_TIMESTAMP)
          OR status = 'unknown_after_dispatch'
        )
        GROUP BY effect_type ORDER BY issue_count DESC, issue_type ASC LIMIT 5
    """


def _internal_issue_types_sql() -> str:
    return """
        SELECT consumer_name AS issue_type, COUNT(*)::BIGINT AS issue_count
        FROM internal_event_consumer_run
        WHERE hold_reason = '' AND (
          (status IN ('pending', 'failed_retryable')
            AND COALESCE(available_at, next_retry_at, created_at) < CURRENT_TIMESTAMP - INTERVAL '10 minutes')
          OR (status = 'running' AND lease_expires_at < CURRENT_TIMESTAMP)
        )
        GROUP BY consumer_name ORDER BY issue_count DESC, issue_type ASC LIMIT 5
    """


def _normalize_external(
    flow: dict[str, Any],
    health_snapshot: dict[str, Any],
    issue_types: list[dict[str, Any]],
    *,
    window_end: datetime,
) -> dict[str, Any]:
    evidence_by_check = health_snapshot.get("evidence_by_check") or {}
    if isinstance(evidence_by_check, str):
        try:
            evidence_by_check = json.loads(evidence_by_check)
        except ValueError:
            evidence_by_check = {}
    if isinstance(evidence_by_check, dict) and evidence_by_check:
        evidence = {}
        for check_id in (
            "external_effect_due_retryable_backlog",
            "external_effect_unclassified_terminal_recent",
            "external_effect_unclassified_blocked_recent",
        ):
            value = evidence_by_check.get(check_id)
            if isinstance(value, dict):
                evidence.update(value)
    else:
        evidence = health_snapshot.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            evidence = {}
    if not isinstance(evidence, dict):
        evidence = {}
    captured_at = health_snapshot.get("captured_at")
    snapshot_fresh = isinstance(captured_at, datetime) and captured_at >= (
        window_end.astimezone(timezone.utc) - timedelta(minutes=30)
    )
    terminal = int(evidence.get("recent_failed_terminal_count") or 0) + int(
        evidence.get("recent_blocked_count") or 0
    )
    normalized = {
        "created": int(flow.get("created") or 0),
        "succeeded": int(flow.get("succeeded") or 0),
        "overdue": int(flow.get("overdue") or 0),
        "stalled": int(flow.get("stalled") or 0),
        "unknown": int(flow.get("unknown") or 0),
        "terminal": terminal,
        "issueTypes": _normalize_issue_types(issue_types),
        "businessExcluded": _business_excluded_count(evidence),
        "healthSnapshotFresh": snapshot_fresh,
    }
    normalized["issueCount"] = sum(
        normalized[key] for key in ("overdue", "stalled", "unknown", "terminal")
    ) + (0 if snapshot_fresh else 1)
    return normalized


def _normalize_internal(row: dict[str, Any], issue_types: list[dict[str, Any]]) -> dict[str, Any]:
    outbox_breaks = sum(
        int(row.get(key) or 0)
        for key in ("outbox_overdue", "outbox_stalled", "outbox_terminal")
    )
    consumer_breaks = sum(
        int(row.get(key) or 0)
        for key in ("consumer_overdue", "consumer_stalled", "consumer_terminal")
    )
    missing = int(row.get("missing_consumers") or 0)
    return {
        "outboxCreated": int(row.get("outbox_created") or 0),
        "outboxRelayed": int(row.get("outbox_relayed") or 0),
        "consumerSucceeded": int(row.get("consumer_succeeded") or 0),
        "outboxBreaks": outbox_breaks,
        "consumerBreaks": consumer_breaks,
        "missingConsumers": missing,
        "issueCount": outbox_breaks + consumer_breaks + missing,
        "issueTypes": _normalize_issue_types(issue_types),
    }


def _business_excluded_count(health: dict[str, Any]) -> int:
    paths = (
        ("pre_cutover_welcome_terminal_acknowledgement", "acknowledged_count"),
        ("production_welcome_41050_acknowledgement", "acknowledged_count"),
        ("production_private_message_84061_acknowledgement", "acknowledged_count"),
        ("production_group_message_40058_no_replay_classification", "classified_count"),
        ("production_private_message_contact_absence_20260728_acknowledgement", "acknowledged_count"),
        ("production_wechat_refund_not_enough_acknowledgement", "acknowledged_count"),
        ("wechat_refund_not_enough_business_outcome", "completed_count"),
        ("wecom_content_validation_business_outcome", "completed_count"),
        ("external_contact_relationship_absent", "count"),
        ("private_message_contact_relationship_absent", "count"),
    )
    return sum(
        int((health.get(group) or {}).get(key) or 0)
        for group, key in paths
        if isinstance(health.get(group), dict)
    )


def _business_issue_lines(inspection: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in inspection["timer"].get("issues") or []:
        lines.append(f"- 定时任务 {item['timer']}：{'；'.join(item['issues'])}，对应业务动作可能未按计划启动。")
    external = inspection["external"]
    if external.get("issueCount"):
        detail = _issue_type_text(external.get("issueTypes") or [])
        if not external.get("healthSnapshotFresh"):
            detail += " 最近一次数据健康快照缺失或超过 30 分钟。"
        lines.append(
            f"- 外部推送链路：共有 {external['issueCount']} 个断点，客户消息、群消息或其他外部动作可能没有真正到达。{detail}"
        )
    internal = inspection["internal"]
    if internal.get("issueCount"):
        detail = _issue_type_text(internal.get("issueTypes") or [])
        lines.append(
            f"- 内部消费链路：共有 {internal['issueCount']} 个断点，业务数据已产生但后续自动处理可能尚未发生。{detail}"
        )
    return lines[:12]


def _issue_type_text(items: Iterable[dict[str, Any]]) -> str:
    values = [f"{item['type']} {item['count']} 个" for item in items]
    return f" 主要类型：{'、'.join(values)}。" if values else ""


def _normalize_issue_types(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"type": str(row.get("issue_type") or "unknown"), "count": int(row.get("issue_count") or 0)}
        for row in rows
        if int(row.get("issue_count") or 0) > 0
    ]


def _calendar_field_matches(expression: str, value: int, maximum: int) -> bool:
    for part in expression.split(","):
        part = part.strip()
        if part == "*":
            return True
        if "/" in part:
            base_text, step_text = part.split("/", 1)
            base = 0 if base_text == "*" else int(base_text)
            step = int(step_text)
            if step <= 0:
                raise ValueError("calendar step must be positive")
            if base <= value <= maximum and (value - base) % step == 0:
                return True
            continue
        if int(part) == value:
            return True
    return False


def _read_on_calendar(timer_path: Path) -> str:
    for line in timer_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("OnCalendar="):
            return line.split("=", 1)[1].strip()
    raise ValueError(f"{timer_path.name} has no OnCalendar")


def _systemd_unit_state(timer: str, *, run: CommandRunner) -> dict[str, bool]:
    completed = run(
        [
            "systemctl",
            "show",
            timer,
            "--property=UnitFileState",
            "--property=ActiveState",
            "--no-pager",
        ]
    )
    values: dict[str, str] = {}
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            key, _, value = line.partition("=")
            values[key] = value.strip()
    return {
        "enabled": values.get("UnitFileState") in {"enabled", "enabled-runtime", "static"},
        "active": values.get("ActiveState") == "active",
    }


def _run_command(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
