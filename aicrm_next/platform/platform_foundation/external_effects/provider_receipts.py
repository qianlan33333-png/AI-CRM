from __future__ import annotations

from typing import Any

from aicrm_next.scrm_contracts import ProviderReceipt

from .models import ExternalEffectAttempt, ExternalEffectJob


def provider_receipt_from_external_effect(
    job: ExternalEffectJob,
    attempt: ExternalEffectAttempt,
    *,
    provider: str,
    raw_payload_ref: str = "",
) -> ProviderReceipt:
    summary = dict(attempt.response_summary_json or {})
    return ProviderReceipt(
        receipt_id=f"receipt:{attempt.attempt_id}",
        provider=str(provider or "").strip(),
        effect_type=job.effect_type,
        effect_id=f"external-effect:{job.id}",
        attempt_id=attempt.attempt_id,
        idempotency_key=job.idempotency_key,
        status=_receipt_status(attempt.status),
        provider_code=_summary_text(summary, "provider_code", "errcode", "code") or attempt.error_code,
        provider_message=(_summary_text(summary, "provider_message", "errmsg", "message") or attempt.error_message)[:500],
        provider_request_id=_summary_text(summary, "provider_request_id", "request_id", "msgid"),
        raw_payload_ref=str(raw_payload_ref or "").strip(),
        received_at=attempt.completed_at or job.completed_at,
    )


def _receipt_status(status: str) -> str:
    return {
        "succeeded": "delivered",
        "simulated": "accepted",
        "failed_retryable": "failed_retryable",
        "failed_terminal": "failed_terminal",
        "blocked": "failed_terminal",
        "unknown_after_dispatch": "unknown",
        "dispatching": "unknown",
        "skipped": "failed_terminal",
    }.get(str(status or "").strip(), "unknown")


def _summary_text(summary: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key not in summary or summary[key] is None:
            continue
        value = str(summary[key]).strip()
        if value:
            return value
    return ""


__all__ = ["provider_receipt_from_external_effect"]
