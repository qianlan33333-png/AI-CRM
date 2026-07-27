from __future__ import annotations

from typing import Any


PUSH_STATUS_PENDING = "pending"
PUSH_STATUS_RUNNING = "running"
PUSH_STATUS_SUCCEEDED = "succeeded"
PUSH_STATUS_FAILED = "failed"
PUSH_STATUS_SENT = "sent"
PUSH_STATUS_SIMULATED = "simulated"
PUSH_STATUS_UNKNOWN_AFTER_DISPATCH = "unknown_after_dispatch"
PUSH_STATUS_SENT_WITH_SHADOW_WARNING = "sent_with_shadow_warning"
PUSH_STATUS_SHADOW_FAILED_NOT_BUSINESS_FAILED = "shadow_failed_not_business_failed"

STANDARD_PUSH_STATUSES = (
    PUSH_STATUS_PENDING,
    PUSH_STATUS_RUNNING,
    PUSH_STATUS_SUCCEEDED,
    PUSH_STATUS_SENT,
    PUSH_STATUS_SIMULATED,
    PUSH_STATUS_UNKNOWN_AFTER_DISPATCH,
    PUSH_STATUS_FAILED,
    PUSH_STATUS_SENT_WITH_SHADOW_WARNING,
    PUSH_STATUS_SHADOW_FAILED_NOT_BUSINESS_FAILED,
)

PUSH_STATUS_LABELS = {
    PUSH_STATUS_PENDING: "待执行",
    PUSH_STATUS_RUNNING: "执行中",
    PUSH_STATUS_SUCCEEDED: "执行成功",
    PUSH_STATUS_SENT: "已发送",
    PUSH_STATUS_SIMULATED: "模拟执行",
    PUSH_STATUS_UNKNOWN_AFTER_DISPATCH: "结果待核对",
    PUSH_STATUS_FAILED: "发送失败",
    PUSH_STATUS_SENT_WITH_SHADOW_WARNING: "已发送 · 影子链路异常",
    PUSH_STATUS_SHADOW_FAILED_NOT_BUSINESS_FAILED: "影子链路失败，未发现主发送记录",
}

PUSH_STATUS_DEFINITIONS = {
    PUSH_STATUS_PENDING: "已进入统一推送任务池；有可用产能时立即领取，否则按所属通道正常排队或等待前置条件。",
    PUSH_STATUS_RUNNING: "任务已被执行 worker 领取，正在执行外部动作。",
    PUSH_STATUS_SUCCEEDED: "外部动作已执行成功，并已有成功 attempt 记录。",
    PUSH_STATUS_SENT: "主发送链路已完成发送。",
    PUSH_STATUS_SIMULATED: "适配器完成模拟执行，没有发生真实外部调用。",
    PUSH_STATUS_UNKNOWN_AFTER_DISPATCH: "外部调用结果不确定，必须先对账且不会自动重试。",
    PUSH_STATUS_FAILED: "主发送链路未成功完成。",
    PUSH_STATUS_SENT_WITH_SHADOW_WARNING: "主发送链路已完成发送，但影子链路或观测链路存在异常。",
    PUSH_STATUS_SHADOW_FAILED_NOT_BUSINESS_FAILED: "仅发现影子链路失败，未发现对应主发送记录；不计入业务发送失败。",
}

_RAW_TO_STANDARD = {
    "planned": PUSH_STATUS_PENDING,
    "approved": PUSH_STATUS_PENDING,
    "queued": PUSH_STATUS_PENDING,
    "dispatching": PUSH_STATUS_RUNNING,
    "succeeded": PUSH_STATUS_SENT,
    "simulated": PUSH_STATUS_SIMULATED,
    "unknown_after_dispatch": PUSH_STATUS_UNKNOWN_AFTER_DISPATCH,
    "failed_retryable": PUSH_STATUS_FAILED,
    "failed_terminal": PUSH_STATUS_FAILED,
    "blocked": PUSH_STATUS_FAILED,
    "cancelled": PUSH_STATUS_FAILED,
    "expired": PUSH_STATUS_FAILED,
    "sent": PUSH_STATUS_SENT,
    "sent_with_shadow_warning": PUSH_STATUS_SENT_WITH_SHADOW_WARNING,
    "shadow_failed_not_business_failed": PUSH_STATUS_SHADOW_FAILED_NOT_BUSINESS_FAILED,
}

_ATTEMPT_TO_STANDARD = {
    "succeeded": PUSH_STATUS_SUCCEEDED,
    "simulated": PUSH_STATUS_SIMULATED,
    "unknown_after_dispatch": PUSH_STATUS_UNKNOWN_AFTER_DISPATCH,
    "failed_retryable": PUSH_STATUS_FAILED,
    "failed_terminal": PUSH_STATUS_FAILED,
    "blocked": PUSH_STATUS_FAILED,
    "skipped": PUSH_STATUS_FAILED,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def standard_push_status(raw_status: Any) -> str:
    return _RAW_TO_STANDARD.get(_text(raw_status), PUSH_STATUS_FAILED)


def standard_attempt_status(raw_status: Any) -> str:
    return _ATTEMPT_TO_STANDARD.get(_text(raw_status), standard_push_status(raw_status))


def push_status_label(status: Any) -> str:
    standard = standard_push_status(status)
    return PUSH_STATUS_LABELS[standard]


def attempt_status_label(status: Any) -> str:
    standard = standard_attempt_status(status)
    return PUSH_STATUS_LABELS[standard]


def status_matches(raw_status: Any, expected: Any) -> bool:
    expected_text = _text(expected)
    if not expected_text:
        return True
    if expected_text in STANDARD_PUSH_STATUSES:
        return standard_push_status(raw_status) == expected_text
    return _text(raw_status) == expected_text


def status_definitions_payload() -> list[dict[str, str]]:
    return [
        {"key": status, "label": PUSH_STATUS_LABELS[status], "definition": PUSH_STATUS_DEFINITIONS[status]}
        for status in STANDARD_PUSH_STATUSES
    ]
