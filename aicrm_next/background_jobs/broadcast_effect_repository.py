from __future__ import annotations

import json

from sqlalchemy import text

from aicrm_next.platform_foundation.external_effects.continuations import ExternalEffectContinuation
from aicrm_next.shared.db_session import get_session_factory
from aicrm_next.shared.runtime import fixture_mode


def _matches(job, _dispatch_result) -> bool:
    return job.business_type == "broadcast_job" and str(job.business_id or "").strip().isdigit()


def _project(job, _dispatch_result):
    if fixture_mode():
        return {"ok": True, "projected": False, "reason": "fixture_read_model_not_persisted"}
    broadcast_job_id = int(job.business_id)
    with get_session_factory()() as session:
        rows = [
            dict(row)
            for row in (
            session.execute(
                text(
                    """
                SELECT status, side_effect_executed, provider_result_received
                FROM external_effect_job
                WHERE business_type = 'broadcast_job' AND business_id = :business_id
                ORDER BY id ASC
                """
                ),
                {"business_id": str(broadcast_job_id)},
            )
            .mappings()
            .all()
            )
        ]
        statuses = [str(row["status"] or "").strip() for row in rows]
        terminal = {
            "succeeded",
            "simulated",
            "unknown_after_dispatch",
            "failed_terminal",
            "blocked",
            "cancelled",
        }
        if not rows or any(status not in terminal for status in statuses):
            session.rollback()
            return {
                "ok": True,
                "projected": False,
                "reason": "broadcast_effects_waiting",
                "effect_count": len(rows),
                "succeeded_count": statuses.count("succeeded"),
            }
        aggregate_status = "sent"
        for candidate in (
            "unknown_after_dispatch",
            "failed_terminal",
            "blocked",
            "cancelled",
            "simulated",
        ):
            if candidate in statuses:
                aggregate_status = candidate
                break
        projection_status = {
            "unknown_after_dispatch": "failed",
            "failed_terminal": "failed",
            "blocked": "failed",
        }.get(aggregate_status, aggregate_status)
        recipient_status = projection_status
        message_status = "skipped" if aggregate_status == "cancelled" else projection_status
        succeeded_count = statuses.count("succeeded")
        failed_count = len(statuses) - succeeded_count
        side_effect_executed = any(bool(row["side_effect_executed"]) for row in rows)
        provider_result_received = bool(rows) and all(bool(row["provider_result_received"]) for row in rows)
        reconciliation_required = aggregate_status == "unknown_after_dispatch"
        result_summary = {
            "projection_owner": "external_effect.settled",
            "effect_count": len(rows),
            "status_counts": {status: statuses.count(status) for status in sorted(set(statuses))},
            "aggregate_status": aggregate_status,
            "reconciliation_required": reconciliation_required,
        }
        session.execute(
            text(
                """
                UPDATE broadcast_jobs
                SET status = :status, sent_count = :sent_count, failed_count = :failed_count,
                    side_effect_executed = :side_effect_executed,
                    provider_result_received = :provider_result_received,
                    reconciliation_required = :reconciliation_required,
                    result_summary_json = CAST(:result_summary AS jsonb),
                    last_error = :last_error,
                    sent_at = CASE WHEN :status = 'sent' THEN COALESCE(sent_at, CURRENT_TIMESTAMP) ELSE sent_at END,
                    completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :job_id AND execution_owner = 'external_effect_job'
                """
            ),
            {
                "job_id": broadcast_job_id,
                "status": aggregate_status,
                "sent_count": succeeded_count,
                "failed_count": failed_count,
                "side_effect_executed": side_effect_executed,
                "provider_result_received": provider_result_received,
                "reconciliation_required": reconciliation_required,
                "result_summary": json.dumps(result_summary, ensure_ascii=False, separators=(",", ":")),
                "last_error": "" if aggregate_status == "sent" else f"external_effect_{aggregate_status}",
            },
        )
        session.execute(
            text(
                """
                UPDATE cloud_broadcast_plan_recipients
                SET send_status = :status, last_error = :last_error, updated_at = CURRENT_TIMESTAMP
                WHERE broadcast_job_id = :job_id
                """
            ),
            {
                "job_id": broadcast_job_id,
                "status": recipient_status,
                "last_error": "" if aggregate_status == "sent" else f"external_effect_{aggregate_status}",
            },
        )
        session.execute(
            text(
                """
                UPDATE cloud_broadcast_plan_recipient_messages message
                SET status = :status,
                    sent_at = CASE WHEN :status = 'sent' THEN COALESCE(sent_at, CURRENT_TIMESTAMP) ELSE sent_at END,
                    last_error = :last_error, updated_at = CURRENT_TIMESTAMP
                FROM cloud_broadcast_plan_recipients recipient
                WHERE recipient.broadcast_job_id = :job_id
                  AND message.recipient_id = recipient.id
                  AND (message.status <> 'sent' OR :status = 'sent')
                """
            ),
            {
                "job_id": broadcast_job_id,
                "status": message_status,
                "last_error": "" if aggregate_status == "sent" else f"external_effect_{aggregate_status}",
            },
        )
        session.commit()
    return {
        "ok": True,
        "projected": True,
        "broadcast_job_id": broadcast_job_id,
        "effect_count": len(rows),
        "aggregate_status": aggregate_status,
    }


def _matches_terminal(job, dispatch_result) -> bool:
    return _matches(job, dispatch_result) and job.status != "succeeded"


BROADCAST_EXTERNAL_EFFECT_READ_MODEL_CONTINUATION = ExternalEffectContinuation(
    name="broadcast_external_effect_read_model",
    matches=_matches,
    run=_project,
)

BROADCAST_EXTERNAL_EFFECT_SETTLEMENT_CONTINUATION = ExternalEffectContinuation(
    name="broadcast_external_effect_settlement",
    matches=_matches_terminal,
    run=_project,
)


__all__ = [
    "BROADCAST_EXTERNAL_EFFECT_READ_MODEL_CONTINUATION",
    "BROADCAST_EXTERNAL_EFFECT_SETTLEMENT_CONTINUATION",
]
