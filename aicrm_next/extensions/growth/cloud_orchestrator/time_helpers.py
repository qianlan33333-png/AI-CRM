from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_SEND_TIME = "09:00"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def campaign_step_due_iso(
    *,
    anchor_date: str,
    day_offset: int,
    send_time: str,
    step_timezone: str = DEFAULT_TIMEZONE,
    fallback_to_timezone_today: bool = False,
) -> str:
    try:
        tzinfo = ZoneInfo(step_timezone or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        tzinfo = ZoneInfo(DEFAULT_TIMEZONE)
    try:
        base = datetime.fromisoformat((anchor_date or "")[:10])
    except ValueError:
        if fallback_to_timezone_today:
            base = datetime.now(tzinfo).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    try:
        hour_str, minute_str = (send_time or DEFAULT_SEND_TIME).split(":")[:2]
        base = base.replace(hour=int(hour_str), minute=int(minute_str))
    except (TypeError, ValueError):
        pass
    base = base + timedelta(days=int(day_offset or 0))
    return base.replace(tzinfo=tzinfo).isoformat()
