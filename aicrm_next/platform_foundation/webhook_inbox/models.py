from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bytes_value(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if value is None:
        return b""
    return str(value).encode("utf-8")


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


@dataclass(frozen=True)
class WebhookInboxItem:
    id: int = 0
    provider: str = ""
    event_family: str = ""
    route: str = ""
    method: str = "POST"
    tenant_id: str = "aicrm"
    corp_id: str = ""
    event_type: str = ""
    change_type: str = ""
    external_event_id: str = ""
    idempotency_key: str = ""
    execution_id: str = ""
    parent_execution_id: str = ""
    lane: str = "webhook_inbox"
    ordering_key: str = ""
    fairness_key: str = ""
    raw_query_json: dict[str, Any] | None = None
    raw_headers_json: dict[str, Any] | None = None
    raw_body: bytes = b""
    payload_json: dict[str, Any] | None = None
    payload_xml: str = ""
    payload_summary_json: dict[str, Any] | None = None
    processing_summary_json: dict[str, Any] | None = None
    status: str = ""
    attempt_count: int = 0
    max_attempts: int = 0
    locked_by: str = ""
    lease_token: str = ""
    worker_generation: int = 0
    policy_version: str = ""
    last_error_code: str = ""
    last_error_message: str = ""
    duplicate_count: int = 0
    received_at: datetime | None = None
    last_seen_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    next_retry_at: datetime | None = None
    available_at: datetime | None = None
    locked_at: datetime | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "WebhookInboxItem":
        return cls(
            id=int(row.get("id") or 0),
            provider=str(row.get("provider") or ""),
            event_family=str(row.get("event_family") or ""),
            route=str(row.get("route") or ""),
            method=str(row.get("method") or "POST"),
            tenant_id=str(row.get("tenant_id") or "aicrm"),
            corp_id=str(row.get("corp_id") or ""),
            event_type=str(row.get("event_type") or ""),
            change_type=str(row.get("change_type") or ""),
            external_event_id=str(row.get("external_event_id") or ""),
            idempotency_key=str(row.get("idempotency_key") or ""),
            execution_id=str(row.get("execution_id") or ""),
            parent_execution_id=str(row.get("parent_execution_id") or ""),
            lane=str(row.get("lane") or "webhook_inbox"),
            ordering_key=str(row.get("ordering_key") or ""),
            fairness_key=str(row.get("fairness_key") or ""),
            raw_query_json=_dict_value(row.get("raw_query_json")),
            raw_headers_json=_dict_value(row.get("raw_headers_json")),
            raw_body=_bytes_value(row.get("raw_body")),
            payload_json=_dict_value(row.get("payload_json")),
            payload_xml=str(row.get("payload_xml") or ""),
            payload_summary_json=_dict_value(row.get("payload_summary_json")),
            processing_summary_json=_dict_value(row.get("processing_summary_json")),
            status=str(row.get("status") or ""),
            attempt_count=int(row.get("attempt_count") or 0),
            max_attempts=int(row.get("max_attempts") or 0),
            locked_by=str(row.get("locked_by") or ""),
            lease_token=str(row.get("lease_token") or ""),
            worker_generation=int(row.get("worker_generation") or 0),
            policy_version=str(row.get("policy_version") or ""),
            last_error_code=str(row.get("last_error_code") or ""),
            last_error_message=str(row.get("last_error_message") or ""),
            duplicate_count=int(row.get("duplicate_count") or 0),
            received_at=_datetime_value(row.get("received_at")),
            last_seen_at=_datetime_value(row.get("last_seen_at")),
            started_at=_datetime_value(row.get("started_at")),
            finished_at=_datetime_value(row.get("finished_at")),
            created_at=_datetime_value(row.get("created_at")),
            updated_at=_datetime_value(row.get("updated_at")),
            next_retry_at=_datetime_value(row.get("next_retry_at")),
            available_at=_datetime_value(row.get("available_at")),
            locked_at=_datetime_value(row.get("locked_at")),
            lease_expires_at=_datetime_value(row.get("lease_expires_at")),
            heartbeat_at=_datetime_value(row.get("heartbeat_at")),
        )


@dataclass(frozen=True)
class WebhookInboxMetrics:
    due_count: int
    processing_count: int
    failed_retryable_count: int
    dead_letter_count: int
    oldest_received_age_seconds: int
    status_counts: dict[str, int]
    provider_distribution: list[dict[str, Any]]
    route_distribution: list[dict[str, Any]]
    recent_errors: list[dict[str, Any]]
    raw_open_count: int = 0
    held_count: int = 0
    eligible_due_count: int = 0
    scheduled_count: int = 0
    retry_wait_count: int = 0
    rate_limited_count: int = 0
    in_flight_count: int = 0
    unknown_count: int = 0
    dlq_count: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WebhookInboxMetrics":
        status_counts = payload.get("status_counts") or {}
        return cls(
            due_count=int(payload.get("due_count") or 0),
            processing_count=int(payload.get("processing_count") or 0),
            failed_retryable_count=int(payload.get("failed_retryable_count") or 0),
            dead_letter_count=int(payload.get("dead_letter_count") or 0),
            oldest_received_age_seconds=int(payload.get("oldest_received_age_seconds") or 0),
            status_counts={str(key): int(value or 0) for key, value in status_counts.items()},
            provider_distribution=list(payload.get("provider_distribution") or []),
            route_distribution=list(payload.get("route_distribution") or []),
            recent_errors=list(payload.get("recent_errors") or []),
            raw_open_count=int(payload.get("raw_open_count") or 0),
            held_count=int(payload.get("held_count") or 0),
            eligible_due_count=int(payload.get("eligible_due_count", payload.get("due_count")) or 0),
            scheduled_count=int(payload.get("scheduled_count") or 0),
            retry_wait_count=int(payload.get("retry_wait_count") or 0),
            rate_limited_count=int(payload.get("rate_limited_count") or 0),
            in_flight_count=int(payload.get("in_flight_count") or 0),
            unknown_count=int(payload.get("unknown_count") or 0),
            dlq_count=int(payload.get("dlq_count", payload.get("dead_letter_count")) or 0),
        )
