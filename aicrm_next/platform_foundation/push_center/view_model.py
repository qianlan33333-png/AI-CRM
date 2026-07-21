from __future__ import annotations

from typing import Any

from aicrm_next.shared.admin_read_fallback import admin_read_unavailable_payload

from . import ROUTE_OWNER
from .projection import EFFECTIVE_STATUS_LABELS
from .repository import PushCenterRepository
from .status_mapper import status_definitions_payload
from .sql_read_model import InvalidPushCenterCursor

FILTER_KEYS = (
    "section",
    "effect_type",
    "status",
    "business_type",
    "business_id",
    "target_type",
    "target_id",
    "external_userid",
    "owner_userid",
    "trace_id",
    "idempotency_key",
    "source_module",
    "source_route",
    "created_from",
    "created_to",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, *, default: int, minimum: int = 0, maximum: int = 200) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def push_center_filters(params: dict[str, Any] | None = None) -> dict[str, str]:
    raw = dict(params or {})
    return {key: _text(raw.get(key)) for key in FILTER_KEYS}


def public_filters(filters: dict[str, Any]) -> dict[str, str]:
    return {key: _text(value) for key, value in filters.items() if _text(value)}


def job_list_item(job: dict[str, Any], *, include_linked_records: bool = False) -> dict[str, Any]:
    payload = dict(job)
    if not include_linked_records:
        payload.pop("linked_records", None)
    payload.setdefault("effective_status", payload.get("status"))
    payload.setdefault("effective_status_label", EFFECTIVE_STATUS_LABELS.get(_text(payload.get("effective_status")), _text(payload.get("status_label"))))
    payload.setdefault("status_label", payload.get("effective_status_label"))
    payload.setdefault("execution_id", "")
    payload.setdefault("lane", "")
    payload.setdefault("available_at", payload.get("next_retry_at") or payload.get("scheduled_at") or payload.get("created_at"))
    payload.setdefault("queue_state", _queue_state(payload))
    payload.setdefault("delivery_state", _delivery_state(payload))
    payload.setdefault("row_version", 0)
    return payload


def _queue_state(payload: dict[str, Any]) -> str:
    raw_status = _text(payload.get("raw_status") or payload.get("status"))
    if raw_status == "unknown_after_dispatch":
        return "unknown"
    if raw_status in {"claimed", "running", "dispatching"}:
        return "running"
    if raw_status == "failed_retryable":
        return "retry_wait"
    if raw_status in {"planned", "approved", "waiting_approval", "blocked"}:
        return "held"
    if raw_status in {"queued", "pending"}:
        return "waiting"
    return "terminal"


def _delivery_state(payload: dict[str, Any]) -> str:
    raw_status = _text(payload.get("raw_status") or payload.get("status"))
    effect_type = _text(payload.get("effect_type"))
    if raw_status == "unknown_after_dispatch":
        return "unknown"
    if raw_status in {"failed", "failed_retryable", "failed_terminal", "blocked", "cancelled", "expired"}:
        return "failed"
    if raw_status in {"succeeded", "sent"}:
        if effect_type.startswith("wecom.message.") or effect_type in {"wecom.welcome_message.send", "broadcast_job", "broadcast_job.group", "broadcast_job.group_ops", "broadcast_job.private"}:
            return "provider_accepted"
        return "not_applicable"
    return "pending"


def build_sections_payload(params: dict[str, Any] | None = None, *, repository: PushCenterRepository | None = None) -> dict[str, Any]:
    repository = repository or PushCenterRepository()
    filters = push_center_filters(params)
    try:
        sections = repository.sections(filters)
    except Exception as exc:
        return _read_unavailable_payload(filters, exc, include_sections=True)
    return {
        "ok": True,
        "sections": sections,
        "status_definitions": status_definitions_payload(),
        "filters": public_filters(filters),
        "route_owner": ROUTE_OWNER,
    }


def build_jobs_payload(params: dict[str, Any] | None = None, *, repository: PushCenterRepository | None = None) -> dict[str, Any]:
    repository = repository or PushCenterRepository()
    filters = push_center_filters(params)
    limit = _int((params or {}).get("limit"), default=50, minimum=1, maximum=200)
    offset = _int((params or {}).get("offset"), default=0, minimum=0, maximum=100000)
    cursor = _text((params or {}).get("cursor"))
    try:
        jobs, total, counts, sections, next_cursor, has_more = repository.list_jobs_with_summary(
            filters,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
    except InvalidPushCenterCursor:
        return {
            "ok": False,
            "error": "invalid_push_center_cursor",
            "items": [],
            "total": 0,
            "counts": {},
            "sections": [],
            "status_definitions": status_definitions_payload(),
            "filters": public_filters(filters),
            "limit": limit,
            "offset": 0,
            "next_cursor": "",
            "has_more": False,
            "route_owner": ROUTE_OWNER,
            "real_external_call_executed": False,
        }
    except Exception as exc:
        return _read_unavailable_payload(filters, exc, limit=limit, offset=offset, include_sections=True)
    return {
        "ok": True,
        "items": [job_list_item(job) for job in jobs],
        "total": total,
        "counts": counts,
        "sections": sections,
        "status_definitions": status_definitions_payload(),
        "filters": public_filters(filters),
        "limit": limit,
        "offset": offset,
        "cursor": cursor,
        "next_cursor": next_cursor,
        "has_more": bool(has_more),
        "route_owner": ROUTE_OWNER,
        "real_external_call_executed": False,
    }


def build_stats_payload(params: dict[str, Any] | None = None, *, repository: PushCenterRepository | None = None) -> dict[str, Any]:
    repository = repository or PushCenterRepository()
    filters = push_center_filters(params)
    try:
        counts, sections = repository.summary(filters)
    except Exception as exc:
        return _read_unavailable_payload(filters, exc, include_sections=True)
    return {
        "ok": True,
        "counts": counts,
        "sections": sections,
        "status_definitions": status_definitions_payload(),
        "filters": public_filters(filters),
        "route_owner": ROUTE_OWNER,
        "real_external_call_executed": False,
    }


def build_job_detail_payload(job_id: int | str, *, repository: PushCenterRepository | None = None) -> dict[str, Any] | None:
    repository = repository or PushCenterRepository()
    try:
        job = repository.get_job(job_id)
    except Exception:
        return None
    if not job:
        return None
    job_payload = job_list_item(job, include_linked_records=True)
    linked_records = job_payload.get("linked_records") if isinstance(job_payload.get("linked_records"), dict) else {}
    return {
        "ok": True,
        "job": job_payload,
        "attempts": list(linked_records.get("external_effect_attempts") or repository.list_attempts(job_id)),
        "linked_records": linked_records,
        "source": {
            "source_type": "push_center_projection",
            "external_effect_job_missing": False,
            "legacy_readonly": False,
        },
        "route_owner": ROUTE_OWNER,
        "real_external_call_executed": False,
    }


def _read_unavailable_payload(
    filters: dict[str, Any],
    exc: Exception,
    *,
    limit: int = 50,
    offset: int = 0,
    include_sections: bool = False,
) -> dict[str, Any]:
    empty_counts = {
        "total": 0,
        "by_effective_status": {},
        "by_status": {},
        "by_section": {},
        "pending": 0,
        "running": 0,
        "sent": 0,
        "failed": 0,
    }
    extra: dict[str, Any] = {
        "counts": empty_counts,
        "status_definitions": status_definitions_payload(),
        "filters": public_filters(filters),
        "limit": limit,
        "offset": offset,
    }
    if include_sections:
        extra["sections"] = []
    return admin_read_unavailable_payload(
        capability_owner="ai_crm_next/platform_foundation/push_center",
        page_error="推送中心读模型暂不可用，请稍后重试。",
        exc=exc,
        items_keys=("items",),
        count_keys=("total",),
        extra=extra,
    )


def _raw_statuses(records: list[dict[str, Any]]) -> list[str]:
    statuses: list[str] = []
    for record in records:
        status = _text(record.get("raw_status") or record.get("status"))
        if status:
            statuses.append(status)
    return statuses


def _last_error(job: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, str]:
    for source in [job, *list(reversed(attempts))]:
        code = _text(source.get("last_error_code") or source.get("error_code"))
        message = _text(source.get("last_error_message") or source.get("error_message"))
        if code or message:
            return {"code": code, "message": message}
    return {"code": "", "message": ""}


def _reconciliation_decision(job: dict[str, Any], attempts: list[dict[str, Any]], linked_records: dict[str, Any]) -> dict[str, Any]:
    effective_status = _text(job.get("effective_status") or job.get("status"))
    raw_statuses = {
        "external_effect_jobs": _raw_statuses(list(linked_records.get("external_effect_jobs") or [])),
        "broadcast_jobs": _raw_statuses(list(linked_records.get("broadcast_jobs") or [])),
        "attempts": _raw_statuses(attempts),
    }
    retryable = "failed_retryable" in raw_statuses["external_effect_jobs"] or "failed_retryable" in raw_statuses["attempts"]
    has_broadcast_sent = "sent" in raw_statuses["broadcast_jobs"]
    has_attempt_failure = any(status in {"failed", "failed_retryable", "failed_terminal", "blocked", "cancelled"} for status in raw_statuses["attempts"])

    if effective_status == "sent":
        return {
            "business_explanation": "主发送链路已完成，当前不需要运营处理。",
            "retryable": False,
            "operator_action_required": False,
            "next_action_label": "无需操作",
        }
    if effective_status == "succeeded":
        return {
            "business_explanation": "外部动作已成功完成；这不代表消息已经送达，需结合 delivery_state 或服务商回执判断。",
            "retryable": False,
            "operator_action_required": False,
            "next_action_label": "查看交付状态",
        }
    if effective_status == "sent_with_shadow_warning":
        return {
            "business_explanation": "主发送链路已完成，但影子链路或观测链路存在异常；不要把它误判为业务发送失败。",
            "retryable": False,
            "operator_action_required": True,
            "next_action_label": "检查影子链路",
        }
    if effective_status == "simulated":
        return {
            "business_explanation": "任务仅完成模拟执行，没有发生真实外部发送。",
            "retryable": False,
            "operator_action_required": False,
            "next_action_label": "无需操作",
        }
    if effective_status == "unknown_after_dispatch":
        return {
            "business_explanation": "外部调用结果不确定；必须先核对服务商回执，禁止自动重试。",
            "retryable": False,
            "operator_action_required": True,
            "next_action_label": "核对服务商结果",
        }
    if effective_status == "shadow_failed_not_business_failed":
        return {
            "business_explanation": "仅发现影子链路失败，尚未发现对应主发送记录；需要确认主发送是否由其他链路完成。",
            "retryable": False,
            "operator_action_required": True,
            "next_action_label": "确认主发送记录",
        }
    if effective_status == "failed":
        return {
            "business_explanation": "主发送或外部动作未成功完成；请根据错误原因决定重试或人工处理。",
            "retryable": retryable,
            "operator_action_required": True,
            "next_action_label": "重试" if retryable else "人工处理",
        }
    if effective_status == "running":
        return {
            "business_explanation": "任务已被外部动作 worker 领取，等待执行结果。",
            "retryable": False,
            "operator_action_required": False,
            "next_action_label": "等待执行完成",
        }
    return {
        "business_explanation": "任务已进入推送中心；有空闲产能时立即领取，否则等待审批、前置条件或所属通道产能。",
        "retryable": False,
        "operator_action_required": bool(has_attempt_failure and not has_broadcast_sent),
        "next_action_label": "正常排队",
    }


def build_job_reconciliation_payload(job_id: int | str, *, repository: PushCenterRepository | None = None) -> dict[str, Any] | None:
    detail = build_job_detail_payload(job_id, repository=repository)
    if not detail:
        return None
    job = dict(detail.get("job") or {})
    linked_records = dict(detail.get("linked_records") or {})
    attempts = list(detail.get("attempts") or [])
    decision = _reconciliation_decision(job, attempts, linked_records)
    linked_record_counts = dict(job.get("linked_record_counts") or {})
    external_jobs = list(linked_records.get("external_effect_jobs") or [])
    broadcast_jobs = list(linked_records.get("broadcast_jobs") or [])
    outbound_tasks = list(linked_records.get("outbound_tasks") or [])
    return {
        "ok": True,
        "reconciliation": {
            "projection_id": job.get("projection_id") or job.get("id"),
            "display_id": job.get("display_id") or "",
            "effective_status": job.get("effective_status") or job.get("status"),
            "effective_status_label": job.get("effective_status_label") or job.get("status_label") or "",
            "business_explanation": decision["business_explanation"],
            "retryable": decision["retryable"],
            "operator_action_required": decision["operator_action_required"],
            "next_action_label": decision["next_action_label"],
            "last_error": _last_error(job, attempts),
            "business_context": {
                "section": job.get("section") or "",
                "section_label": job.get("section_label") or "",
                "effect_type": job.get("effect_type") or "",
                "business_type": job.get("business_type") or "",
                "business_id": job.get("business_id") or "",
                "target_type": job.get("target_type") or "",
                "target_id": job.get("target_id") or "",
                "trace_id": job.get("trace_id") or "",
                "idempotency_key": job.get("idempotency_key") or "",
                "source_module": job.get("source_module") or "",
                "source_route": job.get("source_route") or "",
            },
            "linked_record_counts": linked_record_counts,
            "evidence": {
                "external_effect_jobs": [
                    {
                        "id": item.get("id"),
                        "status": item.get("raw_status") or item.get("status"),
                        "execution_mode": item.get("execution_mode") or "",
                        "effect_type": item.get("effect_type") or "",
                        "last_error_code": item.get("last_error_code") or "",
                        "last_error_message": item.get("last_error_message") or "",
                    }
                    for item in external_jobs
                ],
                "external_effect_attempts": [
                    {
                        "id": item.get("id"),
                        "status": item.get("raw_status") or item.get("status"),
                        "adapter_mode": item.get("adapter_mode") or "",
                        "error_code": item.get("error_code") or "",
                        "error_message": item.get("error_message") or "",
                    }
                    for item in attempts
                ],
                "broadcast_jobs": [
                    {
                        "id": item.get("id"),
                        "status": item.get("raw_status") or item.get("status"),
                        "source_id": item.get("source_id") or "",
                        "trace_id": item.get("trace_id") or "",
                        "sent_count": item.get("sent_count"),
                        "failed_count": item.get("failed_count"),
                        "last_error": item.get("last_error_message") or item.get("last_error") or "",
                    }
                    for item in broadcast_jobs
                ],
                "outbound_tasks": [
                    {
                        "id": item.get("id"),
                        "status": item.get("status") or "",
                        "task_type": item.get("task_type") or "",
                        "trace_id": item.get("trace_id") or "",
                    }
                    for item in outbound_tasks
                ],
            },
        },
        "source": detail.get("source") or {},
        "route_owner": ROUTE_OWNER,
        "real_external_call_executed": False,
    }
