from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from aicrm_next.admin_jobs_archive_sync_gateway import execute_archive_sync
from aicrm_next.shared.retired_contracts import retired_external_effect_payload
from aicrm_next.shared.runtime_settings import runtime_setting
from aicrm_next.shared.sensitive_data import redact_sensitive_data, redact_sensitive_text

from .domain import (
    BROADCAST_SOURCE_TYPES,
    BROADCAST_STATUSES,
    JOB_TABS,
    broadcast_business_domain_label,
    broadcast_channel_label,
    broadcast_source_type_label,
    broadcast_target_kind_label,
    normalized_bool,
    normalized_int,
    normalized_text,
    status_label,
    status_tone,
    webhook_event_label,
)
from .repository import AdminJobsRepository, build_admin_jobs_repository, clean_broadcast_filters

TARGET_JOBS_ACTION = "jobs_console_action"
TARGET_BROADCAST_JOB = "broadcast_job"


def build_legacy_disabled_payload(legacy_key: str, *, error: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = retired_external_effect_payload(legacy_key, error=error)
    payload.update(extra or {})
    return payload


def _operator(value: Any) -> str:
    return normalized_text(value) or "crm_console"


def _audit(
    repo: AdminJobsRepository,
    *,
    operator: str,
    action_type: str,
    target_type: str,
    target_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    repo.insert_audit(
        operator=_operator(operator),
        action_type=normalized_text(action_type),
        target_type=normalized_text(target_type),
        target_id=normalized_text(target_id),
        before=before or {},
        after=after or {},
    )


def jobs_tabs(active_key: str) -> list[dict[str, Any]]:
    active = normalized_text(active_key) or "overview"
    return [
        {
            **item,
            "active": item["key"] == active,
            "href": normalized_text(item.get("href")) or f"/admin/jobs?tab={item['key']}",
        }
        for item in JOB_TABS
    ]


def _archive_sync_form_defaults() -> dict[str, str]:
    end_time = datetime.now().replace(microsecond=0)
    start_time = end_time - timedelta(hours=1)
    return {
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "owner_userid": normalized_text(os.getenv("WECOM_DEFAULT_OWNER_USERID")),
        "cursor": "",
        "operator": "",
    }


def _sync_row_view(row: dict[str, Any]) -> dict[str, Any]:
    status = normalized_text(row.get("status")) or "unknown"
    return {
        **row,
        "status_label": status_label(status),
        "status_tone": status_tone(status),
        "finished_or_created_at": normalized_text(row.get("finished_at")) or normalized_text(row.get("created_at")) or "-",
    }


def _callback_row_view(row: dict[str, Any]) -> dict[str, Any]:
    status = normalized_text(row.get("process_status")) or "pending"
    return {
        **row,
        "status_tone": status_tone(status),
        "process_status_label": status_label(status),
        "event_label": normalized_text(row.get("change_type")) or normalized_text(row.get("event_type")) or "回调事件",
    }


def _batch_row_view(row: dict[str, Any]) -> dict[str, Any]:
    status = normalized_text(row.get("status")) or "pending"
    return {
        **row,
        "status_label": status_label(status),
        "status_tone": status_tone(status),
        "window_label": f"{normalized_text(row.get('window_start'))} ~ {normalized_text(row.get('window_end'))}",
    }


def _deferred_row_view(row: dict[str, Any]) -> dict[str, Any]:
    status = normalized_text(row.get("status")) or "pending"
    return {**row, "status_label": status_label(status), "status_tone": status_tone(status)}


def _mask_target_url(value: Any) -> str:
    text = normalized_text(value)
    if not text:
        return "-"
    if "://" not in text:
        return text[:80]
    scheme, rest = text.split("://", 1)
    host, _, path = rest.partition("/")
    return f"{scheme}://{host}{('/' + path[:32]) if path else ''}"


def _webhook_delivery_state_label(row: dict[str, Any]) -> str:
    status = normalized_text(row.get("status"))
    if normalized_text(row.get("last_error")) == "webhook_not_configured":
        return "未配置"
    if status == "retry_scheduled":
        return "待自动重试"
    if status == "exhausted":
        return "已耗尽"
    if status == "success":
        return "已成功"
    if status == "failed":
        return "发送失败"
    return status_label(status)


def _webhook_delivery_state_tone(row: dict[str, Any]) -> str:
    status = normalized_text(row.get("status"))
    if normalized_text(row.get("last_error")) == "webhook_not_configured":
        return "warn"
    if status == "retry_scheduled":
        return "warn"
    if status in {"failed", "exhausted"}:
        return "danger"
    if status == "success":
        return "ok"
    return "neutral"


def _webhook_row_view(row: dict[str, Any]) -> dict[str, Any]:
    status = normalized_text(row.get("status"))
    source_key = normalized_text(row.get("source_key"))
    source_id = normalized_text(row.get("source_id"))
    return {
        **row,
        "status_label": status_label(status),
        "status_tone": status_tone(status),
        "event_label": webhook_event_label(row.get("event_type")),
        "target_url_masked": _mask_target_url(row.get("target_url")),
        "delivery_state_label": _webhook_delivery_state_label(row),
        "delivery_state_tone": _webhook_delivery_state_tone(row),
        "source_label": f"{source_key}:{source_id}" if source_key or source_id else "-",
        "can_retry": status in {"failed", "retry_scheduled", "exhausted"},
    }


def build_jobs_payload(args: Any, repo: AdminJobsRepository | None = None) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    raw_args = dict(args or {})
    active_tab = normalized_text(raw_args.get("tab")) or "overview"
    if active_tab not in {item["key"] for item in JOB_TABS}:
        active_tab = "overview"

    archive_filters = {
        "status": normalized_text(raw_args.get("archive_status") or raw_args.get("status")),
        "limit": normalized_int(raw_args.get("archive_limit") or raw_args.get("limit"), default=20),
    }
    callback_filters = {
        "process_status": normalized_text(raw_args.get("callback_status") or raw_args.get("status")),
        "query": normalized_text(raw_args.get("callback_query")),
        "limit": normalized_int(raw_args.get("callback_limit") or raw_args.get("limit"), default=20),
    }
    batch_filters = {
        "status": normalized_text(raw_args.get("batch_status") or raw_args.get("status")),
        "limit": normalized_int(raw_args.get("batch_limit") or raw_args.get("limit"), default=20),
        "selected_batch_id": normalized_text(raw_args.get("batch_id")),
    }
    deferred_filters = {
        "status": normalized_text(raw_args.get("job_status") or raw_args.get("status")),
        "owner_userid": normalized_text(raw_args.get("owner_userid")),
        "external_userid": normalized_text(raw_args.get("external_userid")),
        "limit": normalized_int(raw_args.get("job_limit") or raw_args.get("limit"), default=20),
    }
    webhook_filters = {
        "event_type": normalized_text(raw_args.get("webhook_event_type") or raw_args.get("event_type")),
        "status": normalized_text(raw_args.get("webhook_status") or raw_args.get("status")),
        "limit": normalized_int(raw_args.get("webhook_limit") or raw_args.get("limit"), default=20),
    }

    sync_runs = [_sync_row_view(item) for item in repo.list_sync_runs(**archive_filters)]
    callback_logs = [_callback_row_view(item) for item in repo.list_callback_logs(**callback_filters)]
    batch_rows = [_batch_row_view(item) for item in repo.list_message_batches(status=batch_filters["status"], limit=batch_filters["limit"])]
    deferred_jobs = [_deferred_row_view(item) for item in repo.list_deferred_jobs(**deferred_filters)]
    webhook_deliveries = [_webhook_row_view(item) for item in repo.list_webhook_deliveries(**webhook_filters)]

    selected_batch: dict[str, Any] = {}
    selected_batch_messages: list[dict[str, Any]] = []
    if batch_filters["selected_batch_id"].isdigit():
        selected = repo.get_message_batch(int(batch_filters["selected_batch_id"]))
        if selected:
            selected_batch = _batch_row_view(selected)
            selected_batch_messages = repo.list_batch_messages(int(batch_filters["selected_batch_id"]), limit=50)

    sync_counts = repo.sync_run_counts()
    callback_counts = repo.callback_counts()
    batch_counts = repo.message_batch_counts()
    deferred_counts = repo.deferred_job_counts()
    webhook_counts = repo.webhook_counts()
    last_sync_run = sync_runs[0] if sync_runs else {}

    summary_cards = [
        {
            "label": "聊天同步",
            "value": status_label(normalized_text(last_sync_run.get("status")) or "never"),
            "description": f"任务 #{last_sync_run.get('id') or '-'} · {last_sync_run.get('finished_or_created_at') or '暂无记录'}",
            "tone": status_tone(normalized_text(last_sync_run.get("status")) or "unknown"),
        },
        {
            "label": "回调状态",
            "value": "已开启",
            "description": f"失败 {callback_counts.get('failed_count', 0)} · 异步已开启",
            "tone": "danger" if callback_counts.get("failed_count") else "ok",
        },
        {
            "label": "消息批次",
            "value": batch_counts.get("pending_count", 0),
            "description": f"待处理 · 已确认 {batch_counts.get('acked_count', 0)}",
            "tone": "warn" if batch_counts.get("pending_count") else "ok",
        },
        {
            "label": "待处理作业",
            "value": deferred_counts.get("pending_count", 0),
            "description": f"待处理 · 失败 {deferred_counts.get('failed_count', 0)}",
            "tone": "danger" if deferred_counts.get("failed_count") else ("warn" if deferred_counts.get("pending_count") else "ok"),
        },
        {
            "label": "Webhook 投递",
            "value": webhook_counts.get("retry_scheduled_count", 0) + webhook_counts.get("exhausted_count", 0),
            "description": f"待重试 {webhook_counts.get('retry_scheduled_count', 0)} · 已耗尽 {webhook_counts.get('exhausted_count', 0)}",
            "tone": "danger" if webhook_counts.get("exhausted_count") else ("warn" if webhook_counts.get("retry_scheduled_count") else "ok"),
        },
    ]

    return {
        "active_tab": active_tab,
        "tabs": jobs_tabs(active_tab),
        "summary_cards": summary_cards,
        "archive_runtime": {
            "last_sync_run": last_sync_run,
            "sync_counts": sync_counts,
            "health": {"runner": "aicrm_next_admin_jobs"},
            "health_error": "",
            "sync_form": _archive_sync_form_defaults(),
        },
        "callback_runtime": {"enabled": True, "async_enabled": True, "counts": callback_counts},
        "batches_runtime": {"counts": batch_counts},
        "deferred_runtime": {
            "counts": deferred_counts,
            "mcp_auth_configured": bool(runtime_setting("AICRM_AUTH_MCP_CLIENT_ID")),
        },
        "webhook_runtime": {"counts": webhook_counts},
        "archive_filters": archive_filters,
        "callback_filters": callback_filters,
        "batch_filters": batch_filters,
        "deferred_filters": deferred_filters,
        "webhook_filters": webhook_filters,
        "archive_status_options": ["", "success", "failed"],
        "callback_status_options": ["", "pending", "processing", "success", "failed"],
        "batch_status_options": ["", "pending", "acked"],
        "deferred_status_options": ["", "pending", "running", "success", "conflict", "skipped", "failed"],
        "webhook_status_options": ["", "pending", "success", "failed", "retry_scheduled", "exhausted"],
        "webhook_event_type_options": [
            {"value": "", "label": "全部事件"},
            {"value": "openclaw_focus_message", "label": "OpenClaw 焦点消息"},
            {"value": "questionnaire_submit", "label": "问卷提交外发"},
        ],
        "sync_runs": sync_runs,
        "callback_logs": callback_logs,
        "batch_rows": batch_rows,
        "selected_batch": selected_batch,
        "selected_batch_messages": selected_batch_messages,
        "deferred_jobs": deferred_jobs,
        "webhook_deliveries": webhook_deliveries,
        "source_status": repo.source_status,
    }


def build_jobs_summary_payload(args: Any) -> dict[str, Any]:
    payload = build_jobs_payload(args)
    return {key: payload[key] for key in ("summary_cards", "archive_runtime", "callback_runtime", "batches_runtime", "deferred_runtime", "webhook_runtime")}


def build_jobs_archive_sync_payload(args: Any) -> dict[str, Any]:
    payload = build_jobs_payload({**dict(args or {}), "tab": "archive"})
    return {
        "runtime": payload["archive_runtime"],
        "filters": payload["archive_filters"],
        "items": payload["sync_runs"],
        "status_options": payload["archive_status_options"],
    }


def build_jobs_callbacks_payload(args: Any) -> dict[str, Any]:
    payload = build_jobs_payload({**dict(args or {}), "tab": "callbacks"})
    return {
        "runtime": payload["callback_runtime"],
        "filters": payload["callback_filters"],
        "items": payload["callback_logs"],
        "status_options": payload["callback_status_options"],
    }


def build_jobs_message_batches_payload(args: Any) -> dict[str, Any]:
    payload = build_jobs_payload({**dict(args or {}), "tab": "batches"})
    return {
        "runtime": payload["batches_runtime"],
        "filters": payload["batch_filters"],
        "items": payload["batch_rows"],
        "status_options": payload["batch_status_options"],
    }


def build_jobs_message_batch_detail_payload(batch_id: int, args: Any) -> dict[str, Any]:
    payload = build_jobs_payload({**dict(args or {}), "tab": "batches", "batch_id": str(batch_id)})
    return {
        "runtime": payload["batches_runtime"],
        "filters": payload["batch_filters"],
        "batch": payload["selected_batch"],
        "messages": payload["selected_batch_messages"],
    }


def build_jobs_deferred_jobs_payload(args: Any) -> dict[str, Any]:
    payload = build_jobs_payload({**dict(args or {}), "tab": "deferred"})
    return {
        "runtime": payload["deferred_runtime"],
        "filters": payload["deferred_filters"],
        "items": payload["deferred_jobs"],
        "status_options": payload["deferred_status_options"],
    }


def build_jobs_webhook_deliveries_payload(args: Any) -> dict[str, Any]:
    payload = build_jobs_payload({**dict(args or {}), "tab": "webhooks"})
    return {
        "runtime": payload["webhook_runtime"],
        "filters": payload["webhook_filters"],
        "items": payload["webhook_deliveries"],
        "status_options": payload["webhook_status_options"],
        "event_type_options": payload["webhook_event_type_options"],
    }


def execute_jobs_action(*, action: str, form: Any, operator: str, repo: AdminJobsRepository | None = None) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    action = normalized_text(action)
    operator_value = _operator(operator)
    if action == "run-archive-sync":
        request_payload = {key: normalized_text(form.get(key)) for key in ("start_time", "end_time", "owner_userid", "cursor")}
        if not request_payload["start_time"] or not request_payload["end_time"] or not request_payload["owner_userid"]:
            raise ValueError("开始时间、结束时间和负责人账号不能为空")
        if not normalized_bool(form.get("confirm")):
            preview = {"ok": True, "preview_only": True, "confirm_required": True, "request": request_payload}
            _audit(
                repo,
                operator=operator_value,
                action_type="preview_archive_sync",
                target_type=TARGET_JOBS_ACTION,
                target_id="archive_sync",
                before=request_payload,
                after=preview,
            )
            return preview
        if os.getenv("AICRM_ENABLE_IN_PROCESS_ARCHIVE_SYNC", "").strip().lower() not in {"1", "true", "yes", "on"}:
            return {
                "ok": False,
                "error_code": "in_process_archive_sync_disabled",
                "message": "请使用 scripts/run_incremental_archive_sync.py 执行会话存档同步，避免企微 native SDK 运行在 Web 进程内。",
                "runner": "scripts/run_incremental_archive_sync.py",
                "reply_monitor_skipped": True,
            }
        payload = execute_archive_sync(
            start_time=request_payload["start_time"],
            end_time=request_payload["end_time"],
            owner_userid=request_payload["owner_userid"],
            cursor=request_payload["cursor"],
            limit=normalized_int(form.get("limit"), default=100, minimum=1, maximum=1000),
            max_pages=normalized_int(form.get("max_pages"), default=1000, minimum=1, maximum=10000),
        )
        payload["runner"] = "aicrm_next_admin_jobs"
        _audit(
            repo,
            operator=operator_value,
            action_type="run_archive_sync",
            target_type=TARGET_JOBS_ACTION,
            target_id="archive_sync",
            before=request_payload,
            after=payload,
        )
        return payload
    if action == "ack-message-batch":
        if not normalized_bool(form.get("confirm")):
            raise ValueError("确认消息批次前请先勾选确认")
        batch_id = normalized_int(form.get("batch_id"), default=0, minimum=1, maximum=10**9)
        before = repo.get_message_batch(batch_id) or {}
        batch = repo.ack_message_batch(batch_id, ack_note=normalized_text(form.get("ack_note")), acked_by=operator_value)
        if not batch:
            raise ValueError("未找到对应消息批次")
        result = {"ok": True, "batch": _batch_row_view(batch)}
        _audit(
            repo, operator=operator_value, action_type="ack_message_batch", target_type=TARGET_JOBS_ACTION, target_id=str(batch_id), before=before, after=result
        )
        return result
    if action == "run-deferred-jobs":
        return retired_external_effect_payload("old_admin_jobs_deferred_run", error="legacy_deferred_jobs_runner_disabled")
    if action == "retry-webhook-delivery":
        return retired_external_effect_payload("old_customer_webhook_delivery_retry", error="legacy_webhook_retry_disabled")
    if action == "run-webhook-retries":
        return retired_external_effect_payload("old_customer_webhook_delivery_retry", error="legacy_webhook_retry_disabled")
    raise ValueError("不支持的同步任务操作")


def _beijing_time_label(value: Any) -> str:
    text = normalized_text(value)
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return re.sub(r"([+-]\d{2}:?\d{2}|Z)$", "", text).strip().replace("T", " ")[:16] or "-"


def _broadcast_summary_label(value: Any) -> str:
    text = normalized_text(value)
    if not text:
        return "-"
    text = re.sub(r"\bworkflow immediate node=(\d+)", r"自动化流程即时节点 \1", text)
    text = re.sub(r"\bworkflow node=(\d+)", r"自动化流程节点 \1", text)
    text = re.sub(r"\bcampaign=([A-Za-z0-9_-]+)\s+step=(\d+)", r"营销活动第 \2 步", text)
    text = re.sub(r"\b(\d+)\s+customer groups?\b", r"\1 个客户群", text)
    text = re.sub(r"\b(\d+)\s+users?\b", r"\1 人", text)
    text = text.replace("— ~", " · 约 ")
    text = text.replace("— ", " · ")
    text = text.replace("~", "约 ")
    text = text.replace(" people", " 人").replace(" customer groups", " 个客户群")
    return text


def _broadcast_source_detail(row: dict[str, Any]) -> str:
    source_id = normalized_text(row.get("source_id"))
    source_type = normalized_text(row.get("source_type"))
    source_table = normalized_text(row.get("source_table"))
    if source_table == "automation_group_ops_plans":
        return "群运营计划"
    if source_type == "workflow":
        match = re.search(r"node[-_:]?(\d+)|:(\d+)$", source_id)
        node_id = next((item for item in (match.groups() if match else []) if item), "")
        return f"节点 {node_id}" if node_id else "自动化流程"
    if source_type == "campaign":
        match = re.search(r"step[-_:]?(\d+)|:(\d+)$", source_id)
        step = next((item for item in (match.groups() if match else []) if item), "")
        return f"第 {step} 步" if step else "营销活动"
    if source_type == "cloud_plan":
        return "智能助手方案"
    return broadcast_source_type_label(source_type)


def build_broadcast_jobs_payload(args: Any, repo: AdminJobsRepository | None = None) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    raw = dict(args or {})
    statuses = [item.strip() for item in normalized_text(raw.get("status")).split(",") if item.strip()]
    source_types = [item.strip() for item in normalized_text(raw.get("source_type")).split(",") if item.strip()]
    statuses, source_types = clean_broadcast_filters(statuses, source_types)
    limit = normalized_int(raw.get("limit"), default=50, maximum=200)
    offset = normalized_int(raw.get("offset"), default=0, minimum=0, maximum=100000)
    jobs = [_broadcast_job_view(item) for item in repo.list_broadcast_jobs(statuses=statuses, source_types=source_types, limit=limit, offset=offset)]
    return {
        "filters": {"statuses": statuses, "source_types": source_types, "limit": limit, "offset": offset},
        "jobs": jobs,
        "counts": repo.broadcast_counts(statuses=statuses, source_types=source_types),
        "total": repo.broadcast_total(statuses=statuses, source_types=source_types),
        "status_options": [{"key": status, "label": status_label(status)} for status in BROADCAST_STATUSES],
        "source_type_options": [
            {"key": source_type, "label": broadcast_source_type_label(source_type)}
            for source_type in BROADCAST_SOURCE_TYPES
        ],
        "source_status": repo.source_status,
    }


def _broadcast_job_view(row: dict[str, Any]) -> dict[str, Any]:
    idempotency_key = normalized_text(row.get("idempotency_key"))
    status = normalized_text(row.get("status"))
    domain = normalized_text(row.get("business_domain")) or "unknown"
    safe_row = {
        key: row.get(key)
        for key in (
            "id",
            "source_type",
            "source_table",
            "scheduled_for",
            "priority",
            "batch_key",
            "business_domain",
            "channel",
            "target_kind",
            "failure_type",
            "status",
            "requires_approval",
            "approved_by",
            "approved_at",
            "cancelled_by",
            "cancelled_at",
            "target_count",
            "content_type",
            "attempt_count",
            "outbound_task_id",
            "external_effect_job_id",
            "execution_id",
            "execution_owner",
            "outbound_task_status",
            "sent_count",
            "failed_count",
            "trace_id",
            "created_by",
            "created_at",
            "updated_at",
            "claimed_at",
            "sent_at",
        )
        if key in row
    }
    safe_row["target_summary"] = redact_sensitive_text(row.get("target_summary"))
    safe_row["content_summary"] = redact_sensitive_text(row.get("content_summary"))
    safe_row["last_error"] = redact_sensitive_text(row.get("last_error"))
    safe_row["cancel_reason"] = redact_sensitive_text(row.get("cancel_reason"))
    safe_row["row_version"] = int(row.get("row_version") or 1)
    return {
        **safe_row,
        "status_label": status_label(status),
        "status_tone": status_tone(status),
        "business_domain": domain,
        "business_domain_label": broadcast_business_domain_label(domain),
        "source_type_label": broadcast_source_type_label(row.get("source_type")),
        "source_detail_label": _broadcast_source_detail(row),
        "channel_label": broadcast_channel_label(row.get("channel")),
        "target_kind_label": broadcast_target_kind_label(row.get("target_kind")),
        "target_summary_label": redact_sensitive_text(_broadcast_summary_label(row.get("target_summary"))),
        "content_summary_label": redact_sensitive_text(_broadcast_summary_label(row.get("content_summary"))),
        "scheduled_for_label": _beijing_time_label(row.get("scheduled_for")),
        "has_idempotency_key": bool(row.get("has_idempotency_key")) or bool(idempotency_key),
        "can_approve": status == "waiting_approval",
        "can_cancel": status in {"queued", "waiting_approval"},
    }


def build_broadcast_job_detail_payload(
    job_id: int,
    repo: AdminJobsRepository | None = None,
) -> dict[str, Any] | None:
    repo = repo or build_admin_jobs_repository()
    row = repo.get_broadcast_job(int(job_id))
    if not row:
        return None
    job = _broadcast_job_view(row)
    batch = repo.get_message_batch(int(job_id)) or {}
    raw_events = repo.list_batch_messages(int(job_id), limit=100)
    event_total = repo.count_batch_messages(int(job_id))
    events = [
        {
            "message_id": event.get("message_id"),
            "event_type": normalized_text(event.get("msgtype") or event.get("event_type")),
            "occurred_at": event.get("send_time") or event.get("created_at"),
            "chat_type": normalized_text(event.get("chat_type")),
            "has_external_target": bool(
                normalized_text(event.get("external_userid"))
                or normalized_text(event.get("chat_id"))
                or normalized_text(event.get("receiver"))
            ),
            "has_content": bool(normalized_text(event.get("content"))),
        }
        for event in raw_events
    ]
    batch_summary = {
        key: batch.get(key)
        for key in ("id", "batch_key", "window_start", "window_end", "status", "message_count", "created_at", "acked_at")
        if key in batch
    }
    return {
        "ok": True,
        "job": redact_sensitive_data(job),
        "batch": redact_sensitive_data(batch_summary),
        "events": events,
        "linked_record_counts": {
            "broadcast_job_events": event_total,
            "outbound_tasks": 1 if job.get("outbound_task_id") else 0,
            "external_effect_jobs": 1 if job.get("external_effect_job_id") else 0,
        },
        "push_center_url": f"/admin/push-center/jobs/broadcast_job:{int(job_id)}",
        "events_truncated": event_total > len(events),
        "route_owner": "ai_crm_next",
        "real_external_call_executed": False,
    }


def approve_broadcast_job(
    job_id: int,
    *,
    operator: str,
    reason: str = "",
    repo: AdminJobsRepository | None = None,
) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    before = repo.get_broadcast_job(job_id) or {}
    after = repo.approve_broadcast_job(job_id, approved_by=_operator(operator))
    if not after:
        raise ValueError("job not approvable (not waiting_approval)")
    payload = {
        "ok": True,
        "approved": True,
        "job_id": job_id,
        "reason": redact_sensitive_text(reason),
        "job": _broadcast_job_view(after),
    }
    _audit(
        repo,
        operator=operator,
        action_type="approve_broadcast_job",
        target_type=TARGET_BROADCAST_JOB,
        target_id=str(job_id),
        before=redact_sensitive_data(_broadcast_job_view(before)) if before else {},
        after=payload,
    )
    return payload


def cancel_broadcast_job(job_id: int, *, operator: str, reason: str = "", repo: AdminJobsRepository | None = None) -> dict[str, Any]:
    repo = repo or build_admin_jobs_repository()
    before = repo.get_broadcast_job(job_id) or {}
    after = repo.cancel_broadcast_job(job_id, cancelled_by=_operator(operator), reason=normalized_text(reason))
    if not after:
        raise ValueError("job not cancelable (not queued or waiting_approval)")
    payload = {"ok": True, "cancelled": True, "job_id": job_id, "job": _broadcast_job_view(after)}
    _audit(
        repo,
        operator=operator,
        action_type="cancel_broadcast_job",
        target_type=TARGET_BROADCAST_JOB,
        target_id=str(job_id),
        before=redact_sensitive_data(_broadcast_job_view(before)) if before else {},
        after=payload,
    )
    return payload
