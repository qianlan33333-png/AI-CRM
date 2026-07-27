from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from aicrm_next.shared.db_session import get_engine


JsonDict = dict[str, Any]


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _json_value(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


class OperationMemberDirectoryRepository:
    def __init__(self, *, engine: Engine | None = None) -> None:
        self._engine = engine or get_engine()

    @property
    def _json_expr(self) -> str:
        return "CAST(:raw_payload_json AS JSONB)" if self._engine.dialect.name != "sqlite" else ":raw_payload_json"

    @property
    def _departments_expr(self) -> str:
        return "CAST(:department_ids_json AS JSONB)" if self._engine.dialect.name != "sqlite" else ":department_ids_json"

    def replace_wecom_directory_members(
        self,
        *,
        corp_id: str,
        members: list[JsonDict],
        operator: str,
    ) -> dict[str, Any]:
        normalized = [_normalize_member(member) for member in members]
        normalized = [member for member in normalized if member["wecom_userid"]]
        userids = [member["wecom_userid"] for member in normalized]
        upsert_sql = text(
            f"""
            INSERT INTO admin_wecom_directory_members (
                corp_id, wecom_userid, display_name, department_ids_json, department_name,
                position, mobile, avatar_url, wecom_status, is_active, raw_payload_json,
                first_seen_at, last_synced_at, created_at, updated_at, updated_by
            )
            VALUES (
                :corp_id, :wecom_userid, :display_name, {self._departments_expr}, :department_name,
                :position, :mobile, :avatar_url, :wecom_status, :is_active, {self._json_expr},
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :updated_by
            )
            ON CONFLICT (corp_id, wecom_userid) DO UPDATE
            SET display_name = excluded.display_name,
                department_ids_json = excluded.department_ids_json,
                department_name = excluded.department_name,
                position = excluded.position,
                mobile = excluded.mobile,
                avatar_url = excluded.avatar_url,
                wecom_status = excluded.wecom_status,
                is_active = excluded.is_active,
                raw_payload_json = excluded.raw_payload_json,
                last_synced_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                updated_by = excluded.updated_by
            """
        )
        with self._engine.begin() as conn:
            for member in normalized:
                conn.execute(
                    upsert_sql,
                    {
                        **member,
                        "corp_id": _clean_text(corp_id) or "default",
                        "department_ids_json": _json_value(member.get("department_ids") or []),
                        "raw_payload_json": _json_value(member.get("raw_payload") or {}),
                        "updated_by": _clean_text(operator) or "operation_member_sync",
                    },
                )
            if userids:
                deactivate_sql = text(
                    """
                    UPDATE admin_wecom_directory_members
                    SET is_active = FALSE,
                        last_synced_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP,
                        updated_by = :updated_by
                    WHERE corp_id = :corp_id
                      AND wecom_userid NOT IN :seen_userids
                    """
                ).bindparams(bindparam("seen_userids", expanding=True))
                conn.execute(
                    deactivate_sql,
                    {
                        "corp_id": _clean_text(corp_id) or "default",
                        "seen_userids": userids,
                        "updated_by": _clean_text(operator) or "operation_member_sync",
                    },
                )
            else:
                conn.execute(
                    text(
                        """
                        UPDATE admin_wecom_directory_members
                        SET is_active = FALSE,
                            last_synced_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP,
                            updated_by = :updated_by
                        WHERE corp_id = :corp_id
                        """
                    ),
                    {
                        "corp_id": _clean_text(corp_id) or "default",
                        "updated_by": _clean_text(operator) or "operation_member_sync",
                    },
                )
        return {"synced_count": len(normalized), "active_userids": userids}


def _normalize_member(member: JsonDict) -> JsonDict:
    userid = _clean_text(member.get("wecom_userid") or member.get("userid") or member.get("user_id"))
    return {
        "wecom_userid": userid,
        "display_name": _clean_text(member.get("display_name") or member.get("name")) or userid,
        "department_ids": list(member.get("department_ids") or member.get("department") or []),
        "department_name": _clean_text(member.get("department_name")),
        "position": _clean_text(member.get("position")),
        "mobile": _clean_text(member.get("mobile")),
        "avatar_url": _clean_text(member.get("avatar_url") or member.get("avatar")),
        "wecom_status": _clean_text(member.get("wecom_status") or member.get("status")),
        "is_active": bool(member.get("is_active", True)),
        "raw_payload": dict(member.get("raw_payload") or member),
    }
