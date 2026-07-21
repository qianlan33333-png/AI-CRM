from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy import text

from .models import (
    ExternalEffectAttempt,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
    public_datetime,
    utcnow,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def is_rate_limited(result: ExternalEffectDispatchResult) -> bool:
    code = _text(result.error_code).lower()
    if code in {"rate_limited", "http_429"} or "429" in code:
        return True
    summary = dict(result.response_summary or {})
    classification = _text(summary.get("provider_error_classification")).lower()
    if classification in {"rate_limited", "rate_limit", "throttled"}:
        return True
    for key in ("status_code", "http_status"):
        try:
            if int(summary.get(key) or 0) == 429:
                return True
        except (TypeError, ValueError):
            pass
    try:
        return int(summary.get("errcode") or 0) in {45009, 45011}
    except (TypeError, ValueError):
        return False


def _scope_metadata(job: ExternalEffectJob) -> tuple[str, str]:
    payload = dict(job.payload_json or {})
    wecom_provider = _text(job.adapter_name).startswith("wecom")
    corp_id = next(
        (
            _text(payload.get(key))
            for key in ("corp_id", "CorpId", "ToUserName", "wecom_corp_id")
            if _text(payload.get(key))
        ),
        _text(os.getenv("WECOM_CORP_ID")) if wecom_provider else "",
    )
    app_id = next(
        (
            _text(payload.get(key))
            for key in ("app_id", "agent_id", "wecom_agent_id")
            if _text(payload.get(key))
        ),
        _text(os.getenv("WECOM_AGENT_ID")) if wecom_provider else "",
    )
    return corp_id[:160], app_id[:160]


def persist_rate_limit_cooldown(
    session: Any,
    *,
    job: ExternalEffectJob,
    attempt: ExternalEffectAttempt,
    result: ExternalEffectDispatchResult,
    blocked_until: datetime | None,
) -> bool:
    if not is_rate_limited(result):
        return False
    corp_id, app_id = _scope_metadata(job)
    session.execute(
        text(
            """
            INSERT INTO queue_rate_scope_cooldown (
                rate_scope_key, provider, corp_id, app_id, operation,
                blocked_until, reason, source_attempt_id, updated_at
            ) VALUES (
                :rate_scope_key, :provider, :corp_id, :app_id, :operation,
                CAST(:blocked_until AS timestamptz), :reason,
                :source_attempt_id, CURRENT_TIMESTAMP
            )
            ON CONFLICT (rate_scope_key) DO UPDATE
            SET blocked_until = GREATEST(
                    queue_rate_scope_cooldown.blocked_until,
                    EXCLUDED.blocked_until
                ),
                provider = EXCLUDED.provider,
                corp_id = EXCLUDED.corp_id,
                app_id = EXCLUDED.app_id,
                operation = EXCLUDED.operation,
                reason = EXCLUDED.reason,
                source_attempt_id = EXCLUDED.source_attempt_id,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "rate_scope_key": job.rate_scope_key,
            "provider": job.adapter_name,
            "corp_id": corp_id,
            "app_id": app_id,
            "operation": job.operation,
            "blocked_until": public_datetime(blocked_until or utcnow()),
            "reason": _text(result.error_code) or "provider_429",
            "source_attempt_id": attempt.attempt_id,
        },
    )
    return True


__all__ = ["is_rate_limited", "persist_rate_limit_cooldown"]
