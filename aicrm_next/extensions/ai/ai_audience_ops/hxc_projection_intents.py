from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from aicrm_next.platform.platform_foundation.command_bus.models import CommandContext
from aicrm_next.platform.platform_foundation.internal_events.models import (
    InternalEventCreateRequest,
)
from aicrm_next.platform.platform_foundation.internal_events.outbox import (
    enqueue_internal_event_outbox_in_session,
)
from aicrm_next.platform.shared.db_session import get_session_factory

from .event_types import (
    HXC_DAILY_PROJECTION_REQUESTED_EVENT,
    HXC_INCREMENTAL_PROJECTION_REQUESTED_EVENT,
)


class HxcProjectionRefreshIntentService:
    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()

    def daily_refresh_due(
        self,
        *,
        now: datetime | None = None,
        daily_refresh_time: str = "02:00",
        timezone_name: str = "Asia/Shanghai",
    ) -> bool:
        local_zone = ZoneInfo(timezone_name)
        reference = now or datetime.now(local_zone)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=local_zone)
        local_now = reference.astimezone(local_zone)
        hour, minute = _parse_hhmm(daily_refresh_time)
        boundary = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_now < boundary:
            boundary -= timedelta(days=1)
        try:
            with self._session_factory() as session:
                last_full_refreshed_at = session.execute(
                    text(
                        """
                        SELECT last_full_refreshed_at
                        FROM ai_audience_hxc_member_usage_projection_control
                        WHERE singleton = TRUE
                        """
                    )
                ).scalar_one_or_none()
            if not isinstance(last_full_refreshed_at, datetime):
                return True
            current = last_full_refreshed_at
            if current.tzinfo is None:
                current = current.replace(tzinfo=local_zone)
            return current.astimezone(local_zone) < boundary
        except Exception:
            return False

    def request(
        self,
        refresh_kind: str,
        *,
        bucket: str,
        actor_id: str = "ai_audience_scheduler",
    ) -> dict[str, Any]:
        normalized_kind = "daily" if str(refresh_kind or "").strip() == "daily" else "incremental"
        normalized_bucket = str(bucket or "").strip()
        if not normalized_bucket:
            return {
                "ok": False,
                "error_code": "hxc_projection_refresh_bucket_required",
                "real_external_call_executed": False,
            }
        event_type = (
            HXC_DAILY_PROJECTION_REQUESTED_EVENT
            if normalized_kind == "daily"
            else HXC_INCREMENTAL_PROJECTION_REQUESTED_EVENT
        )
        try:
            with self._session_factory() as session:
                outbox = enqueue_internal_event_outbox_in_session(
                    session,
                    InternalEventCreateRequest(
                        event_type=event_type,
                        aggregate_type="ai_audience_hxc_projection",
                        aggregate_id=normalized_kind,
                        payload={
                            "refresh_kind": normalized_kind,
                            "bucket": normalized_bucket,
                        },
                        payload_summary={
                            "refresh_kind": normalized_kind,
                            "bucket": normalized_bucket,
                        },
                        context=CommandContext(
                            actor_id=str(actor_id or "ai_audience_scheduler").strip(),
                            actor_type="system",
                            source_route="ai_audience_refresh_intent_clock",
                        ),
                        idempotency_key=(
                            f"ai_audience.hxc_projection:{normalized_kind}:"
                            f"{normalized_bucket}"
                        ),
                        source_module="aicrm_next.extensions.ai.ai_audience_ops.hxc_projection_intents",
                    ),
                )
                session.commit()
            return {
                "ok": True,
                "refresh_kind": normalized_kind,
                "intent_status": str(outbox.get("status") or "pending"),
                "real_external_call_executed": False,
            }
        except Exception:
            return {
                "ok": False,
                "refresh_kind": normalized_kind,
                "error_code": "hxc_projection_refresh_intent_enqueue_failed",
                "real_external_call_executed": False,
            }


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = str(value or "02:00").strip().split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        return 2, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 2, 0
    return hour, minute
