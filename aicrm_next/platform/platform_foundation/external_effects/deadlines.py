from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


PROVIDER_DEADLINE_FIELD = "provider_deadline_at"


def provider_deadline_at(payload: dict[str, Any] | None) -> datetime | None | bool:
    """Return a UTC deadline, ``None`` when absent, or ``False`` when invalid."""

    raw = str(dict(payload or {}).get(PROVIDER_DEADLINE_FIELD) or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def provider_deadline_elapsed(
    payload: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> bool:
    deadline = provider_deadline_at(payload)
    if deadline is None:
        return False
    if deadline is False:
        return True
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return deadline <= current.astimezone(timezone.utc)


__all__ = [
    "PROVIDER_DEADLINE_FIELD",
    "provider_deadline_at",
    "provider_deadline_elapsed",
]
