from __future__ import annotations

from dataclasses import replace
from typing import Any

from .models import ExternalEffectDispatchResult, ExternalEffectJob


UNCERTAIN_AFTER_DISPATCH_CODES = {
    "adapter_exception",
    "external_call_unknown",
    "network_error",
    "timeout",
}
FAKE_ADAPTER_MODES = {"fake", "fixture", "simulated", "test_fake"}
PROVIDER_EVIDENCE_KEYS = {
    "audit_id",
    "errcode",
    "http_status",
    "msgid",
    "provider_status",
    "receipt_id",
    "refund_id_present",
    "response_json",
    "status_code",
    "task_id",
    "wecom_msgid_present",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _adapter_mode(result: ExternalEffectDispatchResult) -> str:
    response = dict(result.response_summary or {})
    return _text(response.get("adapter_mode") or response.get("mode") or result.adapter_mode).lower()


def _provider_evidence(result: ExternalEffectDispatchResult) -> bool:
    if result.provider_result_received:
        return True
    response = dict(result.response_summary or {})
    return any(key in response and response.get(key) not in (None, "", False, {}) for key in PROVIDER_EVIDENCE_KEYS)


def _with_standard_evidence(
    result: ExternalEffectDispatchResult,
    *,
    status: str,
    side_effect_executed: bool,
    provider_result_received: bool,
    **changes: Any,
) -> ExternalEffectDispatchResult:
    response = {
        **dict(result.response_summary or {}),
        "real_external_call_executed": bool(side_effect_executed),
        "provider_result_received": bool(provider_result_received),
    }
    return replace(
        result,
        status=status,
        response_summary=response,
        real_external_call_executed=bool(side_effect_executed),
        provider_result_received=bool(provider_result_received),
        **changes,
    )


def normalize_dispatch_result(
    job: ExternalEffectJob,
    result: ExternalEffectDispatchResult,
) -> ExternalEffectDispatchResult:
    """Apply the single truth policy at the provider dispatch boundary."""

    status = _text(result.status)
    response = dict(result.response_summary or {})
    side_effect_executed = bool(result.real_external_call_executed)
    internal_side_effect_executed = response.get("internal_side_effect_executed") is True
    provider_result_received = _provider_evidence(result)
    adapter_mode = _adapter_mode(result)

    if status == "succeeded":
        if side_effect_executed and provider_result_received:
            return _with_standard_evidence(
                result,
                status="succeeded",
                side_effect_executed=True,
                provider_result_received=True,
            )
        if internal_side_effect_executed and provider_result_received:
            return _with_standard_evidence(
                result,
                status="succeeded",
                side_effect_executed=False,
                provider_result_received=True,
            )
        if not side_effect_executed and (
            adapter_mode in FAKE_ADAPTER_MODES or job.execution_mode == "execute_dryrun"
        ):
            return _with_standard_evidence(
                result,
                status="simulated",
                side_effect_executed=False,
                provider_result_received=False,
                error_code="",
                error_message="",
            )
        if side_effect_executed:
            return _with_standard_evidence(
                result,
                status="unknown_after_dispatch",
                side_effect_executed=True,
                provider_result_received=False,
                error_code=result.error_code or "provider_result_missing",
                error_message=result.error_message or "Provider call executed but no acceptable result or receipt was persisted.",
            )
        return _with_standard_evidence(
            result,
            status="blocked",
            side_effect_executed=False,
            provider_result_received=False,
            error_code=result.error_code or "success_without_side_effect",
            error_message=result.error_message or "Adapter reported success without executing a side effect.",
        )

    if not side_effect_executed and (response.get("blocked") is True or status == "blocked"):
        return _with_standard_evidence(
            result,
            status="blocked",
            side_effect_executed=False,
            provider_result_received=provider_result_received,
        )

    if (
        side_effect_executed
        and status in {"failed_retryable", "failed_terminal"}
        and _text(result.error_code) in UNCERTAIN_AFTER_DISPATCH_CODES
        and not provider_result_received
    ):
        return _with_standard_evidence(
            result,
            status="unknown_after_dispatch",
            side_effect_executed=True,
            provider_result_received=False,
        )

    return _with_standard_evidence(
        result,
        status=status or "blocked",
        side_effect_executed=side_effect_executed,
        provider_result_received=provider_result_received,
    )
