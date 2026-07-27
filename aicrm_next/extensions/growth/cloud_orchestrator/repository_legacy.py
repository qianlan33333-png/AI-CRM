from __future__ import annotations

from typing import Any

from aicrm_next.platform_foundation.background_jobs.broadcast_job_write_port import (
    BroadcastJobCreate,
    build_broadcast_job_write_port,
)

from .repository import (
    _CAMPAIGN_OPEN_JOB_STATUSES,
    _CAMPAIGN_QUEUE_CONTENT_TYPE,
    _CAMPAIGN_QUEUE_SOURCE_TABLE,
    _CAMPAIGN_QUEUE_SOURCE_TYPE,
    _DEFAULT_CAMPAIGN_SEND_TIME,
    _DEFAULT_CAMPAIGN_TIMEZONE,
    _LEGACY_GROUP_KEY_SQL,
    _LEGACY_GROUP_KEY_UPDATE_SQL,
    _LEGACY_GROUP_LABEL_SQL,
    _campaign_job_source_id,
    _campaign_private_broadcast_payload,
    _content_payload_for_package,
    _json,
    _json_dump,
    _legacy_campaign_job_source_id,
    _legacy_recipient_status,
    _message_view,
    _recipient_view,
    _text,
    _unionid_targets_from_external_members,
    campaign_step_due_iso,
)


class CloudLegacyPostgresRepositoryMixin:
    def _approve_and_start_legacy_group(self, conn, plan_id: str, *, operator: str) -> dict[str, Any] | None:
        normalized_plan_id = _text(plan_id)
        campaigns = conn.execute(
            """
            SELECT c.*
            FROM campaigns c
            WHERE """
            + _LEGACY_GROUP_KEY_SQL
            + """
             = %s
            FOR UPDATE
            """,
            (normalized_plan_id,),
        ).fetchall()
        if not campaigns:
            return None
        before = [dict(row) for row in campaigns]
        if any(_text(row.get("review_status")) == "rejected" for row in campaigns):
            raise ValueError("plan is rejected")
        campaign_ids = [int(row["id"]) for row in campaigns]
        conn.execute(
            """
            UPDATE campaigns
            SET review_status = 'approved',
                run_status = CASE
                    WHEN run_status IN ('finished', 'completed', 'cancelled') THEN run_status
                    ELSE 'active'
                END,
                approved_by = %s,
                approved_at = COALESCE(approved_at, CURRENT_TIMESTAMP),
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(%s)
            """,
            (_text(operator) or "crm_console", campaign_ids),
        )
        conn.execute(
            """
            UPDATE campaign_members cm
            SET anchor_date = COALESCE(NULLIF(c.anchor_date, ''), TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD')),
                joined_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            FROM campaigns c
            WHERE cm.campaign_id = c.id
              AND c.id = ANY(%s)
              AND COALESCE(c.anchor_mode, 'campaign_start_date') = 'campaign_start_date'
              AND cm.status = 'pending'
            """,
            (campaign_ids,),
        )
        conn.execute(
            """
            UPDATE campaign_members cm
            SET anchor_date = TO_CHAR(COALESCE(cm.joined_at, CURRENT_TIMESTAMP) AT TIME ZONE 'UTC', 'YYYY-MM-DD'),
                updated_at = CURRENT_TIMESTAMP
            FROM campaigns c
            WHERE cm.campaign_id = c.id
              AND c.id = ANY(%s)
              AND COALESCE(c.anchor_mode, 'campaign_start_date') <> 'campaign_start_date'
              AND cm.status = 'pending'
            """,
            (campaign_ids,),
        )
        due_rows = conn.execute(
            """
            SELECT cm.id AS cm_id,
                   cm.member_id,
                   cm.unionid,
                   cm.campaign_id,
                   cm.campaign_segment_id,
                   cm.anchor_date,
                   cm.trace_id AS member_trace_id,
                   c.campaign_code,
                   c.owner_userid,
                   c.trace_id AS campaign_trace_id,
                   cs.id AS step_id,
                   cs.step_index,
                   cs.day_offset,
                   cs.send_time,
                   cs.timezone,
                   cs.content_text,
                   cs.content_payload_json,
                   cs.stop_on_reply,
                   cs.skip_if_recently_touched_days
            FROM campaign_members cm
            JOIN campaigns c ON c.id = cm.campaign_id
            JOIN LATERAL (
                SELECT *
                FROM campaign_steps cs
                WHERE cs.campaign_segment_id = cm.campaign_segment_id
                ORDER BY cs.step_index ASC, cs.id ASC
                LIMIT 1
            ) cs ON TRUE
            WHERE cm.campaign_id = ANY(%s)
              AND c.run_status = 'active'
              AND cm.status = 'pending'
            ORDER BY cm.id ASC
            """,
            (campaign_ids,),
        ).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        for row in due_rows:
            due_iso = campaign_step_due_iso(
                anchor_date=_text(row.get("anchor_date")),
                day_offset=int(row.get("day_offset") or 0),
                send_time=_text(row.get("send_time")) or _DEFAULT_CAMPAIGN_SEND_TIME,
                step_timezone=_text(row.get("timezone")) or _DEFAULT_CAMPAIGN_TIMEZONE,
            )
            conn.execute(
                """
                UPDATE campaign_members
                SET next_due_at = %s::timestamptz,
                    current_step_index = -1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (due_iso, int(row["cm_id"])),
            )
            source_id = _campaign_job_source_id(
                campaign_id=int(row["campaign_id"]),
                campaign_segment_id=int(row["campaign_segment_id"]),
                step_index=int(row.get("step_index") or 0),
            )
            if source_id not in groups:
                groups[source_id] = {
                    "source_ids": (
                        source_id,
                        _legacy_campaign_job_source_id(
                            campaign_id=int(row["campaign_id"]),
                            step_index=int(row.get("step_index") or 0),
                        ),
                    ),
                    "scheduled_for": due_iso,
                    "campaign": {
                        "id": int(row["campaign_id"]),
                        "campaign_code": _text(row.get("campaign_code")),
                        "owner_userid": _text(row.get("owner_userid")),
                        "trace_id": _text(row.get("campaign_trace_id") or row.get("member_trace_id")),
                    },
                    "step": {
                        "id": int(row.get("step_id") or 0),
                        "step_index": int(row.get("step_index") or 0),
                        "day_offset": int(row.get("day_offset") or 0),
                        "send_time": _text(row.get("send_time")),
                        "content_text": _text(row.get("content_text")),
                        "timezone": _text(row.get("timezone")),
                        "content_payload_json": _json(row.get("content_payload_json"), default={}),
                        "stop_on_reply": bool(row.get("stop_on_reply")),
                        "skip_if_recently_touched_days": int(row.get("skip_if_recently_touched_days") or 0),
                    },
                    "members": [],
                }
            unionid = _text(row.get("unionid"))
            if unionid:
                groups[source_id]["members"].append(
                    {
                        "cm_id": int(row["cm_id"]),
                        "member_id": int(row.get("member_id") or 0),
                        "unionid": unionid,
                        "trace_id": _text(row.get("member_trace_id")),
                        "campaign_segment_id": int(row["campaign_segment_id"]),
                    }
                )
        enqueued = 0
        for source_id, group in groups.items():
            members = group["members"]
            if not members:
                continue
            existing = conn.execute(
                """
                SELECT id
                FROM broadcast_jobs
                WHERE source_type = %s
                  AND source_table = %s
                  AND source_id = ANY(%s)
                  AND status = ANY(%s)
                LIMIT 1
                """,
                (_CAMPAIGN_QUEUE_SOURCE_TYPE, _CAMPAIGN_QUEUE_SOURCE_TABLE, list(group["source_ids"]), _CAMPAIGN_OPEN_JOB_STATUSES),
            ).fetchone()
            if existing:
                continue
            campaign = group["campaign"]
            step = group["step"]
            target_unionids, normalized_members = _unionid_targets_from_external_members(conn, members)
            if not target_unionids:
                continue
            inserted = build_broadcast_job_write_port().create_dbapi(
                conn,
                BroadcastJobCreate(
                    source_type=_CAMPAIGN_QUEUE_SOURCE_TYPE,
                    source_id=source_id,
                    source_table=_CAMPAIGN_QUEUE_SOURCE_TABLE,
                    scheduled_for=group["scheduled_for"],
                    priority=100,
                    batch_key=normalized_plan_id,
                    idempotency_key=f"campaign_member_step:{source_id}",
                    target_unionids=tuple(target_unionids),
                    target_summary=(
                        f"campaign={campaign.get('campaign_code')} "
                        f"step={step.get('step_index')}"
                    ),
                    content_type=_CAMPAIGN_QUEUE_CONTENT_TYPE,
                    content_payload=_campaign_private_broadcast_payload(
                        campaign=campaign,
                        step=step,
                        members=normalized_members,
                    ),
                    content_summary=_text(step.get("content_text"))[:200],
                    trace_id=_text(campaign.get("trace_id")),
                    created_by=_text(operator) or "crm_console",
                    business_domain="automation_ops",
                    channel="wecom_private",
                    target_kind="unionid",
                ),
            )
            if inserted:
                enqueued += 1
        after = conn.execute(
            """
            SELECT c.*
            FROM campaigns c
            WHERE """
            + _LEGACY_GROUP_KEY_SQL
            + """
             = %s
            ORDER BY c.id ASC
            """,
            (normalized_plan_id,),
        ).fetchall()
        self._audit(
            conn,
            operator=operator,
            action_type="legacy_campaign_group_approve_and_start_from_cloud_plan",
            target_type="legacy_campaign_group",
            target_id=normalized_plan_id,
            before={"campaigns": before},
            after={"campaigns": [dict(row) for row in after], "queued_jobs": enqueued},
        )
        return self._legacy_group_plan_row(conn, normalized_plan_id)

    def _reject_legacy_group(self, conn, plan_id: str, *, operator: str, reason: str = "") -> dict[str, Any] | None:
        normalized_plan_id = _text(plan_id)
        campaigns = conn.execute(
            """
            SELECT c.*
            FROM campaigns c
            WHERE """
            + _LEGACY_GROUP_KEY_SQL
            + """
             = %s
            FOR UPDATE
            """,
            (normalized_plan_id,),
        ).fetchall()
        if not campaigns:
            return None
        campaign_ids = [int(row["id"]) for row in campaigns]
        before = [dict(row) for row in campaigns]
        reason_text = _text(reason)[:200] or "cloud plan rejected"
        updated_campaigns = conn.execute(
            """
            UPDATE campaigns
            SET review_status = 'rejected',
                run_status = CASE
                    WHEN run_status IN ('finished', 'completed') THEN run_status
                    ELSE 'cancelled'
                END,
                paused_reason = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(%s)
            RETURNING *
            """,
            (reason_text, campaign_ids),
        ).fetchall()
        cancelled_members = conn.execute(
            """
            UPDATE campaign_members
            SET status = 'cancelled',
                next_due_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE campaign_id = ANY(%s)
              AND status IN ('pending', 'running', 'queued', 'paused')
            RETURNING id
            """,
            (campaign_ids,),
        ).fetchall()
        cancelled_jobs = build_broadcast_job_write_port().cancel_campaign_jobs_dbapi(
            conn,
            campaign_ids=campaign_ids,
            actor=_text(operator) or "crm_console",
            reason=reason_text,
            source_type=_CAMPAIGN_QUEUE_SOURCE_TYPE,
            source_table=_CAMPAIGN_QUEUE_SOURCE_TABLE,
            open_statuses=_CAMPAIGN_OPEN_JOB_STATUSES,
        )
        self._audit(
            conn,
            operator=operator,
            action_type="legacy_campaign_group_reject_from_cloud_plan",
            target_type="legacy_campaign_group",
            target_id=normalized_plan_id,
            before={"campaigns": before},
            after={
                "campaigns": [dict(row) for row in updated_campaigns],
                "cancelled_members": len(cancelled_members),
                "cancelled_jobs": len(cancelled_jobs),
            },
        )
        return self._legacy_group_plan_row(conn, normalized_plan_id)

    def _legacy_group_plan_row(self, conn, plan_id: str):
        return conn.execute(
            f"""
            SELECT MAX(c.id) AS id,
                   {_LEGACY_GROUP_KEY_SQL} AS plan_id,
                   MAX(c.intent) AS intent,
                   {_LEGACY_GROUP_LABEL_SQL} AS display_name,
                   STRING_AGG(DISTINCT NULLIF(c.owner_userid, ''), ' / ') AS owner_userid,
                   COUNT(cm.id) AS candidate_count,
                   jsonb_build_object('group_code', {_LEGACY_GROUP_KEY_SQL}, 'legacy_campaign_count', COUNT(DISTINCT c.id)) AS selection_json,
                   CASE
                       WHEN BOOL_AND(c.review_status = 'approved') THEN 'approved'
                       WHEN BOOL_OR(c.review_status = 'rejected') AND NOT BOOL_OR(c.review_status <> 'rejected') THEN 'rejected'
                       ELSE 'pending_review'
                   END AS review_status,
                   CASE
                       WHEN BOOL_OR(c.run_status = 'active') THEN 'active'
                       WHEN BOOL_OR(c.run_status = 'paused') THEN 'paused'
                       WHEN BOOL_AND(c.run_status IN ('finished', 'completed')) THEN 'finished'
                       WHEN BOOL_AND(c.run_status = 'cancelled') THEN 'cancelled'
                       ELSE 'draft'
                   END AS run_status,
                   CASE
                       WHEN BOOL_OR(c.run_status = 'active') THEN 'active'
                       WHEN BOOL_AND(c.run_status = 'cancelled') THEN 'cancelled'
                       ELSE 'draft'
                   END AS status,
                   MAX(c.updated_at) AS updated_at,
                   'legacy_campaign' AS source_type
            FROM campaigns c
            LEFT JOIN campaign_members cm ON cm.campaign_id = c.id
            WHERE {_LEGACY_GROUP_KEY_SQL} = %s
            GROUP BY {_LEGACY_GROUP_KEY_SQL}
            """,
            (_text(plan_id),),
        ).fetchone()

    def _legacy_recipients(self, conn, plan_id: str, *, status: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        status_clause = ""
        params: list[Any] = [plan_id]
        if status == "approved":
            status_clause = " AND cm.status IN ('running', 'queued', 'completed', 'sent', 'failed')"
        elif status == "sent":
            status_clause = " AND cm.status IN ('completed', 'sent')"
        elif status == "rejected":
            status_clause = " AND cm.status IN ('cancelled', 'stopped')"
        elif status == "pending":
            status_clause = " AND cm.status IN ('pending', 'paused')"
        elif status:
            status_clause = " AND cm.status = %s"
            params.append(status)
        total = int(
            (
                conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM campaign_members cm
                    JOIN campaigns c ON c.id = cm.campaign_id
                    WHERE """ + _LEGACY_GROUP_KEY_UPDATE_SQL + """ = %s
                    """
                    + status_clause,
                    tuple(params),
                ).fetchone()
                or {}
            ).get("total")
            or 0
        )
        rows = conn.execute(
            """
            SELECT cm.id, cm.unionid,
                   CASE
                       WHEN c.run_status = 'active' AND cm.status = 'pending' AND cm.next_due_at IS NOT NULL THEN 'queued'
                       ELSE cm.status
                   END AS status,
                   cm.updated_at,
                   c.owner_userid,
                   COALESCE(NULLIF(read_model.display_name, ''), cm.unionid) AS display_name,
                   (SELECT COUNT(*) FROM campaign_steps cs WHERE cs.campaign_segment_id = cm.campaign_segment_id) AS planned_message_count
            FROM campaign_members cm
            JOIN campaigns c ON c.id = cm.campaign_id
            LEFT JOIN customer_read_model_current read_model ON read_model.unionid = cm.unionid
            WHERE """ + _LEGACY_GROUP_KEY_SQL + """ = %s
            """
            + status_clause
            + " ORDER BY cm.id ASC LIMIT %s OFFSET %s",
            tuple([*params, limit, offset]),
        ).fetchall()
        recipients = []
        for row in rows:
            approval_status, send_status = _legacy_recipient_status(row.get("status"))
            recipients.append(
                _recipient_view(
                    {
                        "id": -int(row["id"]),
                        "unionid": row.get("unionid"),
                        "display_name": row.get("display_name"),
                        "owner_userid": row.get("owner_userid"),
                        "updated_at": row.get("updated_at"),
                        "planned_message_count": row.get("planned_message_count"),
                        "approval_status": approval_status,
                        "send_status": send_status,
                        "source_type": "legacy_campaign",
                        "supports_recipient_approval": False,
                    }
                )
            )
        return recipients, total

    def _update_legacy_recipient_message(
        self,
        plan_id: str,
        recipient_id: int,
        message_id: int,
        *,
        content_package: dict[str, Any],
        day_offset: Any = None,
        send_time: Any = None,
        operator: str,
    ) -> dict[str, Any]:
        legacy_member_id = abs(int(recipient_id))
        content_payload = _content_payload_for_package(content_package)
        with self._connect() as conn:
            member = conn.execute(
                """
                SELECT cm.*, c.owner_userid, c.review_status AS campaign_review_status,
                       c.run_status AS campaign_run_status, c.status AS campaign_status
                FROM campaign_members cm
                JOIN campaigns c ON c.id = cm.campaign_id
                WHERE """
                + _LEGACY_GROUP_KEY_SQL
                + """
                  = %s
                  AND cm.id = %s
                FOR UPDATE OF cm, c
                """,
                (plan_id, legacy_member_id),
            ).fetchone()
            if not member:
                raise LookupError("recipient not found")
            if _text(member.get("campaign_review_status")) == "rejected":
                raise ValueError("plan is rejected")
            approval_status, send_status = _legacy_recipient_status(_text(member.get("status")))
            if approval_status != "pending" or send_status != "pending":
                raise ValueError("recipient is not editable")
            step = conn.execute(
                """
                SELECT *, 'legacy_campaign' AS source_type
                FROM campaign_steps
                WHERE campaign_segment_id = %s AND id = %s
                FOR UPDATE
                """,
                (int(member.get("campaign_segment_id") or 0), int(message_id)),
            ).fetchone()
            if not step:
                raise LookupError("message not found")
            if int(member.get("current_step_index") or -1) >= int(step.get("step_index") or 0):
                raise ValueError("message is not editable")
            try:
                normalized_day_offset = max(0, int(day_offset if day_offset is not None else step.get("day_offset") or 0))
            except (TypeError, ValueError):
                normalized_day_offset = int(step.get("day_offset") or 0)
            normalized_send_time = (_text(send_time) or _text(step.get("send_time")))[:16]
            row = conn.execute(
                """
                UPDATE campaign_steps
                SET content_text = %s,
                    content_payload_json = %s::jsonb,
                    day_offset = %s,
                    send_time = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *, 'legacy_campaign' AS source_type
                """,
                (
                    _text(content_package.get("content_text")),
                    _json_dump(content_payload),
                    normalized_day_offset,
                    normalized_send_time,
                    int(message_id),
                ),
            ).fetchone()
            self._audit(
                conn,
                operator=operator,
                action_type="legacy_campaign_step_update_from_cloud_plan",
                target_type="campaign_step",
                target_id=f"{plan_id}:{legacy_member_id}:{int(message_id)}",
                before=dict(step),
                after=dict(row or {}),
            )
            conn.commit()
        recipient = self.get_recipient(plan_id, recipient_id)
        return {
            "status": "updated",
            "recipient": recipient or {},
            "message": _message_view(dict(row or {})),
        }
