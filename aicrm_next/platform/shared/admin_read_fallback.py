from __future__ import annotations

import logging
from typing import Any

from .repository_provider import blocked_production_payload

logger = logging.getLogger(__name__)


def admin_read_unavailable_payload(
    *,
    capability_owner: str,
    page_error: str,
    exc: Exception | None = None,
    items_keys: tuple[str, ...] = ("items",),
    count_keys: tuple[str, ...] = ("total",),
    extra: dict[str, Any] | None = None,
    status_code: int = 200,
) -> dict[str, Any]:
    payload = blocked_production_payload(capability_owner=capability_owner, detail=page_error)
    payload.update(
        {
            "ok": True,
            "degraded": True,
            "error": "",
            "error_code": "production_read_unavailable",
            "source_status": "production_unavailable",
            "read_model_status": "unavailable",
            "route_owner": "ai_crm_next",
            "fallback_used": False,
            "real_external_call_executed": False,
            "status_code": int(status_code or 200),
        }
    )
    for key in items_keys:
        payload[key] = []
    for key in count_keys:
        payload[key] = 0
    if exc is not None:
        logger.warning(
            "Admin read model unavailable for %s; returning degraded payload.",
            capability_owner,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        payload.setdefault("diagnostics", {})["error_class"] = exc.__class__.__name__
    if extra:
        payload.update(extra)
    return payload
