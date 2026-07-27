from __future__ import annotations

from .models import ExternalEffectJob, public_datetime, utcnow
from .repo_contract import _json_dumps, _public_job, _text


class ExternalEffectCanaryAuthorizationRepositoryMixin:
    def authorize_allowlisted_canary(
        self,
        job_id: int,
        *,
        actor: str,
        reason: str,
        expected_version: int,
    ) -> ExternalEffectJob | None:
        authorization = {
            "actor": _text(actor),
            "reason": _text(reason)[:500],
            "authorized_at": public_datetime(utcnow()),
            "authorized_job_id": int(job_id),
            "authorized_from_version": int(expected_version),
            "duplicate_risk_confirmed": False,
        }
        return _public_job(
            self._write_one(  # type: ignore[attr-defined]
                """
                UPDATE external_effect_job
                SET payload_json = jsonb_set(
                        COALESCE(payload_json, '{}'::jsonb),
                        '{execution_scope}',
                        to_jsonb('allowlisted_canary'::text),
                        TRUE
                    ),
                    payload_summary_json = jsonb_set(
                        COALESCE(payload_summary_json, '{}'::jsonb),
                        '{canary_authorization}',
                        CAST(:authorization AS jsonb),
                        TRUE
                    ),
                    status = CASE WHEN status = 'blocked' THEN 'queued' ELSE status END,
                    last_error_code = CASE WHEN status = 'blocked' THEN '' ELSE last_error_code END,
                    last_error_message = CASE WHEN status = 'blocked' THEN '' ELSE last_error_message END,
                    completed_at = CASE WHEN status = 'blocked' THEN NULL ELSE completed_at END,
                    available_at = CURRENT_TIMESTAMP,
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id
                  AND row_version = :expected_version
                  AND status IN ('planned', 'approved', 'queued', 'blocked')
                  AND attempt_count = 0
                  AND provider_call_started_at IS NULL
                  AND COALESCE(hold_reason, '') = ''
                  AND cancel_requested_at IS NULL
                RETURNING *
                """,
                {
                    "job_id": int(job_id),
                    "expected_version": int(expected_version),
                    "authorization": _json_dumps(authorization),
                },
            )
        )


__all__ = ["ExternalEffectCanaryAuthorizationRepositoryMixin"]
