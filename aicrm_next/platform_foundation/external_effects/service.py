from __future__ import annotations

from datetime import datetime
from typing import Any

from aicrm_next.platform_foundation.command_bus.models import CommandContext

from .execution_gates import (
    WECOM_EXECUTION_DISABLED_CODE,
    explicit_wecom_execution_disabled,
    is_wecom_effect_type,
    wecom_execution_disabled_message,
)
from .wecom_canary_policy import wecom_canary_job_gate_error
from .models import ExternalEffectCreateRequest, ExternalEffectJob
from .repo import ExternalEffectRepository, build_external_effect_repository


class ExternalEffectService:
    def __init__(self, repository: ExternalEffectRepository | None = None):
        self._repo = repository or build_external_effect_repository()

    def plan_effect(
        self,
        *,
        effect_type: str,
        adapter_name: str,
        operation: str,
        target_type: str,
        target_id: str,
        payload: dict[str, Any] | None = None,
        payload_summary: dict[str, Any] | None = None,
        context: CommandContext | None = None,
        business_type: str = "",
        business_id: str = "",
        source_module: str = "",
        source_event_id: str = "",
        source_command_id: str = "",
        risk_level: str = "medium",
        requires_approval: bool = False,
        execution_mode: str = "execute",
        scheduled_at: datetime | None = None,
        available_at: datetime | None = None,
        priority: int = 100,
        max_attempts: int = 5,
        idempotency_key: str = "",
        status: str = "queued",
        execution_id: str = "",
        parent_execution_id: str = "",
        lane: str = "",
        ordering_key: str = "",
        fairness_key: str = "",
        rate_scope_key: str = "",
        connection: Any | None = None,
    ) -> dict[str, Any]:
        initial_status = str(status or "queued").strip() or "queued"
        if requires_approval and initial_status in {"queued", "approved"}:
            initial_status = "planned"
        effective_execution_mode = execution_mode
        effective_payload_summary = dict(payload_summary or {})
        if is_wecom_effect_type(effect_type) and explicit_wecom_execution_disabled():
            initial_status = "blocked"
            effective_execution_mode = "disabled"
            effective_payload_summary.update(
                {
                    "execution_gate": WECOM_EXECUTION_DISABLED_CODE,
                    "execution_mode_source": "AICRM_WECOM_EXECUTION_MODE",
                    "real_external_call_executed": False,
                }
            )
        request = ExternalEffectCreateRequest(
            effect_type=effect_type,
            adapter_name=adapter_name,
            operation=operation,
            target_type=target_type,
            target_id=target_id,
            payload=dict(payload or {}),
            payload_summary=effective_payload_summary,
            context=context or CommandContext(),
            business_type=business_type,
            business_id=business_id,
            source_module=source_module,
            source_event_id=source_event_id,
            source_command_id=source_command_id,
            risk_level=risk_level,
            requires_approval=requires_approval,
            execution_mode=effective_execution_mode,
            scheduled_at=scheduled_at,
            available_at=available_at,
            priority=priority,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
            status=initial_status,
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            lane=lane,
            ordering_key=ordering_key,
            fairness_key=fairness_key,
            rate_scope_key=rate_scope_key,
        )
        if connection is not None:
            from sqlalchemy.orm import Session

            from .transactional import (
                enqueue_external_effect_job_in_session,
                enqueue_transactional_external_effect_job,
            )

            enqueue = enqueue_external_effect_job_in_session if isinstance(connection, Session) else enqueue_transactional_external_effect_job
            return enqueue(connection, request).to_dict()
        return self._repo.create_job(request).to_dict()

    def get(self, job_id: int) -> ExternalEffectJob | None:
        return self._repo.get_job(job_id)

    def list_jobs(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0) -> tuple[list[ExternalEffectJob], int]:
        return self._repo.list_jobs(filters or {}, limit=limit, offset=offset)

    def find_existing_job(
        self,
        *,
        effect_type: str,
        target_type: str,
        target_id: str,
        business_type: str,
        business_id: str,
    ) -> ExternalEffectJob | None:
        jobs, _ = self.list_jobs(
            {
                "effect_type": effect_type,
                "target_type": target_type,
                "target_id": target_id,
                "business_type": business_type,
                "business_id": business_id,
            },
            limit=1,
        )
        return jobs[0] if jobs else None

    def list_attempts(self, job_id: int):
        return self._repo.list_attempts(job_id)

    def list_attempts_for_jobs(self, job_ids: list[int]):
        return self._repo.list_attempts_for_jobs(job_ids)

    def count_jobs(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._repo.count_jobs(filters or {})

    def queue_metrics(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._repo.queue_metrics(filters or {})

    def list_test_receipts(self, filters: dict[str, Any] | None = None, *, limit: int = 50, offset: int = 0):
        return self._repo.list_test_receipts(filters or {}, limit=limit, offset=offset)

    def get_test_receipt(self, receipt_id: str):
        return self._repo.get_test_receipt(receipt_id)

    def test_receipt_metrics(self) -> dict[str, Any]:
        return self._repo.test_receipt_metrics()

    def enqueue(self, job_id: int) -> ExternalEffectJob | None:
        job = self._repo.get_job(job_id)
        blocked = self._block_if_wecom_execution_disabled(job, action="enqueue")
        if blocked is not None:
            return blocked
        return self._repo.enqueue_job(job_id)

    def approve(self, job_id: int) -> ExternalEffectJob | None:
        job = self._repo.get_job(job_id)
        if job is None or job.status == "unknown_after_dispatch":
            return None
        blocked = self._block_if_wecom_execution_disabled(job, action="approve")
        if blocked is not None:
            return blocked
        return self._repo.approve_job(job_id)

    def authorize_allowlisted_canary(
        self,
        job_id: int,
        *,
        actor: str,
        reason: str,
        expected_version: int,
    ) -> ExternalEffectJob | None:
        normalized_actor = str(actor or "").strip()
        normalized_reason = str(reason or "").strip()
        if not normalized_actor or not normalized_reason or int(expected_version or 0) < 1:
            return None
        job = self._repo.get_job(job_id)
        if job is None or not is_wecom_effect_type(job.effect_type):
            return None
        if wecom_canary_job_gate_error(job, authorize_scope=True):
            return None
        return self._repo.authorize_allowlisted_canary(
            job_id,
            actor=normalized_actor,
            reason=normalized_reason,
            expected_version=int(expected_version),
        )

    def retry(
        self,
        job_id: int,
        *,
        actor: str = "",
        reason: str = "",
        confirm_duplicate_risk: bool = False,
    ) -> ExternalEffectJob | None:
        job = self._repo.get_job(job_id)
        if not job or job.status not in {"failed_retryable", "failed_terminal", "blocked", "unknown_after_dispatch"}:
            return None
        if job.status == "unknown_after_dispatch":
            if not str(actor or "").strip() or not str(reason or "").strip() or confirm_duplicate_risk is not True:
                return None
        blocked = self._block_if_wecom_execution_disabled(job, action="retry")
        if blocked is not None:
            return blocked
        if job.status == "unknown_after_dispatch":
            self._repo.record_attempt(
                job=job,
                status="skipped",
                adapter_mode="manual_retry_authorization",
                request_summary={
                    "action": "manual_retry_unknown_after_dispatch",
                    "actor": str(actor).strip(),
                    "reason": str(reason).strip(),
                    "confirm_duplicate_risk": True,
                    "prior_status": job.status,
                },
                response_summary={
                    "authorized": True,
                    "external_call_executed": False,
                    "real_external_call_executed": False,
                },
            )
        return self._repo.enqueue_job(
            job_id,
            allow_unknown_after_dispatch=job.status == "unknown_after_dispatch",
            extend_attempt_budget=True,
        )

    def cancel(
        self,
        job_id: int,
        *,
        actor: str = "",
        reason: str = "",
        expected_version: int | None = None,
    ) -> ExternalEffectJob | None:
        return self._repo.request_cancel(
            job_id,
            actor=str(actor or "").strip(),
            reason=str(reason or "").strip(),
            expected_version=expected_version,
        )

    def complete_record_only(self, *, dry_run: bool = True, limit: int = 100, operator: str = "system") -> dict[str, Any]:
        jobs = self._repo.list_record_only_jobs(limit=limit)
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "operator": operator,
                "candidate_count": len(jobs),
                "completed_count": 0,
                "items": [job.to_dict() for job in jobs],
                "real_external_call_executed": False,
            }
        completed: list[dict[str, Any]] = []
        for job in jobs:
            attempt = self._repo.record_attempt(
                job=job,
                status="simulated",
                adapter_mode="historical_record_only",
                request_summary={
                    "effect_type": job.effect_type,
                    "target_type": job.target_type,
                    "target_id": job.target_id,
                    "operator": operator,
                },
                response_summary={
                    "historical_record_completed": True,
                    "simulated": True,
                    "real_external_call_executed": False,
                    "provider_result_received": False,
                },
            )
            updated = self._repo.mark_simulated(
                job.id,
                attempt_id=attempt.attempt_id,
                result_summary=attempt.response_summary_json,
            )
            completed.append(
                {
                    "job": updated.to_dict() if updated else job.to_dict(),
                    "attempt": attempt.to_dict(),
                }
            )
        return {
            "ok": True,
            "dry_run": False,
            "operator": operator,
            "candidate_count": len(jobs),
            "completed_count": len(completed),
            "items": completed,
            "real_external_call_executed": False,
        }

    def _block_if_wecom_execution_disabled(self, job: ExternalEffectJob | None, *, action: str) -> ExternalEffectJob | None:
        if job is None or not is_wecom_effect_type(job.effect_type) or not explicit_wecom_execution_disabled():
            return None
        attempt = self._repo.record_attempt(
            job=job,
            status="blocked",
            adapter_mode="disabled",
            request_summary={
                "effect_type": job.effect_type,
                "target_type": job.target_type,
                "target_id": job.target_id,
                "admin_action": action,
                "execution_gate": WECOM_EXECUTION_DISABLED_CODE,
            },
            response_summary={
                "blocked": True,
                "execution_gate": WECOM_EXECUTION_DISABLED_CODE,
                "real_external_call_executed": False,
            },
            error_code=WECOM_EXECUTION_DISABLED_CODE,
            error_message=wecom_execution_disabled_message(),
        )
        return (
            self._repo.mark_blocked(
                job.id,
                attempt_id=attempt.attempt_id,
                error_code=WECOM_EXECUTION_DISABLED_CODE,
                error_message=wecom_execution_disabled_message(),
            )
            or job
        )


def default_external_effect_service() -> ExternalEffectService:
    return ExternalEffectService()
