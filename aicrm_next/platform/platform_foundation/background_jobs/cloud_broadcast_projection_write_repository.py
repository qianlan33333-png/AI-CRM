from __future__ import annotations

from typing import Any

from sqlalchemy import text


class PostgresCloudBroadcastProjectionWriteRepository:
    """Canonical writer for cloud broadcast recipient and message projections."""

    def mark_dispatching_dbapi(self, executor: Any, *, job_id: int) -> None:
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipients recipient
            SET send_status = 'dispatching', last_error = '', updated_at = CURRENT_TIMESTAMP
            FROM broadcast_jobs job
            WHERE job.id = %s
              AND job.source_type = 'cloud_plan'
              AND job.source_table = 'cloud_broadcast_plan_recipients'
              AND recipient.broadcast_job_id = job.id
              AND recipient.send_status IN ('pending', 'queued', 'sending', 'failed_retryable')
            """,
            (int(job_id),),
        )
        executor.execute(
            """
            WITH next_message AS (
                SELECT message.id
                FROM cloud_broadcast_plan_recipient_messages message
                JOIN cloud_broadcast_plan_recipients recipient ON recipient.id = message.recipient_id
                WHERE recipient.broadcast_job_id = %s
                  AND message.status IN ('pending', 'queued', 'failed_retryable')
                ORDER BY message.sequence_index ASC, message.id ASC
                LIMIT 1
            )
            UPDATE cloud_broadcast_plan_recipient_messages message
            SET status = 'dispatching', last_error = '', updated_at = CURRENT_TIMESTAMP
            FROM next_message
            WHERE message.id = next_message.id
            """,
            (int(job_id),),
        )

    def mark_delegated_dbapi(self, executor: Any, *, job_id: int) -> None:
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipients recipient
            SET send_status = 'delegated', last_error = '', updated_at = CURRENT_TIMESTAMP
            WHERE recipient.broadcast_job_id = %s
              AND recipient.send_status IN ('pending', 'queued', 'delegated')
            """,
            (int(job_id),),
        )
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipient_messages message
            SET status = 'delegated', last_error = '', updated_at = CURRENT_TIMESTAMP
            FROM cloud_broadcast_plan_recipients recipient
            WHERE recipient.broadcast_job_id = %s
              AND message.recipient_id = recipient.id
              AND message.status IN ('pending', 'queued', 'delegated')
            """,
            (int(job_id),),
        )

    def finalize_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        status: str,
        last_error: str,
    ) -> None:
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipients recipient
            SET send_status = %s,
                last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            FROM broadcast_jobs job
            WHERE job.id = %s
              AND job.source_type = 'cloud_plan'
              AND job.source_table = 'cloud_broadcast_plan_recipients'
              AND recipient.broadcast_job_id = job.id
              AND recipient.send_status = 'dispatching'
            """,
            (str(status or "").strip(), str(last_error or "")[:1000], int(job_id)),
        )
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipient_messages message
            SET status = %s,
                sent_at = CASE WHEN %s = 'sent' THEN CURRENT_TIMESTAMP ELSE NULL END,
                last_error = %s,
                updated_at = CURRENT_TIMESTAMP
            FROM cloud_broadcast_plan_recipients recipient
            WHERE recipient.broadcast_job_id = %s
              AND message.recipient_id = recipient.id
              AND message.status = 'dispatching'
            """,
            (
                str(status or "").strip(),
                str(status or "").strip(),
                str(last_error or "")[:1000],
                int(job_id),
            ),
        )

    def mark_unknown_after_dispatch_dbapi(
        self,
        executor: Any,
        *,
        job_id: int,
        last_error: str,
    ) -> None:
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipients recipient
            SET send_status = 'unknown_after_dispatch', last_error = %s, updated_at = CURRENT_TIMESTAMP
            WHERE recipient.broadcast_job_id = %s AND recipient.send_status = 'dispatching'
            """,
            (str(last_error or "")[:1000], int(job_id)),
        )
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipient_messages message
            SET status = 'unknown_after_dispatch', sent_at = NULL,
                last_error = %s, updated_at = CURRENT_TIMESTAMP
            FROM cloud_broadcast_plan_recipients recipient
            WHERE recipient.broadcast_job_id = %s
              AND message.recipient_id = recipient.id
              AND message.status = 'dispatching'
            """,
            (str(last_error or "")[:1000], int(job_id)),
        )

    def settle_from_external_effect_sqlalchemy(
        self,
        executor: Any,
        *,
        job_id: int,
        recipient_status: str,
        message_status: str,
        last_error: str,
    ) -> None:
        executor.execute(
            text(
                """
                UPDATE cloud_broadcast_plan_recipients
                SET send_status = :status, last_error = :last_error, updated_at = CURRENT_TIMESTAMP
                WHERE broadcast_job_id = :job_id
                """
            ),
            {
                "job_id": int(job_id),
                "status": str(recipient_status or "").strip(),
                "last_error": str(last_error or "")[:1000],
            },
        )
        executor.execute(
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
                "job_id": int(job_id),
                "status": str(message_status or "").strip(),
                "last_error": str(last_error or "")[:1000],
            },
        )

    def queue_planned_recipient_dbapi(
        self,
        executor: Any,
        *,
        plan_id: str,
        recipient_id: int,
        job_id: int,
        approved_by: str,
    ) -> None:
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipients
            SET approval_status = 'approved',
                send_status = CASE WHEN send_status = 'pending' THEN 'queued' ELSE send_status END,
                approved_by = CASE WHEN COALESCE(approved_by, '') = '' THEN %s ELSE approved_by END,
                approved_at = COALESCE(approved_at, CURRENT_TIMESTAMP),
                broadcast_job_id = COALESCE(broadcast_job_id, %s),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (str(approved_by or "").strip(), int(job_id), int(recipient_id)),
        )
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipient_messages
            SET status = CASE WHEN status = 'pending' THEN 'queued' ELSE status END,
                updated_at = CURRENT_TIMESTAMP
            WHERE plan_id = %s
              AND recipient_id = %s
            """,
            (str(plan_id or "").strip(), int(recipient_id)),
        )

    def insert_campaign_preparation_projection_sqlalchemy(
        self,
        executor: Any,
        *,
        plan_id: str,
        preparation_id: str,
    ) -> None:
        executor.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plan_recipients (
                    plan_id, unionid, owner_userid, display_name,
                    planned_message_count, approval_status, send_status
                )
                SELECT :plan_id, resolved_unionid, resolved_owner_userid,
                       row_key, 1, 'pending', 'pending'
                FROM external_campaign_preparation_recipients
                WHERE preparation_id = :preparation_id AND row_status = 'eligible'
                ORDER BY id
                ON CONFLICT (plan_id, unionid) DO NOTHING
                """
            ),
            {"plan_id": plan_id, "preparation_id": preparation_id},
        )
        executor.execute(
            text(
                """
                INSERT INTO cloud_broadcast_plan_recipient_messages (
                    plan_id, recipient_id, unionid, sequence_index, day_offset,
                    send_time, content_text, content_payload_json,
                    attachments_json, status
                )
                SELECT :plan_id, recipient.id, recipient.unionid, 1, 0,
                       to_char(preparation.scheduled_for AT TIME ZONE preparation.timezone, 'HH24:MI'),
                       staging.content_text,
                       jsonb_build_object(
                           'content_package', jsonb_build_object(
                               'content_text', staging.content_text,
                               'image_library_ids', '[]'::jsonb,
                               'miniprogram_library_ids', '[]'::jsonb,
                               'attachment_library_ids', '[]'::jsonb,
                               'group_invite_library_ids', '[]'::jsonb,
                               'dynamic_miniprogram_card', staging.dynamic_card_json
                           ),
                           'dynamic_miniprogram_card', staging.dynamic_card_json,
                           'scheduled_for', to_jsonb(preparation.scheduled_for),
                           'timezone', to_jsonb(preparation.timezone)
                       ),
                       '[]'::jsonb, 'pending'
                FROM external_campaign_preparations preparation
                JOIN external_campaign_preparation_recipients staging
                  ON staging.preparation_id = preparation.preparation_id
                 AND staging.row_status = 'eligible'
                JOIN cloud_broadcast_plan_recipients recipient
                  ON recipient.plan_id = :plan_id
                 AND recipient.unionid = staging.resolved_unionid
                WHERE preparation.preparation_id = :preparation_id
                ON CONFLICT DO NOTHING
                """
            ),
            {"plan_id": plan_id, "preparation_id": preparation_id},
        )

    def upsert_agent_recipient_dbapi(
        self,
        executor: Any,
        *,
        plan_id: str,
        unionid: str,
        owner_userid: str,
        display_name: str,
        approval_status: str,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            INSERT INTO cloud_broadcast_plan_recipients (
                plan_id, unionid, owner_userid, display_name, planned_message_count,
                approval_status, send_status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, 1, %s, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (plan_id, unionid) WHERE unionid <> '' DO UPDATE SET
                owner_userid = EXCLUDED.owner_userid,
                planned_message_count = 1,
                updated_at = CURRENT_TIMESTAMP
            RETURNING *
            """,
            (
                str(plan_id or "").strip(),
                str(unionid or "").strip(),
                str(owner_userid or "").strip(),
                str(display_name or "").strip(),
                str(approval_status or "pending").strip(),
            ),
        ).fetchone()
        return dict(row) if row else None

    def upsert_agent_message_dbapi(
        self,
        executor: Any,
        *,
        plan_id: str,
        recipient_id: int,
        unionid: str,
        content_text: str,
        content_payload_json: str,
    ) -> int:
        existing = executor.execute(
            """
            SELECT id
            FROM cloud_broadcast_plan_recipient_messages
            WHERE plan_id = %s AND recipient_id = %s AND sequence_index = 1
            ORDER BY id ASC
            LIMIT 1
            """,
            (str(plan_id or "").strip(), int(recipient_id)),
        ).fetchone()
        if existing:
            message_id = int(existing["id"])
            executor.execute(
                """
                UPDATE cloud_broadcast_plan_recipient_messages
                SET content_text = %s,
                    content_payload_json = %s::jsonb,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (str(content_text or ""), str(content_payload_json or "{}"), message_id),
            )
            return message_id
        inserted = executor.execute(
            """
            INSERT INTO cloud_broadcast_plan_recipient_messages (
                plan_id, recipient_id, unionid, sequence_index, day_offset, send_time,
                content_text, content_payload_json, attachments_json, status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, 1, 0, '', %s, %s::jsonb, '[]'::jsonb, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING id
            """,
            (
                str(plan_id or "").strip(),
                int(recipient_id),
                str(unionid or "").strip(),
                str(content_text or ""),
                str(content_payload_json or "{}"),
            ),
        ).fetchone()
        return int((inserted or {}).get("id") or 0)

    def approve_recipient_dbapi(
        self,
        executor: Any,
        *,
        recipient_id: int,
        approved_by: str,
        job_id: int | None,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipients
            SET approval_status = 'approved',
                send_status = CASE WHEN send_status = 'pending' THEN 'queued' ELSE send_status END,
                approved_by = %s,
                approved_at = CURRENT_TIMESTAMP,
                broadcast_job_id = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
            """,
            (str(approved_by or "").strip(), job_id, int(recipient_id)),
        ).fetchone()
        executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipient_messages
            SET status = CASE WHEN status = 'pending' THEN 'queued' ELSE status END,
                updated_at = CURRENT_TIMESTAMP
            WHERE recipient_id = %s
            """,
            (int(recipient_id),),
        )
        return dict(row) if row else None

    def reject_recipient_dbapi(
        self,
        executor: Any,
        *,
        recipient_id: int,
        rejected_by: str,
        reason: str,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipients
            SET approval_status = 'rejected',
                send_status = CASE WHEN send_status IN ('pending', 'queued') THEN 'cancelled' ELSE send_status END,
                rejected_by = %s,
                rejected_at = CURRENT_TIMESTAMP,
                reject_reason = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
            """,
            (str(rejected_by or "").strip(), str(reason or "")[:500], int(recipient_id)),
        ).fetchone()
        return dict(row) if row else None

    def update_recipient_message_dbapi(
        self,
        executor: Any,
        *,
        message_id: int,
        content_text: str,
        content_payload_json: str,
        day_offset: int,
        send_time: str,
    ) -> dict[str, Any] | None:
        row = executor.execute(
            """
            UPDATE cloud_broadcast_plan_recipient_messages
            SET content_text = %s,
                content_payload_json = %s::jsonb,
                attachments_json = '[]'::jsonb,
                day_offset = %s,
                send_time = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
            """,
            (
                str(content_text or ""),
                str(content_payload_json or "{}"),
                max(0, int(day_offset)),
                str(send_time or "")[:16],
                int(message_id),
            ),
        ).fetchone()
        return dict(row) if row else None


__all__ = ["PostgresCloudBroadcastProjectionWriteRepository"]
