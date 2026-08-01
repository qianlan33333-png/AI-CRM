from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import text

from aicrm_next.platform.shared.db_session import get_session_factory


class AudienceSendRecordRepository(Protocol):
    def get_package(self, package_id: int) -> dict[str, Any] | None: ...

    def list_records(
        self,
        *,
        package_id: int,
        package_key: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_record(
        self,
        *,
        package_id: int,
        package_key: str,
        record_id: str,
    ) -> dict[str, Any] | None: ...


class PostgresAudienceSendRecordRepository:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def get_package(self, package_id: int) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = (
                session.execute(
                    text(
                        """
                        SELECT id, package_key, name
                        FROM ai_audience_package
                        WHERE id = :package_id
                          AND status <> 'archived'
                        LIMIT 1
                        """
                    ),
                    {"package_id": int(package_id)},
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def list_records(
        self,
        *,
        package_id: int,
        package_key: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "package_id": int(package_id),
            "package_key": str(package_key),
            "limit": int(limit),
            "offset": int(offset),
        }
        with self._session_factory() as session:
            rows = session.execute(text(_LIST_SQL), params).mappings().all()
            if rows:
                total = int(rows[0].get("total_count") or 0)
            else:
                total = int(session.execute(text(_COUNT_SQL), params).scalar_one())
        return [_without_count(dict(row)) for row in rows], total

    def get_record(
        self,
        *,
        package_id: int,
        package_key: str,
        record_id: str,
    ) -> dict[str, Any] | None:
        params = {
            "package_id": int(package_id),
            "package_key": str(package_key),
            "record_id": str(record_id),
        }
        with self._session_factory() as session:
            row = session.execute(text(_DETAIL_SQL), params).mappings().first()
        return dict(row) if row else None


def _without_count(row: dict[str, Any]) -> dict[str, Any]:
    row.pop("total_count", None)
    return row


def build_audience_send_record_repository() -> AudienceSendRecordRepository:
    return PostgresAudienceSendRecordRepository()


_RECORDS_CTE = r"""
WITH automation_records AS (
    SELECT
        'automation:' || broadcast.id::text AS record_id,
        broadcast.id AS record_numeric_id,
        CASE
            WHEN COALESCE(batch.agent_config_snapshot_json ->> 'automation_type', 'agent') = 'fixed_script'
                THEN 'fixed_script'
            ELSE 'agent_bot'
        END AS source,
        recipient.unionid AS unionid,
        COALESCE(
            NULLIF(item.context_snapshot_json #>> '{customer,customer_name}', ''),
            NULLIF(item.context_snapshot_json #>> '{customer,nickname}', ''),
            NULLIF(item.context_snapshot_json #>> '{customer,name}', ''),
            NULLIF(recipient.display_name, ''),
            NULLIF(identity.customer_name, ''),
            ''
        ) AS nickname,
        COALESCE(
            NULLIF(effect.payload_json -> 'external_userids' ->> 0, ''),
            NULLIF(item.context_snapshot_json #>> '{customer,external_userid}', ''),
            NULLIF(item.context_snapshot_json #>> '{customer,primary_external_userid}', ''),
            NULLIF(identity.primary_external_userid, ''),
            ''
        ) AS external_userid,
        COALESCE(NULLIF(effect.status, ''), NULLIF(broadcast.status, ''), NULLIF(recipient.send_status, ''), 'pending') AS raw_status,
        COALESCE(NULLIF(broadcast.status, ''), NULLIF(recipient.send_status, ''), 'pending') AS business_status,
        COALESCE(effect.side_effect_executed, broadcast.side_effect_executed, FALSE) AS side_effect_executed,
        COALESCE(effect.provider_result_received, broadcast.provider_result_received, FALSE) AS provider_result_received,
        GREATEST(
            COALESCE(effect.attempt_count, 0),
            COALESCE(provider_attempt.attempt_count, 0),
            COALESCE(broadcast.attempt_count, 0)
        )::int AS attempt_count,
        COALESCE(effect.provider_call_started_at, provider_attempt.started_at) AS provider_call_started_at,
        effect.completed_at AS effect_completed_at,
        message.sent_at AS message_sent_at,
        COALESCE(
            NULLIF(effect.last_error_message, ''),
            NULLIF(effect.last_error_code, ''),
            NULLIF(provider_attempt.error_message, ''),
            NULLIF(provider_attempt.error_code, ''),
            NULLIF(broadcast.last_error, ''),
            NULLIF(recipient.last_error, ''),
            NULLIF(message.last_error, ''),
            ''
        ) AS failure_reason,
        COALESCE(NULLIF(effect.payload_json ->> 'content_text', ''), '') AS actual_content_text,
        COALESCE(NULLIF(message.content_text, ''), '') AS message_content_text,
        COALESCE(NULLIF(message.content_text, ''), NULLIF(item.content_package_json ->> 'content_text', ''), '') AS planned_content_text,
        COALESCE(effect.payload_json -> 'attachments', '[]'::jsonb) AS actual_attachments,
        COALESCE(message.attachments_json, '[]'::jsonb) AS planned_attachments,
        COALESCE(effect.payload_json -> 'media_refs', '[]'::jsonb) AS media_refs,
        COALESCE(item.content_package_json, '{}'::jsonb)
            || COALESCE(message.content_payload_json, '{}'::jsonb) AS planned_content_package,
        COALESCE(effect.payload_json -> 'content_payload_json', '{}'::jsonb) AS actual_content_package,
        (effect.id IS NOT NULL) AS effect_materialized,
        COALESCE(broadcast.created_at, recipient.created_at, plan.created_at) AS business_created_at
    FROM cloud_broadcast_plans plan
    JOIN cloud_broadcast_plan_recipients recipient
      ON recipient.plan_id = plan.plan_id
     AND recipient.broadcast_job_id IS NOT NULL
    JOIN broadcast_jobs broadcast
      ON broadcast.id = recipient.broadcast_job_id
    LEFT JOIN LATERAL (
        SELECT candidate.*
        FROM cloud_broadcast_plan_recipient_messages candidate
        WHERE candidate.recipient_id = recipient.id
        ORDER BY candidate.sequence_index ASC, candidate.id ASC
        LIMIT 1
    ) message ON TRUE
    LEFT JOIN LATERAL (
        SELECT candidate.*
        FROM external_effect_job candidate
        WHERE candidate.id = broadcast.external_effect_job_id
           OR (
                candidate.business_type = 'broadcast_job'
                AND candidate.business_id = broadcast.id::text
           )
        ORDER BY
            CASE WHEN candidate.id = broadcast.external_effect_job_id THEN 0 ELSE 1 END,
            candidate.id DESC
        LIMIT 1
    ) effect ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            candidate.started_at,
            candidate.error_code,
            candidate.error_message,
            COUNT(*) OVER()::int AS attempt_count
        FROM external_effect_attempt candidate
        WHERE candidate.job_id = effect.id
        ORDER BY candidate.id DESC
        LIMIT 1
    ) provider_attempt ON TRUE
    LEFT JOIN automation_agent_webhook_item item
      ON item.external_event_id = plan.selection_json ->> 'external_event_id'
    LEFT JOIN automation_agent_webhook_batch batch
      ON batch.batch_id = item.batch_id
    LEFT JOIN crm_user_identity identity
      ON identity.unionid = recipient.unionid
    WHERE plan.content_strategy = 'agent_generated_single'
      AND plan.selection_json ->> 'source' = 'automation_agent'
      AND plan.selection_json ->> 'package_key' = :package_key
),
manual_records AS (
    SELECT
        'manual:' || effect.id::text AS record_id,
        effect.id AS record_numeric_id,
        'manual_broadcast' AS source,
        COALESCE(NULLIF(effect.payload_json ->> 'target_unionid', ''), NULLIF(effect.target_id, ''), '') AS unionid,
        COALESCE(
            NULLIF(effect.payload_json ->> 'target_display_name', ''),
            NULLIF(identity.customer_name, ''),
            ''
        ) AS nickname,
        COALESCE(
            NULLIF(effect.payload_json -> 'external_userids' ->> 0, ''),
            NULLIF(identity.primary_external_userid, ''),
            ''
        ) AS external_userid,
        COALESCE(NULLIF(effect.status, ''), NULLIF(send_record.status, ''), 'planned') AS raw_status,
        COALESCE(NULLIF(send_record.status, ''), 'created') AS business_status,
        COALESCE(effect.side_effect_executed, FALSE) AS side_effect_executed,
        COALESCE(effect.provider_result_received, FALSE) AS provider_result_received,
        GREATEST(COALESCE(effect.attempt_count, 0), COALESCE(provider_attempt.attempt_count, 0))::int AS attempt_count,
        COALESCE(effect.provider_call_started_at, provider_attempt.started_at) AS provider_call_started_at,
        effect.completed_at AS effect_completed_at,
        NULL::timestamptz AS message_sent_at,
        COALESCE(
            NULLIF(effect.last_error_message, ''),
            NULLIF(effect.last_error_code, ''),
            NULLIF(provider_attempt.error_message, ''),
            NULLIF(provider_attempt.error_code, ''),
            ''
        ) AS failure_reason,
        COALESCE(effect.payload_json ->> 'content_text', '') AS actual_content_text,
        ''::text AS message_content_text,
        COALESCE(NULLIF(effect.payload_json ->> 'content_text', ''), NULLIF(send_record.content_preview, ''), '') AS planned_content_text,
        COALESCE(effect.payload_json -> 'attachments', '[]'::jsonb) AS actual_attachments,
        '[]'::jsonb AS planned_attachments,
        COALESCE(effect.payload_json -> 'media_refs', '[]'::jsonb) AS media_refs,
        '{}'::jsonb AS planned_content_package,
        '{}'::jsonb AS actual_content_package,
        TRUE AS effect_materialized,
        COALESCE(effect.created_at, send_record.created_at) AS business_created_at
    FROM user_ops_send_records_next send_record
    JOIN external_effect_job effect
      ON effect.business_type = 'user_ops_batch_send'
     AND effect.business_id = send_record.record_key
    LEFT JOIN LATERAL (
        SELECT
            candidate.started_at,
            candidate.error_code,
            candidate.error_message,
            COUNT(*) OVER()::int AS attempt_count
        FROM external_effect_attempt candidate
        WHERE candidate.job_id = effect.id
        ORDER BY candidate.id DESC
        LIMIT 1
    ) provider_attempt ON TRUE
    LEFT JOIN crm_user_identity identity
      ON identity.unionid = COALESCE(
          NULLIF(effect.payload_json ->> 'target_unionid', ''),
          NULLIF(effect.target_id, '')
      )
    WHERE send_record.target_source = 'ai_audience_package'
      AND send_record.target_source_id = :package_id
),
records AS (
    SELECT * FROM automation_records
    UNION ALL
    SELECT * FROM manual_records
)
"""

_LIST_SQL = (
    _RECORDS_CTE
    + r"""
SELECT records.*, COUNT(*) OVER()::int AS total_count
FROM records
ORDER BY business_created_at DESC, record_numeric_id DESC, record_id DESC
LIMIT :limit OFFSET :offset
"""
)

_COUNT_SQL = _RECORDS_CTE + "SELECT COUNT(*)::int FROM records"

_DETAIL_SQL = (
    _RECORDS_CTE
    + r"""
SELECT *
FROM records
WHERE record_id = :record_id
LIMIT 1
"""
)
