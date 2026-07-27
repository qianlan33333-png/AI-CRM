from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

from aicrm_next.shared.errors import ContractError


TENANT_ID = "aicrm"
BUY_BUTTON_TEXT = "立即报名"
EVENT_TYPES = {
    "activated",
    "renewed",
    "expired",
    "disabled",
    "refunded",
    "grant_failed_missing_unionid",
    "membership_sync_failed",
    "admin_adjusted",
}


def text(value: Any) -> str:
    return str(value or "").strip()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    normalized = text(value)
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def isoformat(value: Any) -> str:
    parsed = parse_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else ""


def normalize_link_slug(value: Any) -> str:
    slug = text(value).strip("/")
    if not slug or "\\" in slug or slug.startswith(".") or "//" in slug:
        raise ContractError("link_slug is invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", slug):
        raise ContractError("link_slug only supports letters, numbers, dot, underscore and dash")
    return slug


def validate_duration_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractError("duration_days must be a positive integer") from exc
    if days <= 0:
        raise ContractError("duration_days must be greater than 0")
    return days


def entitlement_status(end_at: Any, status: Any, *, now: datetime | None = None) -> str:
    normalized = text(status) or "none"
    if normalized and normalized not in {"active", "expired", "disabled", "refunded"}:
        normalized = "none"
    if normalized != "active":
        return normalized
    end = parse_datetime(end_at)
    if end and end <= (now or utcnow()):
        return "expired"
    return "active"


def remaining_days(end_at: Any, *, now: datetime | None = None) -> int:
    end = parse_datetime(end_at)
    if not end:
        return 0
    seconds = (end - (now or utcnow())).total_seconds()
    if seconds <= 0:
        return 0
    return max(1, int(math.ceil(seconds / 86400)))


def cta_text_for_status(status: str) -> str:
    if status == "active":
        return "立即续费"
    if status == "expired":
        return "重新开通"
    return "立即报名"


def event_id_for(event_type: str, out_trade_no: str) -> str:
    normalized_type = text(event_type)
    if normalized_type not in EVENT_TYPES:
        raise ContractError("unsupported service period event_type")
    normalized_trade_no = text(out_trade_no)
    if normalized_trade_no:
        return f"service_period:{normalized_type}:{normalized_trade_no}"
    return f"service_period:{normalized_type}:{utcnow().timestamp():.6f}"
