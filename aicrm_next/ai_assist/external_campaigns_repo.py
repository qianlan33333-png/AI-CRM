from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import resolve_identity_with_dbapi
from aicrm_next.platform_foundation.background_jobs.broadcast_job_write_port import (
    BroadcastJobCreate,
    build_broadcast_job_write_port,
)
from aicrm_next.shared.postgres_connection import get_db

JsonDict = dict[str, Any]


class ExternalCampaignRepositoryError(Exception):
    pass


class ExternalCampaignRepository(Protocol):
    def table_columns(self, table_name: str) -> set[str]: ...
    def fetch_send_target_by_unionid(self, unionid: str) -> JsonDict | None: ...
    def fetch_send_target_by_external_userid(self, external_userid: str) -> JsonDict | None: ...
    def fetch_do_not_disturb_reasons(self, unionid: str) -> list[JsonDict]: ...
    def fetch_contact_row(self, external_userid: str) -> JsonDict: ...
    def get_broadcast_job_by_idempotency_key(self, idempotency_key: str) -> JsonDict | None: ...
    def create_broadcast_job(
        self,
        *,
        source_type: str,
        source_id: str,
        source_table: str,
        scheduled_for: str,
        priority: int,
        batch_key: str,
        idempotency_key: str,
        target_unionids: list[str],
        target_summary: str,
        content_type: str,
        content_payload: JsonDict,
        content_summary: str,
        trace_id: str,
        created_by: str,
        business_domain: str,
        channel: str,
        target_kind: str,
        metadata: JsonDict,
    ) -> JsonDict: ...
    def get_campaign_by_code(self, campaign_code: str) -> JsonDict | None: ...
    def get_campaign_by_id(self, campaign_id: int) -> JsonDict | None: ...
    def count_open_campaign_jobs(self, campaign_id: int) -> int: ...
    def assemble_campaign_overview(self, campaign_id: int) -> JsonDict: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_text(value: Any, *, default: str = "{}") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, str):
        return value or default
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_object(value: Any) -> JsonDict:
    if isinstance(value, dict):
        return dict(value)
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _row_to_dict(row: Any) -> JsonDict:
    return dict(row) if row else {}


class PostgresExternalCampaignRepository:
    def __init__(self, db: Any | None = None) -> None:
        self.db = db or get_db()
        self._columns_cache: dict[str, set[str]] = {}

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    def table_columns(self, table_name: str) -> set[str]:
        safe_name = _text(table_name)
        if not safe_name.replace("_", "").isalnum():
            raise ExternalCampaignRepositoryError(f"invalid_table_name:{safe_name}")
        if safe_name in self._columns_cache:
            return self._columns_cache[safe_name]
        cur = self.db.cursor()
        try:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                """,
                (safe_name,),
            )
            columns = {_text(row.get("column_name")) for row in (cur.fetchall() or []) if _text(row.get("column_name"))}
            if columns:
                self._columns_cache[safe_name] = columns
                return columns
        except Exception:
            self.rollback()
        try:
            cur.execute(f"PRAGMA table_info({safe_name})")
            columns = {_text(row.get("name")) for row in (cur.fetchall() or []) if _text(row.get("name"))}
            self._columns_cache[safe_name] = columns
            return columns
        except Exception:
            self.rollback()
        self._columns_cache[safe_name] = set()
        return set()

    def fetch_send_target_by_unionid(self, unionid: str) -> JsonDict | None:
        return self._send_target(ResolvePersonIdentityRequest(unionid=_text(unionid) or None))

    def fetch_send_target_by_external_userid(self, external_userid: str) -> JsonDict | None:
        return self._send_target(ResolvePersonIdentityRequest(external_userid=_text(external_userid) or None))

    def _send_target(self, query: ResolvePersonIdentityRequest) -> JsonDict | None:
        resolution = resolve_identity_with_dbapi(self.db, query, placeholder="?")
        identity = resolution.identity if resolution.status == "resolved" else None
        if identity is None:
            return None
        return {
            "unionid": _text(identity.unionid),
            "primary_external_userid": _text(identity.external_userid),
            "external_userid": _text(identity.external_userid),
            "primary_owner_userid": _text(identity.owner_userid),
            "owner_userid": _text(identity.owner_userid),
            "customer_name": _text(identity.customer_name),
        }

    def fetch_do_not_disturb_reasons(self, unionid: str) -> list[JsonDict]:
        try:
            rows = self.db.execute(
                """
                SELECT reason_code, reason_text, source_type
                FROM user_ops_do_not_disturb_next
                WHERE unionid = ?
                  AND is_active = TRUE
                ORDER BY id ASC
                """,
                (_text(unionid),),
            ).fetchall()
        except Exception:
            self.rollback()
            return []
        return [
            {
                "reason_code": _text(row.get("reason_code")),
                "reason_text": _text(row.get("reason_text")),
                "source_type": _text(row.get("source_type")),
            }
            for row in rows
        ]

    def fetch_contact_row(self, external_userid: str) -> JsonDict:
        try:
            row = self.db.execute(
                """
                SELECT
                    im.external_userid,
                    COALESCE(NULLIF(fu.user_id, ''), NULLIF(im.follow_user_userid, '')) AS owner_userid,
                    COALESCE(NULLIF(im.name, ''), NULLIF(im.raw_profile ->> 'name', '')) AS customer_name,
                    COALESCE(NULLIF(fu.remark, ''), NULLIF(im.raw_profile ->> 'remark', '')) AS remark
                FROM wecom_external_contact_identity_map im
                LEFT JOIN wecom_external_contact_follow_users fu
                  ON fu.corp_id = im.corp_id
                 AND fu.external_userid = im.external_userid
                 AND COALESCE(fu.relation_status, 'active') = 'active'
                WHERE im.external_userid = ?
                ORDER BY fu.is_primary DESC NULLS LAST, fu.updated_at DESC NULLS LAST, im.updated_at DESC, im.id DESC
                LIMIT 1
                """,
                (_text(external_userid),),
            ).fetchone()
        except Exception:
            self.rollback()
            return {}
        return _row_to_dict(row)

    def _decode_broadcast_job(self, row: Any) -> JsonDict | None:
        if not row:
            return None
        item = _row_to_dict(row)
        item["target_unionids"] = json.loads(item.get("target_unionids_json") or "[]") if isinstance(item.get("target_unionids_json"), str) else list(item.get("target_unionids_json") or [])
        item["content_payload"] = _json_object(item.get("content_payload"))
        item["metadata"] = _json_object(item.get("metadata_json"))
        return item

    def get_broadcast_job_by_idempotency_key(self, idempotency_key: str) -> JsonDict | None:
        key = _text(idempotency_key)
        if not key:
            return None
        row = self.db.execute(
            "SELECT * FROM broadcast_jobs WHERE idempotency_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        return self._decode_broadcast_job(row)

    def create_broadcast_job(
        self,
        *,
        source_type: str,
        source_id: str,
        source_table: str,
        scheduled_for: str,
        priority: int,
        batch_key: str,
        idempotency_key: str,
        target_unionids: list[str],
        target_summary: str,
        content_type: str,
        content_payload: JsonDict,
        content_summary: str,
        trace_id: str,
        created_by: str,
        business_domain: str,
        channel: str,
        target_kind: str,
        metadata: JsonDict,
    ) -> JsonDict:
        existing = self.get_broadcast_job_by_idempotency_key(idempotency_key)
        if existing:
            return {**existing, "status": _text(existing.get("status")) or "exists", "idempotent_existing": True}
        row = build_broadcast_job_write_port().create_dbapi(
            self.db,
            BroadcastJobCreate(
                source_type=_text(source_type),
                source_id=_text(source_id),
                source_table=_text(source_table),
                scheduled_for=_text(scheduled_for) or _now_iso(),
                priority=int(priority),
                batch_key=_text(batch_key),
                idempotency_key=_text(idempotency_key),
                target_unionids=tuple(
                    _text(item) for item in target_unionids if _text(item)
                ),
                target_summary=_text(target_summary),
                content_type=_text(content_type) or "private_message",
                content_payload=dict(content_payload or {}),
                content_summary=_text(content_summary),
                trace_id=_text(trace_id),
                created_by=_text(created_by),
                business_domain=_text(business_domain) or "ai_assistant",
                channel=_text(channel) or "wecom_private",
                target_kind=_text(target_kind) or "unionid",
                metadata=dict(metadata or {}),
            ),
        )
        return self._decode_broadcast_job(row) or {}

    def _decode_campaign(self, row: Any) -> JsonDict | None:
        if not row:
            return None
        item = _row_to_dict(row)
        item["metadata"] = _json_object(item.get("metadata_json"))
        item["stats"] = _json_object(item.get("stats_json"))
        return item

    def get_campaign_by_code(self, campaign_code: str) -> JsonDict | None:
        row = self.db.execute("SELECT * FROM campaigns WHERE campaign_code = ? LIMIT 1", (_text(campaign_code),)).fetchone()
        return self._decode_campaign(row)

    def get_campaign_by_id(self, campaign_id: int) -> JsonDict | None:
        row = self.db.execute("SELECT * FROM campaigns WHERE id = ? LIMIT 1", (int(campaign_id),)).fetchone()
        return self._decode_campaign(row)

    def count_open_campaign_jobs(self, campaign_id: int) -> int:
        row = self.db.execute(
            """
            SELECT COUNT(*) AS job_count
            FROM broadcast_jobs
            WHERE source_type = 'campaign'
              AND source_id LIKE ?
              AND status IN ('waiting_approval', 'queued', 'claimed')
            """,
            (f"{int(campaign_id)}:%",),
        ).fetchone()
        return int((row or {}).get("job_count") or 0)

    def assemble_campaign_overview(self, campaign_id: int) -> JsonDict:
        campaign = self.get_campaign_by_id(campaign_id)
        if not campaign:
            return {}
        segment_rows = self.db.execute(
            """
            SELECT cs.id AS campaign_segment_id, cs.segment_id, cs.segment_code,
                   cs.priority, cs.label,
                   s.display_name AS segment_name, s.cached_headcount,
                   (SELECT COUNT(*) FROM campaign_members cm
                      WHERE cm.campaign_segment_id = cs.id) AS allocated_count
            FROM campaign_segments cs
            JOIN segments s ON s.id = cs.segment_id
            WHERE cs.campaign_id = ?
            ORDER BY cs.priority DESC, cs.id ASC
            """,
            (int(campaign_id),),
        ).fetchall() or []
        segments: list[JsonDict] = []
        for row in segment_rows:
            item = _row_to_dict(row)
            step_rows = self.db.execute(
                """
                SELECT step_index, day_offset, send_time, content_text, stop_on_reply,
                       skip_if_recently_touched_days, content_payload_json
                FROM campaign_steps
                WHERE campaign_segment_id = ?
                ORDER BY step_index ASC
                """,
                (int(item["campaign_segment_id"]),),
            ).fetchall() or []
            steps = []
            for step_row in step_rows:
                step = _row_to_dict(step_row)
                step["content_payload_json"] = _json_object(step.get("content_payload_json"))
                steps.append(step)
            item["steps"] = steps
            segments.append(item)
        status_rows = self.db.execute(
            """
            SELECT status, COUNT(*) AS c
            FROM campaign_members
            WHERE campaign_id = ?
            GROUP BY status
            """,
            (int(campaign_id),),
        ).fetchall() or []
        member_status = {_text(row.get("status")) or "unknown": int(row.get("c") or 0) for row in status_rows}
        return {
            "campaign": campaign,
            "segments": segments,
            "member_status_counts": member_status,
            "total_members": sum(member_status.values()),
        }


def build_external_campaign_repository() -> ExternalCampaignRepository:
    return PostgresExternalCampaignRepository()
