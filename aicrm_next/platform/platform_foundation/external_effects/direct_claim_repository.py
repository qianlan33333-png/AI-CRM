from __future__ import annotations

from typing import Any
from uuid import uuid4

from .models import ExternalEffectJob
from .repo_contract import _public_job, _text


class ExternalEffectDirectClaimRepositoryMixin:
    """Fail-closed claim boundary for the retired pre-runtime worker owner."""

    def direct_claims_allowed(self) -> bool:
        row = self._one(  # type: ignore[attr-defined]
            """
            SELECT NOT (claim_enabled AND active_generation > 0) AS allowed
            FROM queue_runtime_control
            WHERE singleton = TRUE
            """
        )
        return bool(row and row.get("allowed"))

    def list_due_jobs(
        self,
        *,
        limit: int = 50,
        effect_types: list[str] | None = None,
        test_only: bool = False,
    ) -> list[ExternalEffectJob]:
        type_filter = "AND effect_type = ANY(:effect_types)" if effect_types else ""
        test_filter = "AND COALESCE(payload_json->>'execution_scope', '') = 'test_loopback'" if test_only else ""
        params: dict[str, Any] = {"limit": max(1, min(int(limit or 50), 200))}
        if effect_types:
            params["effect_types"] = [_text(item) for item in effect_types if _text(item)]
        rows = self._all(  # type: ignore[attr-defined]
            f"""
            SELECT *
            FROM external_effect_job
            WHERE status IN ('queued', 'failed_retryable')
              AND hold_reason = ''
              AND attempt_count < max_attempts
              AND scheduled_at <= CURRENT_TIMESTAMP
              AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
              AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
              AND NOT EXISTS (
                  SELECT 1
                  FROM queue_runtime_control control
                  WHERE control.singleton = TRUE
                    AND control.claim_enabled
                    AND control.active_generation > 0
              )
              {type_filter}
              {test_filter}
            ORDER BY priority ASC, scheduled_at ASC, id ASC
            LIMIT :limit
            """,
            params,
        )
        return [job for row in rows if (job := _public_job(row)) is not None]

    def acquire_due_jobs(
        self,
        *,
        limit: int = 50,
        locked_by: str,
        effect_types: list[str] | None = None,
        test_only: bool = False,
        lease_seconds: int = 300,
    ) -> list[ExternalEffectJob]:
        type_filter = "AND effect_type = ANY(:effect_types)" if effect_types else ""
        test_filter = "AND COALESCE(payload_json->>'execution_scope', '') = 'test_loopback'" if test_only else ""
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit or 50), 200)),
            "locked_by": _text(locked_by),
            "lease_prefix": "eel_" + uuid4().hex,
            "lease_seconds": max(30, min(int(lease_seconds or 300), 3600)),
        }
        if effect_types:
            params["effect_types"] = [_text(item) for item in effect_types if _text(item)]
        rows = self._write_all(  # type: ignore[attr-defined]
            f"""
            WITH direct_owner AS MATERIALIZED (
                SELECT singleton
                FROM queue_runtime_control
                WHERE singleton = TRUE
                  AND NOT (claim_enabled AND active_generation > 0)
                FOR SHARE
            ), due AS (
                SELECT job.id
                FROM external_effect_job job
                CROSS JOIN direct_owner
                WHERE job.status IN ('queued', 'failed_retryable')
                  AND job.hold_reason = ''
                  AND job.attempt_count < job.max_attempts
                  AND job.scheduled_at <= CURRENT_TIMESTAMP
                  AND (job.next_retry_at IS NULL OR job.next_retry_at <= CURRENT_TIMESTAMP)
                  AND (job.lease_expires_at IS NULL OR job.lease_expires_at <= CURRENT_TIMESTAMP)
                  {type_filter}
                  {test_filter}
                ORDER BY job.priority ASC, job.scheduled_at ASC, job.id ASC
                LIMIT :limit
                FOR UPDATE OF job SKIP LOCKED
            )
            UPDATE external_effect_job j
            SET status = 'dispatching',
                lease_token = CAST(:lease_prefix AS text) || '-' || j.id::text,
                lease_expires_at = CURRENT_TIMESTAMP + (:lease_seconds * INTERVAL '1 second'),
                dispatch_started_at = CURRENT_TIMESTAMP,
                provider_call_started_at = NULL,
                locked_at = CURRENT_TIMESTAMP,
                locked_by = :locked_by,
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            FROM due
            WHERE j.id = due.id
            RETURNING j.*
            """,
            params,
        )
        return [job for row in rows if (job := _public_job(row)) is not None]

    def acquire_job(
        self,
        job_id: int,
        *,
        locked_by: str,
        lease_seconds: int = 300,
    ) -> ExternalEffectJob | None:
        lease_prefix = "eel_" + uuid4().hex
        row = self._write_one(  # type: ignore[attr-defined]
            """
            WITH direct_owner AS MATERIALIZED (
                SELECT singleton
                FROM queue_runtime_control
                WHERE singleton = TRUE
                  AND NOT (claim_enabled AND active_generation > 0)
                FOR SHARE
            )
            UPDATE external_effect_job
            SET status = 'dispatching',
                lease_token = :lease_token,
                lease_expires_at = CURRENT_TIMESTAMP + (:lease_seconds * INTERVAL '1 second'),
                dispatch_started_at = CURRENT_TIMESTAMP,
                provider_call_started_at = NULL,
                locked_at = CURRENT_TIMESTAMP,
                locked_by = :locked_by,
                row_version = row_version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :job_id
              AND hold_reason = ''
              AND status IN ('queued', 'failed_retryable')
              AND attempt_count < max_attempts
              AND scheduled_at <= CURRENT_TIMESTAMP
              AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
              AND (lease_expires_at IS NULL OR lease_expires_at <= CURRENT_TIMESTAMP)
              AND EXISTS (SELECT 1 FROM direct_owner)
            RETURNING *
            """,
            {
                "job_id": int(job_id),
                "locked_by": _text(locked_by),
                "lease_token": f"{lease_prefix}-{int(job_id)}",
                "lease_seconds": max(30, min(int(lease_seconds or 300), 3600)),
            },
        )
        return _public_job(row)


__all__ = ["ExternalEffectDirectClaimRepositoryMixin"]
