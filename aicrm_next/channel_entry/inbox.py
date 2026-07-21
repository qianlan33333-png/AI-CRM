from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Callable

from aicrm_next.platform_foundation.webhook_inbox import WebhookInboxRepository, WebhookInboxService, build_webhook_inbox_repository
from aicrm_next.shared.safe_logging import safe_log_exception

from .application import process_wecom_external_contact_event
from .domain import text
from .schemas import ProcessWeComExternalContactEventCommand


LOGGER = logging.getLogger(__name__)
WECOM_PROVIDER = "wecom"
WECOM_EXTERNAL_CONTACT_FAMILY = "external_contact"


def _event_key(corp_id: str, event_data: dict[str, Any]) -> str:
    fields = [
        corp_id,
        text(event_data.get("Event")),
        text(event_data.get("ChangeType")),
        text(event_data.get("ExternalUserID")),
        text(event_data.get("UserID")),
        text(event_data.get("CreateTime")),
        text(event_data.get("WelcomeCode")),
        text(event_data.get("State")),
    ]
    return "|".join(fields)


def wecom_callback_idempotency_key(corp_id: str, event_data: dict[str, Any]) -> str:
    return _event_key(corp_id, event_data)


def _safe_headers(headers: dict[str, Any]) -> dict[str, str]:
    blocked = {"authorization", "cookie", "set-cookie", "x-api-key"}
    return {str(key).lower(): text(value) for key, value in (headers or {}).items() if str(key).lower() not in blocked}


def _payload_summary(event_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": text(event_data.get("Event")),
        "change_type": text(event_data.get("ChangeType")),
        "external_userid": text(event_data.get("ExternalUserID")),
        "user_id": text(event_data.get("UserID")),
        "create_time": text(event_data.get("CreateTime")),
        "state_present": bool(text(event_data.get("State"))),
        "welcome_code_present": bool(text(event_data.get("WelcomeCode"))),
    }


def ingest_wecom_callback(
    *,
    query: dict[str, str],
    headers: dict[str, Any],
    body: bytes,
    event_data: dict[str, Any],
    plain_xml: str,
    route: str,
    repository: WebhookInboxRepository | None = None,
) -> dict[str, Any]:
    corp_id = text(event_data.get("ToUserName"))
    idempotency_key = wecom_callback_idempotency_key(corp_id, event_data)
    service = WebhookInboxService(repository)
    row = service.ingest(
        provider=WECOM_PROVIDER,
        event_family=WECOM_EXTERNAL_CONTACT_FAMILY,
        route=text(route),
        method="POST",
        tenant_id="aicrm",
        corp_id=corp_id,
        event_type=text(event_data.get("Event")),
        change_type=text(event_data.get("ChangeType")),
        external_event_id=idempotency_key,
        idempotency_key=idempotency_key,
        raw_query_json=dict(query or {}),
        raw_headers_json=_safe_headers(headers),
        raw_body=body or b"",
        payload_xml=plain_xml,
        payload_json=dict(event_data or {}),
        payload_summary_json=_payload_summary(event_data),
        max_attempts=8,
    )
    return {
        "ok": True,
        "id": int(row.get("id") or 0),
        "duplicate": int(row.get("duplicate_count") or 0) > 0,
        "duplicate_count": int(row.get("duplicate_count") or 0),
        "status": text(row.get("status")) or "received",
        "idempotency_key": idempotency_key,
        "ack_boundary": "durable_inbox_only",
    }


def _next_retry_at(attempt_count: int) -> datetime:
    delay_seconds = min(3600, max(30, 30 * (2 ** max(0, int(attempt_count or 0)))))
    return datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)


def _int_list(values: list[Any]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return result


def _processing_summary(result: dict[str, Any]) -> dict[str, Any]:
    event_log = result.get("event_log") or {}
    entry_result = result.get("entry_result") or {}
    internal_event = entry_result.get("channel_entry_internal_event") or {}
    baseline_effects = entry_result.get("baseline_effects") or {}
    identity_sync = result.get("identity_sync") or {}
    identity_wecom_result = identity_sync.get("wecom_result") if isinstance(identity_sync.get("wecom_result"), dict) else {}
    effect_jobs: list[Any] = []
    for effect_result in baseline_effects.values():
        if isinstance(effect_result, dict):
            effect_jobs.append(effect_result.get("external_effect_job_id"))
    effect_jobs.append(identity_sync.get("external_effect_job_id"))
    return {
        "handled": bool(result.get("handled")),
        "event_log_id": int(event_log.get("id") or 0),
        "internal_event_id": text(internal_event.get("event_id")),
        "internal_event_consumer_run_count": int(internal_event.get("consumer_run_count") or 0),
        "external_effect_job_ids": _int_list(effect_jobs),
        "identity_sync_status": text(identity_sync.get("status")),
        "identity_sync_reason": text(identity_sync.get("reason")),
        "identity_sync_error_code": text(identity_wecom_result.get("errcode")),
        "entry_mode": text(entry_result.get("mode")),
        "entry_reason": text(entry_result.get("reason")),
    }


class WeComCallbackInboxWorker:
    def __init__(
        self,
        repository: WebhookInboxRepository | None = None,
        *,
        locked_by: str = "wecom-callback-inbox-worker",
        processor: Callable[[ProcessWeComExternalContactEventCommand], dict[str, Any]] | None = None,
    ) -> None:
        self._repo = repository or build_webhook_inbox_repository()
        self._locked_by = text(locked_by) or "wecom-callback-inbox-worker"
        self._processor = processor or process_wecom_external_contact_event

    def preview_due(self, *, limit: int = 50) -> dict[str, Any]:
        rows = self._repo.preview_due(provider=WECOM_PROVIDER, limit=max(1, int(limit or 50)))
        return {
            "ok": True,
            "dry_run": True,
            "provider": WECOM_PROVIDER,
            "due_count": len(rows),
            "items": [self._row_summary(row) for row in rows],
        }

    def run_due(self, *, limit: int = 50, dry_run: bool = True) -> dict[str, Any]:
        if dry_run:
            return self.preview_due(limit=limit)
        rows = self._repo.claim_due(provider=WECOM_PROVIDER, limit=max(1, int(limit or 50)), locked_by=self._locked_by)
        summary = {
            "ok": True,
            "dry_run": False,
            "provider": WECOM_PROVIDER,
            "claimed_count": len(rows),
            "succeeded_count": 0,
            "failed_retryable_count": 0,
            "failed_terminal_count": 0,
            "dead_letter_count": 0,
            "items": [],
        }
        for row in rows:
            result = self.dispatch_row(row)
            status = text(result.get("status"))
            if status == "succeeded":
                summary["succeeded_count"] += 1
            elif status == "failed_retryable":
                summary["failed_retryable_count"] += 1
            elif status == "failed_terminal":
                summary["failed_terminal_count"] += 1
            elif status == "dead_letter":
                summary["dead_letter_count"] += 1
            summary["items"].append(result)
        return summary

    def dispatch_one(self, inbox_id: int, *, dry_run: bool = False, reason: str = "operator_dispatch_one") -> dict[str, Any]:
        row = self._repo.get_item(int(inbox_id))
        if not row:
            return {"ok": False, "id": int(inbox_id), "status": "not_found", "error": "webhook_inbox_item_not_found"}
        if text(row.get("provider")) != WECOM_PROVIDER:
            return {
                "ok": False,
                "id": int(inbox_id),
                "status": text(row.get("status")),
                "error": "webhook_inbox_provider_not_supported",
                "provider": text(row.get("provider")),
            }
        if text(row.get("status")) in {"succeeded", "ignored"}:
            return {
                "ok": False,
                "id": int(inbox_id),
                "status": text(row.get("status")),
                "error": "webhook_inbox_item_not_dispatchable",
            }
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "id": int(inbox_id),
                "status": text(row.get("status")),
                "item": self._row_summary(row),
            }
        original_status = text(row.get("status"))
        if original_status in {"received", "failed_retryable"}:
            claimed = self._repo.claim_one(int(inbox_id), locked_by=f"{self._locked_by}:{reason}") or {}
            if not claimed:
                latest = self._repo.get_item(int(inbox_id)) or row
                return {
                    "ok": False,
                    "id": int(inbox_id),
                    "status": text(latest.get("status")),
                    "error": "webhook_inbox_item_not_claimed",
                }
            row = claimed
        elif original_status in {"failed_terminal", "dead_letter", "processing"}:
            row = self._repo.mark_retryable_now(int(inbox_id), reason=reason) or row
        result = self.dispatch_row(row)
        result["ok"] = text(result.get("status")) == "succeeded"
        result["dry_run"] = False
        return result

    def dispatch_row(self, row: dict[str, Any]) -> dict[str, Any]:
        inbox_id = int(row.get("id") or 0)
        try:
            payload_json = row.get("payload_json") or {}
            if not isinstance(payload_json, dict):
                raise ValueError("payload_json must be an object")
            result = self._processor(
                ProcessWeComExternalContactEventCommand(
                    corp_id=text(row.get("corp_id")),
                    event_data=payload_json,
                    payload_xml=text(row.get("payload_xml")),
                    route=text(row.get("route")),
                )
            )
            summary = _processing_summary(result)
            updated = self._repo.mark_succeeded(
                inbox_id,
                processing_summary_json=summary,
                expected_lease_token=text(row.get("lease_token")),
                expected_generation=int(row.get("worker_generation") or 0),
            )
            if updated is None:
                return {
                    "id": inbox_id,
                    "status": "unknown",
                    "error_code": "lost_lease",
                    "error_message": "Webhook result was not persisted because queue ownership changed.",
                }
            return {
                "id": inbox_id,
                "status": text(updated.get("status")) or "succeeded",
                "handled": bool(result.get("handled")),
                "event_log_id": int(summary.get("event_log_id") or 0),
                "internal_event_id": text(summary.get("internal_event_id")),
                "external_effect_job_ids": summary.get("external_effect_job_ids") or [],
            }
        except Exception as exc:
            attempt_count = int(row.get("attempt_count") or 0)
            provider_classification = text(getattr(exc, "classification", ""))
            if provider_classification in {"retryable", "terminal", "blocked"}:
                error_code = text(getattr(exc, "error_code", "")) or exc.__class__.__name__
                retryable = provider_classification == "retryable"
                retry_after_seconds = getattr(exc, "retry_after_seconds", None)
            elif isinstance(exc, (TypeError, ValueError)):
                error_code = "callback_payload_invalid"
                retryable = False
                retry_after_seconds = None
            else:
                error_code = exc.__class__.__name__
                retryable = True
                retry_after_seconds = None
            next_retry = _next_retry_at(attempt_count)
            if retry_after_seconds is not None:
                next_retry = max(
                    next_retry,
                    datetime.now(timezone.utc) + timedelta(seconds=max(0.0, min(float(retry_after_seconds), 86400.0))),
                )
            updated = self._repo.mark_failed(
                inbox_id,
                error_code=error_code,
                error_message=str(exc),
                retryable=retryable,
                next_retry_at=next_retry if retryable else None,
                expected_lease_token=text(row.get("lease_token")),
                expected_generation=int(row.get("worker_generation") or 0),
            )
            if updated is None:
                updated = {
                    "status": "unknown",
                    "attempt_count": attempt_count,
                }
                error_code = "lost_lease"
            safe_log_exception(
                LOGGER,
                "wecom callback inbox dispatch failed",
                exc,
                webhook_inbox_id=inbox_id,
            )
            return {
                "id": inbox_id,
                "status": text(updated.get("status")),
                "error_code": error_code,
                "error_message": str(exc),
                "attempt_count": int(updated.get("attempt_count") or attempt_count + 1),
            }

    @staticmethod
    def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row.get("id") or 0),
            "status": text(row.get("status")),
            "event_type": text(row.get("event_type")),
            "change_type": text(row.get("change_type")),
            "external_event_id": text(row.get("external_event_id")),
            "attempt_count": int(row.get("attempt_count") or 0),
            "received_at": text(row.get("received_at")),
            "next_retry_at": text(row.get("next_retry_at")),
        }
