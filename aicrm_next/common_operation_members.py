from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from aicrm_next.channels.integration_gateway.wecom_operation_members_client import WeComOperationMembersClientError
from aicrm_next.crm.operation_members.application import SyncOperationMembersFromWeComCommand
from aicrm_next.platform.shared.operation_members import (
    bool_from_query,
    clamp_page,
    clamp_page_size,
    clean_text,
    normalize_scope,
    operation_members_payload,
)
from aicrm_next.platform.platform_foundation.repository import connect_operation_members_db as _connect

router = APIRouter()


def _operator_from_request(request: Request) -> str:
    return (
        clean_text(request.headers.get("X-Admin-User"))
        or clean_text(request.headers.get("X-Forwarded-User"))
        or "admin_console"
    )


def _fetch_rows(conn: Any, sql: str, *, source: str, priority: int) -> list[dict[str, Any]]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = [dict(row) for row in (cur.fetchall() or [])]
    except Exception:
        return []
    for row in rows:
        row.setdefault("source", source)
        row.setdefault("priority", priority)
    return rows


def list_operation_member_rows(*, scope: str = "common") -> list[dict[str, Any]]:
    conn = _connect()
    if conn is None:
        if normalize_scope(scope) == "wecom_directory":
            return []
        try:
            from aicrm_next.automation_engine.group_ops.repo import build_group_ops_repository

            return [
                {
                    "user_id": item.get("userid"),
                    "display_name": item.get("name") or item.get("userid"),
                    "status": "active",
                    "source": "fixture_group_ops_owner",
                    "priority": 90,
                }
                for item in build_group_ops_repository().list_owners()
            ]
        except Exception:
            return []
    with conn:
        rows: list[dict[str, Any]] = []
        rows.extend(
            _fetch_rows(
                conn,
                """
                SELECT
                    wecom_userid AS user_id,
                    display_name,
                    position,
                    wecom_status,
                    is_active,
                    raw_payload_json,
                    '' AS avatar_url,
                    '' AS department_name
                FROM admin_wecom_directory_members
                WHERE wecom_userid <> ''
                """,
                source="wecom_directory",
                priority=10,
            )
        )
        if normalize_scope(scope) == "wecom_directory":
            return rows
        rows.extend(
            _fetch_rows(
                conn,
                """
                SELECT
                    wecom_userid AS user_id,
                    display_name,
                    is_active,
                    '' AS avatar_url,
                    '' AS department_name
                FROM admin_users
                WHERE wecom_userid <> ''
                """,
                source="admin_user",
                priority=20,
            )
        )
        rows.extend(
            _fetch_rows(
                conn,
                """
                SELECT
                    userid AS user_id,
                    display_name,
                    role,
                    active AS is_active,
                    '' AS avatar_url,
                    '' AS department_name
                FROM owner_role_map
                WHERE userid <> ''
                """,
                source="owner_role_map",
                priority=30,
            )
        )
        business_sources = [
            ("SELECT DISTINCT owner_userid AS user_id FROM group_chats WHERE COALESCE(owner_userid, '') <> ''", "group_chats"),
            (
                "SELECT DISTINCT owner_userid AS user_id FROM automation_group_ops_plans WHERE COALESCE(owner_userid, '') <> ''",
                "group_ops_plan_owner",
            ),
            (
                "SELECT DISTINCT owner_staff_id AS user_id FROM automation_channel WHERE COALESCE(owner_staff_id, '') <> ''",
                "channel_owner_field",
            ),
            (
                "SELECT DISTINCT follow_user_userid AS user_id FROM wecom_external_contact_identity_map WHERE COALESCE(follow_user_userid, '') <> ''",
                "external_contact_follow_user",
            ),
            (
                "SELECT DISTINCT user_id AS user_id FROM wecom_external_contact_follow_users WHERE COALESCE(user_id, '') <> ''",
                "external_contact_follow_user",
            ),
        ]
        for sql, source in business_sources:
            rows.extend(_fetch_rows(conn, sql, source=source, priority=80))
        return rows


def search_operation_members(
    *,
    q: str = "",
    scope: str = "common",
    page: int = 1,
    page_size: int = 30,
    include_inactive: bool = False,
) -> dict[str, Any]:
    return operation_members_payload(
        list_operation_member_rows(scope=scope),
        q=clean_text(q),
        scope=normalize_scope(scope),
        page=clamp_page(page),
        page_size=clamp_page_size(page_size),
        include_inactive=include_inactive,
    )


@router.get("/api/admin/common/operation-members")
def api_operation_members(
    q: str = "",
    scope: str = "common",
    page: int = Query(1),
    page_size: int = Query(30),
    include_inactive: str = "0",
) -> dict[str, Any]:
    return search_operation_members(
        q=q,
        scope=scope,
        page=page,
        page_size=page_size,
        include_inactive=bool_from_query(include_inactive),
    )


@router.post("/api/admin/common/operation-members/sync", name="api_operation_members_sync")
def api_operation_members_sync(request: Request):
    try:
        return SyncOperationMembersFromWeComCommand().execute(operator=_operator_from_request(request))
    except WeComOperationMembersClientError as exc:
        status_code = 400 if exc.error_code == "wecom_operation_members_config_missing" else 502
        return JSONResponse(
            {
                "ok": False,
                "error": exc.error_code,
                "message": str(exc),
                "stage": exc.stage,
                "real_external_call_executed": exc.stage != "config",
            },
            status_code=status_code,
        )
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": "operation_member_sync_failed",
                "message": str(exc),
                "real_external_call_executed": True,
            },
            status_code=502,
        )
