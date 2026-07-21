# ruff: noqa: F401
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import date, datetime, timezone
from typing import Any, Protocol

from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import resolve_external_userid_with_dbapi, resolve_identity_with_dbapi, resolved_unionid
from aicrm_next.shared.repository_provider import RepositoryProviderError
from aicrm_next.shared.runtime import production_data_ready, raw_database_url

from .time_helpers import DEFAULT_SEND_TIME as _DEFAULT_CAMPAIGN_SEND_TIME
from .time_helpers import DEFAULT_TIMEZONE as _DEFAULT_CAMPAIGN_TIMEZONE
from .time_helpers import campaign_step_due_iso


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any, *, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _json_dump(value: Any) -> str:
    def _default(item: Any) -> str:
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"Object of type {item.__class__.__name__} is not JSON serializable")

    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=_default)


def _unionid_targets_from_external_members(conn: Any, members: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    unionids: list[str] = []
    normalized_members: list[dict[str, Any]] = []
    for member in members:
        unionid = resolved_unionid(
            resolve_identity_with_dbapi(
                conn,
                ResolvePersonIdentityRequest(
                    unionid=_text(member.get("unionid")) or None,
                    external_userid=_text(member.get("external_contact_id")) or None,
                ),
            )
        )
        if not unionid:
            continue
        if unionid not in unionids:
            unionids.append(unionid)
        sanitized = {key: value for key, value in dict(member).items() if key not in {"external_contact_id", "external_userid"}}
        sanitized["unionid"] = unionid
        normalized_members.append(sanitized)
    return unionids, normalized_members


def _limit(value: int, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _offset(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def connect_cloud_campaign_read_db(database_url: str) -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(_psycopg_url(database_url), row_factory=dict_row)
    except Exception as exc:
        raise RepositoryProviderError(f"cloud campaign read repository unavailable: {exc}") from exc


class CloudPlanRepository(Protocol):
    def list_plans(self, *, status: str = "", keyword: str = "", limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]: ...
    def get_plan(self, plan_id: str) -> dict[str, Any] | None: ...
    def plan_stats(self, plan_id: str) -> dict[str, int]: ...
    def list_recipients(self, plan_id: str, *, status: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]: ...
    def get_recipient(self, plan_id: str, recipient_id: int) -> dict[str, Any] | None: ...
    def list_recipient_messages(self, recipient_id: int) -> list[dict[str, Any]]: ...
    def approve_plan(self, plan_id: str, *, operator: str) -> dict[str, Any] | None: ...
    def reject_plan(self, plan_id: str, *, operator: str, reason: str = "") -> dict[str, Any] | None: ...
    def create_or_reuse_plan_broadcast_job(
        self,
        plan_id: str,
        *,
        operator: str,
        source_event_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]: ...
    def create_or_reuse_recipient_broadcast_jobs(
        self,
        plan_id: str,
        *,
        operator: str,
        source_event_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]: ...
    def create_or_reuse_agent_send_plan(
        self,
        *,
        external_event_id: str,
        package_key: str,
        external_userid: str,
        owner_userid: str,
        content_package: dict[str, Any],
        operator: str,
        requires_review: bool = False,
    ) -> dict[str, Any]: ...
    def approve_recipient(self, plan_id: str, recipient_id: int, *, operator: str) -> dict[str, Any]: ...
    def reject_recipient(self, plan_id: str, recipient_id: int, *, operator: str, reason: str = "") -> dict[str, Any]: ...
    def update_recipient_message(
        self,
        plan_id: str,
        recipient_id: int,
        message_id: int,
        *,
        content_package: dict[str, Any],
        day_offset: Any = None,
        send_time: Any = None,
        operator: str,
    ) -> dict[str, Any]: ...


def _plan_view(row: dict[str, Any], stats: dict[str, int] | None = None) -> dict[str, Any]:
    selection = _json(row.get("selection_json"), default={})
    stats = stats or {}
    target_count = int(row.get("target_count") or row.get("candidate_count") or stats.get("target_count") or 0)
    return {
        "plan_id": _text(row.get("plan_id")),
        "display_name": _text(row.get("display_name")) or _text(row.get("intent")) or _text(row.get("plan_id")),
        "owner_userid": _text(row.get("owner_userid")) or _text(selection.get("owner_userid")),
        "target_count": target_count,
        "approved_count": int(stats.get("approved_count") or 0),
        "pending_count": int(stats.get("pending_count") or 0),
        "rejected_count": int(stats.get("rejected_count") or 0),
        "sent_count": int(stats.get("sent_count") or 0),
        "failed_count": int(stats.get("failed_count") or 0),
        "review_status": _text(row.get("review_status")) or ("rejected" if _text(row.get("status")) == "rejected" else "pending_review"),
        "run_status": _text(row.get("run_status")) or _text(row.get("status")) or "draft",
        "updated_at": row.get("updated_at") or "",
        "source_type": _text(row.get("source_type")) or "cloud_plan",
    }


def _recipient_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "recipient_id": int(row.get("id") or row.get("recipient_id") or 0),
        "external_userid": _text(row.get("external_userid")),
        "display_name": _text(row.get("display_name")) or _text(row.get("external_userid")),
        "owner_userid": _text(row.get("owner_userid")),
        "updated_at": row.get("updated_at") or "",
        "planned_message_count": int(row.get("planned_message_count") or 0),
        "approval_status": _text(row.get("approval_status")) or "pending",
        "send_status": _text(row.get("send_status")) or "pending",
        "approved_by": _text(row.get("approved_by")),
        "approved_at": row.get("approved_at"),
        "rejected_by": _text(row.get("rejected_by")),
        "rejected_at": row.get("rejected_at"),
        "reject_reason": _text(row.get("reject_reason")),
        "broadcast_job_id": row.get("broadcast_job_id"),
        "last_error": _text(row.get("last_error")),
        "source_type": _text(row.get("source_type")) or "cloud_plan",
        "supports_recipient_approval": bool(row.get("supports_recipient_approval", True)),
    }


def _message_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": int(row.get("id") or row.get("message_id") or 0),
        "sequence_index": int(row.get("sequence_index") or 0),
        "day_offset": int(row.get("day_offset") or 0),
        "send_time": _text(row.get("send_time")),
        "content_text": _text(row.get("content_text")),
        "content_payload": _json(row.get("content_payload_json"), default={}),
        "attachments": _json(row.get("attachments_json"), default=[]),
        "status": _text(row.get("status")) or "pending",
        "sent_at": row.get("sent_at"),
        "last_error": _text(row.get("last_error")),
        "source_type": _text(row.get("source_type")) or "cloud_plan",
    }


def _content_payload_for_package(content_package: dict[str, Any]) -> dict[str, Any]:
    package = {
        "content_text": _text(content_package.get("content_text")),
        "image_library_ids": list(content_package.get("image_library_ids") or []),
        "miniprogram_library_ids": list(content_package.get("miniprogram_library_ids") or []),
        "attachment_library_ids": list(content_package.get("attachment_library_ids") or []),
        "group_invite_library_ids": list(content_package.get("group_invite_library_ids") or []),
    }
    return {
        "content_package": package,
        "image_library_ids": package["image_library_ids"],
        "image_media_ids": [],
        "miniprogram_library_ids": package["miniprogram_library_ids"],
        "attachment_library_ids": package["attachment_library_ids"],
        "group_invite_library_ids": package["group_invite_library_ids"],
    }


def _legacy_recipient_status(member_status: str) -> tuple[str, str]:
    status = _text(member_status)
    if status in {"completed", "sent"}:
        return "approved", "sent"
    if status in {"failed"}:
        return "approved", "failed"
    if status in {"cancelled", "stopped"}:
        return "rejected", "cancelled"
    if status in {"running", "queued"}:
        return "approved", "queued"
    return "pending", "pending"


_LEGACY_GROUP_KEY_SQL = "COALESCE(NULLIF(c.metadata_json->>'group_code', ''), c.campaign_code)"
_LEGACY_GROUP_KEY_UPDATE_SQL = _LEGACY_GROUP_KEY_SQL.replace("c.", "")
_LEGACY_GROUP_LABEL_SQL = "COALESCE(MAX(NULLIF(c.metadata_json->>'group_label', '')), MAX(NULLIF(c.display_name, '')), " + _LEGACY_GROUP_KEY_SQL + ")"
_CAMPAIGN_QUEUE_SOURCE_TYPE = "campaign"
_CAMPAIGN_QUEUE_SOURCE_TABLE = "campaign_members"
_CAMPAIGN_QUEUE_CONTENT_TYPE = "private_message"
_CAMPAIGN_QUEUE_CHANNEL = "wecom_private"
_CAMPAIGN_QUEUE_TARGET_KIND = "unionid"
_CAMPAIGN_OPEN_JOB_STATUSES = ["waiting_approval", "queued", "claimed"]
_CLOUD_HAS_MATCHING_LEGACY_GROUP_SQL = f"""
EXISTS (
    SELECT 1
    FROM campaigns c
    WHERE {_LEGACY_GROUP_KEY_SQL} = cloud_broadcast_plans.plan_id
)
"""
_CLOUD_PLAN_HAS_TARGETS_SQL = "COALESCE(candidate_count, 0) > 0"
_LEGACY_HAS_TARGETED_CLOUD_PLAN_SQL = f"""
EXISTS (
    SELECT 1
    FROM cloud_broadcast_plans p
    WHERE p.plan_id = {_LEGACY_GROUP_KEY_SQL}
      AND COALESCE(p.candidate_count, 0) > 0
)
"""


def _campaign_job_source_id(*, campaign_id: int, campaign_segment_id: int, step_index: int) -> str:
    return f"{int(campaign_id)}:{int(campaign_segment_id)}:{int(step_index)}"


def _legacy_campaign_job_source_id(*, campaign_id: int, step_index: int) -> str:
    return f"{int(campaign_id)}:{int(step_index)}"


def _campaign_private_broadcast_payload(*, campaign: dict[str, Any], step: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "channel": _CAMPAIGN_QUEUE_CHANNEL,
        "target_kind": _CAMPAIGN_QUEUE_TARGET_KIND,
        "campaign": campaign,
        "step": step,
        "members": members,
    }


def _broadcast_job_columns(conn: Any) -> set[str]:
    try:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = ANY(current_schemas(false))
              AND table_name = 'broadcast_jobs'
            """
        ).fetchall()
    except Exception:
        return set()
    return {_text(row.get("column_name")) for row in rows if _text(row.get("column_name"))}


def _campaign_private_broadcast_job_extra_fields(available_columns: set[str]) -> tuple[list[str], list[str], list[Any]]:
    fields: list[tuple[str, Any]] = []
    if "business_domain" in available_columns:
        fields.append(("business_domain", "automation_ops"))
    if "channel" in available_columns:
        fields.append(("channel", _CAMPAIGN_QUEUE_CHANNEL))
    if "target_kind" in available_columns:
        fields.append(("target_kind", _CAMPAIGN_QUEUE_TARGET_KIND))
    if not fields:
        return [], [], []
    return [field for field, _value in fields], ["%s"] * len(fields), [value for _field, value in fields]


def _plan_broadcast_idempotency_key(plan_id: str, *, source_event_id: str = "", idempotency_key: str = "") -> str:
    source = _text(idempotency_key) or _text(source_event_id) or _text(plan_id)
    return f"ops_plan_approved_broadcast:{source}"[:240]


def _recipient_broadcast_execution_id(recipient_idempotency_key: str) -> str:
    normalized = _text(recipient_idempotency_key)
    if not normalized:
        raise ValueError("recipient broadcast idempotency key is required")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"exe_broadcast_{digest}"


def _plan_broadcast_content_payload(*, plan_id: str, owner_userid: str, target_count: int, source_event_id: str) -> dict[str, Any]:
    return {
        "plan_id": _text(plan_id),
        "message_mode": "ops_plan_approval_broadcast",
        "owner_userid": _text(owner_userid),
        "target_count": int(target_count or 0),
        "source_event_id": _text(source_event_id),
    }


def _plan_broadcast_summary(plan: dict[str, Any], *, target_count: int) -> str:
    label = _text(plan.get("display_name")) or _text(plan.get("intent")) or _text(plan.get("plan_id"))
    return f"{label} · {int(target_count or 0)} recipients"


def _agent_plan_id(external_event_id: str) -> str:
    normalized = _text(external_event_id)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", ":"} else "_" for ch in normalized)[:120]
    return f"agent_plan:{safe or digest}:{digest}"


from .repository_legacy import CloudLegacyPostgresRepositoryMixin

class PostgresCloudPlanRepository(CloudLegacyPostgresRepositoryMixin):
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = _psycopg_url(_text(database_url or raw_database_url() or os.getenv("DATABASE_URL")))
        if not self._database_url:
            raise RepositoryProviderError("cloud_orchestrator production repository unavailable: DATABASE_URL is required")

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self._database_url, row_factory=dict_row)
        except Exception as exc:
            raise RepositoryProviderError(f"cloud_orchestrator production repository unavailable: {exc}") from exc

    def _audit(self, conn, *, operator: str, action_type: str, target_type: str, target_id: str, before: dict[str, Any], after: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO admin_operation_logs (operator, action_type, target_type, target_id, before_json, after_json, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, CURRENT_TIMESTAMP)
            """,
            (_text(operator) or "crm_console", action_type, target_type, target_id, _json_dump(before), _json_dump(after)),
        )



    def _stats_for_plan_ids(self, conn, plan_ids: list[str]) -> dict[str, dict[str, int]]:
        if not plan_ids:
            return {}
        try:
            rows = conn.execute(
                """
                SELECT plan_id,
                       COUNT(*) AS target_count,
                       COALESCE(SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END), 0) AS approved_count,
                       COALESCE(SUM(CASE WHEN approval_status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
                       COALESCE(SUM(CASE WHEN approval_status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count,
                       COALESCE(SUM(CASE WHEN send_status = 'sent' THEN 1 ELSE 0 END), 0) AS sent_count,
                       COALESCE(SUM(CASE WHEN send_status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count
                FROM cloud_broadcast_plan_recipients
                WHERE plan_id = ANY(%s)
                GROUP BY plan_id
                """,
                (plan_ids,),
            ).fetchall()
        except Exception as exc:
            if "cloud_broadcast_plan_recipients" in str(exc) and "permission denied" in str(exc).lower():
                conn.rollback()
                return {}
            raise
        return {str(row["plan_id"]): {key: int(row.get(key) or 0) for key in row.keys() if key != "plan_id"} for row in rows}

    def _legacy_stats_for_plan_ids(self, conn, plan_ids: list[str]) -> dict[str, dict[str, int]]:
        if not plan_ids:
            return {}
        rows = conn.execute(
            f"""
            SELECT {_LEGACY_GROUP_KEY_SQL} AS plan_id,
                   COUNT(cm.id) AS target_count,
                   COALESCE(SUM(CASE WHEN c.review_status = 'approved' THEN 1 ELSE 0 END), 0) AS approved_count,
                   COALESCE(SUM(CASE WHEN c.review_status NOT IN ('approved', 'rejected') THEN 1 ELSE 0 END), 0) AS pending_count,
                   COALESCE(SUM(CASE WHEN c.review_status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count,
                   COALESCE(SUM(CASE WHEN cm.status IN ('completed', 'sent') THEN 1 ELSE 0 END), 0) AS sent_count,
                   COALESCE(SUM(CASE WHEN cm.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count
            FROM campaigns c
            LEFT JOIN campaign_members cm ON cm.campaign_id = c.id
            WHERE {_LEGACY_GROUP_KEY_SQL} = ANY(%s)
            GROUP BY {_LEGACY_GROUP_KEY_SQL}
            """,
            (plan_ids,),
        ).fetchall()
        return {str(row["plan_id"]): {key: int(row.get(key) or 0) for key in row.keys() if key != "plan_id"} for row in rows}

    def list_plans(self, *, status: str = "", keyword: str = "", limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("(COALESCE(review_status, '') = %s OR COALESCE(run_status, '') = %s OR status = %s)")
            params.extend([status, status, status])
        if keyword:
            like = f"%{keyword.lower()}%"
            clauses.append("(LOWER(plan_id) LIKE %s OR LOWER(COALESCE(display_name, intent, '')) LIKE %s OR LOWER(COALESCE(owner_userid, '')) LIKE %s)")
            params.extend([like, like, like])
        cloud_clauses = [
            *clauses,
            f"({_CLOUD_PLAN_HAS_TARGETS_SQL} OR NOT {_CLOUD_HAS_MATCHING_LEGACY_GROUP_SQL})",
        ]
        cloud_where = " WHERE " + " AND ".join(cloud_clauses)
        legacy_clauses: list[str] = []
        legacy_params: list[Any] = []
        if status:
            legacy_clauses.append("(COALESCE(c.review_status, '') = %s OR COALESCE(c.run_status, '') = %s)")
            legacy_params.extend([status, status])
        if keyword:
            like = f"%{keyword.lower()}%"
            legacy_clauses.append(
                f"(LOWER({_LEGACY_GROUP_KEY_SQL}) LIKE %s OR LOWER(COALESCE(c.metadata_json->>'group_label', c.display_name, c.intent, '')) LIKE %s OR LOWER(COALESCE(c.owner_userid, '')) LIKE %s)"
            )
            legacy_params.extend([like, like, like])
        legacy_where = " AND " + " AND ".join(legacy_clauses) if legacy_clauses else ""
        limit = _limit(limit, default=20, maximum=100)
        offset = _offset(offset)
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH merged AS (
                    SELECT id, plan_id, intent, display_name, owner_userid, candidate_count,
                           selection_json, review_status, run_status, status, updated_at,
                           'cloud_plan' AS source_type
                    FROM cloud_broadcast_plans
                    """
                + cloud_where
                + f"""
                    UNION ALL
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
                    WHERE NOT {_LEGACY_HAS_TARGETED_CLOUD_PLAN_SQL}
                    """
                + legacy_where
                + f"""
                    GROUP BY {_LEGACY_GROUP_KEY_SQL}
                    """
                + """
                )
                SELECT *
                FROM merged
                ORDER BY updated_at DESC, id DESC
                LIMIT %s OFFSET %s
                """
                ,
                tuple([*params, *legacy_params, limit, offset]),
            ).fetchall()
            total = int(
                (
                    conn.execute(
                        """
                        WITH merged AS (
                            SELECT plan_id FROM cloud_broadcast_plans
                            """
                        + cloud_where
                + f"""
                            UNION ALL
                            SELECT {_LEGACY_GROUP_KEY_SQL} AS plan_id FROM campaigns c
                            LEFT JOIN campaign_members cm ON cm.campaign_id = c.id
                            WHERE NOT {_LEGACY_HAS_TARGETED_CLOUD_PLAN_SQL}
                            """
                        + legacy_where
                        + f"""
                            GROUP BY {_LEGACY_GROUP_KEY_SQL}
                            """
                        + """
                        )
                        SELECT COUNT(*) AS total FROM merged
                        """,
                        tuple([*params, *legacy_params]),
                    ).fetchone()
                    or {}
                ).get("total")
                or 0
            )
            cloud_ids = [str(row["plan_id"]) for row in rows if _text(row.get("source_type")) == "cloud_plan"]
            legacy_ids = [str(row["plan_id"]) for row in rows if _text(row.get("source_type")) == "legacy_campaign"]
            stats = {**self._stats_for_plan_ids(conn, cloud_ids), **self._legacy_stats_for_plan_ids(conn, legacy_ids)}
        return [_plan_view(dict(row), stats.get(str(row["plan_id"]), {})) for row in rows], total


    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, plan_id, intent, display_name, owner_userid, candidate_count, selection_json,
                       review_status, run_status, status, updated_at
                FROM cloud_broadcast_plans
                WHERE plan_id = %s
                """,
                (_text(plan_id),),
            ).fetchone()
            if row:
                if int(row.get("candidate_count") or 0) <= 0:
                    row = self._legacy_group_plan_row(conn, plan_id) or row
            else:
                row = self._legacy_group_plan_row(conn, plan_id)
                if not row:
                    return None
            if _text(row.get("source_type")) == "legacy_campaign":
                stats = self._legacy_stats_for_plan_ids(conn, [_text(plan_id)]).get(_text(plan_id), {})
            else:
                stats = self._stats_for_plan_ids(conn, [_text(plan_id)]).get(_text(plan_id), {})
        return _plan_view(dict(row), stats)

    def plan_stats(self, plan_id: str) -> dict[str, int]:
        with self._connect() as conn:
            cloud = conn.execute(
                "SELECT candidate_count FROM cloud_broadcast_plans WHERE plan_id = %s",
                (_text(plan_id),),
            ).fetchone()
            if cloud and int(cloud.get("candidate_count") or 0) > 0:
                stats = self._stats_for_plan_ids(conn, [_text(plan_id)]).get(_text(plan_id), {})
                if stats:
                    return stats
            return self._legacy_stats_for_plan_ids(conn, [_text(plan_id)]).get(_text(plan_id), {})

    def list_recipients(self, plan_id: str, *, status: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        clauses = ["plan_id = %s"]
        params: list[Any] = [_text(plan_id)]
        if status:
            clauses.append("(approval_status = %s OR send_status = %s)")
            params.extend([status, status])
        where = " WHERE " + " AND ".join(clauses)
        limit = _limit(limit, default=50, maximum=200)
        offset = _offset(offset)
        with self._connect() as conn:
            cloud = conn.execute(
                "SELECT candidate_count FROM cloud_broadcast_plans WHERE plan_id = %s",
                (_text(plan_id),),
            ).fetchone()
            if not cloud or int(cloud.get("candidate_count") or 0) <= 0:
                return self._legacy_recipients(conn, _text(plan_id), status=status, limit=limit, offset=offset)
            total = int((conn.execute("SELECT COUNT(*) AS total FROM cloud_broadcast_plan_recipients" + where, tuple(params)).fetchone() or {}).get("total") or 0)
            if total:
                rows = conn.execute(
                    """
                    SELECT *, 'cloud_plan' AS source_type, TRUE AS supports_recipient_approval
                    FROM cloud_broadcast_plan_recipients
                    """
                    + where
                    + " ORDER BY id ASC LIMIT %s OFFSET %s",
                    tuple([*params, limit, offset]),
                ).fetchall()
                return [_recipient_view(dict(row)) for row in rows], total
            return self._legacy_recipients(conn, _text(plan_id), status=status, limit=limit, offset=offset)


    def get_recipient(self, plan_id: str, recipient_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            if int(recipient_id) < 0:
                legacy_rows, _total = self._legacy_recipients(conn, _text(plan_id), limit=1, offset=0)
                row = None
                legacy_id = abs(int(recipient_id))
                for item in legacy_rows:
                    if abs(int(item["recipient_id"])) == legacy_id:
                        return item
                row = conn.execute(
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
                    WHERE """ + _LEGACY_GROUP_KEY_SQL + """ = %s AND cm.id = %s
                    """,
                    (_text(plan_id), legacy_id),
                ).fetchone()
                if row:
                    approval_status, send_status = _legacy_recipient_status(row.get("status"))
                    return _recipient_view(
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
            row = conn.execute(
                """
                SELECT *, 'cloud_plan' AS source_type, TRUE AS supports_recipient_approval
                FROM cloud_broadcast_plan_recipients
                WHERE plan_id = %s AND id = %s
                """,
                (_text(plan_id), int(recipient_id)),
            ).fetchone()
        return _recipient_view(dict(row)) if row else None

    def list_recipient_messages(self, recipient_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if int(recipient_id) < 0:
                rows = conn.execute(
                    """
                    SELECT cs.id, cs.step_index AS sequence_index, cs.day_offset, cs.send_time,
                           cs.content_text, cs.content_payload_json, '[]'::jsonb AS attachments_json,
                           CASE
                               WHEN cm.status IN ('completed', 'sent') OR cm.current_step_index >= cs.step_index THEN 'sent'
                               WHEN cm.status = 'failed' THEN 'failed'
                               ELSE 'pending'
                           END AS status,
                           NULL AS sent_at,
                           cm.last_error_text AS last_error,
                           'legacy_campaign' AS source_type
                    FROM campaign_members cm
                    JOIN campaign_steps cs ON cs.campaign_segment_id = cm.campaign_segment_id
                    WHERE cm.id = %s
                    ORDER BY cs.step_index ASC, cs.id ASC
                    """,
                    (abs(int(recipient_id)),),
                ).fetchall()
                return [_message_view(dict(row)) for row in rows]
            rows = conn.execute(
                """
                SELECT *, 'cloud_plan' AS source_type
                FROM cloud_broadcast_plan_recipient_messages
                WHERE recipient_id = %s
                ORDER BY sequence_index ASC, id ASC
                """,
                (int(recipient_id),),
            ).fetchall()
        return [_message_view(dict(row)) for row in rows]

    def approve_plan(self, plan_id: str, *, operator: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            before = conn.execute("SELECT * FROM cloud_broadcast_plans WHERE plan_id = %s FOR UPDATE", (_text(plan_id),)).fetchone()
            if not before:
                row = self._approve_and_start_legacy_group(conn, _text(plan_id), operator=operator)
                if not row:
                    return None
                conn.commit()
                return _plan_view(dict(row), self.plan_stats(plan_id))
            if _text(before.get("review_status") or before.get("status")) == "rejected":
                raise ValueError("plan is rejected")
            row = conn.execute(
                """
                UPDATE cloud_broadcast_plans
                SET review_status = 'approved', run_status = COALESCE(NULLIF(run_status, ''), status, 'draft'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE plan_id = %s
                RETURNING *
                """,
                (_text(plan_id),),
            ).fetchone()
            self._audit(conn, operator=operator, action_type="cloud_plan_approve", target_type="cloud_broadcast_plan", target_id=_text(plan_id), before=dict(before), after=dict(row or {}))
            conn.commit()
        return self.get_plan(plan_id)

    def reject_plan(self, plan_id: str, *, operator: str, reason: str = "") -> dict[str, Any] | None:
        with self._connect() as conn:
            before = conn.execute("SELECT * FROM cloud_broadcast_plans WHERE plan_id = %s FOR UPDATE", (_text(plan_id),)).fetchone()
            if not before:
                row = self._reject_legacy_group(conn, _text(plan_id), operator=operator, reason=reason)
                if not row:
                    return None
                conn.commit()
                return _plan_view(dict(row), self.plan_stats(plan_id))
            row = conn.execute(
                """
                UPDATE cloud_broadcast_plans
                SET review_status = 'rejected', status = CASE WHEN status = 'committed' THEN status ELSE 'rejected' END,
                    error_message = %s, updated_at = CURRENT_TIMESTAMP
                WHERE plan_id = %s
                RETURNING *
                """,
                (_text(reason)[:200], _text(plan_id)),
            ).fetchone()
            self._audit(conn, operator=operator, action_type="cloud_plan_reject", target_type="cloud_broadcast_plan", target_id=_text(plan_id), before=dict(before), after=dict(row or {}))
            conn.commit()
        return self.get_plan(plan_id)

    def create_or_reuse_recipient_broadcast_jobs(
        self,
        plan_id: str,
        *,
        operator: str,
        source_event_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        normalized_plan_id = _text(plan_id)
        if not normalized_plan_id:
            return {"status": "skipped", "reason": "missing_plan_id"}
        job_ids: list[int] = []
        created_count = 0
        reused_count = 0
        target_count = 0
        planner_idempotency_key = _plan_broadcast_idempotency_key(
            normalized_plan_id,
            source_event_id=source_event_id,
            idempotency_key=idempotency_key,
        )
        with self._connect() as conn:
            plan = conn.execute("SELECT * FROM cloud_broadcast_plans WHERE plan_id = %s FOR UPDATE", (normalized_plan_id,)).fetchone()
            if not plan:
                return {"status": "skipped", "reason": "missing_plan_id"}
            plan_dict = dict(plan)
            source_type = _text(plan_dict.get("source_type")) or "cloud_plan"
            if source_type and source_type != "cloud_plan":
                return {"status": "skipped", "reason": "unsupported_plan_type", "plan_type": source_type}
            review_status = _text(plan_dict.get("review_status") or plan_dict.get("status"))
            if review_status not in {"approved", "reviewing"}:
                return {"status": "skipped", "reason": "unsupported_plan_type", "review_status": review_status}
            recipients = conn.execute(
                """
                SELECT id, unionid, display_name, owner_userid
                FROM cloud_broadcast_plan_recipients
                WHERE plan_id = %s
                  AND COALESCE(approval_status, 'pending') <> 'rejected'
                  AND COALESCE(send_status, 'pending') NOT IN ('cancelled', 'sent')
                  AND COALESCE(unionid, '') <> ''
                  AND EXISTS (
                    SELECT 1
                    FROM cloud_broadcast_plan_recipient_messages m
                    WHERE m.plan_id = cloud_broadcast_plan_recipients.plan_id
                      AND m.recipient_id = cloud_broadcast_plan_recipients.id
                      AND COALESCE(m.status, 'pending') <> 'cancelled'
                  )
                ORDER BY id ASC
                """,
                (normalized_plan_id,),
            ).fetchall()
            if not recipients:
                return {"status": "skipped", "reason": "missing_audience"}
            target_count = len(recipients)
            for recipient_row in recipients:
                recipient = dict(recipient_row)
                recipient_id = int(recipient["id"])
                recipient_idempotency_key = f"cloud_plan_recipient:{normalized_plan_id}:{recipient_id}"
                execution_id = _recipient_broadcast_execution_id(recipient_idempotency_key)
                existing = conn.execute(
                    "SELECT id FROM broadcast_jobs WHERE idempotency_key = %s ORDER BY id DESC LIMIT 1",
                    (recipient_idempotency_key,),
                ).fetchone()
                job_id = int(existing["id"]) if existing else 0
                if job_id:
                    reused_count += 1
                else:
                    metadata = {
                        "planner_consumer": "broadcast_task_planner_consumer",
                        "source_event_id": _text(source_event_id),
                        "plan_idempotency_key": planner_idempotency_key,
                        "duplicate_policy": "reuse_recipient_idempotency_key",
                    }
                    inserted = conn.execute(
                        """
                        INSERT INTO broadcast_jobs (
                            source_type, source_id, source_table, scheduled_for, priority, batch_key,
                            business_domain, idempotency_key, channel, target_kind, retry_policy_json, metadata_json,
                            status, requires_approval, target_unionids_json, target_count, target_summary,
                            content_type, content_payload, content_summary, trace_id, created_by, execution_id
                        ) VALUES (
                            'cloud_plan', %s, 'cloud_broadcast_plan_recipients', CURRENT_TIMESTAMP, 100, %s,
                            'ai_assistant', %s, 'wecom_private', 'unionid', '{}'::jsonb, %s::jsonb,
                            'queued', FALSE, %s::jsonb, 1, %s,
                            'cloud_plan', %s::jsonb, %s, %s, %s, %s
                        )
                        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
                        DO NOTHING
                        RETURNING id
                        """,
                        (
                            f"{normalized_plan_id}:{recipient_id}",
                            f"cloud_plan_recipient:{normalized_plan_id}",
                            recipient_idempotency_key,
                            _json_dump(metadata),
                            _json_dump([_text(recipient.get("unionid"))]),
                            _text(recipient.get("display_name")) or _text(recipient.get("unionid")),
                            _json_dump(
                                {
                                    "plan_id": normalized_plan_id,
                                    "recipient_id": recipient_id,
                                    "unionid": _text(recipient.get("unionid")),
                                    "message_mode": "recipient_messages",
                                }
                            ),
                            f"{_text(plan_dict.get('display_name')) or _text(plan_dict.get('intent')) or normalized_plan_id} · {_text(recipient.get('display_name')) or _text(recipient.get('unionid'))}",
                            _text(plan_dict.get("trace_id")) or normalized_plan_id,
                            _text(operator) or "internal_event_worker",
                            execution_id,
                        ),
                    ).fetchone()
                    if inserted:
                        job_id = int(inserted["id"])
                        created_count += 1
                    else:
                        existing = conn.execute(
                            "SELECT id FROM broadcast_jobs WHERE idempotency_key = %s ORDER BY id DESC LIMIT 1",
                            (recipient_idempotency_key,),
                        ).fetchone()
                        job_id = int(existing["id"]) if existing else 0
                        if job_id:
                            reused_count += 1
                if job_id:
                    conn.execute(
                        """
                        UPDATE broadcast_jobs
                        SET execution_id = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND COALESCE(execution_id, '') = ''
                        """,
                        (execution_id, job_id),
                    )
                    job_ids.append(job_id)
                    conn.execute(
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
                        (_text(operator) or "internal_event_worker", job_id, recipient_id),
                    )
                    conn.execute(
                        """
                        UPDATE cloud_broadcast_plan_recipient_messages
                        SET status = CASE WHEN status = 'pending' THEN 'queued' ELSE status END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE plan_id = %s
                          AND recipient_id = %s
                        """,
                        (normalized_plan_id, recipient_id),
                    )
            if not job_ids:
                return {"status": "skipped", "reason": "missing_send_content"}
            self._audit(
                conn,
                operator=operator,
                action_type="ops_plan_recipient_broadcast_jobs_plan",
                target_type="cloud_broadcast_plan",
                target_id=normalized_plan_id,
                before={},
                after={
                    "plan_id": normalized_plan_id,
                    "broadcast_job_id": job_ids[0],
                    "broadcast_job_count": len(set(job_ids)),
                    "created_count": created_count,
                    "reused_count": reused_count,
                    "target_count": target_count,
                    "idempotency_key": planner_idempotency_key,
                },
            )
            conn.commit()
        job_status = "created" if created_count else "reused"
        first_job_id = job_ids[0] if job_ids else 0
        return {
            "status": job_status,
            "broadcast_job_id": first_job_id,
            "broadcast_job_count": len(set(job_ids)),
            "created_count": created_count,
            "reused_count": reused_count,
            "idempotency_key": planner_idempotency_key,
            "target_count": target_count,
            "source_id": normalized_plan_id,
            "trace_id": normalized_plan_id,
            "downstream_status": "broadcast_job_queued",
            "push_center_job_id": f"broadcast_job:{first_job_id}" if first_job_id else "",
        }

    def create_or_reuse_plan_broadcast_job(
        self,
        plan_id: str,
        *,
        operator: str,
        source_event_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        return self.create_or_reuse_recipient_broadcast_jobs(
            plan_id,
            operator=operator,
            source_event_id=source_event_id,
            idempotency_key=idempotency_key,
        )

    def create_or_reuse_agent_send_plan(
        self,
        *,
        external_event_id: str,
        package_key: str,
        external_userid: str,
        owner_userid: str,
        content_package: dict[str, Any],
        operator: str,
        requires_review: bool = False,
    ) -> dict[str, Any]:
        normalized_event_id = _text(external_event_id)
        normalized_external_userid = _text(external_userid)
        normalized_owner = _text(owner_userid)
        if not normalized_event_id:
            return {"status": "skipped", "reason": "missing_external_event_id"}
        if not normalized_external_userid:
            return {"status": "skipped", "reason": "missing_external_userid"}
        if not normalized_owner:
            return {"status": "skipped", "reason": "missing_owner_userid"}
        plan_id = _agent_plan_id(normalized_event_id)
        review_status = "pending_review" if requires_review else "approved"
        approval_status = "pending" if requires_review else "approved"
        content_payload = _content_payload_for_package(content_package)
        with self._connect() as conn:
            normalized_unionid = resolve_external_userid_with_dbapi(conn, normalized_external_userid)
            if not normalized_unionid:
                return {"status": "skipped", "reason": "identity_pending_unionid"}
            existing_plan = conn.execute(
                "SELECT plan_id FROM cloud_broadcast_plans WHERE plan_id = %s",
                (plan_id,),
            ).fetchone()
            if not existing_plan:
                conn.execute(
                    """
                    INSERT INTO cloud_broadcast_plans (
                        plan_id, trace_id, session_id, operator, intent, display_name, owner_userid,
                        selection_json, content_strategy, max_recipients, candidate_count,
                        explanation_json, status, review_status, run_status, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, 'agent_generated_single', 1, 1,
                        %s::jsonb, 'draft', %s, 'draft', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (plan_id) DO NOTHING
                    """,
                    (
                        plan_id,
                        normalized_event_id,
                        normalized_event_id,
                        _text(operator) or "automation_agent",
                        f"Agent generated send plan {normalized_external_userid}",
                        f"Agent 生成待发送计划 · {normalized_external_userid}",
                        normalized_owner,
                        _json_dump(
                            {
                                "source": "automation_agent",
                                "package_key": _text(package_key),
                                "external_event_id": normalized_event_id,
                                "unionid": normalized_unionid,
                            }
                        ),
                        _json_dump({"source": "automation_agent", "external_event_id": normalized_event_id}),
                        review_status,
                    ),
                )
            recipient = conn.execute(
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
                (plan_id, normalized_unionid, normalized_owner, normalized_unionid, approval_status),
            ).fetchone()
            recipient_id = int((recipient or {}).get("id") or 0)
            existing_message = conn.execute(
                """
                SELECT id
                FROM cloud_broadcast_plan_recipient_messages
                WHERE plan_id = %s AND recipient_id = %s AND sequence_index = 1
                ORDER BY id ASC
                LIMIT 1
                """,
                (plan_id, recipient_id),
            ).fetchone()
            if existing_message:
                message_id = int(existing_message["id"])
                conn.execute(
                    """
                    UPDATE cloud_broadcast_plan_recipient_messages
                    SET content_text = %s,
                        content_payload_json = %s::jsonb,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (_text(content_package.get("content_text")), _json_dump(content_payload), message_id),
                )
            else:
                inserted_message = conn.execute(
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
                        plan_id,
                        recipient_id,
                        normalized_unionid,
                        _text(content_package.get("content_text")),
                        _json_dump(content_payload),
                    ),
                ).fetchone()
                message_id = int((inserted_message or {}).get("id") or 0)
            self._audit(
                conn,
                operator=_text(operator) or "automation_agent",
                action_type="automation_agent_enqueue_send_plan",
                target_type="cloud_broadcast_plan",
                target_id=plan_id,
                before={},
                after={
                    "plan_id": plan_id,
                    "recipient_id": recipient_id,
                    "message_id": message_id,
                    "external_event_id": normalized_event_id,
                    "duplicate_handling": "reused" if existing_plan else "created",
                },
            )
            conn.commit()
        return {
            "status": "reused" if existing_plan else "created",
            "plan_id": plan_id,
            "recipient_id": recipient_id,
            "message_id": message_id,
            "downstream_status": "send_plan_pending",
            "push_center_job_id": f"cloud_plan:{plan_id}",
        }

    def approve_recipient(self, plan_id: str, recipient_id: int, *, operator: str) -> dict[str, Any]:
        normalized_plan_id = _text(plan_id)
        with self._connect() as conn:
            plan = conn.execute("SELECT * FROM cloud_broadcast_plans WHERE plan_id = %s FOR UPDATE", (normalized_plan_id,)).fetchone()
            if not plan:
                raise LookupError("plan not found")
            if _text(plan.get("review_status") or plan.get("status")) == "rejected":
                raise ValueError("plan is rejected")
            if _text(plan.get("review_status")) not in {"approved", "reviewing"}:
                raise ValueError("plan is not approved for recipient review")
            recipient = conn.execute(
                "SELECT * FROM cloud_broadcast_plan_recipients WHERE plan_id = %s AND id = %s FOR UPDATE",
                (normalized_plan_id, int(recipient_id)),
            ).fetchone()
            if not recipient:
                raise LookupError("recipient not found")
            before = dict(recipient)
            if _text(recipient.get("approval_status")) == "rejected":
                raise ValueError("recipient is rejected")
            if _text(recipient.get("send_status")) == "sent":
                return {"status": "already_sent", "recipient": _recipient_view(dict(recipient)), "job_id": recipient.get("broadcast_job_id")}
            idempotency_key = f"cloud_plan_recipient:{normalized_plan_id}:{int(recipient_id)}"
            existing = conn.execute("SELECT id FROM broadcast_jobs WHERE idempotency_key = %s ORDER BY id DESC LIMIT 1", (idempotency_key,)).fetchone()
            job_id = int(existing["id"]) if existing else 0
            if not job_id:
                inserted = conn.execute(
                    """
                    INSERT INTO broadcast_jobs (
                        source_type, source_id, source_table, scheduled_for, priority, batch_key,
                        business_domain, idempotency_key, channel, target_kind, retry_policy_json, metadata_json,
                        status, requires_approval, target_unionids_json, target_count, target_summary,
                        content_type, content_payload, content_summary, trace_id, created_by
                    ) VALUES (
                        'cloud_plan', %s, 'cloud_broadcast_plan_recipients', CURRENT_TIMESTAMP, 100, %s,
                        'ai_assistant', %s, 'wecom_private', 'unionid', '{}'::jsonb, '{}'::jsonb,
                        'queued', FALSE, %s::jsonb, 1, %s,
                        'cloud_plan', %s::jsonb, %s, %s, %s
                    )
                    ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
                    DO NOTHING
                    RETURNING id
                    """,
                    (
                        f"{normalized_plan_id}:{int(recipient_id)}",
                        f"cloud_plan_recipient:{normalized_plan_id}",
                        idempotency_key,
                        _json_dump([_text(recipient.get("unionid"))]),
                        _text(recipient.get("display_name")) or _text(recipient.get("unionid")),
                        _json_dump(
                            {
                                "plan_id": normalized_plan_id,
                                "recipient_id": int(recipient_id),
                                "unionid": _text(recipient.get("unionid")),
                                "message_mode": "recipient_messages",
                            }
                        ),
                        f"{_text(plan.get('display_name')) or _text(plan.get('intent')) or normalized_plan_id} · {_text(recipient.get('display_name')) or _text(recipient.get('unionid'))}",
                        _text(plan.get("trace_id")),
                        _text(operator) or "crm_console",
                    ),
                ).fetchone()
                if inserted:
                    job_id = int(inserted["id"])
                else:
                    existing = conn.execute("SELECT id FROM broadcast_jobs WHERE idempotency_key = %s ORDER BY id DESC LIMIT 1", (idempotency_key,)).fetchone()
                    job_id = int(existing["id"]) if existing else 0
            row = conn.execute(
                """
                UPDATE cloud_broadcast_plan_recipients
                SET approval_status = 'approved', send_status = CASE WHEN send_status = 'pending' THEN 'queued' ELSE send_status END,
                    approved_by = %s, approved_at = CURRENT_TIMESTAMP, broadcast_job_id = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """,
                (_text(operator) or "crm_console", job_id or None, int(recipient_id)),
            ).fetchone()
            conn.execute(
                """
                UPDATE cloud_broadcast_plan_recipient_messages
                SET status = CASE WHEN status = 'pending' THEN 'queued' ELSE status END, updated_at = CURRENT_TIMESTAMP
                WHERE recipient_id = %s
                """,
                (int(recipient_id),),
            )
            self._audit(conn, operator=operator, action_type="cloud_plan_recipient_approve", target_type="cloud_broadcast_plan_recipient", target_id=f"{normalized_plan_id}:{int(recipient_id)}", before=before, after=dict(row or {}))
            conn.commit()
        return {"status": "already_approved" if existing else "approved", "recipient": _recipient_view(dict(row or {})), "job_id": job_id}

    def reject_recipient(self, plan_id: str, recipient_id: int, *, operator: str, reason: str = "") -> dict[str, Any]:
        with self._connect() as conn:
            plan = conn.execute("SELECT * FROM cloud_broadcast_plans WHERE plan_id = %s", (_text(plan_id),)).fetchone()
            if not plan:
                raise LookupError("plan not found")
            if _text(plan.get("review_status") or plan.get("status")) == "rejected":
                raise ValueError("plan is rejected")
            recipient = conn.execute(
                "SELECT * FROM cloud_broadcast_plan_recipients WHERE plan_id = %s AND id = %s FOR UPDATE",
                (_text(plan_id), int(recipient_id)),
            ).fetchone()
            if not recipient:
                raise LookupError("recipient not found")
            before = dict(recipient)
            if _text(recipient.get("send_status")) == "sent":
                raise ValueError("sent recipient cannot be rejected")
            row = conn.execute(
                """
                UPDATE cloud_broadcast_plan_recipients
                SET approval_status = 'rejected', send_status = CASE WHEN send_status IN ('pending', 'queued') THEN 'cancelled' ELSE send_status END,
                    rejected_by = %s, rejected_at = CURRENT_TIMESTAMP, reject_reason = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """,
                (_text(operator) or "crm_console", _text(reason)[:500], int(recipient_id)),
            ).fetchone()
            self._audit(conn, operator=operator, action_type="cloud_plan_recipient_reject", target_type="cloud_broadcast_plan_recipient", target_id=f"{_text(plan_id)}:{int(recipient_id)}", before=before, after=dict(row or {}))
            conn.commit()
        return {"status": "rejected", "recipient": _recipient_view(dict(row or {}))}

    def update_recipient_message(
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
        normalized_plan_id = _text(plan_id)
        normalized_recipient_id = int(recipient_id)
        normalized_message_id = int(message_id)
        if normalized_recipient_id < 0:
            return self._update_legacy_recipient_message(
                normalized_plan_id,
                normalized_recipient_id,
                normalized_message_id,
                content_package=content_package,
                day_offset=day_offset,
                send_time=send_time,
                operator=operator,
            )
        content_payload = _content_payload_for_package(content_package)
        with self._connect() as conn:
            plan = conn.execute("SELECT * FROM cloud_broadcast_plans WHERE plan_id = %s FOR UPDATE", (normalized_plan_id,)).fetchone()
            if not plan:
                raise LookupError("plan not found")
            if _text(plan.get("review_status") or plan.get("status")) == "rejected":
                raise ValueError("plan is rejected")
            recipient = conn.execute(
                "SELECT * FROM cloud_broadcast_plan_recipients WHERE plan_id = %s AND id = %s FOR UPDATE",
                (normalized_plan_id, normalized_recipient_id),
            ).fetchone()
            if not recipient:
                raise LookupError("recipient not found")
            if _text(recipient.get("approval_status")) != "pending" or _text(recipient.get("send_status")) != "pending":
                raise ValueError("recipient is not editable")
            message = conn.execute(
                """
                SELECT *
                FROM cloud_broadcast_plan_recipient_messages
                WHERE recipient_id = %s AND id = %s
                FOR UPDATE
                """,
                (normalized_recipient_id, normalized_message_id),
            ).fetchone()
            if not message:
                raise LookupError("message not found")
            if _text(message.get("status")) != "pending":
                raise ValueError("message is not editable")
            try:
                normalized_day_offset = max(0, int(day_offset if day_offset is not None else message.get("day_offset") or 0))
            except (TypeError, ValueError):
                normalized_day_offset = int(message.get("day_offset") or 0)
            normalized_send_time = (_text(send_time) or _text(message.get("send_time")))[:16]
            row = conn.execute(
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
                    _text(content_package.get("content_text")),
                    _json_dump(content_payload),
                    normalized_day_offset,
                    normalized_send_time,
                    normalized_message_id,
                ),
            ).fetchone()
            recipient_row = conn.execute(
                "SELECT *, 'cloud_plan' AS source_type, TRUE AS supports_recipient_approval FROM cloud_broadcast_plan_recipients WHERE id = %s",
                (normalized_recipient_id,),
            ).fetchone()
            self._audit(
                conn,
                operator=operator,
                action_type="cloud_plan_recipient_message_update",
                target_type="cloud_broadcast_plan_recipient_message",
                target_id=f"{normalized_plan_id}:{normalized_recipient_id}:{normalized_message_id}",
                before=dict(message),
                after=dict(row or {}),
            )
            conn.commit()
        return {
            "status": "updated",
            "recipient": _recipient_view(dict(recipient_row or {})),
            "message": _message_view({**dict(row or {}), "source_type": "cloud_plan"}),
        }



from .repository_memory import InMemoryCloudPlanRepository


_FIXTURE_REPO = InMemoryCloudPlanRepository()


def reset_cloud_plan_fixture_state() -> None:
    _FIXTURE_REPO.reset()


def build_cloud_plan_repository() -> CloudPlanRepository:
    if production_data_ready():
        return PostgresCloudPlanRepository()
    return _FIXTURE_REPO
