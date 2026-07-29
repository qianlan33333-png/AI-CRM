from __future__ import annotations

from dataclasses import fields
from typing import Any, Callable

from aicrm_next.platform.platform_foundation.external_effects.worker import ExternalEffectWorker
from aicrm_next.platform.platform_foundation.internal_events.models import InternalEventOutboxRecord
from aicrm_next.platform.platform_foundation.internal_events.outbox import InternalEventOutboxRelay
from aicrm_next.platform.platform_foundation.internal_events.worker import InternalEventWorker

from .repository import RuntimeClaim
from .start_rate import SharedStartRateLimiter


WECOM_BULK_START_RATE_LANES = frozenset(
    {"wecom_bulk", "wecom_ai_assistant_bulk"}
)


def external_effect_handler(
    worker: ExternalEffectWorker,
    *,
    start_rate_limiter: SharedStartRateLimiter | None = None,
) -> Callable[[RuntimeClaim], dict[str, Any]]:
    def handle(claim: RuntimeClaim) -> dict[str, Any]:
        limiter = start_rate_limiter
        rate_scope_key = str(claim.payload.get("rate_scope_key") or "").strip()
        if limiter is not None and claim.lane in WECOM_BULK_START_RATE_LANES:
            limiter.acquire(rate_scope_key)
        result = worker.dispatch_claimed(
            claim.item_id,
            lease_token=claim.lease_token,
        )
        if limiter is not None and claim.lane in WECOM_BULK_START_RATE_LANES:
            job = result.get("job") if isinstance(result.get("job"), dict) else {}
            summary = (
                job.get("result_summary_json")
                if isinstance(job.get("result_summary_json"), dict)
                else {}
            )
            limiter.record_outcome(
                rate_scope_key,
                error_code=str(job.get("last_error_code") or result.get("error") or ""),
                provider_errcode=int(summary.get("errcode") or 0),
            )
        return result

    return handle


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
    "WECOM_BULK_START_RATE_LANES",
    "internal_event_handler",
    "internal_outbox_handler",
    "webhook_inbox_handler",
]
