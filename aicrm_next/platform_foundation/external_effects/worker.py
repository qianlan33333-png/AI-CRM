from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from aicrm_next.platform_foundation.push_center.capability_registry import capability_for_section
from aicrm_next.shared.automation_agent_webhook_contract import automation_agent_code_from_webhook_url
from aicrm_next.platform_foundation.push_center.section_mapper import section_for_job
from aicrm_next.shared.runtime_settings import runtime_bool, runtime_setting
from aicrm_next.shared.safe_logging import safe_log_exception

from .adapters import DEFAULT_ADAPTER_REGISTRY, ExternalEffectAdapterRegistry
from .continuations import (
    EMPTY_EXTERNAL_EFFECT_CONTINUATION_REGISTRY,
    ExternalEffectContinuationRegistry,
)
from .execution_gates import (
    is_wecom_effect_type,
    typed_wecom_execution_block_reason,
    wecom_execution_disabled_message,
)
from .execution_policy import normalize_dispatch_result
from .models import (
    WECOM_MESSAGE_GROUP_SEND,
    ExternalEffectDispatchResult,
    ExternalEffectJob,
)
from .repo import ExternalEffectRepository, build_external_effect_repository
from .retry_policy import next_retry_at, status_for_failure
from .rate_limit import is_rate_limited

LOGGER = logging.getLogger(__name__)


def _enabled(name: str) -> bool:
    return runtime_bool(name)


def _is_test_job(job: ExternalEffectJob) -> bool:
    payload = dict(job.payload_json or {})
    return payload.get("execution_scope") == "test_loopback" or payload.get("is_test") is True


def _test_only_adapter_gate_error(job: ExternalEffectJob) -> str:
    """Allow only loopbacks that cannot reach a real provider adapter."""

    payload = dict(job.payload_json or {})
    if payload.get("execution_scope") != "test_loopback" or payload.get("is_test") is not True:
        return "test_execution_scope_invalid"
    adapter_name = str(job.adapter_name or "").strip()
    target_url = str(payload.get("webhook_url") or payload.get("target_url") or "").strip()
    if adapter_name == "outbound_webhook":
        from .test_receiver import TEST_RECEIVER_PATH_PREFIX

        if (
            urlparse(target_url).path == TEST_RECEIVER_PATH_PREFIX
            and bool(str(payload.get("expected_payload_hash") or "").strip())
        ):
            return ""
        return "test_receiver_contract_invalid"
    if adapter_name == "webhook":
        if automation_agent_code_from_webhook_url(target_url):
            return ""
        return "test_internal_webhook_contract_invalid"
    return "test_execution_adapter_not_allowed"


def _capability_gate_error(job: ExternalEffectJob) -> str:
    payload = dict(job.payload_json or {})
    if payload.get("bypass_push_capability") is True:
        return ""
    capability = capability_for_section(section_for_job(job))
    if capability is None:
        return ""
    if not capability.supports_real_execution:
        return "push_capability_readonly"
    if not capability.toggleable:
        return ""
    value = runtime_setting(capability.setting_key, "__aicrm_missing__")
    if value == "__aicrm_missing__":
        return ""
    if str(value or "").strip().lower() not in {"1", "true", "yes", "y", "on"}:
        return "push_capability_disabled"
    return ""


class ExternalEffectWorker:
    def __init__(
        self,
        repository: ExternalEffectRepository | None = None,
        adapter_registry: ExternalEffectAdapterRegistry | None = None,
        *,
        continuation_registry: ExternalEffectContinuationRegistry | None = None,
        locked_by: str = "",
        lease_seconds: int = 300,
    ):
        self._repo = repository or build_external_effect_repository()
        self._adapters = adapter_registry or DEFAULT_ADAPTER_REGISTRY
        self._continuations = continuation_registry or EMPTY_EXTERNAL_EFFECT_CONTINUATION_REGISTRY
        self._locked_by = locked_by or f"external-effect-worker-{uuid4().hex[:8]}"
        self._lease_seconds = max(30, min(int(lease_seconds or 300), 3600))

    @staticmethod
    def _empty_counts(*, candidate_count: int = 0) -> dict[str, int]:
        return {
            "candidate_count": int(candidate_count),
            "processed_count": 0,
            "succeeded_count": 0,
            "simulated_count": 0,
            "skipped_count": 0,
            "unknown_after_dispatch_count": 0,
            "failed_count": 0,
            "blocked_count": 0,
            "lost_lease_count": 0,
        }

    def preview_due(self, *, batch_size: int = 10, effect_types: list[str] | None = None, test_only: bool = False) -> dict[str, Any]:
        jobs = self._repo.list_due_jobs(limit=batch_size, effect_types=effect_types, test_only=test_only)
        counts = self._empty_counts(candidate_count=len(jobs))
        counts["skipped_count"] = len(jobs)
        return {
            "ok": True,
            "items": [
                {
                    **job.to_dict(),
                    "dispatch_status": "skipped",
                    "preview_only": True,
                }
                for job in jobs
            ],
            "counts": counts,
            "dry_run": True,
            "test_only": bool(test_only),
            "real_external_call_executed": False,
        }

    def run_due(self, *, batch_size: int = 10, dry_run: bool = True, effect_types: list[str] | None = None, test_only: bool = False) -> dict[str, Any]:
        if dry_run:
            payload = self.preview_due(batch_size=batch_size, effect_types=effect_types, test_only=test_only)
            payload["dry_run"] = True
            return payload
        if WECOM_MESSAGE_GROUP_SEND in set(effect_types or []) and int(batch_size or 0) != 1:
            return {
                "ok": False,
                "error": "batch_size_one_required",
                "items": [],
                "counts": self._empty_counts(),
                "dry_run": False,
                "test_only": bool(test_only),
                "real_external_call_executed": False,
            }
        if _enabled("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY") and not test_only:
            return {
                "ok": False,
                "error": "test_only_required",
                "items": [],
                "counts": self._empty_counts(),
                "dry_run": False,
                "test_only": False,
                "real_external_call_executed": False,
            }

        if not self._repo.direct_claims_allowed():
            return {
                "ok": True,
                "items": [],
                "counts": self._empty_counts(),
                "quarantined_stale_dispatching_count": 0,
                "dry_run": False,
                "test_only": bool(test_only),
                "owner_disabled": True,
                "reason": "postgres_queue_runtime_is_active",
                "real_external_call_executed": False,
            }

        quarantined_count = self._repo.quarantine_stale_dispatching()
        jobs = self._repo.acquire_due_jobs(
            limit=batch_size,
            locked_by=self._locked_by,
            effect_types=effect_types,
            test_only=test_only,
            lease_seconds=self._lease_seconds,
        )
        items: list[dict[str, Any]] = []
        counts = self._empty_counts(candidate_count=len(jobs))
        counts["unknown_after_dispatch_count"] = int(quarantined_count)
        real_external_call_executed = False
        for job in jobs:
            result = self._dispatch_claimed(job)
            items.append(result)
            counts["processed_count"] += 1
            status = str(result.get("job", {}).get("status") or "")
            if status == "succeeded":
                counts["succeeded_count"] += 1
            elif status == "simulated":
                counts["simulated_count"] += 1
            elif status == "unknown_after_dispatch":
                counts["unknown_after_dispatch_count"] += 1
            elif status == "blocked":
                counts["blocked_count"] += 1
            elif status.startswith("failed"):
                counts["failed_count"] += 1
            if result.get("error") == "lost_lease":
                counts["lost_lease_count"] += 1
            real_external_call_executed = real_external_call_executed or bool(result.get("real_external_call_executed"))
        ok = not any(counts[key] for key in ("unknown_after_dispatch_count", "failed_count", "blocked_count", "lost_lease_count"))
        return {
            "ok": ok,
            "exit_code": 0 if ok else 1,
            "items": items,
            "counts": counts,
            "quarantined_stale_dispatching_count": int(quarantined_count),
            "dry_run": False,
            "test_only": bool(test_only),
            "real_external_call_executed": real_external_call_executed,
        }

    def dispatch_one(self, job_or_id: int | ExternalEffectJob) -> dict[str, Any]:
        job_id = int(job_or_id.id if isinstance(job_or_id, ExternalEffectJob) else job_or_id)
        existing = self._repo.get_job(job_id)
        if existing is None:
            return {"ok": False, "error": "job_not_found", "real_external_call_executed": False}
        claimed = self._repo.acquire_job(job_id, locked_by=self._locked_by, lease_seconds=self._lease_seconds)
        if claimed is None:
            current = self._repo.get_job(job_id)
            return {
                "ok": False,
                "error": "not_claimed",
                "job": current.to_dict() if current else existing.to_dict(),
                "real_external_call_executed": False,
            }
        return self._dispatch_claimed(claimed)

    def dispatch_claimed(self, job_id: int, *, lease_token: str) -> dict[str, Any]:
        """Dispatch a row already claimed by the PostgreSQL lane runtime."""

        claimed = self._repo.get_active_claim(int(job_id), lease_token=str(lease_token or ""))
        if claimed is None:
            current = self._repo.get_job(int(job_id))
            return {
                "ok": False,
                "error": "lost_lease",
                "job": current.to_dict() if current else {"id": int(job_id)},
                "real_external_call_executed": False,
            }
        return self._dispatch_claimed(claimed)

    def _dispatch_claimed(self, job: ExternalEffectJob) -> dict[str, Any]:
        active = self._repo.get_active_claim(job.id, lease_token=job.lease_token)
        if active is None:
            return {
                "ok": False,
                "error": "lost_lease",
                "job": (self._repo.get_job(job.id) or job).to_dict(),
                "real_external_call_executed": False,
            }
        job = active
        if job.cancel_requested_at:
            cancelled = self._repo.settle_cancel(job=job)
            current = cancelled or self._repo.get_job(job.id) or job
            return {
                "ok": bool(cancelled),
                "error": "" if cancelled else "cancel_settlement_lost_lease",
                "job": current.to_dict(),
                "post_success_continuation": {
                    "applicable": False,
                    "reason": "dispatch_cancelled_before_provider",
                },
                "completion_event_queued": False,
                "settlement_event_queued": bool(cancelled),
                "real_external_call_executed": False,
            }
        dispatch_result = self._block_if_wecom_execution_disabled(job)
        if dispatch_result is None and _enabled("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY") and not _is_test_job(job):
            dispatch_result = ExternalEffectDispatchResult(
                status="blocked",
                adapter_mode=job.execution_mode or "execute",
                request_summary={"effect_type": job.effect_type, "test_execution_only": True},
                response_summary={"blocked": True, "real_external_call_executed": False},
                error_code="test_execution_only_required",
                error_message="AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY=1 blocks non-test jobs.",
            )
        if dispatch_result is None and _enabled("AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY"):
            test_gate_error = _test_only_adapter_gate_error(job)
            if test_gate_error:
                dispatch_result = ExternalEffectDispatchResult(
                    status="blocked",
                    adapter_mode=job.execution_mode or "execute",
                    request_summary={
                        "effect_type": job.effect_type,
                        "adapter_name": job.adapter_name,
                        "execution_scope": "test_loopback",
                    },
                    response_summary={"blocked": True, "real_external_call_executed": False},
                    error_code=test_gate_error,
                    error_message="Test-loopback policy blocks this provider adapter contract.",
                    real_external_call_executed=False,
                )
        capability_error = _capability_gate_error(job)
        if dispatch_result is None and capability_error:
            message = (
                "Push capability is disabled by admin config."
                if capability_error == "push_capability_disabled"
                else "Push capability does not support real execution."
            )
            dispatch_result = ExternalEffectDispatchResult(
                status="blocked",
                adapter_mode=job.execution_mode or "execute",
                request_summary={
                    "effect_type": job.effect_type,
                    "business_type": job.business_type,
                    "section": section_for_job(job),
                    "capability_gate": capability_error,
                },
                response_summary={"blocked": True, "real_external_call_executed": False},
                error_code=capability_error,
                error_message=message,
            )
        if dispatch_result is None:
            begun = self._repo.begin_provider_attempt(
                job=job,
                request_summary={
                    "effect_type": job.effect_type,
                    "adapter_name": job.adapter_name,
                    "operation": job.operation,
                    "target_type": job.target_type,
                    "provider_boundary": "durable_attempt_committed_before_adapter_dispatch",
                },
            )
            if begun is None:
                current = self._repo.get_job(job.id)
                if current is not None and current.status == "dispatching" and current.cancel_requested_at and current.lease_token == job.lease_token:
                    cancelled = self._repo.settle_cancel(job=current)
                    if cancelled is not None:
                        return {
                            "ok": True,
                            "job": cancelled.to_dict(),
                            "post_success_continuation": {
                                "applicable": False,
                                "reason": "dispatch_cancelled_before_provider",
                            },
                            "completion_event_queued": False,
                            "settlement_event_queued": True,
                            "real_external_call_executed": False,
                        }
                return {
                    "ok": False,
                    "error": "provider_attempt_not_started",
                    "job": (current or job).to_dict(),
                    "post_success_continuation": {
                        "applicable": False,
                        "reason": "provider_attempt_not_started",
                    },
                    "completion_event_queued": False,
                    "real_external_call_executed": False,
                }
            job, _provider_attempt = begun
            try:
                dispatch_result = self._adapters.get(job.adapter_name).dispatch(job)
            except Exception as exc:
                safe_log_exception(
                    LOGGER,
                    "external effect adapter dispatch raised",
                    exc,
                    external_effect_job_id=int(job.id or 0),
                    effect_type=job.effect_type,
                    adapter_name=job.adapter_name,
                )
                dispatch_result = ExternalEffectDispatchResult(
                    status="unknown_after_dispatch",
                    adapter_mode=job.execution_mode or "execute",
                    request_summary={
                        "effect_type": job.effect_type,
                        "adapter_name": job.adapter_name,
                        "operation": job.operation,
                        "dispatch_started": True,
                    },
                    response_summary={
                        "adapter_exception": True,
                        "provider_result_received": False,
                        "provider_boundary_crossed": True,
                        "external_call_outcome_unknown": True,
                    },
                    error_code="adapter_exception",
                    error_message=str(exc)[:500],
                    real_external_call_executed=False,
                    provider_result_received=False,
                )

        dispatch_result = normalize_dispatch_result(job, dispatch_result)
        continuation = self._run_post_success_continuations(job, dispatch_result)

        rate_limited = is_rate_limited(dispatch_result)
        if (
            dispatch_result.status == "failed_retryable"
            and status_for_failure(
                error_code=dispatch_result.error_code,
                attempt_count=int(job.attempt_count or 0) + 1,
                max_attempts=int(job.max_attempts or 5),
            )
            != "failed_retryable"
        ):
            dispatch_result = replace(dispatch_result, status="failed_terminal")

        retry_at = None
        if dispatch_result.status == "failed_retryable" or rate_limited:
            retry_after_seconds = dispatch_result.retry_after_seconds
            if retry_after_seconds is None:
                retry_after_seconds = (dispatch_result.response_summary or {}).get("retry_after_seconds")
            retry_at = next_retry_at(
                job.attempt_count,
                retry_after_seconds=retry_after_seconds,
            )

        try:
            completed = self._repo.complete_dispatch(job=job, result=dispatch_result, next_retry_at=retry_at)
        except Exception as exc:
            safe_log_exception(
                LOGGER,
                "external effect result persistence failed",
                exc,
                external_effect_job_id=int(job.id or 0),
                effect_type=job.effect_type,
            )
            try:
                updated = self._repo.mark_dispatch_unknown(
                    job=job,
                    error_code="result_persistence_failed",
                    error_message=str(exc)[:500],
                    side_effect_executed=dispatch_result.real_external_call_executed,
                    provider_result_received=dispatch_result.provider_result_received,
                )
            except Exception as mark_exc:
                safe_log_exception(
                    LOGGER,
                    "external effect unknown-result persistence failed",
                    mark_exc,
                    external_effect_job_id=int(job.id or 0),
                )
                updated = None
            return {
                "ok": False,
                "error": "result_persistence_failed",
                "job": updated.to_dict() if updated else job.to_dict(),
                "post_success_continuation": continuation,
                "completion_event_queued": False,
                "settlement_event_queued": bool(updated),
                "real_external_call_executed": dispatch_result.real_external_call_executed,
            }
        if completed is None:
            current = self._repo.get_job(job.id)
            return {
                "ok": False,
                "error": "lost_lease",
                "job": current.to_dict() if current else job.to_dict(),
                "post_success_continuation": continuation,
                "completion_event_queued": False,
                "real_external_call_executed": dispatch_result.real_external_call_executed,
            }
        updated, attempt = completed
        return {
            "ok": updated.status in {"succeeded", "simulated"},
            "job": updated.to_dict(),
            "attempt": attempt.to_dict(),
            "post_success_continuation": continuation,
            "completion_event_queued": updated.status == "succeeded",
            "settlement_event_queued": updated.status
            in {"succeeded", "simulated", "unknown_after_dispatch", "failed_terminal", "blocked", "cancelled"},
            "real_external_call_executed": dispatch_result.real_external_call_executed,
        }

    def _block_if_wecom_execution_disabled(self, job: ExternalEffectJob) -> ExternalEffectDispatchResult | None:
        if not is_wecom_effect_type(job.effect_type):
            return None
        block_code = typed_wecom_execution_block_reason(job.effect_type)
        if not block_code:
            return None
        block_message = wecom_execution_disabled_message(effect_type=job.effect_type)
        return ExternalEffectDispatchResult(
            status="blocked",
            adapter_mode="disabled",
            request_summary={
                "effect_type": job.effect_type,
                "adapter_name": job.adapter_name,
                "operation": job.operation,
                "target_type": job.target_type,
                "target_id": job.target_id,
                "execution_gate": block_code,
            },
            response_summary={
                "blocked": True,
                "execution_gate": block_code,
                "real_external_call_executed": False,
            },
            error_code=block_code,
            error_message=block_message,
        )

    def _run_post_success_continuations(self, job: ExternalEffectJob, dispatch_result) -> dict[str, Any]:
        if dispatch_result.status != "succeeded":
            return {"applicable": False, "reason": "dispatch_not_succeeded"}
        return {
            "applicable": False,
            "reason": "durable_completion_event_pending",
            "registered_compatibility_continuations": list(self._continuations.names),
        }
