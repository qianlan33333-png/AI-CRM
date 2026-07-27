from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .repository import (
    connect as connect,
    database_url as database_url,
    has_database_url as has_database_url,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
