from __future__ import annotations

import copy
import json
from typing import Any, Protocol

from aicrm_next.platform.shared import runtime
from aicrm_next.platform.shared.repository_provider import RepositoryProviderError
from aicrm_next.platform.shared.runtime import raw_database_url
from .repository import connect_cloud_campaign_read_db

SOURCE_STATUS = "next_cloud_orchestrator_campaign_read"
ROUTE_OWNER = "ai_crm_next"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any, *, default: Any) -> Any:
    if value in (None, ""):
        return copy.deepcopy(default)
    if isinstance(value, (dict, list)):
        return value
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return copy.deepcopy(default)
    return loaded


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _offset(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://") :]
    return url


def _base_contract() -> dict[str, Any]:
    return {
        "source_status": SOURCE_STATUS,
        "route_owner": ROUTE_OWNER,
        "fallback_used": False,
        "real_external_call_executed": False,
    }


def _campaign_view(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _json(row.get("metadata_json"), default={})
    return {
        "id": int(row.get("id") or 0),
        "campaign_code": _text(row.get("campaign_code")),
        "display_name": _text(row.get("display_name")) or _text(row.get("campaign_code")),
        "intent": _text(row.get("intent")),
        "anchor_mode": _text(row.get("anchor_mode")),
        "anchor_date": _json_value(row.get("anchor_date") or ""),
        "review_status": _text(row.get("review_status")) or "draft",
        "run_status": _text(row.get("run_status")) or "draft",
        "created_by_agent": _text(row.get("created_by_agent")),
        "owner_userid": _text(row.get("owner_userid")),
        "trace_id": _text(row.get("trace_id")),
        "started_at": _json_value(row.get("started_at") or ""),
        "finished_at": _json_value(row.get("finished_at") or ""),
        "created_at": _json_value(row.get("created_at") or ""),
        "updated_at": _json_value(row.get("updated_at") or ""),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "metadata_json": _json_value(row.get("metadata_json") or {}),
        "group_code": _text((metadata or {}).get("group_code")) if isinstance(metadata, dict) else "",
        "group_label": _text((metadata or {}).get("group_label")) if isinstance(metadata, dict) else "",
        "segment_count": int(row.get("segment_count") or 0),
        "member_count": int(row.get("member_count") or 0),
        "source_type": "campaign_read_model",
    }


def _step_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_index": int(row.get("step_index") or 0),
        "day_offset": int(row.get("day_offset") or 0),
        "send_time": _text(row.get("send_time")) or "10:00",
        "content_text": _text(row.get("content_text")),
        "stop_on_reply": bool(row.get("stop_on_reply")),
        "skip_if_recently_touched_days": int(row.get("skip_if_recently_touched_days") or 0),
        "content_payload_json": _json(row.get("content_payload_json"), default={}),
    }


def _member_view(row: dict[str, Any]) -> dict[str, Any]:
    current_step_index = row.get("current_step_index")
    return {
        "id": int(row.get("id") or 0),
        "member_id": row.get("member_id"),
        "unionid": _text(row.get("unionid")),
        "external_contact_id": _text(row.get("external_contact_id")),
        "status": _text(row.get("status")) or "pending",
        "stop_reason": _text(row.get("stop_reason")),
        "current_step_index": int(current_step_index) if current_step_index not in (None, "") else -1,
        "next_due_at": _json_value(row.get("next_due_at") or ""),
        "last_step_sent_at": _json_value(row.get("last_step_sent_at") or ""),
        "last_error_text": _text(row.get("last_error_text")),
        "retry_count": int(row.get("retry_count") or 0),
        "anchor_date": _json_value(row.get("anchor_date") or ""),
        "joined_at": _json_value(row.get("joined_at") or ""),
        "segment_label": _text(row.get("segment_label")),
        "segment_priority": int(row.get("segment_priority") or 0),
        "segment_name": _text(row.get("segment_name")),
        "segment_code": _text(row.get("segment_code")),
        "phone": _text(row.get("phone")),
        "current_pool": _text(row.get("current_pool")),
        "current_audience_code": _text(row.get("current_audience_code")),
        "profile_segment_key": _text(row.get("profile_segment_key")),
        "behavior_tier_key": _text(row.get("behavior_tier_key")),
    }


def _step_payload_view(payload: dict[str, Any]) -> dict[str, Any]:
    content_package = _json(payload.get("content_package_json") or payload.get("content_package"), default={})
    content_payload = _json(payload.get("content_payload_json"), default={})
    if not content_payload:
        content_payload = {
            "content_text": _text(payload.get("content_text") or payload.get("message_text")),
            "image_library_ids": payload.get("image_library_ids") or [],
            "miniprogram_library_ids": payload.get("miniprogram_library_ids") or [],
            "attachment_library_ids": payload.get("attachment_library_ids") or [],
            "group_invite_library_ids": payload.get("group_invite_library_ids") or [],
        }
    if content_package and isinstance(content_package, dict):
        content_payload.update({key: value for key, value in content_package.items() if value not in (None, "")})
    if payload.get("message_text") not in (None, "") or payload.get("content_text") not in (None, ""):
        content_payload["content_text"] = _text(payload.get("message_text") or payload.get("content_text"))
    return {
        "step_index": int(payload.get("step_index") or 0),
        "day_offset": int(payload.get("day_offset") or 0),
        "send_time": _text(payload.get("send_time")) or "10:00",
        "content_text": _text(payload.get("message_text") or payload.get("content_text") or content_payload.get("content_text")),
        "stop_on_reply": bool(payload.get("stop_on_reply", True)),
        "skip_if_recently_touched_days": int(payload.get("skip_if_recently_touched_days") or 0),
        "content_payload_json": content_payload,
        "content_package_json": content_package if isinstance(content_package, dict) else {},
        "image_library_ids": content_payload.get("image_library_ids") or [],
        "miniprogram_library_ids": content_payload.get("miniprogram_library_ids") or [],
        "attachment_library_ids": content_payload.get("attachment_library_ids") or [],
        "group_invite_library_ids": content_payload.get("group_invite_library_ids") or [],
    }


class CloudCampaignReadRepository(Protocol):
    def list_campaigns(self, *, review_status: str = "", run_status: str = "", group_code: str = "", limit: int = 5000, offset: int = 0) -> tuple[list[dict[str, Any]], int]: ...
    def get_campaign(self, campaign_code: str) -> dict[str, Any] | None: ...
    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any] | None: ...
    def campaign_overview(self, campaign_code: str) -> dict[str, Any] | None: ...
    def list_members(self, campaign_code: str, *, status: str = "", limit: int = 200, offset: int = 0) -> dict[str, Any] | None: ...
    def list_steps(self, campaign_code: str) -> dict[str, Any] | None: ...


class PostgresCloudCampaignReadRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = _psycopg_url(_text(database_url or raw_database_url()))
        if not self._database_url:
            raise RepositoryProviderError("cloud campaign read repository unavailable: DATABASE_URL is required")

    def _connect(self):
        return connect_cloud_campaign_read_db(self._database_url)

    def list_campaigns(self, *, review_status: str = "", run_status: str = "", group_code: str = "", limit: int = 5000, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        where = ["1=1"]
        args: list[Any] = []
        if review_status:
            where.append("c.review_status = %s")
            args.append(review_status)
        if run_status:
            where.append("c.run_status = %s")
            args.append(run_status)
        if group_code:
            where.append("COALESCE(NULLIF(c.metadata_json->>'group_code', ''), c.campaign_code) = %s")
            args.append(group_code)
        normalized_limit = _limit(limit, default=5000, maximum=5000)
        normalized_offset = _offset(offset)
        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM campaigns c WHERE " + " AND ".join(where),
                tuple(args),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT c.id, c.campaign_code, c.display_name, c.intent, c.anchor_mode, c.anchor_date,
                       c.review_status, c.run_status, c.created_by_agent, c.owner_userid, c.trace_id,
                       c.started_at, c.finished_at, c.created_at, c.updated_at, c.metadata_json,
                       (SELECT COUNT(*) FROM campaign_segments cs WHERE cs.campaign_id = c.id) AS segment_count,
                       (SELECT COUNT(*) FROM campaign_members cm WHERE cm.campaign_id = c.id) AS member_count
                FROM campaigns c
                WHERE """
                + " AND ".join(where)
                + """
                ORDER BY c.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple([*args, normalized_limit, normalized_offset]),
            ).fetchall()
        return [_campaign_view(dict(row)) for row in rows], int((total_row or {}).get("total") or 0)

    def get_campaign(self, campaign_code: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.id, c.campaign_code, c.display_name, c.intent, c.anchor_mode, c.anchor_date,
                       c.review_status, c.run_status, c.created_by_agent, c.owner_userid, c.trace_id,
                       c.started_at, c.finished_at, c.created_at, c.updated_at, c.metadata_json,
                       (SELECT COUNT(*) FROM campaign_segments cs WHERE cs.campaign_id = c.id) AS segment_count,
                       (SELECT COUNT(*) FROM campaign_members cm WHERE cm.campaign_id = c.id) AS member_count
                FROM campaigns c
                WHERE c.campaign_code = %s
                """,
                (campaign_code,),
            ).fetchone()
        return _campaign_view(dict(row)) if row else None

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        campaign_code = _text(payload.get("campaign_code"))
        if not campaign_code:
            return None
        existing = self.get_campaign(campaign_code)
        if existing:
            return existing
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if isinstance(payload.get("metadata_json"), dict):
            metadata = dict(payload["metadata_json"])
        display_name = _text(payload.get("display_name") or payload.get("name") or campaign_code)
        intent = _text(payload.get("intent") or payload.get("objective"))
        anchor_mode = _text(payload.get("anchor_mode") or "campaign_start_date")
        anchor_date = _text(payload.get("anchor_date"))
        created_by_agent = _text(payload.get("created_by_agent") or payload.get("operator") or "admin_ui")
        owner_userid = _text(payload.get("owner_userid") or payload.get("operator"))
        trace_id = _text(payload.get("trace_id") or campaign_code)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO campaigns (
                    campaign_code, display_name, intent, anchor_mode, anchor_date,
                    review_status, run_status, created_by_agent, owner_userid, trace_id,
                    metadata_json, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, 'draft', 'draft', %s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    campaign_code,
                    display_name,
                    intent,
                    anchor_mode,
                    anchor_date,
                    created_by_agent,
                    owner_userid,
                    trace_id,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            conn.commit()
        return self.get_campaign(campaign_code)

    def campaign_overview(self, campaign_code: str) -> dict[str, Any] | None:
        campaign = self.get_campaign(campaign_code)
        if not campaign:
            return None
        with self._connect() as conn:
            segments = conn.execute(
                """
                SELECT cs.id AS campaign_segment_id, cs.segment_id, cs.segment_code,
                       cs.priority, cs.label,
                       s.display_name AS segment_name, s.cached_headcount,
                       (SELECT COUNT(*) FROM campaign_members cm WHERE cm.campaign_segment_id = cs.id) AS allocated_count
                FROM campaign_segments cs
                JOIN segments s ON s.id = cs.segment_id
                WHERE cs.campaign_id = %s
                ORDER BY cs.priority DESC, cs.id ASC
                """,
                (int(campaign["id"]),),
            ).fetchall()
            status_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM campaign_members
                WHERE campaign_id = %s
                GROUP BY status
                """,
                (int(campaign["id"]),),
            ).fetchall()
            overview_segments: list[dict[str, Any]] = []
            for segment in segments:
                segment_dict = dict(segment)
                steps = conn.execute(
                    """
                    SELECT step_index, day_offset, send_time, content_text, stop_on_reply,
                           skip_if_recently_touched_days, content_payload_json
                    FROM campaign_steps
                    WHERE campaign_segment_id = %s
                    ORDER BY step_index ASC
                    """,
                    (int(segment_dict["campaign_segment_id"]),),
                ).fetchall()
                segment_dict["steps"] = [_step_view(dict(row)) for row in steps]
                overview_segments.append(segment_dict)
        member_status = {str(row.get("status") or "unknown"): int(row.get("count") or 0) for row in status_rows}
        return {
            "campaign": campaign,
            "segments": overview_segments,
            "member_status_counts": member_status,
            "total_members": sum(member_status.values()),
        }

    def list_members(self, campaign_code: str, *, status: str = "", limit: int = 200, offset: int = 0) -> dict[str, Any] | None:
        campaign = self.get_campaign(campaign_code)
        if not campaign:
            return None
        where = ["cm.campaign_id = %s"]
        args: list[Any] = [int(campaign["id"])]
        if status:
            where.append("cm.status = %s")
            args.append(status)
        normalized_limit = _limit(limit, default=200, maximum=500)
        normalized_offset = _offset(offset)
        with self._connect() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM campaign_members cm WHERE " + " AND ".join(where),
                tuple(args),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT cm.id, cm.member_id, cm.unionid, cm.status, cm.stop_reason,
                       cm.current_step_index, cm.next_due_at, cm.last_step_sent_at,
                       cm.last_error_text, cm.retry_count, cm.anchor_date, cm.joined_at,
                       cs.label AS segment_label, cs.priority AS segment_priority,
                       s.display_name AS segment_name, s.segment_code,
                       '' AS phone, '' AS current_pool, '' AS current_audience_code,
                       '' AS profile_segment_key, '' AS behavior_tier_key
                FROM campaign_members cm
                JOIN campaign_segments cs ON cs.id = cm.campaign_segment_id
                JOIN segments s ON s.id = cs.segment_id
                WHERE """
                + " AND ".join(where)
                + """
                ORDER BY cm.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple([*args, normalized_limit, normalized_offset]),
            ).fetchall()
        return {
            "campaign": campaign,
            "rows": [_member_view(dict(row)) for row in rows],
            "members": [_member_view(dict(row)) for row in rows],
            "total": int((total_row or {}).get("total") or 0),
            "limit": normalized_limit,
            "offset": normalized_offset,
        }

    def list_steps(self, campaign_code: str) -> dict[str, Any] | None:
        overview = self.campaign_overview(campaign_code)
        if not overview:
            return None
        steps = []
        for segment in overview.get("segments") or []:
            for step in segment.get("steps") or []:
                enriched = dict(step)
                enriched["campaign_segment_id"] = segment.get("campaign_segment_id")
                enriched["segment_code"] = segment.get("segment_code")
                enriched["segment_label"] = segment.get("label")
                steps.append(enriched)
        return {"campaign": overview["campaign"], "segments": overview.get("segments") or [], "steps": steps, "count": len(steps)}


class InMemoryCloudCampaignReadRepository:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.campaigns = [
            {
                "id": 1,
                "campaign_code": "camp_next_read_fixture",
                "display_name": "Next read fixture campaign",
                "intent": "read-only workspace smoke",
                "anchor_mode": "campaign_start_date",
                "anchor_date": "2026-06-03",
                "review_status": "pending_review",
                "run_status": "draft",
                "created_by_agent": "fixture-agent",
                "owner_userid": "owner_fixture",
                "trace_id": "trace_campaign_fixture",
                "created_at": "2026-06-03T10:00:00+08:00",
                "updated_at": "2026-06-03T10:05:00+08:00",
                "metadata_json": {"group_code": "fixture_group", "group_label": "Fixture group"},
                "segment_count": 1,
                "member_count": 2,
            }
        ]
        self.segments = [
            {
                "campaign_segment_id": 11,
                "segment_id": 21,
                "segment_code": "seg_fixture",
                "priority": 100,
                "label": "Fixture segment",
                "segment_name": "Fixture segment",
                "cached_headcount": 2,
                "allocated_count": 2,
                "steps": [
                    {
                        "step_index": 0,
                        "day_offset": 0,
                        "send_time": "10:00",
                        "content_text": "fixture hello",
                        "stop_on_reply": True,
                        "skip_if_recently_touched_days": 0,
                        "content_payload_json": {"image_library_ids": []},
                    }
                ],
            }
        ]
        self.members = [
            {"id": 101, "member_id": 501, "unionid": "union_fixture_a", "external_contact_id": "wm_fixture_a", "status": "pending", "current_step_index": -1, "next_due_at": "2026-06-03T09:00:00+00:00", "phone": "13800000001", "segment_label": "Fixture segment", "segment_priority": 100, "segment_name": "Fixture segment", "segment_code": "seg_fixture", "profile_segment_key": "trial", "behavior_tier_key": "warm"},
            {"id": 102, "member_id": 502, "unionid": "union_fixture_b", "external_contact_id": "wm_fixture_b", "status": "pending", "current_step_index": -1, "next_due_at": "2026-06-03T09:00:00+00:00", "phone": "13800000002", "segment_label": "Fixture segment", "segment_priority": 100, "segment_name": "Fixture segment", "segment_code": "seg_fixture", "profile_segment_key": "trial", "behavior_tier_key": "cold"},
        ]
        self.deleted_campaign_codes: set[str] = set()

    def list_campaigns(self, *, review_status: str = "", run_status: str = "", group_code: str = "", limit: int = 5000, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        rows = [_campaign_view(copy.deepcopy(row)) for row in self.campaigns]
        if review_status:
            rows = [row for row in rows if row["review_status"] == review_status]
        if run_status:
            rows = [row for row in rows if row["run_status"] == run_status]
        if group_code:
            rows = [row for row in rows if row["group_code"] == group_code]
        total = len(rows)
        normalized_offset = _offset(offset)
        rows = rows[normalized_offset : normalized_offset + _limit(limit, default=5000, maximum=5000)]
        return rows, total

    def get_campaign(self, campaign_code: str) -> dict[str, Any] | None:
        for row in self.campaigns:
            if row["campaign_code"] == campaign_code:
                return _campaign_view(copy.deepcopy(row))
        return None

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        campaign_code = _text(payload.get("campaign_code"))
        if not campaign_code:
            return None
        existing = self.get_campaign(campaign_code)
        if existing:
            return existing
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if isinstance(payload.get("metadata_json"), dict):
            metadata = dict(payload["metadata_json"])
        row = {
            "id": max([int(item.get("id") or 0) for item in self.campaigns] or [0]) + 1,
            "campaign_code": campaign_code,
            "display_name": _text(payload.get("display_name") or payload.get("name") or campaign_code),
            "intent": _text(payload.get("intent") or payload.get("objective")),
            "anchor_mode": _text(payload.get("anchor_mode") or "campaign_start_date"),
            "anchor_date": _text(payload.get("anchor_date")),
            "review_status": _text(payload.get("review_status") or "draft"),
            "run_status": _text(payload.get("run_status") or "draft"),
            "created_by_agent": _text(payload.get("created_by_agent") or payload.get("operator") or "admin_ui"),
            "owner_userid": _text(payload.get("owner_userid") or payload.get("operator")),
            "trace_id": _text(payload.get("trace_id") or campaign_code),
            "created_at": "2026-06-03T15:00:00+08:00",
            "updated_at": "2026-06-03T15:00:00+08:00",
            "metadata_json": metadata,
            "segment_count": int(payload.get("segment_count") or 0),
            "member_count": int(payload.get("member_count") or payload.get("target_count") or 0),
        }
        self.campaigns.append(row)
        return _campaign_view(copy.deepcopy(row))

    def update_campaign_status(
        self,
        campaign_code: str,
        *,
        review_status: str | None = None,
        run_status: str | None = None,
        deleted: bool = False,
    ) -> dict[str, Any] | None:
        for row in self.campaigns:
            if row["campaign_code"] != campaign_code:
                continue
            if deleted:
                row["review_status"] = "deleted"
                row["run_status"] = "cancelled"
                self.deleted_campaign_codes.add(campaign_code)
            if review_status is not None:
                row["review_status"] = review_status
            if run_status is not None:
                row["run_status"] = run_status
            row["updated_at"] = "2026-06-03T14:00:00+08:00"
            return _campaign_view(copy.deepcopy(row))
        return None

    def _segment_for_step(self, campaign_code: str, campaign_segment_id: int | None = None) -> dict[str, Any] | None:
        if not self.get_campaign(campaign_code):
            return None
        if campaign_segment_id:
            for segment in self.segments:
                if int(segment.get("campaign_segment_id") or 0) == int(campaign_segment_id):
                    return segment
        return self.segments[0] if self.segments else None

    def add_step(self, campaign_code: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        segment = self._segment_for_step(campaign_code, payload.get("campaign_segment_id"))
        if not segment:
            return None
        steps = segment.setdefault("steps", [])
        next_index = max([int(step.get("step_index") or 0) for step in steps] or [-1]) + 1
        item = _step_payload_view({**payload, "step_index": payload.get("step_index", next_index)})
        steps.append(item)
        self.update_campaign_status(campaign_code)
        return copy.deepcopy(item)

    def update_step(self, campaign_code: str, step_index: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.get_campaign(campaign_code):
            return None
        for segment in self.segments:
            for index, step in enumerate(segment.get("steps") or []):
                if int(step.get("step_index") or 0) != int(step_index):
                    continue
                updated = _step_payload_view({**step, **payload, "step_index": int(step_index)})
                segment["steps"][index] = updated
                self.update_campaign_status(campaign_code)
                return copy.deepcopy(updated)
        return None

    def delete_step(self, campaign_code: str, step_index: int) -> dict[str, Any] | None:
        if not self.get_campaign(campaign_code):
            return None
        for segment in self.segments:
            steps = list(segment.get("steps") or [])
            for index, step in enumerate(steps):
                if int(step.get("step_index") or 0) != int(step_index):
                    continue
                removed = steps.pop(index)
                segment["steps"] = steps
                self.update_campaign_status(campaign_code)
                return copy.deepcopy(removed)
        return None

    def campaign_overview(self, campaign_code: str) -> dict[str, Any] | None:
        campaign = self.get_campaign(campaign_code)
        if not campaign:
            return None
        return {
            "campaign": campaign,
            "segments": copy.deepcopy(self.segments),
            "member_status_counts": {"pending": len(self.members)},
            "total_members": len(self.members),
        }

    def list_members(self, campaign_code: str, *, status: str = "", limit: int = 200, offset: int = 0) -> dict[str, Any] | None:
        if not self.get_campaign(campaign_code):
            return None
        rows = [_member_view(copy.deepcopy(row)) for row in self.members]
        if status:
            rows = [row for row in rows if row["status"] == status]
        total = len(rows)
        normalized_offset = _offset(offset)
        rows = rows[normalized_offset : normalized_offset + _limit(limit, default=200, maximum=500)]
        return {"rows": rows, "members": rows, "total": total, "limit": _limit(limit, default=200, maximum=500), "offset": normalized_offset}

    def list_steps(self, campaign_code: str) -> dict[str, Any] | None:
        overview = self.campaign_overview(campaign_code)
        if not overview:
            return None
        steps = []
        for segment in overview["segments"]:
            for step in segment["steps"]:
                item = dict(step)
                item["campaign_segment_id"] = segment["campaign_segment_id"]
                item["segment_code"] = segment["segment_code"]
                item["segment_label"] = segment["label"]
                steps.append(item)
        return {"campaign": overview["campaign"], "segments": overview["segments"], "steps": steps, "count": len(steps)}


_FIXTURE_REPO = InMemoryCloudCampaignReadRepository()


def reset_campaign_read_fixture_state() -> None:
    _FIXTURE_REPO.reset()


def build_campaign_read_repository() -> CloudCampaignReadRepository:
    if runtime.production_data_ready():
        return PostgresCloudCampaignReadRepository()
    return _FIXTURE_REPO


def degraded_payload(*, error: str) -> dict[str, Any]:
    return {**_base_contract(), "page_error": error, "degraded": True}


class ListCloudCampaignsQuery:
    def __init__(self, repo: CloudCampaignReadRepository | None = None) -> None:
        self._repo = repo or build_campaign_read_repository()

    def execute(self, *, review_status: str = "", run_status: str = "", group_code: str = "", limit: int = 5000, offset: int = 0) -> dict[str, Any]:
        normalized_limit = _limit(limit, default=5000, maximum=5000)
        normalized_offset = _offset(offset)
        try:
            rows, total = self._repo.list_campaigns(review_status=review_status, run_status=run_status, group_code=group_code, limit=normalized_limit, offset=normalized_offset)
        except Exception as exc:
            return {
                "ok": True,
                "campaigns": [],
                "items": [],
                "count": 0,
                "total": 0,
                "limit": normalized_limit,
                "offset": normalized_offset,
                **degraded_payload(error=str(exc)),
            }
        return {
            "ok": True,
            "campaigns": rows,
            "items": rows,
            "count": len(rows),
            "total": total,
            "limit": normalized_limit,
            "offset": normalized_offset,
            **_base_contract(),
        }

    __call__ = execute


class GetCloudCampaignQuery:
    def __init__(self, repo: CloudCampaignReadRepository | None = None) -> None:
        self._repo = repo or build_campaign_read_repository()

    def execute(self, campaign_code: str) -> dict[str, Any]:
        overview = self._repo.campaign_overview(campaign_code)
        if not overview:
            raise LookupError("campaign_not_found")
        return {"ok": True, "campaign": overview, **_base_contract()}

    __call__ = execute


class ListCloudCampaignMembersQuery:
    def __init__(self, repo: CloudCampaignReadRepository | None = None) -> None:
        self._repo = repo or build_campaign_read_repository()

    def execute(self, campaign_code: str, *, status: str = "", limit: int = 200, offset: int = 0) -> dict[str, Any]:
        result = self._repo.list_members(campaign_code, status=status, limit=_limit(limit, default=200, maximum=500), offset=_offset(offset))
        if not result:
            raise LookupError("campaign_not_found")
        return {"ok": True, **result, **_base_contract()}

    __call__ = execute


class ListCloudCampaignStepsQuery:
    def __init__(self, repo: CloudCampaignReadRepository | None = None) -> None:
        self._repo = repo or build_campaign_read_repository()

    def execute(self, campaign_code: str) -> dict[str, Any]:
        result = self._repo.list_steps(campaign_code)
        if not result:
            raise LookupError("campaign_not_found")
        return {"ok": True, **result, **_base_contract()}

    __call__ = execute
