from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

from aicrm_next.platform_foundation.command_bus import CommandContext
from aicrm_next.platform_foundation.internal_events.outbox import (
    enqueue_transactional_internal_event_outbox,
)
from aicrm_next.platform_foundation.internal_events.questionnaire import (
    build_questionnaire_submitted_event_request,
)
from aicrm_next.shared.db_session import connect_raw_postgres
from aicrm_next.shared.runtime import raw_database_url

from aicrm_next.shared.release_cutovers import QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_AT, QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL


_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL = QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_SQL


_ANOMALY_QUERIES = {
    "submission_without_outbox": f"""
        SELECT qs.id
        FROM questionnaire_submissions qs
        WHERE qs.submitted_at >= {_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL}
          AND NOT EXISTS (
            SELECT 1
            FROM internal_event_outbox outbox
            WHERE outbox.tenant_id = 'aicrm'
              AND outbox.idempotency_key = 'questionnaire.submitted:' || qs.id::text
        )
          AND NOT EXISTS (
              SELECT 1
              FROM internal_event event
              WHERE event.tenant_id = 'aicrm'
                AND event.event_type = 'questionnaire.submitted'
                AND event.idempotency_key = 'questionnaire.submitted:' || qs.id::text
          )
    """,
    "relayed_outbox_without_event": """
        SELECT outbox.id
        FROM internal_event_outbox outbox
        WHERE outbox.event_type = 'questionnaire.submitted'
          AND outbox.status = 'relayed'
          AND NOT EXISTS (
              SELECT 1
              FROM internal_event event
              WHERE event.tenant_id = outbox.tenant_id
                AND event.idempotency_key = outbox.idempotency_key
          )
    """,
    "event_without_required_webhook_effect": f"""
        SELECT event.id
        FROM internal_event event
        JOIN questionnaire_submissions submission
          ON event.aggregate_id ~ '^[0-9]+$'
         AND submission.id = event.aggregate_id::bigint
        JOIN questionnaires questionnaire ON questionnaire.id = submission.questionnaire_id
        WHERE event.event_type = 'questionnaire.submitted'
          AND submission.submitted_at >= {_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL}
          AND questionnaire.external_push_enabled = TRUE
          AND questionnaire.external_push_url <> ''
          AND NOT EXISTS (
              SELECT 1
              FROM external_effect_job job
              WHERE job.effect_type = 'webhook.questionnaire_submission.push'
                AND job.target_type = 'questionnaire_submission'
                AND job.target_id = submission.id::text
          )
    """,
    "event_without_required_tag_effect": f"""
        SELECT event.id
        FROM internal_event event
        JOIN questionnaire_submissions submission
          ON event.aggregate_id ~ '^[0-9]+$'
         AND submission.id = event.aggregate_id::bigint
        WHERE event.event_type = 'questionnaire.submitted'
          AND submission.submitted_at >= {_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL}
          AND jsonb_array_length(COALESCE(submission.final_tags, '[]'::jsonb)) > 0
          AND NOT EXISTS (
              SELECT 1
              FROM external_effect_job job
              WHERE job.effect_type = 'wecom.contact.tag.mark'
                AND job.business_type = 'questionnaire_submission'
                AND job.business_id = submission.id::text
          )
    """,
    "duplicate_questionnaire_effect": """
        SELECT MIN(job.id) AS id
        FROM external_effect_job job
        WHERE job.effect_type IN (
            'webhook.questionnaire_submission.push',
            'wecom.contact.tag.mark'
        )
          AND (
              job.target_type = 'questionnaire_submission'
              OR job.business_type = 'questionnaire_submission'
          )
        GROUP BY job.effect_type, job.target_type, job.target_id, job.business_type, job.business_id
        HAVING COUNT(*) > 1
    """,
    "effect_without_succeeded_planner": """
        SELECT job.id
        FROM external_effect_job job
        JOIN internal_event event
          ON event.tenant_id = job.tenant_id
         AND event.event_id = job.source_event_id
         AND event.event_type = 'questionnaire.submitted'
        WHERE job.effect_type IN (
            'webhook.questionnaire_submission.push',
            'wecom.contact.tag.mark'
        )
          AND job.source_event_id <> ''
          AND NOT EXISTS (
              SELECT 1
              FROM internal_event_consumer_run run
              WHERE run.event_id = job.source_event_id
                AND run.consumer_name = CASE
                    WHEN job.effect_type = 'wecom.contact.tag.mark'
                    THEN 'questionnaire_tag_consumer'
                    ELSE 'questionnaire_webhook_consumer'
                END
                AND run.status = 'succeeded'
          )
    """,
    "succeeded_effect_without_succeeded_attempt": """
        SELECT job.id
        FROM external_effect_job job
        WHERE job.effect_type IN (
            'webhook.questionnaire_submission.push',
            'wecom.contact.tag.mark'
        )
          AND job.status = 'succeeded'
          AND NOT EXISTS (
              SELECT 1
              FROM external_effect_attempt attempt
              WHERE attempt.job_id = job.id AND attempt.status = 'succeeded'
          )
    """,
    "succeeded_tag_effect_without_projection": """
        SELECT job.id
        FROM external_effect_job job
        WHERE job.effect_type = 'wecom.contact.tag.mark'
          AND job.business_type = 'questionnaire_submission'
          AND job.status = 'succeeded'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements_text(
                  CASE
                      WHEN jsonb_typeof(job.payload_json -> 'tag_ids') = 'array'
                      THEN job.payload_json -> 'tag_ids'
                      ELSE '[]'::jsonb
                  END
              ) tag(tag_id)
              WHERE NOT EXISTS (
                  SELECT 1
                  FROM contact_tags projection
                  WHERE projection.unionid = job.target_id
                    AND projection.userid = COALESCE(job.payload_json ->> 'follow_user_userid', '')
                    AND projection.tag_id = tag.tag_id
                    AND projection.submission_id = job.business_id
                    AND projection.idempotency_key = job.idempotency_key
              )
          )
    """,
    "stale_legacy_retry_residue": """
        SELECT log.id
        FROM questionnaire_external_push_logs log
        WHERE log.status IN ('planned', 'pending', 'queued')
        UNION ALL
        SELECT job.id
        FROM external_effect_job job
        WHERE job.source_module = 'questionnaire.external_push_logs'
           OR job.target_type = 'questionnaire_external_push_log'
    """,
}


_HISTORICAL_ANOMALY_QUERIES = {
    "event_without_required_webhook_effect": _ANOMALY_QUERIES["event_without_required_webhook_effect"].replace(
        f"submission.submitted_at >= {_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL}",
        f"submission.submitted_at < {_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL}",
    ),
    "event_without_required_tag_effect": _ANOMALY_QUERIES["event_without_required_tag_effect"].replace(
        f"submission.submitted_at >= {_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL}",
        f"submission.submitted_at < {_QUESTIONNAIRE_R09_PRODUCTION_CUTOVER_SQL}",
    ),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _hash(value: str) -> str:
    normalized = _text(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


class QuestionnaireRadarReconciliationService:
    """Diagnose R09 lineage gaps without exposing PII or calling a provider."""

    def __init__(self, *, database_url: str = "") -> None:
        self._database_url = _text(database_url) or raw_database_url()

    def diagnose(self) -> dict[str, Any]:
        if not self._database_url:
            return self._error("database_url_required")
        from psycopg.rows import dict_row

        counts: dict[str, int] = {}
        historical_counts: dict[str, int] = {}
        with connect_raw_postgres(self._database_url) as conn:
            conn.row_factory = dict_row
            for name, query in _ANOMALY_QUERIES.items():
                row = conn.execute(f"WITH anomalies AS ({query}) SELECT COUNT(*)::integer AS anomaly_count FROM anomalies").fetchone()
                counts[name] = int((row or {}).get("anomaly_count") or 0)
            for name, query in _HISTORICAL_ANOMALY_QUERIES.items():
                row = conn.execute(f"WITH anomalies AS ({query}) SELECT COUNT(*)::integer AS anomaly_count FROM anomalies").fetchone()
                historical_counts[name] = int((row or {}).get("anomaly_count") or 0)
        return {
            "ok": True,
            "mode": "count_only",
            "repair_supported": True,
            "has_anomalies": any(counts.values()),
            "counts": counts,
            "historical_counts": historical_counts,
            "actionable_cutover_at": QUESTIONNAIRE_AUTO_EXECUTE_CUTOVER_AT,
            "database_mutation_performed": False,
            "consumer_executed": False,
            "provider_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }

    def repair(self, *, actor: str, reason: str, limit: int = 100) -> dict[str, Any]:
        normalized_actor = _text(actor)
        normalized_reason = _text(reason)
        if not normalized_actor or not normalized_reason:
            return self._error("actor_and_reason_required")
        if not self._database_url:
            return self._error("database_url_required")
        bounded_limit = max(1, min(int(limit or 100), 500))
        before = self.diagnose()
        repaired_outbox_count = 0
        from psycopg.rows import dict_row

        with connect_raw_postgres(self._database_url) as conn:
            conn.row_factory = dict_row
            rows = conn.execute(
                f"""
                {_ANOMALY_QUERIES["submission_without_outbox"]}
                ORDER BY qs.id
                LIMIT %s
                FOR UPDATE OF qs SKIP LOCKED
                """,
                (bounded_limit,),
            ).fetchall()
            for row in rows:
                request = self._build_repair_request(
                    conn,
                    submission_id=int(row["id"]),
                    actor_hash=_hash(normalized_actor),
                    reason_hash=_hash(normalized_reason),
                )
                if request is None:
                    continue
                enqueue_transactional_internal_event_outbox(conn, request)
                repaired_outbox_count += 1
            conn.commit()
        after = self.diagnose()
        return {
            "ok": bool(before.get("ok")) and bool(after.get("ok")),
            "mode": "repair_continuation_only",
            "before": before,
            "after": after,
            "repaired": {"questionnaire_submitted_outbox_count": repaired_outbox_count},
            "repair_actor_hash": _hash(normalized_actor),
            "repair_reason_hash": _hash(normalized_reason),
            "database_mutation_performed": repaired_outbox_count > 0,
            "consumer_executed": False,
            "provider_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }

    @staticmethod
    def _build_repair_request(
        conn: Any,
        *,
        submission_id: int,
        actor_hash: str,
        reason_hash: str,
    ):
        row = conn.execute(
            """
            SELECT submission.id, submission.questionnaire_id, submission.unionid,
                   submission.follow_user_userid, submission.final_tags,
                   submission.submitted_at, questionnaire.slug, questionnaire.title,
                   questionnaire.external_push_enabled, questionnaire.external_push_url,
                   questionnaire.external_push_type, questionnaire.external_push_expires_at_ts,
                   questionnaire.external_push_day, questionnaire.external_push_frequency,
                   questionnaire.external_push_remark, questionnaire.external_push_custom_params,
                   COALESCE(identity.primary_external_userid, '') AS external_userid,
                   COALESCE(identity.primary_openid, '') AS openid,
                   COALESCE(identity.mobile, '') AS mobile
            FROM questionnaire_submissions submission
            JOIN questionnaires questionnaire ON questionnaire.id = submission.questionnaire_id
            LEFT JOIN crm_user_identity identity ON identity.unionid = submission.unionid
            WHERE submission.id = %s
            """,
            (submission_id,),
        ).fetchone()
        if not row:
            return None
        answers = [
            dict(item)
            for item in conn.execute(
                """
                SELECT question_id, question_type, question_title_snapshot,
                       selected_option_ids, selected_option_texts_snapshot,
                       selected_option_scores_snapshot, selected_option_tags_snapshot,
                       text_value, score_contribution
                FROM questionnaire_submission_answers
                WHERE submission_id = %s
                ORDER BY id
                """,
                (submission_id,),
            ).fetchall()
        ]
        item = dict(row)
        questionnaire = {
            "id": int(item["questionnaire_id"]),
            "slug": _text(item.get("slug")),
            "title": _text(item.get("title")),
            "external_push_config": {
                "enabled": bool(item.get("external_push_enabled")),
                "webhook_url": _text(item.get("external_push_url")),
                "type": _text(item.get("external_push_type")),
                "expires_at_ts": item.get("external_push_expires_at_ts"),
                "day": item.get("external_push_day"),
                "frequency": item.get("external_push_frequency"),
                "remark": _text(item.get("external_push_remark")),
                "custom_params": list(item.get("external_push_custom_params") or []),
            },
        }
        submission = {
            "submission_id": str(submission_id),
            "questionnaire_id": int(item["questionnaire_id"]),
            "slug": _text(item.get("slug")),
            "external_userid": _text(item.get("external_userid")),
            "follow_user_userid": _text(item.get("follow_user_userid")),
            "openid": _text(item.get("openid")),
            "unionid": _text(item.get("unionid")),
            "mobile": _text(item.get("mobile")),
            "submitted_at": _text(item.get("submitted_at")),
            "final_tags": list(item.get("final_tags") or []),
        }
        request = build_questionnaire_submitted_event_request(
            questionnaire=questionnaire,
            submission=submission,
            answer_snapshots=answers,
            context=CommandContext(
                actor_id=f"repair:{actor_hash}",
                actor_type="operator",
                request_id=f"r09-repair-{submission_id}",
                trace_id=f"r09-repair-{submission_id}",
                source_route="questionnaire.reconciliation.repair",
            ),
            source_command_id=f"r09-repair-{submission_id}",
        )
        if request is None:
            return None
        return replace(
            request,
            source_module="questionnaire.reconciliation",
            payload_summary={
                **dict(request.payload_summary or {}),
                "reconciliation_repair": True,
                "repair_actor_hash": actor_hash,
                "repair_reason_hash": reason_hash,
            },
        )

    @staticmethod
    def _error(error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": error,
            "database_mutation_performed": False,
            "consumer_executed": False,
            "provider_executed": False,
            "real_external_call_executed": False,
            "pii_in_output": False,
        }


__all__ = ["QuestionnaireRadarReconciliationService"]
