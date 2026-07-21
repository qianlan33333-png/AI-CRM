from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aicrm_next.platform_foundation.internal_events import PAYMENT_SUCCEEDED_EVENT_TYPES, QUESTIONNAIRE_SUBMITTED_EVENT_TYPE
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker

from .event_types import (
    DAILY_REFRESH_CONSUMER,
    DAILY_TICK_EVENT,
    INCREMENTAL_REFRESH_CONSUMER,
    INCREMENTAL_TICK_EVENT,
    OUTBOUND_EFFECT_CONSUMER,
    REFRESH_INTENT_CONSUMER,
    REFRESH_REQUESTED_EVENT,
    RUN_REFRESHED_EVENT,
    SOURCE_CHANGED_EVENT,
    SOURCE_POKE_CONSUMER,
)
from .service import AudiencePackageService


DEFAULT_DAILY_REFRESH_TIME = "02:00"
DEFAULT_DAILY_TICK_WINDOW_MINUTES = 60


def emit_due_ticks(
    *,
    include_daily: bool = True,
    include_incremental: bool = True,
    now: datetime | None = None,
    daily_refresh_time: str = DEFAULT_DAILY_REFRESH_TIME,
    daily_window_minutes: int = DEFAULT_DAILY_TICK_WINDOW_MINUTES,
    force_daily: bool = False,
) -> dict[str, Any]:
    service = AudiencePackageService()
    items: list[dict[str, Any]] = []
    if include_incremental:
        items.append({"tick_type": "incremental", "result": service.emit_tick("incremental")})
    window_daily_due = False
    daily_due = False
    if include_daily:
        window_daily_due = _daily_tick_window_open(
            now=now,
            daily_refresh_time=daily_refresh_time,
            daily_window_minutes=daily_window_minutes,
        )
        daily_due = bool(force_daily) or window_daily_due or _launch_daily_refresh_due(service)
    if include_daily and daily_due:
        items.append({"tick_type": "daily", "result": service.emit_tick("daily")})
    return {
        "ok": True,
        "items": items,
        "daily_tick_due": daily_due,
        "daily_tick_window_due": window_daily_due,
        "daily_tick_forced": bool(force_daily),
        "daily_refresh_time": daily_refresh_time,
        "daily_window_minutes": max(1, int(daily_window_minutes or DEFAULT_DAILY_TICK_WINDOW_MINUTES)),
        "real_external_call_executed": False,
    }


def run_due_refresh_consumers(
    *,
    dry_run: bool = True,
    batch_size: int = 20,
    include_incremental: bool = True,
    include_daily: bool = True,
) -> dict[str, Any]:
    event_types: list[str] = []
    consumer_names: list[str] = []
    if include_incremental:
        event_types.append(INCREMENTAL_TICK_EVENT)
        consumer_names.append(INCREMENTAL_REFRESH_CONSUMER)
    if include_daily:
        event_types.append(DAILY_TICK_EVENT)
        consumer_names.append(DAILY_REFRESH_CONSUMER)
    if not event_types:
        return {
            "ok": True,
            "candidate_count": 0,
            "processed_count": 0,
            "skipped": "no_refresh_consumers_selected",
            "real_external_call_executed": False,
        }
    return InternalEventWorker(relay_role="consumer_only").run_due(
        batch_size=batch_size,
        dry_run=dry_run,
        event_types=event_types,
        consumer_names=consumer_names,
    )


def run_due_source_poke_consumers(*, dry_run: bool = True, batch_size: int = 20) -> dict[str, Any]:
    return InternalEventWorker(relay_role="consumer_only").run_due(
        batch_size=batch_size,
        dry_run=dry_run,
        event_types=_source_poke_event_types(),
        consumer_names=[SOURCE_POKE_CONSUMER],
    )


def run_due_outbound_consumers(*, dry_run: bool = True, batch_size: int = 20) -> dict[str, Any]:
    return InternalEventWorker(relay_role="consumer_only").run_due(
        batch_size=batch_size,
        dry_run=dry_run,
        event_types=[RUN_REFRESHED_EVENT],
        consumer_names=[OUTBOUND_EFFECT_CONSUMER],
    )


def run_due_ai_audience_consumers(
    *,
    dry_run: bool = True,
    batch_size: int = 20,
    include_incremental_refresh: bool = True,
    include_daily_refresh: bool = True,
) -> dict[str, Any]:
    source_poke = run_due_source_poke_consumers(dry_run=dry_run, batch_size=batch_size)
    refresh = run_due_refresh_consumers(
        dry_run=dry_run,
        batch_size=batch_size,
        include_incremental=include_incremental_refresh,
        include_daily=include_daily_refresh,
    )
    outbound = run_due_outbound_consumers(dry_run=dry_run, batch_size=batch_size)
    return {
        "ok": bool(source_poke.get("ok")) and bool(refresh.get("ok")) and bool(outbound.get("ok")),
        "source_poke": source_poke,
        "refresh": refresh,
        "outbound": outbound,
        "real_external_call_executed": False,
    }


def ai_audience_event_consumer_pairs(*, include_source_poke: bool = True, include_refresh: bool = True, include_outbound: bool = True) -> list[str]:
    pairs: list[str] = []
    if include_source_poke:
        pairs.extend(f"{event_type}:{SOURCE_POKE_CONSUMER}" for event_type in _source_poke_event_types())
    if include_refresh:
        pairs.extend(
            [
                f"{INCREMENTAL_TICK_EVENT}:{INCREMENTAL_REFRESH_CONSUMER}",
                f"{DAILY_TICK_EVENT}:{DAILY_REFRESH_CONSUMER}",
                f"{REFRESH_REQUESTED_EVENT}:{REFRESH_INTENT_CONSUMER}",
            ]
        )
    if include_outbound:
        pairs.append(f"{RUN_REFRESHED_EVENT}:{OUTBOUND_EFFECT_CONSUMER}")
    return pairs


def _source_poke_event_types() -> list[str]:
    return [
        SOURCE_CHANGED_EVENT,
        "channel_entry.entered",
        QUESTIONNAIRE_SUBMITTED_EVENT_TYPE,
        "external_form.submitted",
        *PAYMENT_SUCCEEDED_EVENT_TYPES,
    ]


def _launch_daily_refresh_due(service: Any) -> bool:
    checker = getattr(service, "has_launch_refresh_due", None)
    if not callable(checker):
        return False
    try:
        return bool(checker("daily"))
    except Exception:
        return False


def _daily_tick_window_open(
    *,
    now: datetime | None = None,
    daily_refresh_time: str = DEFAULT_DAILY_REFRESH_TIME,
    daily_window_minutes: int = DEFAULT_DAILY_TICK_WINDOW_MINUTES,
    timezone_name: str = "Asia/Shanghai",
) -> bool:
    local_now = (now or datetime.now(ZoneInfo(timezone_name))).astimezone(ZoneInfo(timezone_name))
    hour, minute = _parse_hhmm(daily_refresh_time)
    start = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    minutes_since_start = (local_now - start).total_seconds() / 60
    return 0 <= minutes_since_start < max(1, int(daily_window_minutes or DEFAULT_DAILY_TICK_WINDOW_MINUTES))


def _parse_hhmm(value: str) -> tuple[int, int]:
    raw = str(value or "").strip() or DEFAULT_DAILY_REFRESH_TIME
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        return 2, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 2, 0
    return hour, minute
