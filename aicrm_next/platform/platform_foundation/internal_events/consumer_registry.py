from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from .fanout import build_fanout_manifest
from .models import InternalEvent, InternalEventConsumerResult, InternalEventConsumerType, InternalEventConsumerRun

InternalEventConsumerHandler = Callable[[InternalEvent, InternalEventConsumerRun], InternalEventConsumerResult]


@dataclass(frozen=True)
class RegisteredInternalEventConsumer:
    event_type: str
    consumer_name: str
    consumer_type: InternalEventConsumerType = "projection"
    handler: InternalEventConsumerHandler | None = None
    max_attempts: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "consumer_name": self.consumer_name,
            "consumer_type": self.consumer_type,
            "max_attempts": self.max_attempts,
            "handler_registered": self.handler is not None,
        }


class InternalEventConsumerRegistry:
    def __init__(self) -> None:
        self._consumers: dict[str, list[RegisteredInternalEventConsumer]] = defaultdict(list)
        self._handler_aliases: dict[tuple[str, str], InternalEventConsumerHandler] = {}
        self._fanout_authoritative = False

    def register(
        self,
        event_type: str,
        consumer_name: str,
        handler: InternalEventConsumerHandler,
        *,
        consumer_type: InternalEventConsumerType = "projection",
        max_attempts: int = 5,
    ) -> None:
        event_type = str(event_type or "").strip()
        consumer_name = str(consumer_name or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        if not consumer_name:
            raise ValueError("consumer_name is required")
        normalized_max_attempts = max(1, int(max_attempts or 5))
        registered = RegisteredInternalEventConsumer(
            event_type=event_type,
            consumer_name=consumer_name,
            consumer_type=consumer_type,
            handler=handler,
            max_attempts=normalized_max_attempts,
        )
        if self._fanout_authoritative:
            for index, current in enumerate(self._consumers[event_type]):
                if current.consumer_name != consumer_name:
                    continue
                if current.consumer_type != consumer_type or current.max_attempts != normalized_max_attempts:
                    raise RuntimeError("internal event fanout contract is sealed")
                # Handler bindings are runtime composition details and may be
                # refreshed without changing the authoritative fan-out shape.
                self._consumers[event_type][index] = registered
                return
            raise RuntimeError("internal event fanout contract is sealed")
        existing = [item for item in self._consumers[event_type] if item.consumer_name != consumer_name]
        existing.append(registered)
        self._consumers[event_type] = existing

    def register_handler_alias(
        self,
        event_type: str,
        consumer_name: str,
        handler: InternalEventConsumerHandler,
    ) -> None:
        event_type = str(event_type or "").strip()
        consumer_name = str(consumer_name or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        if not consumer_name:
            raise ValueError("consumer_name is required")
        self._handler_aliases[(event_type, consumer_name)] = handler

    def list_for_event_type(self, event_type: str) -> list[RegisteredInternalEventConsumer]:
        return list(self._consumers.get(str(event_type or "").strip(), []))

    def get_handler(self, event_type: str, consumer_name: str) -> InternalEventConsumerHandler | None:
        event_type = str(event_type or "").strip()
        consumer_name = str(consumer_name or "").strip()
        for consumer in self.list_for_event_type(event_type):
            if consumer.consumer_name == consumer_name:
                return consumer.handler
        return self._handler_aliases.get((event_type, consumer_name))

    def aliases_to_dict(self) -> list[dict[str, Any]]:
        return [
            {"event_type": event_type, "consumer_name": consumer_name, "handler_registered": handler is not None}
            for (event_type, consumer_name), handler in sorted(self._handler_aliases.items())
        ]

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {event_type: [consumer.to_dict() for consumer in consumers] for event_type, consumers in self._consumers.items()}

    @property
    def is_fanout_authoritative(self) -> bool:
        return self._fanout_authoritative

    def seal_fanout_contract(self) -> None:
        self._fanout_authoritative = True

    def remove_event_type(self, event_type: str) -> None:
        if self._fanout_authoritative:
            raise RuntimeError("internal event fanout contract is sealed")
        normalized = str(event_type or "").strip()
        self._consumers.pop(normalized, None)
        self._handler_aliases = {
            key: handler
            for key, handler in self._handler_aliases.items()
            if key[0] != normalized
        }

    def fanout_manifest_for(self, event_type: str) -> dict[str, Any]:
        if not self._fanout_authoritative:
            raise RuntimeError("internal event fanout contract is not sealed")
        normalized_event_type = str(event_type or "").strip()
        return build_fanout_manifest(
            normalized_event_type,
            self.list_for_event_type(normalized_event_type),
        )

    def clear(self) -> None:
        self._consumers.clear()
        self._handler_aliases.clear()
        self._fanout_authoritative = False


DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY = InternalEventConsumerRegistry()
_SCOPED_INTERNAL_EVENT_CONSUMER_REGISTRY: ContextVar[InternalEventConsumerRegistry | None] = ContextVar(
    "aicrm_internal_event_consumer_registry",
    default=None,
)


def current_internal_event_consumer_registry() -> InternalEventConsumerRegistry:
    return _SCOPED_INTERNAL_EVENT_CONSUMER_REGISTRY.get() or DEFAULT_INTERNAL_EVENT_CONSUMER_REGISTRY


@contextmanager
def internal_event_consumer_registry_scope(
    registry: InternalEventConsumerRegistry,
) -> Iterator[InternalEventConsumerRegistry]:
    token = _SCOPED_INTERNAL_EVENT_CONSUMER_REGISTRY.set(registry)
    try:
        yield registry
    finally:
        _SCOPED_INTERNAL_EVENT_CONSUMER_REGISTRY.reset(token)
