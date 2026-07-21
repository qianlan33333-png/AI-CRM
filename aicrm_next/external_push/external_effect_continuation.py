from __future__ import annotations

import logging
from typing import Any

from aicrm_next.platform_foundation.external_effects.continuations import ExternalEffectContinuation
from aicrm_next.platform_foundation.external_effects.models import (
    WEBHOOK_GENERIC_PUSH,
    WEBHOOK_ORDER_PAID_PUSH,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)
from aicrm_next.shared.safe_logging import safe_log_exception

from . import repo

LOGGER = logging.getLogger(__name__)


def _matches_external_push_delivery(
    job: ExternalEffectJob,
    _dispatch_result: ExternalEffectDispatchResult,
) -> bool:
    return (
        job.target_type == "external_push_delivery"
        and bool(str(job.target_id or "").strip())
        and job.effect_type in {WEBHOOK_ORDER_PAID_PUSH, WEBHOOK_GENERIC_PUSH}
    )


def _matches_external_push_terminal_delivery(
    job: ExternalEffectJob,
    dispatch_result: ExternalEffectDispatchResult,
) -> bool:
    return _matches_external_push_delivery(job, dispatch_result) and job.status != "succeeded"


def _response_status(dispatch_result: ExternalEffectDispatchResult) -> int | None:
    value: Any = (dispatch_result.response_summary or {}).get("status_code")
    try:
        status = int(value)
    except (TypeError, ValueError):
        return None
    return status if 100 <= status <= 599 else None


def _project_external_push_delivery_after_success(
    job: ExternalEffectJob,
    dispatch_result: ExternalEffectDispatchResult,
) -> dict[str, Any]:
    try:
        delivery = repo.build_external_push_repository().mark_delivery_succeeded_from_external_effect(
            str(job.target_id or "").strip(),
            external_effect_job_id=int(job.id or 0),
            response_status=_response_status(dispatch_result),
        )
    except Exception as exc:
        safe_log_exception(
            LOGGER,
            "external push delivery post-success projection failed",
            exc,
            external_effect_job_id=int(job.id or 0),
        )
        return {"ok": False, "projection_type": "external_push_delivery", "error": str(exc)[:500]}
    if not delivery:
        return {
            "ok": False,
            "projection_type": "external_push_delivery",
            "error": "external_push_delivery_not_found",
        }
    return {
        "ok": True,
        "projection_type": "external_push_delivery",
        "delivery_status": "success",
        "response_status": delivery.get("response_status"),
    }


def _project_external_push_terminal_delivery(
    job: ExternalEffectJob,
    _dispatch_result: ExternalEffectDispatchResult,
) -> dict[str, Any]:
    try:
        delivery = repo.build_external_push_repository().mark_delivery_settled_from_external_effect(
            str(job.target_id or "").strip(),
            external_effect_job_id=int(job.id or 0),
            external_effect_status=str(job.status or "").strip(),
            error_code=str(job.last_error_code or "").strip(),
        )
    except Exception as exc:
        safe_log_exception(
            LOGGER,
            "external push delivery terminal projection failed",
            exc,
            external_effect_job_id=int(job.id or 0),
        )
        return {"ok": False, "projection_type": "external_push_delivery", "error": str(exc)[:500]}
    if not delivery:
        return {"ok": False, "projection_type": "external_push_delivery", "error": "external_push_delivery_not_found"}
    return {
        "ok": True,
        "projection_type": "external_push_delivery",
        "delivery_status": delivery.get("status"),
        "external_effect_status": job.status,
    }


EXTERNAL_PUSH_DELIVERY_CONTINUATION = ExternalEffectContinuation(
    name="external_push_delivery",
    matches=_matches_external_push_delivery,
    run=_project_external_push_delivery_after_success,
)

EXTERNAL_PUSH_DELIVERY_SETTLEMENT_CONTINUATION = ExternalEffectContinuation(
    name="external_push_delivery_settlement",
    matches=_matches_external_push_terminal_delivery,
    run=_project_external_push_terminal_delivery,
)


__all__ = [
    "EXTERNAL_PUSH_DELIVERY_CONTINUATION",
    "EXTERNAL_PUSH_DELIVERY_SETTLEMENT_CONTINUATION",
]
