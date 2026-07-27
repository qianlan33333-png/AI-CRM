from __future__ import annotations

import logging
from typing import Any

from aicrm_next.platform_foundation.external_effects.continuations import ExternalEffectContinuation
from aicrm_next.platform_foundation.external_effects.models import (
    WEBHOOK_GENERIC_PUSH,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)
from aicrm_next.shared.safe_logging import safe_log_exception

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
        )
    except Exception as exc:
        safe_log_exception(
            LOGGER,
            "automation agent post-success continuation failed",
            exc,
            external_effect_job_id=int(job.id or 0),
            batch_id=batch_id,
        )
        return {"ok": False, "batch_id": batch_id, "error": str(exc)[:500]}
    return dict(result)


AUTOMATION_AGENT_AUDIENCE_WEBHOOK_CONTINUATION = ExternalEffectContinuation(
    name="automation_agent_audience_webhook",
    matches=_matches_automation_agent_audience_webhook,
    run=_continue_automation_agent_audience_webhook,
)
