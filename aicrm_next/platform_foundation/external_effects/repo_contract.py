from __future__ import annotations

import json
import os
from dataclasses import fields
from datetime import datetime
from typing import Any

from aicrm_next.platform_foundation.external_calls import scrub_summary
from aicrm_next.shared.sensitive_data import redact_sensitive_text

from .models import (
    ExternalEffectAttempt,
    ExternalEffectCreateRequest,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
    ExternalEffectTestReceipt,
    public_datetime,
)


_MODEL_FIELD_NAMES: dict[type[Any], set[str]] = {}
EXTERNAL_EFFECT_LANES = frozenset(
    {"wecom_interactive", "wecom_bulk", "wecom_media", "outbound_webhook"}
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_error_message(value: Any) -> str:
    return redact_sensitive_text(_text(value))[:1000]


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str, separators=(",", ":"))


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(data) if isinstance(data, dict) else {}
    return {}


def _model_payload(model: type[Any], payload: dict[str, Any]) -> dict[str, Any]:
    field_names = _MODEL_FIELD_NAMES.setdefault(model, {field.name for field in fields(model)})
    return {key: value for key, value in payload.items() if key in field_names}


def _public_job(row: dict[str, Any] | None) -> ExternalEffectJob | None:
    if not row:
        return None
    payload = dict(row)
    for key in ("payload_json", "payload_summary_json", "result_summary_json"):
        payload[key] = _json_obj(payload.get(key))
    for key in (
        "scheduled_at",
        "next_retry_at",
        "locked_at",
        "lease_expires_at",
        "available_at",
        "heartbeat_at",
        "dispatch_started_at",
        "provider_call_started_at",
        "created_at",
        "updated_at",
        "approved_at",
        "executed_at",
        "completed_at",
        "cancelled_at",
        "cancel_requested_at",
        "hold_at",
    ):
        payload[key] = public_datetime(payload.get(key))
    payload["id"] = int(payload.get("id") or 0)
    payload["created_on_plan"] = bool(payload.get("created_on_plan"))
    payload["priority"] = int(payload.get("priority") or 0)
    payload["row_version"] = max(1, int(payload.get("row_version") or 1))
    payload["attempt_count"] = int(payload.get("attempt_count") or 0)
    payload["max_attempts"] = int(payload.get("max_attempts") or 0)
    payload["worker_generation"] = int(payload.get("worker_generation") or 0)
    payload["requires_approval"] = bool(payload.get("requires_approval"))
    payload["side_effect_executed"] = bool(payload.get("side_effect_executed"))
    payload["provider_result_received"] = bool(payload.get("provider_result_received"))
    payload["reconciliation_required"] = bool(payload.get("reconciliation_required"))
    return ExternalEffectJob(**_model_payload(ExternalEffectJob, payload))


def _public_attempt(row: dict[str, Any] | None) -> ExternalEffectAttempt | None:
    if not row:
        return None
    payload = dict(row)
    for key in ("request_summary_json", "response_summary_json"):
        payload[key] = _json_obj(payload.get(key))
    for key in ("provider_call_started_at", "started_at", "completed_at"):
        payload[key] = public_datetime(payload.get(key))
    payload["id"] = int(payload.get("id") or 0)
    payload["job_id"] = int(payload.get("job_id") or 0)
    payload["worker_generation"] = int(payload.get("worker_generation") or 0)
    return ExternalEffectAttempt(**_model_payload(ExternalEffectAttempt, payload))


def _public_receipt(row: dict[str, Any] | None) -> ExternalEffectTestReceipt | None:
    if not row:
        return None
    payload = dict(row)
    for key in ("headers_summary_json", "payload_summary_json", "body_json"):
        payload[key] = _json_obj(payload.get(key))
    payload["received_at"] = public_datetime(payload.get("received_at"))
    payload["id"] = int(payload.get("id") or 0)
    payload["job_id"] = int(payload.get("job_id") or 0)
    payload["response_status"] = int(payload.get("response_status") or 200)
    signature_valid = payload.get("signature_valid")
    payload["signature_valid"] = None if signature_valid is None else bool(signature_valid)
    return ExternalEffectTestReceipt(**_model_payload(ExternalEffectTestReceipt, payload))


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return scrub_summary(dict(payload or {}))


def _idempotency_key(request: ExternalEffectCreateRequest) -> str:
    explicit = _text(request.idempotency_key)
    if explicit:
        return explicit
    context_key = ":".join(
        item
        for item in (
            request.effect_type,
            request.target_type,
            request.target_id,
            request.business_type,
            request.business_id,
            request.source_command_id or request.context.request_id or request.context.trace_id,
        )
        if _text(item)
    )
    return context_key or f"{request.effect_type}:{request.target_type}:{request.target_id}"


def _initial_status(request: ExternalEffectCreateRequest) -> str:
    status = _text(request.status) or "queued"
    if request.requires_approval and status in {"queued", "approved"}:
        return "planned"
    return status


def _execution_lane(request: ExternalEffectCreateRequest) -> str:
    explicit = _text(request.lane)
    if explicit:
        if explicit not in EXTERNAL_EFFECT_LANES:
            raise ValueError(f"unsupported external effect lane: {explicit}")
        return explicit
    if request.effect_type == "wecom.media.upload":
        return "wecom_media"
    if request.effect_type == "wecom.message.broadcast.send":
        return "wecom_bulk"
    if request.effect_type.startswith("webhook.") or request.effect_type in {
        "feishu.webhook.notify",
        "openclaw.context.push",
    }:
        return "outbound_webhook"
    return "wecom_interactive"


def _rate_scope_key(request: ExternalEffectCreateRequest) -> str:
    explicit = _text(request.rate_scope_key)
    if explicit:
        return explicit
    payload = dict(request.payload or {})
    provider = _text(request.adapter_name) or "unknown_provider"
    corp_id = next(
        (
            _text(payload.get(key))
            for key in ("corp_id", "CorpId", "ToUserName", "wecom_corp_id")
            if _text(payload.get(key))
        ),
        (
            _text(os.getenv("WECOM_CORP_ID"))
            if provider.startswith("wecom")
            else ""
        )
        or _text(request.tenant_id)
        or "aicrm",
    )
    app_id = next(
        (
            _text(payload.get(key))
            for key in ("app_id", "agent_id", "wecom_agent_id")
            if _text(payload.get(key))
        ),
        (
            _text(os.getenv("WECOM_AGENT_ID"))
            if provider.startswith("wecom")
            else ""
        )
        or "default",
    )
    operation = _text(request.operation) or "unknown_operation"
    return ":".join((provider, corp_id, app_id, operation))


class ExternalEffectRepository:
    def direct_claims_allowed(self) -> bool:
        """Whether the pre-runtime worker may own claims in this repository.

        In-memory repositories have no generation-control table and keep their
        unit-test semantics.  The PostgreSQL repository overrides this with a
        fail-closed durable owner gate.
        """

        return True

    def create_job(self, request: ExternalEffectCreateRequest) -> ExternalEffectJob:
        raise NotImplementedError

    def get_job(self, job_id: int) -> ExternalEffectJob | None:
        raise NotImplementedError

    def list_jobs(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExternalEffectJob], int]:
        raise NotImplementedError

    def list_attempts(self, job_id: int) -> list[ExternalEffectAttempt]:
        raise NotImplementedError

    def get_attempt(self, attempt_id: str) -> ExternalEffectAttempt | None:
        raise NotImplementedError

    def get_attempt_provider_result(self, attempt_id: str, *, job_id: int | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def consume_attempt_provider_result(self, attempt_id: str, *, job_id: int) -> bool:
        raise NotImplementedError

    def list_attempts_for_jobs(self, job_ids: list[int]) -> dict[int, list[ExternalEffectAttempt]]:
        normalized = sorted({int(job_id) for job_id in job_ids})
        return {job_id: self.list_attempts(job_id) for job_id in normalized}

    def count_jobs(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def list_due_jobs(
        self,
        *,
        limit: int = 50,
        effect_types: list[str] | None = None,
        test_only: bool = False,
    ) -> list[ExternalEffectJob]:
        raise NotImplementedError

    def acquire_due_jobs(
        self,
        *,
        limit: int = 50,
        locked_by: str,
        effect_types: list[str] | None = None,
        test_only: bool = False,
        lease_seconds: int = 300,
    ) -> list[ExternalEffectJob]:
        raise NotImplementedError

    def acquire_job(self, job_id: int, *, locked_by: str, lease_seconds: int = 300) -> ExternalEffectJob | None:
        raise NotImplementedError

    def get_active_claim(self, job_id: int, *, lease_token: str) -> ExternalEffectJob | None:
        raise NotImplementedError

    def quarantine_stale_dispatching(self) -> int:
        raise NotImplementedError

    def begin_provider_attempt(
        self,
        *,
        job: ExternalEffectJob,
        request_summary: dict[str, Any],
    ) -> tuple[ExternalEffectJob, ExternalEffectAttempt] | None:
        raise NotImplementedError

    def complete_dispatch(
        self,
        *,
        job: ExternalEffectJob,
        result: ExternalEffectDispatchResult,
        next_retry_at: datetime | None = None,
    ) -> tuple[ExternalEffectJob, ExternalEffectAttempt] | None:
        raise NotImplementedError

    def mark_dispatch_unknown(
        self,
        *,
        job: ExternalEffectJob,
        error_code: str,
        error_message: str,
        side_effect_executed: bool = True,
        provider_result_received: bool = False,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def mark_dispatching(self, job_id: int, *, locked_by: str) -> ExternalEffectJob | None:
        raise NotImplementedError

    def mark_succeeded(self, job_id: int, *, attempt_id: str) -> ExternalEffectJob | None:
        raise NotImplementedError

    def mark_simulated(
        self,
        job_id: int,
        *,
        attempt_id: str,
        result_summary: dict[str, Any],
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def mark_failed_retryable(
        self,
        job_id: int,
        *,
        attempt_id: str,
        error_code: str,
        error_message: str,
        next_retry_at: datetime,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def mark_failed_terminal(
        self,
        job_id: int,
        *,
        attempt_id: str,
        error_code: str,
        error_message: str,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def mark_blocked(
        self,
        job_id: int,
        *,
        attempt_id: str,
        error_code: str,
        error_message: str,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def request_cancel(
        self,
        job_id: int,
        *,
        actor: str = "",
        reason: str = "",
        expected_version: int | None = None,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def settle_cancel(self, *, job: ExternalEffectJob) -> ExternalEffectJob | None:
        raise NotImplementedError

    def cancel_job(
        self,
        job_id: int,
        *,
        actor: str = "",
        reason: str = "",
        expected_version: int | None = None,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def enqueue_job(
        self,
        job_id: int,
        *,
        allow_unknown_after_dispatch: bool = False,
        extend_attempt_budget: bool = False,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def approve_job(self, job_id: int) -> ExternalEffectJob | None:
        raise NotImplementedError

    def authorize_allowlisted_canary(
        self,
        job_id: int,
        *,
        actor: str,
        reason: str,
        expected_version: int,
    ) -> ExternalEffectJob | None:
        raise NotImplementedError

    def record_attempt(
        self,
        *,
        job: ExternalEffectJob,
        status: str,
        adapter_mode: str,
        request_summary: dict[str, Any],
        response_summary: dict[str, Any],
        error_code: str = "",
        error_message: str = "",
    ) -> ExternalEffectAttempt:
        raise NotImplementedError

    def get_job_by_event_id(self, event_id: str) -> ExternalEffectJob | None:
        raise NotImplementedError

    def create_test_receipt(
        self,
        *,
        event_id: str,
        job: ExternalEffectJob,
        request_method: str,
        request_path: str,
        headers_summary: dict[str, Any],
        payload_summary: dict[str, Any],
        payload_hash: str,
        body_json: dict[str, Any],
        signature_valid: bool | None,
        response_status: int,
    ) -> ExternalEffectTestReceipt:
        raise NotImplementedError

    def list_test_receipts(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExternalEffectTestReceipt], int]:
        raise NotImplementedError

    def get_test_receipt(self, receipt_id: str) -> ExternalEffectTestReceipt | None:
        raise NotImplementedError

    def test_receipt_metrics(self) -> dict[str, Any]:
        raise NotImplementedError

    def list_record_only_jobs(self, *, limit: int = 100) -> list[ExternalEffectJob]:
        raise NotImplementedError


__all__ = [
    "ExternalEffectRepository",
    "_execution_lane",
    "_idempotency_key",
    "_initial_status",
    "_json_dumps",
    "_json_obj",
    "_payload_summary",
    "_public_attempt",
    "_public_job",
    "_public_receipt",
    "_safe_error_message",
    "_text",
]
