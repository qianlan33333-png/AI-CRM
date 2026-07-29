from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from itertools import count
from typing import Any


Json = dict[str, Any]

_COUNTER = count(1)
_EVENTS: list[Json] = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AuditEvent:
    audit_id: str
    adapter: str
    operation: str
    mode: str
    idempotency_key: str
    side_effect_executed: bool
    status: str
    error_code: str
    created_at: str


def record_audit_event(
    *,
    adapter: str,
    operation: str,
    mode: str,
    idempotency_key: str,
    side_effect_executed: bool,
    status: str,
    error_code: str = "",
) -> Json:
    event = AuditEvent(
        audit_id=f"audit_gateway_{next(_COUNTER):06d}",
        adapter=adapter,
        operation=operation,
        mode=mode,
        idempotency_key=idempotency_key,
        side_effect_executed=side_effect_executed,
        status=status,
        error_code=error_code,
        created_at=_now_iso(),
    )
    payload = asdict(event)
    _EVENTS.append(payload)
    return deepcopy(payload)


def list_audit_events() -> list[Json]:
    return deepcopy(_EVENTS)


def reset_audit_events() -> None:
    global _COUNTER
    _EVENTS.clear()
    _COUNTER = count(1)
