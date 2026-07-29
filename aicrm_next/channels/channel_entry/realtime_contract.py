"""Hard-real-time contract for WeCom channel welcome messages.

WeCom accepts a welcome code only for a short provider window.  The durable
rows below are therefore an idempotency/audit boundary, not a backlog that may
be replayed later.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from aicrm_next.platform.platform_foundation.external_effects.deadlines import PROVIDER_DEADLINE_FIELD


WECOM_WELCOME_PROVIDER_WINDOW_SECONDS = 20
WECOM_WELCOME_PROVIDER_GUARD_SECONDS = 2
WECOM_WELCOME_INGRESS_LANE = "wecom_welcome_ingress"
WECOM_WELCOME_EFFECT_LANE = "wecom_welcome"
WECOM_WELCOME_FALLBACK_SECONDS = 0.25


def utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def welcome_provider_deadline(received_at: datetime) -> datetime:
    normalized = utc_datetime(received_at)
    if normalized is None:
        raise ValueError("welcome callback received_at is required")
    usable_seconds = WECOM_WELCOME_PROVIDER_WINDOW_SECONDS - WECOM_WELCOME_PROVIDER_GUARD_SECONDS
    return normalized + timedelta(seconds=usable_seconds)


def welcome_ingress_lane(payload_summary: dict[str, Any] | None) -> str:
    summary = dict(payload_summary or {})
    if summary.get("welcome_code_present") is True:
        return WECOM_WELCOME_INGRESS_LANE
    return "webhook_inbox"


__all__ = [
    "PROVIDER_DEADLINE_FIELD",
    "WECOM_WELCOME_EFFECT_LANE",
    "WECOM_WELCOME_FALLBACK_SECONDS",
    "WECOM_WELCOME_INGRESS_LANE",
    "WECOM_WELCOME_PROVIDER_GUARD_SECONDS",
    "WECOM_WELCOME_PROVIDER_WINDOW_SECONDS",
    "utc_datetime",
    "welcome_ingress_lane",
    "welcome_provider_deadline",
]
