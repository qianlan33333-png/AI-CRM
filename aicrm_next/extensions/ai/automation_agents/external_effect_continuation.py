from __future__ import annotations

import logging
from typing import Any

from aicrm_next.platform.platform_foundation.external_effects.continuations import ExternalEffectContinuation
from aicrm_next.platform.platform_foundation.external_effects.models import (
    AI_AGENT_GENERATE,
    WEBHOOK_GENERIC_PUSH,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)
from aicrm_next.platform.shared.safe_logging import safe_exception_summary, safe_log_exception

from .worker import AutomationAgentWorker
from .internal_webhook_adapter import automation_agent_code_from_webhook_url

LOGGER = logging.getLogger(__name__)


def _matches_automation_agent_audience_webhook(
    job: ExternalEffectJob,
    _dispatch_result: ExternalEffectDispatchResult,
) -> bool:
    if job.effect_type != WEBHOOK_GENERIC_PUSH:
        return False
    payload = dict(job.payload_json or {})
    url = str(payload.get("webhook_url") or payload.get("target_url") or "").strip()
    return bool(automation_agent_code_from_webhook_url(url))


def _automation_agent_batch_id(response_summary: dict[str, Any] | None) -> str:
    summary = dict(response_summary or {})
    candidates = [summary.get("automation_agent_batch_id"), summary.get("batch_id")]
    response_json = summary.get("response_json") if isinstance(summary.get("response_json"), dict) else {}
    candidates.extend([response_json.get("automation_agent_batch_id"), response_json.get("batch_id")])
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value.startswith("agent_batch_"):
            return value
    return ""


def _continue_automation_agent_audience_webhook(
    job: ExternalEffectJob,
    dispatch_result: ExternalEffectDispatchResult,
) -> dict[str, Any]:
    batch_id = _automation_agent_batch_id(dispatch_result.response_summary)
    if not batch_id:
        return {"ok": False, "error": "automation_agent_batch_id_missing"}
    try:
        result = AutomationAgentWorker().run_batch_and_enqueue_broadcast_jobs(
            batch_id,
            operator="external_effect_agent_continuation",
            parent_execution_id=job.execution_id,
        )
    except Exception as exc:
        safe_log_exception(
            LOGGER,
            (
                "automation agent post-success continuation failed; "
                f"batch_id={batch_id}; external_effect_job_id={int(job.id or 0)}; "
                "recovery=durable_internal_event_retry"
            ),
            exc,
            external_effect_job_id=int(job.id or 0),
            batch_id=batch_id,
        )
        return {
            "ok": False,
            "batch_id": batch_id,
            "error": safe_exception_summary(exc, limit=500),
            "recovery": "durable_internal_event_retry",
        }
    return dict(result)


AUTOMATION_AGENT_AUDIENCE_WEBHOOK_CONTINUATION = ExternalEffectContinuation(
    name="automation_agent_audience_webhook",
    matches=_matches_automation_agent_audience_webhook,
    run=_continue_automation_agent_audience_webhook,
)


def _generation_item_id(job: ExternalEffectJob) -> int:
    payload = dict(job.payload_json or {})
    try:
        payload_item_id = int(payload.get("item_id") or 0)
        target_item_id = int(job.target_id or 0)
    except (TypeError, ValueError):
        return 0
    if payload_item_id <= 0 or target_item_id <= 0 or payload_item_id != target_item_id:
        return 0
    return payload_item_id


def _matches_generation(
    job: ExternalEffectJob,
    _dispatch_result: ExternalEffectDispatchResult,
) -> bool:
    return (
        job.effect_type == AI_AGENT_GENERATE
        and str(job.business_type or "").strip() == "automation_agent_generation"
        and _generation_item_id(job) > 0
    )


def _complete_generation(
    job: ExternalEffectJob,
    dispatch_result: ExternalEffectDispatchResult,
) -> dict[str, Any]:
    final_text = str((dispatch_result.provider_result or {}).get("final_text") or "").strip()
    if not final_text:
        return {"ok": False, "error": "generation_provider_result_missing"}
    result = AutomationAgentWorker().complete_generation(
        item_id=_generation_item_id(job),
        generation_effect_job_id=int(job.id),
        final_text=final_text,
    )
    if result.get("ok"):
        from aicrm_next.platform.platform_foundation.external_effects.repo import build_external_effect_repository

        consume = getattr(build_external_effect_repository(), "consume_attempt_provider_result", None)
        if callable(consume):
            result["provider_result_consumed"] = bool(
                consume(str(job.last_attempt_id or ""), job_id=int(job.id))
            )
    return result


def _matches_generation_terminal(
    job: ExternalEffectJob,
    dispatch_result: ExternalEffectDispatchResult,
) -> bool:
    return _matches_generation(job, dispatch_result) and job.status != "succeeded"


def _settle_generation_terminal(
    job: ExternalEffectJob,
    _dispatch_result: ExternalEffectDispatchResult,
) -> dict[str, Any]:
    return AutomationAgentWorker().settle_generation_failure(
        item_id=_generation_item_id(job),
        generation_effect_job_id=int(job.id),
        error_code=str(job.last_error_code or f"ai_generation_{job.status}"),
        error_message=str(job.last_error_message or f"AI generation settled as {job.status}"),
    )


AUTOMATION_AGENT_GENERATION_CONTINUATION = ExternalEffectContinuation(
    name="automation_agent_generation_completion",
    matches=_matches_generation,
    run=_complete_generation,
    requires_provider_result=True,
)

AUTOMATION_AGENT_GENERATION_SETTLEMENT_CONTINUATION = ExternalEffectContinuation(
    name="automation_agent_generation_settlement",
    matches=_matches_generation_terminal,
    run=_settle_generation_terminal,
)


__all__ = [
    "AUTOMATION_AGENT_AUDIENCE_WEBHOOK_CONTINUATION",
    "AUTOMATION_AGENT_GENERATION_CONTINUATION",
    "AUTOMATION_AGENT_GENERATION_SETTLEMENT_CONTINUATION",
]
