from __future__ import annotations

from typing import Any, Protocol


class DomainEventOutboxPort(Protocol):
    """Public owner port for transactional domain-event outbox writes."""

    def enqueue_dbapi(
        self,
        executor: Any,
        *,
        tenant_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None: ...


def build_domain_event_outbox_port() -> DomainEventOutboxPort:
    from .repo import DBAPIDomainEventOutboxRepository

    return DBAPIDomainEventOutboxRepository()


__all__ = ["DomainEventOutboxPort", "build_domain_event_outbox_port"]
