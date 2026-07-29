from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from .command_bus.models import utcnow_iso


@dataclass(frozen=True)
class AuditEvent:
    audit_event_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: str = ""
    actor_id: str = ""
    actor_type: str = ""
    target_type: str = ""
    target_id: str = ""
    source_route: str = ""
    command_id: str = ""
    trace_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class InMemoryAuditLedger:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record_event(self, **kwargs: Any) -> AuditEvent:
        event = AuditEvent(**kwargs)
        self._events.append(event)
        return event

    def list_events(self) -> list[AuditEvent]:
        return list(self._events)

    def query_events(self, **filters: str) -> list[AuditEvent]:
        events = self.list_events()
        for key, value in filters.items():
            if value == "":
                continue
            events = [event for event in events if str(getattr(event, key, "")) == value]
        return events
