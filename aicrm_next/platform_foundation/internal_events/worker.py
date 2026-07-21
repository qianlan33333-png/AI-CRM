from __future__ import annotations

import os
from datetime import timedelta
from typing import Any
from uuid import uuid4

from aicrm_next.shared.runtime import fixture_mode

from .config import (
    allowed_consumers,
    allowed_event_consumer_pairs,
    allowed_event_consumers,
    allowed_event_types,
    auto_execute_enabled,
    auto_execute_max_batch_size,
    diagnostics_payload,
    internal_events_enabled,
)
from .consumer_registry import InternalEventConsumerRegistry, current_internal_event_consumer_registry
from .models import (
    AUTOMATIC_RECOVERABLE_STATUSES,
    MANUAL_ONLY_STATUSES,
    InternalEventConsumerResult,
    InternalEventConsumerRun,
    utcnow,
)
from .outbox import InternalEventOutboxRelay
from .repository import InternalEventRepository, build_internal_event_repository

_FINISHED_STATUSES = {"succeeded", "skipped"}
_FORCE_REASON_HINTS = ("gray", "grey", "test", "loopback", "production_gray", "灰度")
RELAY_ROLE_OWNER = "owner"
RELAY_ROLE_CONSUMER_ONLY = "consumer_only"
_RELAY_ROLES = {RELAY_ROLE_OWNER, RELAY_ROLE_CONSUMER_ONLY}


def _next_retry_at(attempt_count: int, retry_after_seconds: int | None = None):
    if retry_after_seconds is not None and retry_after_seconds > 0:
        return utcnow() + timedelta(seconds=min(int(retry_after_seconds), 24 * 60 * 60))
    delays = [60, 300, 900, 3600, 6 * 3600]
    return utcnow() + timedelta(seconds=delays[min(max(int(attempt_count or 0), 0), len(delays) - 1)])


def _single_counts(*, candidate: int = 0, processed: int = 0, status: str = "") -> dict[str, int]:
    counts = {
        "candidate_count": int(candidate or 0),
        "processed_count": int(processed or 0),
        "succeeded_count": 0,
        "failed_retryable_count": 0,
        "failed_terminal_count": 0,
        "blocked_count": 0,
        "skipped_count": 0,
        "lost_lease_count": 0,
        "unhandled_failure_count": 0,
    }
    if status in {"succeeded", "failed_retryable", "failed_terminal", "blocked", "skipped"}:
        counts[f"{status}_count"] = 1
    return counts


def _force_reason_allowed(reason: str) -> bool:
    lowered = str(reason or "").strip().lower()
    return any(hint in lowered for hint in _FORCE_REASON_HINTS)


def _empty_counts() -> dict[str, int]:
    return {
        "candidate_count": 0,
        "processed_count": 0,
        "succeeded_count": 0,
        "failed_retryable_count": 0,
        "failed_terminal_count": 0,
        "blocked_count": 0,
        "skipped_count": 0,
        "lost_lease_count": 0,
        "unhandled_failure_count": 0,
    }


class InternalEventWorker:
    """Dispatch internal event consumer handlers.

    The worker is intentionally not an external adapter runner. Handlers that need
    external work must create external_effect_job records and return.
    """

    def __init__(
        self,
        repository: InternalEventRepository | None = None,
        consumer_registry: InternalEventConsumerRegistry | None = None,
        *,
        locked_by: str = "",
        relay_role: str = RELAY_ROLE_CONSUMER_ONLY,
    ):
        self._repo = repository or build_internal_event_repository()
        self._registry = consumer_registry or current_internal_event_consumer_registry()
        self._locked_by = locked_by or f"internal-event-worker-{uuid4().hex[:8]}"
        normalized_role = str(relay_role or "").strip() or RELAY_ROLE_CONSUMER_ONLY
        if normalized_role not in _RELAY_ROLES:
            raise ValueError(f"unsupported internal event relay role: {normalized_role}")
        self._relay_role = normalized_role
        self._outbox_relay = (
            InternalEventOutboxRelay(
                self._repo,
                self._registry,
                locked_by=f"{self._locked_by}-relay",
            )
            if self._relay_role == RELAY_ROLE_OWNER
            else None
        )

    def _disabled_outbox_relay(self, *, dry_run: bool) -> dict[str, Any]:
        return {
            "ok": True,
            "dry_run": bool(dry_run),
            "enabled": False,
            "relay_role": self._relay_role,
            "reason": "consumer_only_worker",
            "items": [],
            "counts": {
                "candidate_count": 0,
                "relayed_count": 0,
                "failed_retryable_count": 0,
                "failed_terminal_count": 0,
                "lost_lease_count": 0,
                "unhandled_failure_count": 0,
            },
            "real_external_call_executed": False,
        }

    def _preview_outbox_relay(self, *, limit: int) -> dict[str, Any]:
        if self._outbox_relay is None:
            return self._disabled_outbox_relay(dry_run=True)
        payload = self._outbox_relay.preview_due(limit=limit)
        payload["enabled"] = True
        payload["relay_role"] = self._relay_role
        return payload

    def _run_outbox_relay(self, *, limit: int) -> dict[str, Any]:
        if self._outbox_relay is None:
            return self._disabled_outbox_relay(dry_run=False)
        payload = self._outbox_relay.relay_due(limit=limit)
        payload["enabled"] = True
        payload["relay_role"] = self._relay_role
        return payload

    def _effective_event_types(self, event_types: list[str] | None = None) -> list[str] | None:
        configured = allowed_event_types()
        requested = [str(item or "").strip() for item in (event_types or []) if str(item or "").strip()]
        if requested and configured:
            configured_set = set(configured)
            return [item for item in requested if item in configured_set]
        return requested or configured or None

    def _effective_consumers(self, consumer_names: list[str] | None = None) -> list[str] | None:
        configured = allowed_consumers()
        requested = [str(item or "").strip() for item in (consumer_names or []) if str(item or "").strip()]
        if requested and configured:
            configured_set = set(configured)
            return [item for item in requested if item in configured_set]
        return requested or configured or None

    def _requested_event_types(self, event_types: list[str] | None = None) -> list[str]:
        return [str(item or "").strip() for item in (event_types or []) if str(item or "").strip()]

    def _requested_consumers(self, consumer_names: list[str] | None = None) -> list[str]:
        return [str(item or "").strip() for item in (consumer_names or []) if str(item or "").strip()]

    def _effective_event_consumers(
        self,
        *,
        event_types: list[str] | None = None,
        consumer_names: list[str] | None = None,
    ) -> list[tuple[str, str]] | None:
        pairs = allowed_event_consumer_pairs()
        if not pairs:
            return None
        configured_event_types = set(allowed_event_types())
        requested_event_types = set(self._requested_event_types(event_types))
        requested_consumers = set(self._requested_consumers(consumer_names))
        effective: list[tuple[str, str]] = []
        for event_type, consumer_name in pairs:
            if configured_event_types and event_type not in configured_event_types:
                continue
            if requested_event_types and event_type not in requested_event_types:
                continue
            if requested_consumers and consumer_name not in requested_consumers:
                continue
            effective.append((event_type, consumer_name))
        return effective

    def _empty_due_response(
        self,
        *,
        dry_run: bool,
        event_types: list[str] | None,
        consumer_names: list[str] | None,
        event_consumers: list[tuple[str, str]] | None = None,
        error: str = "",
        message: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": not bool(error),
            "items": [],
            "counts": _empty_counts(),
            "dry_run": bool(dry_run),
            "event_types": event_types or [],
            "consumer_names": consumer_names or [],
            "event_consumers": [f"{event_type}:{consumer_name}" for event_type, consumer_name in (event_consumers or [])],
            "relay_role": self._relay_role,
            "config": diagnostics_payload(),
            "real_external_call_executed": False,
        }
        if error:
            payload["error"] = error
        if message:
            payload["message"] = message
        return payload

    def _auto_execute_gate(
        self,
        *,
        batch_size: int,
        event_types: list[str] | None,
        consumer_names: list[str] | None,
        event_consumers: list[tuple[str, str]] | None,
    ) -> dict[str, Any] | None:
        if not auto_execute_enabled():
            return self._empty_due_response(
                dry_run=False,
                event_types=event_types,
                consumer_names=consumer_names,
                event_consumers=event_consumers,
                error="internal_events_auto_execute_disabled",
                message="Set AICRM_INTERNAL_EVENTS_AUTO_EXECUTE=1 before executing due consumers.",
            )
        if not allowed_event_consumers() and len(allowed_event_types()) > 1:
            return self._empty_due_response(
                dry_run=False,
                event_types=event_types,
                consumer_names=consumer_names,
                event_consumers=event_consumers,
                error="pair_allowlist_required_for_multi_event_auto_execute",
                message="Set AICRM_INTERNAL_EVENTS_ALLOWED_EVENT_CONSUMERS before auto-executing multiple event types.",
            )
        max_batch_size_explicit = bool(str(os.getenv("AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE") or "").strip())
        if int(batch_size or 0) > auto_execute_max_batch_size() and (not fixture_mode() or max_batch_size_explicit):
            return self._empty_due_response(
                dry_run=False,
                event_types=event_types,
                consumer_names=consumer_names,
                event_consumers=event_consumers,
                error="batch_size_exceeds_auto_execute_limit",
                message="Reduce batch_size or explicitly raise AICRM_INTERNAL_EVENTS_AUTO_EXECUTE_MAX_BATCH_SIZE.",
            )
        if not fixture_mode() and (not allowed_event_types() or (not allowed_consumers() and not allowed_event_consumers())):
            return self._empty_due_response(
                dry_run=False,
                event_types=event_types,
                consumer_names=consumer_names,
                event_consumers=event_consumers,
                error="auto_execute_allowlist_required",
                message="Production auto-execute requires both event type and consumer allowlists.",
            )
        return None

    def preview_due(self, *, batch_size: int = 10, event_types: list[str] | None = None, consumer_names: list[str] | None = None) -> dict[str, Any]:
        outbox_relay = self._preview_outbox_relay(limit=batch_size)
        effective_event_types = self._effective_event_types(event_types)
        effective_event_consumers = self._effective_event_consumers(event_types=event_types, consumer_names=consumer_names)
        if effective_event_consumers is not None:
            effective_consumers = self._requested_consumers(consumer_names) or None
        else:
            effective_consumers = self._effective_consumers(consumer_names)
        if effective_event_types == [] or effective_consumers == [] or effective_event_consumers == []:
            return self._empty_due_response(
                dry_run=True,
                event_types=effective_event_types,
                consumer_names=effective_consumers,
                event_consumers=effective_event_consumers,
            )
        runs = self._repo.list_due_runs(
            limit=batch_size,
            event_types=effective_event_types,
            consumer_names=effective_consumers,
            event_consumers=effective_event_consumers,
        )
        items: list[dict[str, Any]] = []
        for run in runs:
            event = self._repo.get_event(run.event_id)
            event_payload = event.to_dict() if event else {}
            items.append(
                {
                    **run.to_dict(),
                    "event_type": str(event_payload.get("event_type") or ""),
                    "aggregate_type": str(event_payload.get("aggregate_type") or ""),
                    "aggregate_id": str(event_payload.get("aggregate_id") or ""),
                    "would_execute": True,
                }
            )
        return {
            "ok": True,
            "items": items,
            "counts": {
                "candidate_count": len(runs),
                "processed_count": 0,
                "succeeded_count": 0,
                "failed_retryable_count": 0,
                "failed_terminal_count": 0,
                "blocked_count": 0,
                "skipped_count": 0,
                "lost_lease_count": 0,
                "unhandled_failure_count": 0,
            },
            "outbox_relay": outbox_relay,
            "dry_run": True,
            "event_types": effective_event_types or [],
            "consumer_names": effective_consumers or [],
            "event_consumers": [f"{event_type}:{consumer_name}" for event_type, consumer_name in (effective_event_consumers or [])],
            "relay_role": self._relay_role,
            "config": diagnostics_payload(),
            "real_external_call_executed": False,
        }

    def run_due(
        self,
        *,
        batch_size: int = 10,
        dry_run: bool = True,
        event_types: list[str] | None = None,
        consumer_names: list[str] | None = None,
    ) -> dict[str, Any]:
        if dry_run:
            payload = self.preview_due(batch_size=batch_size, event_types=event_types, consumer_names=consumer_names)
            payload["dry_run"] = True
            return payload
        effective_event_types = self._effective_event_types(event_types)
        effective_event_consumers = self._effective_event_consumers(event_types=event_types, consumer_names=consumer_names)
        if effective_event_consumers is not None:
            effective_consumers = self._requested_consumers(consumer_names) or None
        else:
            effective_consumers = self._effective_consumers(consumer_names)
        if effective_event_types == [] or effective_consumers == [] or effective_event_consumers == []:
            return self._empty_due_response(
                dry_run=False,
                event_types=effective_event_types,
                consumer_names=effective_consumers,
                event_consumers=effective_event_consumers,
            )
        if not internal_events_enabled():
            return self._empty_due_response(
                dry_run=False,
                event_types=effective_event_types,
                consumer_names=effective_consumers,
                event_consumers=effective_event_consumers,
                error="internal_events_disabled",
            )
        gated = self._auto_execute_gate(
            batch_size=batch_size,
            event_types=effective_event_types,
            consumer_names=effective_consumers,
            event_consumers=effective_event_consumers,
        )
        if gated is not None:
            return gated
        try:
            outbox_relay = self._run_outbox_relay(limit=batch_size)
        except Exception as exc:
            outbox_relay = {
                "ok": False,
                "error": "outbox_relay_unhandled_failure",
                "error_class": exc.__class__.__name__,
                "items": [],
                "counts": {"unhandled_failure_count": 1},
                "real_external_call_executed": False,
            }
        try:
            runs = self._repo.acquire_due_runs(
                limit=batch_size,
                locked_by=self._locked_by,
                event_types=effective_event_types,
                consumer_names=effective_consumers,
                event_consumers=effective_event_consumers,
            )
        except Exception as exc:
            counts = _empty_counts()
            counts["unhandled_failure_count"] = 1
            return {
                "ok": False,
                "exit_code": 1,
                "error": "consumer_run_acquire_failed",
                "error_class": exc.__class__.__name__,
                "items": [],
                "processed": [],
                "counts": counts,
                "dry_run": False,
                "event_types": effective_event_types or [],
                "consumer_names": effective_consumers or [],
                "event_consumers": [f"{event_type}:{consumer_name}" for event_type, consumer_name in (effective_event_consumers or [])],
                "relay_role": self._relay_role,
                "outbox_relay": outbox_relay,
                "config": diagnostics_payload(),
                "real_external_call_executed": False,
            }
        items: list[dict[str, Any]] = []
        processed: list[dict[str, Any]] = []
        counts = {
            "candidate_count": len(runs),
            "processed_count": 0,
            "succeeded_count": 0,
            "failed_retryable_count": 0,
            "failed_terminal_count": 0,
            "blocked_count": 0,
            "skipped_count": 0,
            "lost_lease_count": 0,
            "unhandled_failure_count": 0,
        }
        for run in runs:
            try:
                result = self.dispatch_one(run)
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": "unhandled_worker_failure",
                    "error_class": exc.__class__.__name__,
                    "event": {"event_id": run.event_id},
                    "consumer_run": run.to_dict(),
                    "real_external_call_executed": False,
                }
                counts["unhandled_failure_count"] += 1
            items.append(result)
            counts["processed_count"] += 1
            status = str(result.get("consumer_run", {}).get("status") or "")
            if status in {"succeeded", "failed_retryable", "failed_terminal", "blocked", "skipped"}:
                counts[f"{status}_count"] += 1
            if result.get("error") == "lost_lease":
                counts["lost_lease_count"] += 1
            processed.append(
                {
                    "event_id": str(result.get("event", {}).get("event_id") or run.event_id),
                    "consumer_name": str(result.get("consumer_run", {}).get("consumer_name") or run.consumer_name),
                    "status": status,
                    "attempt_id": str(result.get("attempt", {}).get("attempt_id") or ""),
                    "real_external_call_executed": False,
                }
            )
        failure_count = sum(
            counts[key]
            for key in (
                "failed_retryable_count",
                "failed_terminal_count",
                "blocked_count",
                "lost_lease_count",
                "unhandled_failure_count",
            )
        )
        ok = bool(outbox_relay.get("ok")) and failure_count == 0
        return {
            "ok": ok,
            "exit_code": 0 if ok else 1,
            "items": items,
            "processed": processed,
            "counts": counts,
            "dry_run": False,
            "event_types": effective_event_types or [],
            "consumer_names": effective_consumers or [],
            "event_consumers": [f"{event_type}:{consumer_name}" for event_type, consumer_name in (effective_event_consumers or [])],
            "relay_role": self._relay_role,
            "outbox_relay": outbox_relay,
            "config": diagnostics_payload(),
            "real_external_call_executed": False,
        }

    def dispatch_one_consumer(
        self,
        event_id: str,
        consumer_name: str,
        *,
        dry_run: bool = True,
        force: bool = False,
        reason: str = "",
    ) -> dict[str, Any]:
        event_id = str(event_id or "").strip()
        consumer_name = str(consumer_name or "").strip()
        reason = str(reason or "").strip()
        run = self._repo.get_consumer_run(event_id, consumer_name)
        if run is None:
            return {
                "ok": False,
                "error": "consumer_run_not_found",
                "items": [],
                "counts": _single_counts(),
                "dry_run": bool(dry_run),
                "force": bool(force),
                "reason": reason,
                "real_external_call_executed": False,
            }
        event = self._repo.get_event(run.event_id)
        if event is None:
            return {
                "ok": False,
                "error": "internal_event_not_found",
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": bool(dry_run),
                "force": bool(force),
                "reason": reason,
                "real_external_call_executed": False,
            }
        handler = self._registry.get_handler(event.event_type, run.consumer_name)
        if handler is None:
            return {
                "ok": False,
                "error": "consumer_handler_not_registered",
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": bool(dry_run),
                "force": bool(force),
                "reason": reason,
                "real_external_call_executed": False,
            }
        if run.status in _FINISHED_STATUSES and not force:
            return {
                "ok": False,
                "error": "consumer_run_already_finished",
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": bool(dry_run),
                "force": False,
                "reason": reason,
                "real_external_call_executed": False,
            }
        if run.status in MANUAL_ONLY_STATUSES:
            return {
                "ok": False,
                "error": "manual_retry_or_skip_required",
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": bool(dry_run),
                "force": bool(force),
                "reason": reason,
                "real_external_call_executed": False,
            }
        if force and run.status in _FINISHED_STATUSES and not _force_reason_allowed(reason):
            return {
                "ok": False,
                "error": "force_requires_test_or_gray_reason",
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": bool(dry_run),
                "force": True,
                "reason": reason,
                "real_external_call_executed": False,
            }
        if run.status not in AUTOMATIC_RECOVERABLE_STATUSES and not force:
            return {
                "ok": False,
                "error": "consumer_run_not_executable",
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": bool(dry_run),
                "force": False,
                "reason": reason,
                "real_external_call_executed": False,
            }
        if dry_run:
            return {
                "ok": True,
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [{"event": event.to_dict(), "consumer_run": run.to_dict(), "would_execute": True}],
                "counts": _single_counts(candidate=1, processed=0),
                "dry_run": True,
                "force": bool(force),
                "reason": reason,
                "config": diagnostics_payload(),
                "real_external_call_executed": False,
            }
        if not internal_events_enabled():
            return {
                "ok": False,
                "error": "internal_events_disabled",
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": False,
                "force": bool(force),
                "reason": reason,
                "config": diagnostics_payload(),
                "real_external_call_executed": False,
            }

        acquired = self._repo.acquire_consumer_run(
            event_id=event.event_id,
            consumer_name=run.consumer_name,
            locked_by=self._locked_by,
            force=bool(force),
        )
        if acquired is None:
            return {
                "ok": False,
                "error": "consumer_run_locked_or_not_executable",
                "event": event.to_dict(),
                "consumer_run": run.to_dict(),
                "items": [],
                "counts": _single_counts(),
                "dry_run": False,
                "force": bool(force),
                "reason": reason,
                "config": diagnostics_payload(),
                "real_external_call_executed": False,
            }
        item = self.dispatch_one(acquired)
        status = str(item.get("consumer_run", {}).get("status") or "")
        return {
            "ok": bool(item.get("ok")),
            "items": [item],
            "event": item.get("event") or event.to_dict(),
            "consumer_run": item.get("consumer_run") or acquired.to_dict(),
            "attempt": item.get("attempt"),
            "counts": _single_counts(candidate=1, processed=1, status=status),
            "dry_run": False,
            "force": bool(force),
            "reason": reason,
            "config": diagnostics_payload(),
            "real_external_call_executed": False,
        }

    def dispatch_one(self, run_or_id: int | InternalEventConsumerRun) -> dict[str, Any]:
        run = run_or_id if isinstance(run_or_id, InternalEventConsumerRun) else self._repo.get_consumer_run_by_id(int(run_or_id))
        if run is None:
            return {"ok": False, "error": "consumer_run_not_found", "real_external_call_executed": False}
        if not run.lease_token:
            acquired = self._repo.acquire_consumer_run(
                event_id=run.event_id,
                consumer_name=run.consumer_name,
                locked_by=self._locked_by,
                force=False,
            )
            if acquired is None:
                return {
                    "ok": False,
                    "error": "consumer_run_not_executable",
                    "consumer_run": run.to_dict(),
                    "real_external_call_executed": False,
                }
            run = acquired
        running = self._repo.mark_running(
            run.id,
            locked_by=self._locked_by,
            expected_lease_token=run.lease_token,
            expected_generation=run.worker_generation,
        )
        if running is None:
            return {
                "ok": False,
                "error": "lost_lease",
                "consumer_run": run.to_dict(),
                "real_external_call_executed": False,
            }
        event = self._repo.get_event(running.event_id)
        if event is None:
            return self._blocked_result(running, "internal_event_not_found", f"event {running.event_id} was not found")

        handler = self._registry.get_handler(event.event_type, running.consumer_name)
        if handler is None:
            return self._blocked_result(
                running,
                "consumer_handler_not_registered",
                f"consumer handler is not registered: {event.event_type}/{running.consumer_name}",
            )

        try:
            handler_result = handler(event, running)
        except Exception as exc:
            handler_result = InternalEventConsumerResult(
                status="failed_retryable",
                request_summary={"event_id": event.event_id, "consumer_name": running.consumer_name},
                response_summary={"handler_exception": exc.__class__.__name__},
                error_code="handler_exception",
                error_message=str(exc),
            )

        status = handler_result.status
        if status == "failed_retryable" and int(running.attempt_count or 0) + 1 >= int(running.max_attempts or 5):
            status = "failed_terminal"
        completed = self._repo.complete_consumer_attempt(
            run=running,
            status=status,
            request_summary=handler_result.request_summary,
            response_summary={
                **handler_result.response_summary,
                "real_external_call_executed": False,
                "external_effect_boundary": "handler_must_enqueue_external_effect_job_for_external_calls",
            },
            result_summary=handler_result.result_summary or handler_result.response_summary,
            error_code=handler_result.error_code,
            error_message=handler_result.error_message,
            next_retry_at=_next_retry_at(running.attempt_count, handler_result.retry_after_seconds) if status == "failed_retryable" else None,
        )
        if completed is None:
            return {
                "ok": False,
                "error": "lost_lease",
                "event": event.to_dict(),
                "consumer_run": running.to_dict(),
                "real_external_call_executed": False,
            }
        updated, attempt = completed
        return {
            "ok": status == "succeeded",
            "event": event.to_dict(),
            "consumer_run": updated.to_dict() if updated else running.to_dict(),
            "attempt": attempt.to_dict(),
            "real_external_call_executed": False,
        }

    def _blocked_result(self, run: InternalEventConsumerRun, error_code: str, error_message: str) -> dict[str, Any]:
        completed = self._repo.complete_consumer_attempt(
            run=run,
            status="blocked",
            request_summary={"event_id": run.event_id, "consumer_name": run.consumer_name},
            response_summary={"blocked": True, "real_external_call_executed": False},
            result_summary={"blocked": True},
            error_code=error_code,
            error_message=error_message,
        )
        if completed is None:
            return {
                "ok": False,
                "error": "lost_lease",
                "consumer_run": run.to_dict(),
                "real_external_call_executed": False,
            }
        updated, attempt = completed
        return {
            "ok": False,
            "consumer_run": updated.to_dict() if updated else run.to_dict(),
            "attempt": attempt.to_dict(),
            "real_external_call_executed": False,
        }
