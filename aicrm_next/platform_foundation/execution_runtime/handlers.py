from __future__ import annotations

from dataclasses import fields
from typing import Any, Callable

from aicrm_next.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform_foundation.internal_events.models import InternalEventOutboxRecord
from aicrm_next.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker

from .repository import RuntimeClaim


def external_effect_handler(worker: ExternalEffectWorker) -> Callable[[RuntimeClaim], dict[str, Any]]:
    return lambda claim: worker.dispatch_claimed(
        claim.item_id,
        lease_token=claim.lease_token,
    )


def internal_event_handler(worker: InternalEventWorker) -> Callable[[RuntimeClaim], dict[str, Any]]:
    return lambda claim: worker.dispatch_one(claim.item_id)


def internal_outbox_handler(relay: InternalEventOutboxRelay) -> Callable[[RuntimeClaim], dict[str, Any]]:
    field_names = {field.name for field in fields(InternalEventOutboxRecord)}

    def handle(claim: RuntimeClaim) -> dict[str, Any]:
        payload = {key: value for key, value in claim.payload.items() if key in field_names}
        payload["id"] = int(claim.item_id)
        payload["lease_token"] = claim.lease_token
        payload["worker_generation"] = claim.worker_generation
        return relay.relay_claimed(InternalEventOutboxRecord(**payload))

    return handle


def webhook_inbox_handler(worker: Any) -> Callable[[RuntimeClaim], dict[str, Any]]:
    return lambda claim: worker.dispatch_row(dict(claim.payload))


__all__ = [
    "external_effect_handler",
    "internal_event_handler",
    "internal_outbox_handler",
    "webhook_inbox_handler",
]
