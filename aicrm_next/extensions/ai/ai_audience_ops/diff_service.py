from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .repository import _text, payload_hash


def normalize_audience_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = {"value": payload}
        payload = decoded if isinstance(decoded, dict) else {"value": decoded}
    elif not isinstance(payload, dict):
        payload = {"value": payload} if payload is not None else {}

    identity_type = _text(row.get("identity_type"))
    identity_value = _text(row.get("identity_value"))
    event_source_key = _text(row.get("event_source_key"))
    if not identity_type or not identity_value or not event_source_key:
        raise ValueError("identity_type, identity_value and event_source_key are required")
    normalized = {
        "identity_type": identity_type,
        "identity_value": identity_value,
        "unionid": _text(row.get("unionid") or (identity_value if identity_type == "unionid" else "")),
        "event_source_key": event_source_key,
        "payload_json": dict(payload),
        "payload_hash": payload_hash(dict(payload)),
        "external_userid": _text(row.get("external_userid") or (identity_value if identity_type == "external_userid" else "")),
        "mobile_hash": _text(row.get("mobile_hash") or (identity_value if identity_type == "mobile_hash" else "")),
        "owner_userid": _text(row.get("owner_userid")),
        "event_at": parse_datetime(row.get("event_at")) or parse_datetime(row.get("submitted_at")) or parse_datetime(row.get("paid_at")),
    }
    return normalized


def identity_key(row: dict[str, Any]) -> tuple[str, str]:
    return (_text(row.get("identity_type")), _text(row.get("identity_value")))


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text_value = _text(value)
    if not text_value:
        return None
    try:
        dt = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def member_event_idempotency_key(*, package_id: int, event_type: str, normalized: dict[str, Any], run_id: int | None = None) -> str:
    source_key = _text(normalized.get("event_source_key")) or f"{normalized.get('identity_type')}:{normalized.get('identity_value')}"
    if event_type == "updated":
        source_key = f"{source_key}:{normalized.get('payload_hash')}"
    if event_type == "exited":
        source_key = f"{normalized.get('identity_type')}:{normalized.get('identity_value')}:{run_id or ''}"
    return f"ai_audience:{int(package_id)}:{event_type}:{source_key}"
