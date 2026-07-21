from __future__ import annotations

import re
from typing import Any

from aicrm_next.shared.sensitive_data import SECRET_MASK, redact_sensitive_data

from .adapters import webhook_execution_settings, wecom_execution_settings
from .jobs import external_effect_scheduler_state
from .models import ExternalEffectAttempt, ExternalEffectJob, ExternalEffectTestReceipt
from .realtime import realtime_wakeup_state
from .service import ExternalEffectService
from .test_receiver import test_execution_only_enabled, test_receiver_enabled

ROUTE_OWNER = "ai_crm_next"
JOB_DISPLAY_FIELDS = [
    "effect_type",
    "status",
    "target_type",
    "target_id",
    "business_type",
    "business_id",
    "trace_id",
    "idempotency_key",
    "attempt_count",
    "side_effect_executed",
    "provider_result_received",
    "reconciliation_required",
    "last_error_code",
    "last_error_message",
    "created_at",
    "updated_at",
]
EXPECTED_INDEXES = [
    "uq_external_effect_job_tenant_idempotency",
    "idx_external_effect_job_due",
    "idx_external_effect_job_target",
    "idx_external_effect_job_business",
    "idx_external_effect_job_trace",
    "idx_external_effect_job_effect_type",
    "idx_external_effect_attempt_job",
    "idx_external_effect_attempt_trace",
    "idx_external_effect_job_lease_due",
    "idx_external_effect_job_reconciliation",
    "uq_external_effect_attempt_open_job",
    "idx_external_effect_job_cancel_requested",
]
PROBLEM_STATUSES = {"failed_retryable", "failed_terminal", "blocked", "dispatching", "unknown_after_dispatch"}
REDACTED = SECRET_MASK
SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "webhook_url",
    "target_url",
    "external_userid",
    "external_user_id",
    "openid",
    "open_id",
    "unionid",
    "union_id",
    "mobile",
    "phone",
    "transaction_id",
    "provider_transaction",
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"\bwm[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:openid|unionid)[A-Za-z0-9_-]{4,}\b", re.IGNORECASE),
    re.compile(r"\b420000\d{10,}\b"),
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}\b", re.IGNORECASE),
    re.compile(r"/api/external-effects/test-receiver/[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)


def _sensitive_scalar(value: Any) -> bool:
    if value in (None, ""):
        return False
    text = str(value)
    return any(pattern.search(text) for pattern in SENSITIVE_VALUE_PATTERNS)


def redact_external_effect_payload(value: Any, *, key: str = "") -> Any:
    if _sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {str(item_key): redact_external_effect_payload(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_external_effect_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_external_effect_payload(item) for item in value]
    shared_redacted = redact_sensitive_data(value, key=key)
    if shared_redacted != value:
        return shared_redacted
    if _sensitive_scalar(value):
        return REDACTED
    return value


def redact_external_effect_admin_response(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for item_key, item_value in value.items():
            if item_key == "payload_json" and isinstance(item_value, dict):
                redacted[item_key] = redact_external_effect_payload(item_value)
            elif item_key in {"payload_summary_json", "request_summary_json", "response_summary_json", "body_json", "headers_summary_json"} and isinstance(
                item_value, dict
            ):
                redacted[item_key] = redact_external_effect_payload(item_value)
            else:
                redacted[item_key] = redact_external_effect_admin_response(redact_external_effect_payload(item_value, key=str(item_key)))
        return redacted
    if isinstance(value, list):
        return [redact_external_effect_admin_response(item) for item in value]
    if isinstance(value, tuple):
        return [redact_external_effect_admin_response(item) for item in value]
    return redact_external_effect_payload(value)


def _safe_value(value: Any, *, key: str = "") -> Any:
    return redact_external_effect_payload(value, key=key)


def external_effect_job_list_item(job: ExternalEffectJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "tenant_id": job.tenant_id,
        "effect_type": job.effect_type,
        "adapter_name": job.adapter_name,
        "operation": job.operation,
        "status": job.status,
        "row_version": job.row_version,
        "execution_mode": job.execution_mode,
        "target_type": job.target_type,
        "target_id": _safe_value(job.target_id, key="target_id"),
        "business_type": job.business_type,
        "business_id": _safe_value(job.business_id, key="business_id"),
        "source_module": job.source_module,
        "source_route": _safe_value(job.source_route, key="source_route"),
        "source_event_id": _safe_value(job.source_event_id, key="source_event_id"),
        "source_command_id": _safe_value(job.source_command_id, key="source_command_id"),
        "trace_id": _safe_value(job.trace_id, key="trace_id"),
        "request_id": _safe_value(job.request_id, key="request_id"),
        "correlation_id": _safe_value(job.correlation_id, key="correlation_id"),
        "idempotency_key": _safe_value(job.idempotency_key, key="idempotency_key"),
        "actor_id": _safe_value(job.actor_id, key="actor_id"),
        "actor_type": job.actor_type,
        "risk_level": job.risk_level,
        "requires_approval": job.requires_approval,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "last_attempt_id": _safe_value(job.last_attempt_id, key="last_attempt_id"),
        "last_error_code": job.last_error_code,
        "last_error_message": _safe_value(job.last_error_message, key="last_error_message"),
        "scheduled_at": job.scheduled_at,
        "next_retry_at": job.next_retry_at,
        "locked_at": job.locked_at,
        "locked_by": _safe_value(job.locked_by, key="locked_by"),
        "lease_expires_at": job.lease_expires_at,
        "dispatch_started_at": job.dispatch_started_at,
        "side_effect_executed": job.side_effect_executed,
        "provider_result_received": job.provider_result_received,
        "reconciliation_required": job.reconciliation_required,
        "result_summary_json": redact_external_effect_payload(dict(job.result_summary_json or {})),
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "approved_at": job.approved_at,
        "executed_at": job.executed_at,
        "completed_at": job.completed_at,
        "cancelled_at": job.cancelled_at,
        "cancel_requested_at": job.cancel_requested_at,
        "cancel_requested_by": _safe_value(job.cancel_requested_by, key="cancel_requested_by"),
        "cancel_reason": _safe_value(job.cancel_reason, key="cancel_reason"),
        "payload_summary_json": redact_external_effect_payload(dict(job.payload_summary_json or {})),
        "payload_redacted": True,
        "payload_json_redacted": True,
    }


def external_effect_job_detail_item(job: ExternalEffectJob) -> dict[str, Any]:
    item = external_effect_job_list_item(job)
    item["payload_json"] = redact_external_effect_payload(dict(job.payload_json or {}))
    return item


def external_effect_attempt_item(attempt: ExternalEffectAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "attempt_id": _safe_value(attempt.attempt_id, key="attempt_id"),
        "job_id": attempt.job_id,
        "adapter_name": attempt.adapter_name,
        "adapter_mode": attempt.adapter_mode,
        "operation": attempt.operation,
        "trace_id": _safe_value(attempt.trace_id, key="trace_id"),
        "request_id": _safe_value(attempt.request_id, key="request_id"),
        "status": attempt.status,
        "request_summary_json": redact_external_effect_payload(dict(attempt.request_summary_json or {})),
        "response_summary_json": redact_external_effect_payload(dict(attempt.response_summary_json or {})),
        "error_code": attempt.error_code,
        "error_message": _safe_value(attempt.error_message, key="error_message"),
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
    }


def external_effect_receipt_item(receipt: ExternalEffectTestReceipt) -> dict[str, Any]:
    return {
        "id": receipt.id,
        "receipt_id": _safe_value(receipt.receipt_id, key="receipt_id"),
        "event_id": _safe_value(receipt.event_id, key="event_id"),
        "job_id": receipt.job_id,
        "effect_type": receipt.effect_type,
        "trace_id": _safe_value(receipt.trace_id, key="trace_id"),
        "idempotency_key": _safe_value(receipt.idempotency_key, key="idempotency_key"),
        "target_type": receipt.target_type,
        "target_id": _safe_value(receipt.target_id, key="target_id"),
        "business_type": receipt.business_type,
        "business_id": _safe_value(receipt.business_id, key="business_id"),
        "request_method": receipt.request_method,
        "request_path": _safe_value(receipt.request_path, key="request_path"),
        "headers_summary_json": redact_external_effect_payload(dict(receipt.headers_summary_json or {})),
        "payload_summary_json": redact_external_effect_payload(dict(receipt.payload_summary_json or {})),
        "payload_hash": receipt.payload_hash,
        "body_json": redact_external_effect_payload(dict(receipt.body_json or {})),
        "signature_valid": receipt.signature_valid,
        "response_status": receipt.response_status,
        "received_at": receipt.received_at,
    }


def external_effect_filters(params: dict[str, Any]) -> dict[str, str]:
    return {
        "effect_type": _text(params.get("effect_type")),
        "status": _text(params.get("status")),
        "target_type": _text(params.get("target_type")),
        "target_id": _text(params.get("target_id")),
        "business_type": _text(params.get("business_type")),
        "business_id": _text(params.get("business_id")),
        "trace_id": _text(params.get("trace_id")),
    }


def _public_filters(filters: dict[str, Any]) -> dict[str, str]:
    return {key: _text(_safe_value(value, key=key)) for key, value in filters.items() if _text(value)}


def _execution_summary() -> dict[str, Any]:
    settings = webhook_execution_settings()
    wecom_settings = wecom_execution_settings()
    allowed = [item for item in settings["allowed_types"] if item in set(settings["supported_types"])]
    real_execution_enabled = bool(settings["enabled"] and allowed)
    if not settings["enabled"]:
        mode = "disabled"
    elif real_execution_enabled:
        mode = "executable"
    else:
        mode = "shadow"
    return {
        "execution_mode": mode,
        "real_execution_enabled": real_execution_enabled,
        "allowed_effect_types": list(settings["allowed_types"]),
        "executable_effect_types": allowed,
        "supported_effect_types": list(settings["supported_types"]),
        "webhook_execution": settings,
        "wecom_execution": wecom_settings,
    }


def build_external_effect_jobs_payload(
    params: dict[str, Any],
    *,
    service: ExternalEffectService | None = None,
    current_base_url: str = "",
) -> dict[str, Any]:
    service = service or ExternalEffectService()
    filters = external_effect_filters(params)
    limit = _bounded_int(params.get("limit"), default=50, minimum=1, maximum=200)
    offset = _bounded_int(params.get("offset"), default=0, minimum=0, maximum=100000)
    items, total = service.list_jobs(filters, limit=limit, offset=offset)
    counts = service.count_jobs(filters)
    queue_metrics = service.queue_metrics(filters)
    selected_job_id = _bounded_int(params.get("job_id"), default=0, minimum=0, maximum=10**12)
    selected_job = service.get(selected_job_id) if selected_job_id else None
    attempts = service.list_attempts(selected_job_id) if selected_job else []
    receipt_items, receipt_total = service.list_test_receipts({}, limit=10, offset=0)
    recent_jobs, _recent_jobs_total = service.list_jobs({}, limit=50, offset=0)
    test_jobs = [item for item in recent_jobs if item.payload_json.get("execution_scope") == "test_loopback"][:10]
    receipt_metrics = service.test_receipt_metrics()
    return {
        "ok": True,
        "items": [external_effect_job_list_item(item) for item in items],
        "total": total,
        "filters": _public_filters(filters),
        "limit": limit,
        "offset": offset,
        "counts": counts,
        "queue_metrics": queue_metrics,
        "selected_job": external_effect_job_list_item(selected_job) if selected_job else None,
        "attempts": [external_effect_attempt_item(attempt) for attempt in attempts],
        "display_fields": list(JOB_DISPLAY_FIELDS),
        "route_owner": ROUTE_OWNER,
        "real_external_call_executed": False,
        "test_receiver_enabled": test_receiver_enabled(),
        "test_execution_only": test_execution_only_enabled(),
        "current_base_url_detected": current_base_url,
        "recent_test_jobs": [external_effect_job_list_item(item) for item in test_jobs],
        "recent_test_receipts": [external_effect_receipt_item(item) for item in receipt_items],
        "test_receipt_total": receipt_total,
        **receipt_metrics,
        **_execution_summary(),
    }


def build_external_effect_diagnostics_payload(
    params: dict[str, Any] | None = None,
    *,
    service: ExternalEffectService | None = None,
    current_base_url: str = "",
) -> dict[str, Any]:
    service = service or ExternalEffectService()
    filters = external_effect_filters(dict(params or {}))
    counts = service.count_jobs(filters)
    queue_metrics = service.queue_metrics(filters)
    execution = _execution_summary()
    receipt_metrics = service.test_receipt_metrics()
    return {
        "ok": True,
        "route_owner": ROUTE_OWNER,
        "capability_owner": "ai_crm_next/platform_foundation",
        "real_external_call_executed": False,
        "real_execution_enabled": execution["real_execution_enabled"],
        "allowed_effect_types": execution["allowed_effect_types"],
        "execution_mode": execution["execution_mode"],
        "test_receiver_enabled": test_receiver_enabled(),
        "test_execution_only": test_execution_only_enabled(),
        "current_base_url_detected": current_base_url,
        **receipt_metrics,
        "webhook_execution": execution["webhook_execution"],
        "wecom_execution": execution["wecom_execution"],
        "realtime_wakeup": realtime_wakeup_state(),
        "scheduler": external_effect_scheduler_state(),
        "execution_default": "dry_run",
        "adapter_execution_default": "blocked",
        **queue_metrics,
        "schema_contract": {
            "tables": ["external_effect_job", "external_effect_attempt"],
            "idempotency_constraint": "UNIQUE (tenant_id, idempotency_key)",
            "expected_indexes": list(EXPECTED_INDEXES),
            "required_display_fields": list(JOB_DISPLAY_FIELDS),
        },
        "counts": counts,
        "queue_metrics": queue_metrics,
        "filters": _public_filters(filters),
    }


def _problem_label(job: ExternalEffectJob) -> str:
    if job.status == "failed_retryable":
        return "retryable_failure"
    if job.status == "failed_terminal":
        return "terminal_failure"
    if job.status == "blocked":
        return "blocked_by_policy"
    if job.status == "unknown_after_dispatch":
        return "provider_outcome_requires_reconciliation"
    if job.status == "dispatching":
        return "possibly_stuck_dispatching"
    if job.last_error_code or job.last_error_message:
        return "has_last_error"
    return ""


def _troubleshooting_job_item(job: ExternalEffectJob) -> dict[str, Any]:
    item = external_effect_job_list_item(job)
    item["problem_label"] = _problem_label(job)
    return item


def _troubleshooting_attempt_item(attempt: ExternalEffectAttempt) -> dict[str, Any]:
    return external_effect_attempt_item(attempt)


def _troubleshooting_filters(params: dict[str, Any] | None = None) -> dict[str, str]:
    raw = dict(params or {})
    return {
        **external_effect_filters(raw),
        "last_error_code": _text(raw.get("last_error_code")),
        "idempotency_key": _text(raw.get("idempotency_key")),
    }


def _problem_only(params: dict[str, Any] | None = None) -> bool:
    value = (params or {}).get("problem_only")
    if value in (None, ""):
        return True
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _matches_extra_filters(job: ExternalEffectJob, filters: dict[str, str], *, problem_only: bool) -> bool:
    if problem_only and job.status not in PROBLEM_STATUSES and not job.last_error_code and not job.last_error_message:
        return False
    if filters.get("last_error_code") and filters["last_error_code"] not in job.last_error_code:
        return False
    if filters.get("idempotency_key") and filters["idempotency_key"] not in job.idempotency_key:
        return False
    return True


def build_troubleshooting_summary_payload(
    params: dict[str, Any] | None = None,
    *,
    service: ExternalEffectService | None = None,
) -> dict[str, Any]:
    service = service or ExternalEffectService()
    filters = _troubleshooting_filters(params)
    base_filters = {
        key: value for key, value in filters.items() if key in {"effect_type", "status", "target_type", "target_id", "business_type", "business_id", "trace_id"}
    }
    counts = service.count_jobs(base_filters)
    queue_metrics = service.queue_metrics(base_filters)
    by_status = dict(counts.get("by_status") or {})
    problem_count = sum(int(by_status.get(status, 0) or 0) for status in PROBLEM_STATUSES)
    return {
        "ok": True,
        "route_owner": ROUTE_OWNER,
        "capability_owner": "ai_crm_next/platform_foundation",
        "source": "external_effect_job/external_effect_attempt",
        "purpose": "external_effect_queue_troubleshooting",
        "real_external_call_executed": False,
        "problem_count": problem_count,
        "counts": counts,
        "queue_metrics": queue_metrics,
        "problem_statuses": sorted(PROBLEM_STATUSES),
        "filters": _public_filters(filters),
        **_execution_summary(),
    }


def build_troubleshooting_jobs_payload(
    params: dict[str, Any] | None = None,
    *,
    service: ExternalEffectService | None = None,
) -> dict[str, Any]:
    service = service or ExternalEffectService()
    filters = _troubleshooting_filters(params)
    base_filters = {
        key: value for key, value in filters.items() if key in {"effect_type", "status", "target_type", "target_id", "business_type", "business_id", "trace_id"}
    }
    limit = _bounded_int((params or {}).get("limit"), default=50, minimum=1, maximum=200)
    offset = _bounded_int((params or {}).get("offset"), default=0, minimum=0, maximum=100000)
    problem_only = _problem_only(params)
    fetch_limit = min(1000, max(200, limit + offset + 50))
    jobs, _total = service.list_jobs(base_filters, limit=fetch_limit, offset=0)
    filtered = [job for job in jobs if _matches_extra_filters(job, filters, problem_only=problem_only)]
    page = filtered[offset : offset + limit]
    return {
        "ok": True,
        "route_owner": ROUTE_OWNER,
        "source": "external_effect_job",
        "purpose": "external_effect_queue_troubleshooting",
        "items": [_troubleshooting_job_item(job) for job in page],
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
        "filters": _public_filters(filters),
        "problem_only": problem_only,
        "real_external_call_executed": False,
    }


def build_troubleshooting_job_detail_payload(
    job_id: int,
    *,
    service: ExternalEffectService | None = None,
) -> dict[str, Any] | None:
    service = service or ExternalEffectService()
    job = service.get(job_id)
    if not job:
        return None
    return {
        "ok": True,
        "route_owner": ROUTE_OWNER,
        "source": "external_effect_job/external_effect_attempt",
        "purpose": "external_effect_queue_troubleshooting",
        "job": _troubleshooting_job_item(job),
        "attempts": [_troubleshooting_attempt_item(attempt) for attempt in service.list_attempts(job_id)],
        "real_external_call_executed": False,
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))
