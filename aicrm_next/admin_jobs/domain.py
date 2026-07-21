from __future__ import annotations

from typing import Any

JOB_TABS = (
    {"key": "overview", "label": "概览"},
    {"key": "archive", "label": "聊天同步"},
    {"key": "callbacks", "label": "回调状态"},
    {"key": "batches", "label": "消息批次"},
    {"key": "deferred", "label": "待处理作业"},
    {"key": "webhooks", "label": "Webhook 投递"},
    {"key": "broadcast_queue", "label": "群发队列", "href": "/admin/broadcast-jobs"},
)

BROADCAST_STATUSES = (
    "waiting_approval",
    "queued",
    "claimed",
    "dispatching",
    "delegated",
    "simulated",
    "sent",
    "failed_retryable",
    "failed_terminal",
    "blocked",
    "unknown_after_dispatch",
    # Compatibility for rows written before the delivery state machine.
    "failed",
    "cancelled",
)

BROADCAST_SOURCE_TYPES = (
    "campaign",
    "sop",
    "workflow",
    "cloud_plan",
    "focus_send",
    "deferred",
    "manual",
)

BROADCAST_BUSINESS_DOMAIN_LABELS = {
    "automation_ops": "自动化运营",
    "ai_assistant": "智能助手",
    "group_ops": "群运营计划",
    "manual": "手动",
    "unknown": "未知",
}

BROADCAST_SOURCE_TYPE_LABELS = {
    "campaign": "营销活动",
    "sop": "标准运营流程",
    "workflow": "自动化流程",
    "cloud_plan": "智能助手方案",
    "focus_send": "重点触达",
    "deferred": "延迟任务",
    "manual": "手动群发",
}

BROADCAST_CHANNEL_LABELS = {
    "wecom_private": "企微私聊",
    "wecom_customer_group": "企微客户群",
    "wechat": "微信",
    "manual": "手动",
    "unknown": "未知渠道",
}

BROADCAST_TARGET_KIND_LABELS = {
    "external_userid": "客户",
    "chat_id": "客户群",
    "mixed": "混合目标",
    "dynamic": "动态目标",
    "unknown": "未知目标",
}


def normalized_text(value: Any) -> str:
    return str(value or "").strip()


def normalized_int(value: Any, *, default: int, minimum: int = 1, maximum: int = 200) -> int:
    try:
        parsed = int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def normalized_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return normalized_text(value).lower() in {"1", "true", "yes", "y", "on"}


def status_tone(status: str) -> str:
    normalized = normalized_text(status).lower()
    if normalized in {"success", "acked", "enabled", "healthy", "sent"}:
        return "ok"
    if normalized in {"failed", "failed_terminal", "blocked", "disabled", "error", "exhausted", "cancelled"}:
        return "danger"
    if normalized in {
        "pending",
        "running",
        "processing",
        "conflict",
        "skipped",
        "retry_scheduled",
        "waiting_approval",
        "queued",
        "claimed",
        "dispatching",
        "delegated",
        "failed_retryable",
        "unknown_after_dispatch",
    }:
        return "warn"
    return "neutral"


def status_label(status: Any) -> str:
    mapping = {
        "success": "成功",
        "failed": "失败",
        "pending": "待处理",
        "processing": "处理中",
        "running": "运行中",
        "conflict": "冲突",
        "skipped": "已跳过",
        "acked": "已确认",
        "enabled": "已开启",
        "disabled": "未开启",
        "healthy": "正常",
        "never": "暂无记录",
        "retry_scheduled": "待自动重试",
        "exhausted": "已耗尽",
        "waiting_approval": "待审批",
        "queued": "排队中",
        "claimed": "执行中",
        "dispatching": "发送中",
        "delegated": "已委托外部动作",
        "simulated": "模拟执行",
        "sent": "已发送",
        "failed_retryable": "失败可重试",
        "failed_terminal": "失败不可重试",
        "blocked": "执行受阻",
        "unknown_after_dispatch": "结果待核对",
        "cancelled": "已取消",
    }
    normalized = normalized_text(status).lower()
    return mapping.get(normalized, normalized_text(status) or "-")


def broadcast_business_domain_label(value: Any) -> str:
    normalized = normalized_text(value).lower() or "unknown"
    return BROADCAST_BUSINESS_DOMAIN_LABELS.get(normalized, normalized_text(value) or "未知")


def broadcast_source_type_label(value: Any) -> str:
    normalized = normalized_text(value).lower()
    return BROADCAST_SOURCE_TYPE_LABELS.get(normalized, normalized_text(value) or "未知来源")


def broadcast_channel_label(value: Any) -> str:
    normalized = normalized_text(value).lower() or "unknown"
    return BROADCAST_CHANNEL_LABELS.get(normalized, normalized_text(value) or "未知渠道")


def broadcast_target_kind_label(value: Any) -> str:
    normalized = normalized_text(value).lower() or "unknown"
    return BROADCAST_TARGET_KIND_LABELS.get(normalized, normalized_text(value) or "未知目标")


def webhook_event_label(event_type: Any) -> str:
    mapping = {
        "openclaw_focus_message": "OpenClaw 焦点消息",
        "questionnaire_submit": "问卷提交外发",
    }
    return mapping.get(normalized_text(event_type), normalized_text(event_type) or "-")
