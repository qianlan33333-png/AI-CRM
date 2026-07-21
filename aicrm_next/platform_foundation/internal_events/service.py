from __future__ import annotations

from datetime import datetime
from typing import Any

from aicrm_next.platform_foundation.command_bus.models import CommandContext

from .config import (
    allowed_consumers,
    allowed_event_consumer_pairs,
    allowed_event_consumers,
    allowed_event_types,
    diagnostics_payload as config_diagnostics_payload,
)
from .consumer_registry import InternalEventConsumerRegistry, current_internal_event_consumer_registry
from .models import InternalEvent, InternalEventConsumerRun, InternalEventConsumerSpec, InternalEventCreateRequest
from .repository import InternalEventRepository, build_internal_event_repository


class InternalEventService:
    def __init__(
        self,
        repository: InternalEventRepository | None = None,
        consumer_registry: InternalEventConsumerRegistry | None = None,
    ):
        self._repo = repository or build_internal_event_repository()
        self._registry = consumer_registry or current_internal_event_consumer_registry()

    def emit_event(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any] | None = None,
        payload_summary: dict[str, Any] | None = None,
        context: CommandContext | None = None,
        event_version: int = 1,
        subject_type: str = "",
        subject_id: str = "",
        idempotency_key: str = "",
        source_module: str = "",
        source_command_id: str = "",
        correlation_id: str = "",
        execution_id: str = "",
        parent_execution_id: str = "",
        occurred_at: datetime | None = None,
        tenant_id: str = "aicrm",
    ) -> dict[str, Any]:
        request = InternalEventCreateRequest(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=dict(payload or {}),
            payload_summary=dict(payload_summary or {}),
            context=context or CommandContext(),
            event_version=event_version,
            subject_type=subject_type,
            subject_id=subject_id,
            idempotency_key=idempotency_key,
            source_module=source_module,
            source_command_id=source_command_id,
            correlation_id=correlation_id,
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            occurred_at=occurred_at,
            tenant_id=tenant_id,
        )
        event, runs = self._repo.create_event_with_consumer_runs(request, self.consumer_specs_for_event_type(event_type))
        return {"event": event.to_dict(), "consumer_runs": [run.to_dict() for run in runs]}

    def consumer_specs_for_event_type(self, event_type: str) -> list[InternalEventConsumerSpec]:
        return [
            InternalEventConsumerSpec(
                consumer_name=consumer.consumer_name,
                consumer_type=consumer.consumer_type,
                max_attempts=consumer.max_attempts,
            )
            for consumer in self._registry.list_for_event_type(event_type)
        ]

    def get_event(self, event_id: str) -> InternalEvent | None:
        return self._repo.get_event(event_id)

    def list_events(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[InternalEvent], int]:
        return self._repo.list_events(filters or {}, limit=limit, offset=offset)

    def list_consumer_runs(self, filters: dict[str, Any] | None = None, *, limit: int = 100, offset: int = 0) -> tuple[list[InternalEventConsumerRun], int]:
        return self._repo.list_consumer_runs(filters or {}, limit=limit, offset=offset)

    def list_attempts(self, consumer_run_id: int | None = None, *, event_id: str = ""):
        return self._repo.list_attempts(consumer_run_id, event_id=event_id)

    def get_event_reconciliation(self, event_id: str) -> dict[str, Any]:
        from .reconciliation import InternalEventReconciliationService

        return InternalEventReconciliationService(repository=self._repo).build_event_reconciliation(event_id)

    def retry_consumer_run(
        self,
        event_id: str,
        consumer_name: str,
        *,
        actor_id: str,
        actor_type: str,
        reason: str,
    ):
        return self._repo.retry_consumer_run(
            event_id,
            consumer_name,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=reason,
        )

    def skip_consumer_run(
        self,
        event_id: str,
        consumer_name: str,
        *,
        actor_id: str,
        actor_type: str,
        reason: str,
    ):
        return self._repo.skip_consumer_run(
            event_id,
            consumer_name,
            actor_id=actor_id,
            actor_type=actor_type,
            reason=reason,
        )

    def diagnostics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        base_filters = dict(filters or {})
        metrics = self._repo.queue_metrics(base_filters)
        outbox_metrics = self._repo.outbox_metrics()
        configured_event_types = allowed_event_types()
        configured_consumers = allowed_consumers()
        configured_pairs = allowed_event_consumer_pairs()
        allowed_filters = dict(base_filters)
        if configured_event_types:
            allowed_filters["event_types"] = configured_event_types
        if configured_pairs:
            allowed_filters["event_consumers"] = configured_pairs
        elif configured_consumers:
            allowed_filters["consumer_names"] = configured_consumers
        allowed_metrics = self._repo.queue_metrics(allowed_filters) if (configured_event_types or configured_consumers or configured_pairs) else metrics
        legacy_filters = dict(base_filters)
        if configured_event_types:
            legacy_filters["event_types"] = configured_event_types
        if configured_consumers:
            legacy_filters["consumer_names"] = configured_consumers
        legacy_metrics = self._repo.queue_metrics(legacy_filters) if (configured_event_types or configured_consumers) else metrics
        blocked_by_config_count = max(0, int(metrics.get("due_count") or 0) - int(allowed_metrics.get("due_count") or 0))
        blocked_by_pair_allowlist_count = (
            max(0, int(legacy_metrics.get("due_count") or 0) - int(allowed_metrics.get("due_count") or 0))
            if allowed_event_consumers()
            else 0
        )
        config = config_diagnostics_payload()
        return {
            "ok": True,
            **metrics,
            **config,
            "queue_metrics": metrics,
            "outbox_metrics": outbox_metrics,
            "effective_queue_metrics": allowed_metrics,
            "blocked_by_config_count": blocked_by_config_count,
            "blocked_by_pair_allowlist_count": blocked_by_pair_allowlist_count,
            "schema_contract": {
                "event_idempotency_constraint": "UNIQUE (tenant_id, idempotency_key)",
                "consumer_run_uniqueness_constraint": "UNIQUE (tenant_id, event_id, consumer_name)",
                "external_effect_boundary": "external_effect_job remains external side effects only",
            },
            "registered_consumers": self._registry.to_dict(),
            "config": config,
            "real_external_call_executed": False,
        }


def default_internal_event_service() -> InternalEventService:
    return InternalEventService()
