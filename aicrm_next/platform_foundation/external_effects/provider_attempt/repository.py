from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from aicrm_next.platform_foundation.external_calls import scrub_summary

from ..models import ExternalEffectAttempt, ExternalEffectJob
from ..repo_contract import _json_dumps, _public_attempt, _public_job, _text
from ..settlement_events import enqueue_external_effect_terminal_events_in_session


class ExternalEffectProviderAttemptRepositoryMixin:
    """Own the atomic transition across the external provider boundary."""

    def begin_provider_attempt(
        self,
        *,
        job: ExternalEffectJob,
        request_summary: dict[str, Any],
    ) -> tuple[ExternalEffectJob, ExternalEffectAttempt] | None:
        lease_token = _text(job.lease_token)
        if not lease_token:
            return None
        attempt_id = "eea_" + uuid4().hex
        summary = scrub_summary(
            {
                **dict(request_summary or {}),
                "provider_boundary_crossed": True,
            }
        )
        request_hash = hashlib.sha256(
            _json_dumps(
                {
                    "effect_type": job.effect_type,
                    "operation": job.operation,
                    "target_type": job.target_type,
                    "target_id": job.target_id,
                    "payload": dict(job.payload_json or {}),
                }
            ).encode("utf-8")
        ).hexdigest()
        with self._session_factory() as session:  # type: ignore[attr-defined]
            current = (
                session.execute(
                    text(
                        """
                        SELECT *
                        FROM external_effect_job
                        WHERE id = :job_id
                          AND hold_reason = ''
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                          AND lease_expires_at > CURRENT_TIMESTAMP
                          AND cancel_requested_at IS NULL
                          AND (NULLIF(payload_json->>'provider_deadline_at', '') IS NULL
                               OR (payload_json->>'provider_deadline_at')::timestamptz > clock_timestamp())
                        FOR UPDATE
                        """
                    ),
                    {"job_id": int(job.id), "lease_token": lease_token},
                )
                .mappings()
                .fetchone()
            )
            if not current:
                session.rollback()
                return None
            attempt_row = (
                session.execute(
                    text(
                        """
                        INSERT INTO external_effect_attempt (
                            attempt_id, job_id, adapter_name, adapter_mode, operation, trace_id,
                            request_id, lease_token, request_hash, provider_call_started_at,
                            worker_generation, status, request_summary_json, response_summary_json,
                            error_code, error_message, started_at, completed_at
                        ) VALUES (
                            :attempt_id, :job_id, :adapter_name, :adapter_mode, :operation, :trace_id,
                            :request_id, :lease_token, :request_hash, CURRENT_TIMESTAMP,
                            :worker_generation, 'dispatching', CAST(:request_summary AS jsonb), '{}'::jsonb,
                            '', '', CURRENT_TIMESTAMP, NULL
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "attempt_id": attempt_id,
                        "job_id": int(job.id),
                        "adapter_name": job.adapter_name,
                        "adapter_mode": _text(job.execution_mode) or "execute",
                        "operation": job.operation,
                        "trace_id": job.trace_id,
                        "request_id": job.request_id,
                        "lease_token": lease_token,
                        "request_hash": request_hash,
                        "worker_generation": int(current.get("worker_generation") or job.worker_generation or 0),
                        "request_summary": _json_dumps(summary),
                    },
                )
                .mappings()
                .fetchone()
            )
            updated_row = (
                session.execute(
                    text(
                        """
                        UPDATE external_effect_job
                        SET last_attempt_id = :attempt_id,
                            provider_call_started_at = CURRENT_TIMESTAMP,
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :job_id
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                          AND cancel_requested_at IS NULL
                          AND (NULLIF(payload_json->>'provider_deadline_at', '') IS NULL
                               OR (payload_json->>'provider_deadline_at')::timestamptz > clock_timestamp())
                        RETURNING *
                        """
                    ),
                    {"attempt_id": attempt_id, "job_id": int(job.id), "lease_token": lease_token},
                )
                .mappings()
                .fetchone()
            )
            if not attempt_row or not updated_row:
                session.rollback()
                return None
            session.commit()
            updated = _public_job(dict(updated_row))
            attempt = _public_attempt(dict(attempt_row))
            return (updated, attempt) if updated and attempt else None

    def expire_provider_deadline(self, *, job: ExternalEffectJob) -> ExternalEffectJob | None:
        lease_token = _text(job.lease_token)
        if not lease_token:
            return None
        with self._session_factory() as session:  # type: ignore[attr-defined]
            row = (
                session.execute(
                    text(
                        """
                        UPDATE external_effect_job
                        SET status = 'expired',
                            side_effect_executed = FALSE,
                            provider_result_received = FALSE,
                            reconciliation_required = FALSE,
                            last_error_code = 'provider_deadline_elapsed',
                            last_error_message = 'Provider deadline elapsed before dispatch; replay is prohibited.',
                            result_summary_json = result_summary_json || jsonb_build_object(
                                'provider_deadline_elapsed', TRUE,
                                'provider_boundary_crossed', FALSE,
                                'real_external_call_executed', FALSE
                            ),
                            lease_token = '', lease_expires_at = NULL,
                            locked_by = '', locked_at = NULL,
                            completed_at = CURRENT_TIMESTAMP,
                            row_version = row_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :job_id
                          AND status = 'dispatching'
                          AND lease_token = :lease_token
                          AND provider_call_started_at IS NULL
                          AND NULLIF(payload_json->>'provider_deadline_at', '') IS NOT NULL
                          AND (payload_json->>'provider_deadline_at')::timestamptz <= clock_timestamp()
                        RETURNING *
                        """
                    ),
                    {"job_id": int(job.id), "lease_token": lease_token},
                )
                .mappings()
                .fetchone()
            )
            if not row:
                session.rollback()
                return None
            expired = _public_job(dict(row))
            if expired is None:
                session.rollback()
                return None
            enqueue_external_effect_terminal_events_in_session(
                session,
                job=expired,
                attempt=None,
            )
            session.commit()
            return expired
