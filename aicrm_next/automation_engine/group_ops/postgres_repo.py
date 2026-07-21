# ruff: noqa: F401
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from aicrm_next.shared.errors import ContractError, NotFoundError
from aicrm_next.shared.repository_provider import RepositoryProviderError

from .domain import (
    clean_text,
    derive_node_scheduled_time,
    generate_webhook_key,
    normalize_group_admin_userids,
    normalize_plan_payload,
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return deepcopy(default)
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return deepcopy(default)


def _json_list(value: Any) -> str:
    return json.dumps(normalize_group_admin_userids(value), ensure_ascii=False, sort_keys=True)


def _legacy_group_chat_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    raw_payload = _json_loads(row.get("raw_payload"), {})
    group_chat = raw_payload.get("group_chat") if isinstance(raw_payload, dict) and isinstance(raw_payload.get("group_chat"), dict) else raw_payload
    if not isinstance(group_chat, dict):
        group_chat = {}
    members = group_chat.get("member_list") if isinstance(group_chat.get("member_list"), list) else []
    internal_count = 0
    external_count = 0
    for member in members:
        if not isinstance(member, dict):
            continue
        try:
            member_type = int(member.get("type") or 0)
        except (TypeError, ValueError):
            member_type = 0
        if member_type == 1 or (member.get("userid") and not member.get("unionid")):
            internal_count += 1
        else:
            external_count += 1
    if not members:
        external_count = _int(row.get("member_count"))
    owner_userid = clean_text(group_chat.get("owner") or group_chat.get("owner_userid") or row.get("owner_userid"))
    return {
        "chat_id": clean_text(group_chat.get("chat_id") or row.get("chat_id")),
        "group_name": clean_text(group_chat.get("name") or group_chat.get("group_name") or row.get("group_name")),
        "owner_userid": owner_userid,
        "owner_name": clean_text(group_chat.get("owner_name") or owner_userid),
        "admin_userids": normalize_group_admin_userids(group_chat.get("admin_list") or group_chat.get("admin_userids")),
        "internal_member_count": internal_count,
        "external_member_count": external_count,
        "status": clean_text(row.get("status") or "active"),
    }


def _as_mapping(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    return dict(mapping or row)


def _iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


from .postgres_repo_mapping import GroupOpsPostgresMappingMixin

class PostgresGroupOpsRepository(GroupOpsPostgresMappingMixin):
    source_status = "postgres_group_ops_repository"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_plans(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        keyword = clean_text(filters.get("keyword")).lower()
        plan_type = clean_text(filters.get("plan_type")).lower()
        operator_member_id = clean_text(filters.get("operator_member_id") or filters.get("operatorMemberId"))
        status = clean_text(filters.get("status")).lower()
        clauses = ["(archived_at IS NULL)"]
        params: dict[str, Any] = {
            "keyword": f"%{keyword}%",
            "plan_type": plan_type,
            "operator_member_id": operator_member_id,
            "status": status,
            "limit": max(1, _int(filters.get("limit")) or 50),
            "offset": max(0, _int(filters.get("offset"))),
        }
        if keyword:
            clauses.append("(LOWER(plan_name) LIKE :keyword OR LOWER(plan_code) LIKE :keyword OR LOWER(owner_userid) LIKE :keyword)")
        if plan_type:
            clauses.append("plan_type = :plan_type")
        if operator_member_id:
            clauses.append("owner_userid = :operator_member_id")
        if status:
            clauses.append("status = :status")
        where = f"WHERE {' AND '.join(clauses)}"
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT *
                        FROM automation_group_ops_plans
                        {where}
                        ORDER BY id ASC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    params,
                ).fetchall()
                total = conn.execute(
                    text(f"SELECT COUNT(*) AS total FROM automation_group_ops_plans {where}"),
                    {key: value for key, value in params.items() if key not in {"limit", "offset"}},
                ).scalar_one()
                return [self._row_to_plan(conn, _as_mapping(row) or {}) for row in rows], int(total or 0)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def get_plan(self, plan_id: int) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                return self._get_plan_sql(conn, int(plan_id))
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def get_plan_by_webhook_key(self, webhook_key: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT *
                        FROM automation_group_ops_plans
                        WHERE webhook_key = :webhook_key
                          AND archived_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {"webhook_key": clean_text(webhook_key)},
                ).fetchone()
                return self._row_to_plan(conn, _as_mapping(row)) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def create_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_plan_payload(payload)
        webhook_key = ""
        if normalized["plan_type"] == "webhook":
            webhook_key = generate_webhook_key(normalized["plan_name"])
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_plans (
                            plan_code, plan_name, plan_type, owner_userid, status,
                            webhook_key, created_by, updated_by
                        )
                        VALUES (
                            :plan_code, :plan_name, :plan_type, :owner_userid, :status,
                            :webhook_key, :created_by, :updated_by
                        )
                        RETURNING id
                        """
                    ),
                    {
                        **normalized,
                        "webhook_key": webhook_key,
                    },
                ).fetchone()
                plan_id = int((_as_mapping(row) or {}).get("id") or 0)
                if not normalized["plan_code"]:
                    conn.execute(
                        text("UPDATE automation_group_ops_plans SET plan_code = :plan_code WHERE id = :plan_id"),
                        {"plan_code": f"group_plan_{plan_id:03d}", "plan_id": plan_id},
                    )
                self._update_plan_extra_fields(conn, plan_id, normalized)
                return self._get_plan_sql(conn, plan_id) or {}
        except IntegrityError as exc:
            raise ContractError("group ops plan code or webhook key already exists") from exc
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def update_plan(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._engine.begin() as conn:
                current = self._get_plan_sql(conn, int(plan_id))
                if not current:
                    raise NotFoundError("group ops plan not found")
                normalized = normalize_plan_payload(payload, existing=current)
                conn.execute(
                    text(
                        """
                        UPDATE automation_group_ops_plans
                        SET plan_code = :plan_code,
                            plan_name = :plan_name,
                            plan_type = :plan_type,
                            owner_userid = :owner_userid,
                            status = :status,
                            updated_by = :updated_by,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :plan_id
                        """
                    ),
                    {**normalized, "plan_code": normalized["plan_code"] or current["plan_code"], "plan_id": int(plan_id)},
                )
                self._update_plan_extra_fields(conn, int(plan_id), normalized)
                return self._get_plan_sql(conn, int(plan_id)) or {}
        except NotFoundError:
            raise
        except IntegrityError as exc:
            raise ContractError("group ops plan code already exists") from exc
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_bound_groups(self, plan_id: int) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT *
                        FROM automation_group_ops_plan_groups
                        WHERE plan_id = :plan_id
                          AND status = 'active'
                        ORDER BY id ASC
                        """
                    ),
                    {"plan_id": int(plan_id)},
                ).fetchall()
                return [self._row_to_plan_group(_as_mapping(row) or {}) for row in rows]
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def bind_group(self, plan_id: int, group: dict[str, Any]) -> dict[str, Any]:
        chat_id = clean_text(group.get("chat_id"))
        try:
            with self._engine.begin() as conn:
                existing = conn.execute(
                    text(
                        """
                        SELECT id
                        FROM automation_group_ops_plan_groups
                        WHERE plan_id = :plan_id AND chat_id = :chat_id
                        LIMIT 1
                        """
                    ),
                    {"plan_id": int(plan_id), "chat_id": chat_id},
                ).fetchone()
                if existing:
                    binding_id = int((_as_mapping(existing) or {}).get("id") or 0)
                    conn.execute(
                        text(
                            """
                            UPDATE automation_group_ops_plan_groups
                            SET group_name_snapshot = :group_name,
                                owner_userid_snapshot = :owner_userid,
                                internal_member_count_snapshot = :internal_count,
                                external_member_count_snapshot = :external_count,
                                status = 'active',
                                removed_at = NULL
                            WHERE id = :binding_id
                            """
                        ),
                        self._group_binding_params(binding_id=binding_id, group=group),
                    )
                else:
                    row = conn.execute(
                        text(
                            """
                            INSERT INTO automation_group_ops_plan_groups (
                                plan_id, chat_id, group_name_snapshot, owner_userid_snapshot,
                                internal_member_count_snapshot, external_member_count_snapshot, status
                            )
                            VALUES (
                                :plan_id, :chat_id, :group_name, :owner_userid,
                                :internal_count, :external_count, 'active'
                            )
                            RETURNING id
                            """
                        ),
                        {"plan_id": int(plan_id), "chat_id": chat_id, **self._group_binding_params(group=group)},
                    ).fetchone()
                    binding_id = int((_as_mapping(row) or {}).get("id") or 0)
                conn.execute(
                    text(
                        "UPDATE automation_group_ops_plans "
                        "SET updated_at = CURRENT_TIMESTAMP WHERE id = :plan_id"
                    ),
                    {"plan_id": int(plan_id)},
                )
                return self._get_plan_group_sql(conn, binding_id) or {}
        except IntegrityError as exc:
            raise ContractError("group is already bound to this plan") from exc
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def remove_group(self, plan_id: int, chat_id: str) -> bool:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    text(
                        """
                        UPDATE automation_group_ops_plan_groups
                        SET status = 'removed', removed_at = CURRENT_TIMESTAMP
                        WHERE plan_id = :plan_id
                          AND chat_id = :chat_id
                          AND status = 'active'
                        """
                    ),
                    {"plan_id": int(plan_id), "chat_id": clean_text(chat_id)},
                )
                if result.rowcount:
                    conn.execute(
                        text(
                            "UPDATE automation_group_ops_plans "
                            "SET updated_at = CURRENT_TIMESTAMP WHERE id = :plan_id"
                        ),
                        {"plan_id": int(plan_id)},
                    )
                return bool(result.rowcount)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_group_assets(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        keyword = clean_text(filters.get("keyword")).lower()
        owner_userid = clean_text(filters.get("owner_userid"))
        plan_id = _int(filters.get("plan_id"))
        bind_status = clean_text(filters.get("bind_status")).lower()
        clauses = ["g.status = 'active'"]
        join_extra = "AND pg.plan_id = :filter_plan_id" if plan_id else ""
        params: dict[str, Any] = {
            "keyword": f"%{keyword}%",
            "owner_userid": owner_userid,
            "filter_plan_id": plan_id,
            "limit": max(1, _int(filters.get("limit")) or 50),
            "offset": max(0, _int(filters.get("offset"))),
        }
        if keyword:
            clauses.append("(LOWER(g.group_name) LIKE :keyword OR LOWER(g.chat_id) LIKE :keyword)")
        if owner_userid:
            clauses.append("(g.owner_userid = :owner_userid OR g.admin_userids LIKE :admin_userid_pattern)")
            params["admin_userid_pattern"] = f'%"{owner_userid}"%'
        if bind_status == "bound":
            clauses.append("p.id IS NOT NULL")
        elif bind_status == "unbound":
            clauses.append("p.id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}"
        base = f"""
            FROM wecom_group_chat_snapshots g
            LEFT JOIN automation_group_ops_plan_groups pg
              ON pg.chat_id = g.chat_id
             AND pg.status = 'active'
             {join_extra}
            LEFT JOIN automation_group_ops_plans p
              ON p.id = pg.plan_id
             AND p.archived_at IS NULL
        """
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT
                            g.chat_id, g.group_name, g.owner_userid, g.owner_name,
                            g.admin_userids, g.internal_member_count, g.external_member_count,
                            g.synced_at, g.status,
                            p.id AS bound_plan_id, p.plan_name AS plan_name
                        {base}
                        {where}
                        ORDER BY g.group_name ASC, g.chat_id ASC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    params,
                ).fetchall()
                total = conn.execute(
                    text(f"SELECT COUNT(*) AS total {base} {where}"),
                    {key: value for key, value in params.items() if key not in {"limit", "offset"}},
                ).scalar_one()
                return [self._row_to_group_asset(_as_mapping(row) or {}) for row in rows], int(total or 0)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def get_group_asset(self, chat_id: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT *
                        FROM wecom_group_chat_snapshots
                        WHERE chat_id = :chat_id
                          AND status = 'active'
                        LIMIT 1
                        """
                    ),
                    {"chat_id": clean_text(chat_id)},
                ).fetchone()
                return self._row_to_group_asset(_as_mapping(row)) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def upsert_group_snapshots(self, groups: list[dict[str, Any]]) -> int:
        count = 0
        for group in groups:
            if not clean_text((group or {}).get("chat_id")):
                continue
            self.upsert_group_asset(group)
            count += 1
        return count

    def upsert_group_asset(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], str]:
        chat_id = clean_text(snapshot.get("chat_id"))
        if not chat_id:
            raise ContractError("chat_id is required")
        try:
            with self._engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT chat_id FROM wecom_group_chat_snapshots WHERE chat_id = :chat_id LIMIT 1"),
                    {"chat_id": chat_id},
                ).fetchone()
                params = {
                    "chat_id": chat_id,
                    "group_name": clean_text(snapshot.get("group_name") or chat_id),
                    "owner_userid": clean_text(snapshot.get("owner_userid")),
                    "owner_name": clean_text(snapshot.get("owner_name") or snapshot.get("owner_userid")),
                    "admin_userids": _json_list(snapshot.get("admin_userids") or snapshot.get("admin_list")),
                    "internal_member_count": _int(snapshot.get("internal_member_count")),
                    "external_member_count": _int(snapshot.get("external_member_count")),
                    "status": clean_text(snapshot.get("status") or "active"),
                }
                conn.execute(
                    text(
                        """
                        INSERT INTO wecom_group_chat_snapshots (
                            chat_id, group_name, owner_userid, owner_name, admin_userids,
                            internal_member_count, external_member_count,
                            synced_at, status
                        )
                        VALUES (
                            :chat_id, :group_name, :owner_userid, :owner_name, :admin_userids,
                            :internal_member_count, :external_member_count,
                            CURRENT_TIMESTAMP, :status
                        )
                        ON CONFLICT(chat_id) DO UPDATE SET
                            group_name = excluded.group_name,
                            owner_userid = excluded.owner_userid,
                            owner_name = excluded.owner_name,
                            admin_userids = excluded.admin_userids,
                            internal_member_count = excluded.internal_member_count,
                            external_member_count = excluded.external_member_count,
                            synced_at = CURRENT_TIMESTAMP,
                            status = excluded.status
                        """
                    ),
                    params,
                )
                saved = conn.execute(
                    text("SELECT * FROM wecom_group_chat_snapshots WHERE chat_id = :chat_id LIMIT 1"),
                    {"chat_id": chat_id},
                ).fetchone()
                return self._row_to_group_asset(_as_mapping(saved) or {}), "updated" if existing else "created"
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def mark_owner_group_assets_inactive_except(self, owner_userid: str, active_chat_ids: list[str]) -> list[str]:
        owner = clean_text(owner_userid)
        active = {clean_text(chat_id) for chat_id in active_chat_ids if clean_text(chat_id)}
        try:
            with self._engine.begin() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT chat_id
                        FROM wecom_group_chat_snapshots
                        WHERE owner_userid = :owner_userid
                          AND status = 'active'
                        """
                    ),
                    {"owner_userid": owner},
                ).fetchall()
                inactive = [clean_text(row.chat_id) for row in rows if clean_text(row.chat_id) and clean_text(row.chat_id) not in active]
                for chat_id in inactive:
                    conn.execute(
                        text(
                            """
                            UPDATE wecom_group_chat_snapshots
                            SET status = 'inactive', synced_at = CURRENT_TIMESTAMP
                            WHERE chat_id = :chat_id AND status = 'active'
                            """
                        ),
                        {"chat_id": chat_id},
                    )
                return inactive
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_admin_group_assets(self, owner_userid: str) -> list[dict[str, Any]]:
        owner = clean_text(owner_userid)
        if not owner:
            return []
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT chat_id, group_name, owner_userid, member_count, status, raw_payload
                        FROM group_chats
                        WHERE status = 'active'
                          AND COALESCE(raw_payload, '') <> ''
                        ORDER BY group_name ASC, chat_id ASC
                        """
                    )
                ).fetchall()
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc
        groups: list[dict[str, Any]] = []
        for row in rows:
            group = _legacy_group_chat_snapshot(_as_mapping(row) or {})
            if not group.get("chat_id") or group.get("owner_userid") == owner:
                continue
            if owner in normalize_group_admin_userids(group.get("admin_userids")):
                groups.append(group)
        return groups

    def list_admin_candidate_group_assets(self, owner_userid: str, *, limit: int = 100) -> list[dict[str, Any]]:
        owner = clean_text(owner_userid)
        if not owner:
            return []
        max_items = max(1, min(_int(limit) or 100, 200))
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT chat_id, group_name, owner_userid, member_count, status, raw_payload, updated_at
                        FROM group_chats
                        WHERE status = 'active'
                          AND COALESCE(owner_userid, '') <> :owner_userid
                          AND COALESCE(raw_payload, '') <> ''
                        ORDER BY
                          CASE
                            WHEN raw_payload LIKE :admin_userid_pattern THEN 0
                            WHEN raw_payload LIKE :member_userid_pattern THEN 1
                            ELSE 2
                          END,
                          updated_at ASC NULLS FIRST,
                          group_name ASC,
                          chat_id ASC
                        LIMIT :limit
                        """
                    ),
                    {
                        "owner_userid": owner,
                        "admin_userid_pattern": f'%"userid": "{owner}"%',
                        "member_userid_pattern": f'%"userid": "{owner}"%',
                        "limit": max_items,
                    },
                ).fetchall()
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc
        return [group for row in rows if (group := _legacy_group_chat_snapshot(_as_mapping(row) or {})).get("chat_id")]

    def list_owners(self) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT
                            owner_userid AS userid,
                            COALESCE(NULLIF(MAX(owner_name), ''), owner_userid) AS name,
                            COUNT(*) AS group_count
                        FROM wecom_group_chat_snapshots
                        WHERE status = 'active'
                          AND owner_userid <> ''
                        GROUP BY owner_userid
                        ORDER BY owner_userid ASC
                        """
                    )
                ).fetchall()
                owners = {}
                for row in rows:
                    item = _as_mapping(row) or {}
                    userid = clean_text(item.get("userid"))
                    if userid:
                        owners[userid] = {
                            "userid": userid,
                            "name": clean_text(item.get("name") or userid),
                            "group_count": _int(item.get("group_count")),
                        }
                admin_rows = conn.execute(
                    text(
                        """
                        SELECT admin_userids
                        FROM wecom_group_chat_snapshots
                        WHERE status = 'active'
                          AND admin_userids <> '[]'
                        """
                    )
                ).fetchall()
                for row in admin_rows:
                    for admin_userid in normalize_group_admin_userids((_as_mapping(row) or {}).get("admin_userids")):
                        owners.setdefault(admin_userid, {"userid": admin_userid, "name": admin_userid, "group_count": 0})
                return [owners[userid] for userid in sorted(owners)]
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_nodes(self, plan_id: int) -> list[dict[str, Any]]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT *
                        FROM automation_group_ops_plan_nodes
                        WHERE plan_id = :plan_id
                          AND status <> 'deleted'
                        ORDER BY day_index ASC, sort_order ASC, id ASC
                        """
                    ),
                    {"plan_id": int(plan_id)},
                ).fetchall()
                return [self._row_to_node(_as_mapping(row) or {}) for row in rows]
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def create_node(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_plan_nodes (
                            plan_id, day_index, trigger_time_label, action_title,
                            text_content, attachments_json, content_package_json, sort_order, status
                        )
                        VALUES (
                            :plan_id, :day_index, :trigger_time_label, :action_title,
                            :text_content, :attachments_json, :content_package_json, :sort_order, :status
                        )
                        RETURNING id
                        """
                    ),
                    {"plan_id": int(plan_id), **self._node_params(payload)},
                ).fetchone()
                node_id = int((_as_mapping(row) or {}).get("id") or 0)
                return self._get_node_sql(conn, node_id) or {}
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def update_node(self, plan_id: int, node_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    text(
                        """
                        UPDATE automation_group_ops_plan_nodes
                        SET day_index = :day_index,
                            trigger_time_label = :trigger_time_label,
                            action_title = :action_title,
                            text_content = :text_content,
                            attachments_json = :attachments_json,
                            content_package_json = :content_package_json,
                            sort_order = :sort_order,
                            status = :status,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :node_id
                          AND plan_id = :plan_id
                        """
                    ),
                    {"node_id": int(node_id), "plan_id": int(plan_id), **self._node_params(payload)},
                )
                if not result.rowcount:
                    raise NotFoundError("group ops node not found")
                return self._get_node_sql(conn, int(node_id)) or {}
        except NotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def delete_node(self, plan_id: int, node_id: int) -> bool:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    text(
                        """
                        UPDATE automation_group_ops_plan_nodes
                        SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
                        WHERE id = :node_id
                          AND plan_id = :plan_id
                          AND status <> 'deleted'
                        """
                    ),
                    {"node_id": int(node_id), "plan_id": int(plan_id)},
                )
                return bool(result.rowcount)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def find_webhook_event(self, plan_id: int, idempotency_key: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT *
                        FROM automation_group_ops_webhook_events
                        WHERE plan_id = :plan_id
                          AND idempotency_key = :idempotency_key
                        LIMIT 1
                        """
                    ),
                    {"plan_id": int(plan_id), "idempotency_key": clean_text(idempotency_key)},
                ).fetchone()
                return self._row_to_event(_as_mapping(row)) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def create_webhook_event(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_webhook_events (
                            plan_id, idempotency_key, request_payload,
                            normalized_content_payload, scheduled_at, status,
                            broadcast_job_ids_json, error_message
                        )
                        VALUES (
                            :plan_id, :idempotency_key, :request_payload,
                            :normalized_content_payload, :scheduled_at, :status,
                            :broadcast_job_ids_json, :error_message
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "plan_id": int(plan_id),
                        "idempotency_key": clean_text(payload.get("idempotency_key")),
                        "request_payload": _json_dumps(payload.get("request_payload") or {}),
                        "normalized_content_payload": _json_dumps(payload.get("normalized_content_payload") or {}),
                        "scheduled_at": clean_text(payload.get("scheduled_at")) or None,
                        "status": clean_text(payload.get("status") or "accepted"),
                        "broadcast_job_ids_json": _json_dumps(list(payload.get("broadcast_job_ids") or [])),
                        "error_message": clean_text(payload.get("error_message")),
                    },
                ).fetchone()
                event_id = int((_as_mapping(row) or {}).get("id") or 0)
                return self._get_event_sql(conn, event_id) or {}
        except IntegrityError as exc:
            raise ContractError("webhook idempotency_key already exists for this plan") from exc
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def update_webhook_event(self, event_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if "status" in payload:
            updates["status"] = clean_text(payload.get("status"))
        if "error_message" in payload:
            updates["error_message"] = clean_text(payload.get("error_message"))
        if "broadcast_job_ids" in payload:
            updates["broadcast_job_ids_json"] = _json_dumps(list(payload.get("broadcast_job_ids") or []))
        if not updates:
            current = self._get_event_public(int(event_id))
            if not current:
                raise NotFoundError("group ops webhook event not found")
            return current
        set_clause = ", ".join(f"{key} = :{key}" for key in updates)
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    text(f"UPDATE automation_group_ops_webhook_events SET {set_clause} WHERE id = :event_id"),
                    {**updates, "event_id": int(event_id)},
                )
                if not result.rowcount:
                    raise NotFoundError("group ops webhook event not found")
                return self._get_event_sql(conn, int(event_id)) or {}
        except NotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def archive_plan(self, plan_id: int, *, operator: str = "system") -> dict[str, Any]:
        try:
            with self._engine.begin() as conn:
                result = conn.execute(
                    text(
                        """
                        UPDATE automation_group_ops_plans
                        SET status = 'disabled',
                            updated_by = :operator,
                            updated_at = CURRENT_TIMESTAMP,
                            archived_at = CURRENT_TIMESTAMP
                        WHERE id = :plan_id AND archived_at IS NULL
                        """
                    ),
                    {"plan_id": int(plan_id), "operator": clean_text(operator)},
                )
                if not result.rowcount:
                    raise NotFoundError("group ops plan not found")
                return self._get_plan_sql(conn, int(plan_id)) or {}
        except NotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def replace_plan_scopes(self, plan_id: int, *, scope_type: str, scope_ref_ids: list[str]) -> list[dict[str, Any]]:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM automation_group_ops_plan_scope WHERE plan_id = :plan_id AND scope_type = :scope_type"),
                    {"plan_id": int(plan_id), "scope_type": clean_text(scope_type)},
                )
                for ref_id in scope_ref_ids:
                    conn.execute(
                        text(
                            """
                            INSERT INTO automation_group_ops_plan_scope (plan_id, scope_type, scope_ref_id)
                            VALUES (:plan_id, :scope_type, :scope_ref_id)
                            """
                        ),
                        {"plan_id": int(plan_id), "scope_type": clean_text(scope_type), "scope_ref_id": clean_text(ref_id)},
                    )
            return self.list_plan_scopes(int(plan_id), scope_type=scope_type)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_plan_scopes(self, plan_id: int, scope_type: str = "") -> list[dict[str, Any]]:
        clauses = ["plan_id = :plan_id"]
        params = {"plan_id": int(plan_id), "scope_type": clean_text(scope_type)}
        if clean_text(scope_type):
            clauses.append("scope_type = :scope_type")
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT id, plan_id, scope_type, scope_ref_id, created_at
                        FROM automation_group_ops_plan_scope
                        WHERE {" AND ".join(clauses)}
                        ORDER BY id ASC
                        """
                    ),
                    params,
                ).fetchall()
                return [
                    {
                        "id": _int((_as_mapping(row) or {}).get("id")),
                        "plan_id": _int((_as_mapping(row) or {}).get("plan_id")),
                        "scope_type": clean_text((_as_mapping(row) or {}).get("scope_type")),
                        "scope_ref_id": clean_text((_as_mapping(row) or {}).get("scope_ref_id")),
                        "created_at": _iso((_as_mapping(row) or {}).get("created_at")),
                    }
                    for row in rows
                ]
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_plan_members(self, plan_id: int, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        clauses = ["plan_id = :plan_id", "status <> 'removed'"]
        params = {
            "plan_id": int(plan_id),
            "layer_key": clean_text(filters.get("layer_key")),
            "source_type": clean_text(filters.get("source_type")),
            "keyword": f"%{clean_text(filters.get('keyword')).lower()}%",
            "limit": max(1, _int(filters.get("limit")) or 50),
            "offset": max(0, _int(filters.get("offset"))),
        }
        if params["layer_key"]:
            clauses.append("layer_key = :layer_key")
        if params["source_type"]:
            clauses.append("source_type = :source_type")
        if clean_text(filters.get("keyword")):
            clauses.append("(LOWER(user_id) LIKE :keyword OR LOWER(external_user_id) LIKE :keyword OR LOWER(group_id) LIKE :keyword)")
        where = " AND ".join(clauses)
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(f"SELECT * FROM automation_group_ops_plan_member WHERE {where} ORDER BY id ASC LIMIT :limit OFFSET :offset"), params
                ).fetchall()
                total = conn.execute(
                    text(f"SELECT COUNT(*) FROM automation_group_ops_plan_member WHERE {where}"),
                    {k: v for k, v in params.items() if k not in {"limit", "offset"}},
                ).scalar_one()
                return [self._row_to_plan_member(_as_mapping(row) or {}) for row in rows], int(total or 0)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def upsert_plan_members(self, plan_id: int, members: list[dict[str, Any]], *, source_type: str, source_ref_id: str = "") -> int:
        count = 0
        try:
            with self._engine.begin() as conn:
                for member in members:
                    external_user_id = clean_text(member.get("external_user_id") or member.get("external_userid") or member.get("externalUserId"))
                    user_id = clean_text(member.get("user_id") or member.get("userId"))
                    group_id = clean_text(member.get("group_id") or member.get("groupId") or member.get("chat_id"))
                    if not (external_user_id or user_id or group_id):
                        continue
                    conn.execute(
                        text(
                            """
                            INSERT INTO automation_group_ops_plan_member (
                                plan_id, member_key, user_id, external_user_id, group_id, layer_key,
                                source_type, source_ref_id, status, joined_at, updated_at
                            )
                            VALUES (
                                :plan_id, :member_key, :user_id, :external_user_id, :group_id, :layer_key,
                                :source_type, :source_ref_id, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                            )
                            ON CONFLICT (plan_id, member_key)
                            DO UPDATE SET
                                layer_key = excluded.layer_key,
                                source_type = excluded.source_type,
                                source_ref_id = excluded.source_ref_id,
                                status = 'active',
                                updated_at = CURRENT_TIMESTAMP
                            """
                        ),
                        {
                            "plan_id": int(plan_id),
                            "member_key": external_user_id or user_id or group_id,
                            "user_id": user_id,
                            "external_user_id": external_user_id,
                            "group_id": group_id,
                            "layer_key": clean_text(member.get("layer_key") or member.get("layerKey")),
                            "source_type": clean_text(source_type),
                            "source_ref_id": clean_text(source_ref_id or member.get("source_ref_id") or member.get("sourceRefId")),
                        },
                    )
                    count += 1
            return count
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def get_segmentation(self, plan_id: int) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT * FROM automation_group_ops_plan_segmentation WHERE plan_id = :plan_id LIMIT 1"), {"plan_id": int(plan_id)}
                ).fetchone()
                return self._row_to_segmentation(_as_mapping(row)) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def save_segmentation(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        params = {
            "plan_id": int(plan_id),
            "segmentation_type": clean_text(payload.get("segmentation_type") or payload.get("segmentationType") or "preset_rule"),
            "rule_key": clean_text(payload.get("rule_key") or payload.get("ruleKey")),
            "rule_version": _int(payload.get("rule_version") or payload.get("ruleVersion")),
            "params_json": _json_dumps(payload.get("params") or {}),
            "layer_actions_json": _json_dumps(payload.get("layer_actions") or payload.get("layerActions") or {}),
        }
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_plan_segmentation (
                            plan_id, segmentation_type, rule_key, rule_version, params_json, layer_actions_json, updated_at
                        )
                        VALUES (:plan_id, :segmentation_type, :rule_key, :rule_version, :params_json, :layer_actions_json, CURRENT_TIMESTAMP)
                        ON CONFLICT (plan_id) DO UPDATE SET
                            segmentation_type = excluded.segmentation_type,
                            rule_key = excluded.rule_key,
                            rule_version = excluded.rule_version,
                            params_json = excluded.params_json,
                            layer_actions_json = excluded.layer_actions_json,
                            updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    params,
                )
            return self.get_segmentation(int(plan_id)) or {}
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_audience_rules(self, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(text("SELECT * FROM audience_rule ORDER BY rule_key ASC")).fetchall()
                return [self._row_to_audience_rule(_as_mapping(row) or {}) for row in rows], len(rows)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def create_audience_rule(self, payload: dict[str, Any]) -> dict[str, Any]:
        rule_key = clean_text(payload.get("rule_key") or payload.get("ruleKey"))
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO audience_rule (rule_key, display_name, description, rule_type, owner, status, updated_at)
                        VALUES (:rule_key, :display_name, :description, :rule_type, :owner, :status, CURRENT_TIMESTAMP)
                        ON CONFLICT (rule_key) DO UPDATE SET
                            display_name = excluded.display_name,
                            description = excluded.description,
                            rule_type = excluded.rule_type,
                            owner = excluded.owner,
                            status = excluded.status,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING *
                        """
                    ),
                    {
                        "rule_key": rule_key,
                        "display_name": clean_text(payload.get("display_name") or payload.get("displayName") or rule_key),
                        "description": clean_text(payload.get("description")),
                        "rule_type": clean_text(payload.get("rule_type") or payload.get("ruleType") or "module"),
                        "owner": clean_text(payload.get("owner") or "growth_platform"),
                        "status": clean_text(payload.get("status") or "active"),
                    },
                ).fetchone()
                return self._row_to_audience_rule(_as_mapping(row) or {})
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def get_audience_rule(self, rule_key: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(text("SELECT * FROM audience_rule WHERE rule_key = :rule_key LIMIT 1"), {"rule_key": clean_text(rule_key)}).fetchone()
                return self._row_to_audience_rule(_as_mapping(row)) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def create_audience_rule_version(self, rule_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        rule = self.get_audience_rule(rule_key)
        if not rule:
            raise NotFoundError("audience rule not found")
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO audience_rule_version (
                            rule_id, version, executor_type, code_or_sql,
                            params_schema, output_schema, refresh_policy, status, published_at
                        )
                        VALUES (
                            :rule_id, :version, :executor_type, :code_or_sql,
                            :params_schema, :output_schema, :refresh_policy, :status, CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (rule_id, version) DO UPDATE SET
                            executor_type = excluded.executor_type,
                            code_or_sql = excluded.code_or_sql,
                            params_schema = excluded.params_schema,
                            output_schema = excluded.output_schema,
                            refresh_policy = excluded.refresh_policy,
                            status = excluded.status
                        RETURNING *
                        """
                    ),
                    {
                        "rule_id": int(rule["id"]),
                        "version": int(payload.get("version") or 0),
                        "executor_type": clean_text(payload.get("executor_type") or payload.get("executorType") or "module"),
                        "code_or_sql": clean_text(payload.get("code_or_sql") or payload.get("codeOrSql")),
                        "params_schema": _json_dumps(payload.get("params_schema") or payload.get("paramsSchema") or {}),
                        "output_schema": _json_dumps(payload.get("output_schema") or payload.get("outputSchema") or {}),
                        "refresh_policy": _json_dumps(payload.get("refresh_policy") or payload.get("refreshPolicy") or {}),
                        "status": clean_text(payload.get("status") or "active"),
                    },
                ).fetchone()
                item = self._row_to_audience_rule_version(_as_mapping(row) or {})
                item["rule_key"] = clean_text(rule_key)
                return item
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def get_audience_rule_version(self, rule_key: str, version: int) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT v.*, r.rule_key
                        FROM audience_rule_version v
                        JOIN audience_rule r ON r.id = v.rule_id
                        WHERE r.rule_key = :rule_key AND v.version = :version
                        LIMIT 1
                        """
                    ),
                    {"rule_key": clean_text(rule_key), "version": int(version)},
                ).fetchone()
                return self._row_to_audience_rule_version(_as_mapping(row)) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def replace_audience_rule_results(self, rule_key: str, version: int, plan_id: int, results: list[dict[str, Any]]) -> int:
        rule = self.get_audience_rule(rule_key)
        if not rule:
            raise NotFoundError("audience rule not found")
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM audience_rule_result WHERE rule_id = :rule_id AND rule_version = :version AND plan_id = :plan_id"),
                    {"rule_id": int(rule["id"]), "version": int(version), "plan_id": int(plan_id)},
                )
                for item in results:
                    conn.execute(
                        text(
                            """
                            INSERT INTO audience_rule_result (
                                rule_id, rule_version, plan_id, user_id, external_user_id,
                                layer_key, score, reason, evidence_json, computed_at
                            )
                            VALUES (
                                :rule_id, :rule_version, :plan_id, :user_id, :external_user_id,
                                :layer_key, :score, :reason, :evidence_json, CURRENT_TIMESTAMP
                            )
                            """
                        ),
                        {
                            "rule_id": int(rule["id"]),
                            "rule_version": int(version),
                            "plan_id": int(plan_id),
                            "user_id": clean_text(item.get("user_id") or item.get("userId")),
                            "external_user_id": clean_text(item.get("external_user_id") or item.get("external_userid") or item.get("externalUserId")),
                            "layer_key": clean_text(item.get("layer_key") or item.get("layerKey")),
                            "score": float(item.get("score") or 0),
                            "reason": clean_text(item.get("reason")),
                            "evidence_json": _json_dumps(item.get("evidence_json") or item.get("evidence") or {}),
                        },
                    )
                return len(results)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_audience_rule_results(self, rule_key: str, version: int, plan_id: int, filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        filters = filters or {}
        layers = [clean_text(item) for item in list(filters.get("layers") or []) if clean_text(item)]
        params: dict[str, Any] = {
            "rule_key": clean_text(rule_key),
            "version": int(version),
            "plan_id": int(plan_id),
            "limit": max(1, _int(filters.get("limit")) or 50),
            "offset": max(0, _int(filters.get("offset"))),
        }
        layer_clause = "AND rr.layer_key = ANY(:layers)" if layers else ""
        if layers:
            params["layers"] = layers
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT rr.*, r.rule_key
                        FROM audience_rule_result rr
                        JOIN audience_rule r ON r.id = rr.rule_id
                        WHERE r.rule_key = :rule_key
                          AND rr.rule_version = :version
                          AND rr.plan_id = :plan_id
                          {layer_clause}
                        ORDER BY rr.id ASC
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    params,
                ).fetchall()
                total = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM audience_rule_result rr
                        JOIN audience_rule r ON r.id = rr.rule_id
                        WHERE r.rule_key = :rule_key
                          AND rr.rule_version = :version
                          AND rr.plan_id = :plan_id
                          {layer_clause}
                        """
                    ),
                    {k: v for k, v in params.items() if k not in {"limit", "offset"}},
                ).scalar_one()
                return [self._row_to_rule_result(_as_mapping(row) or {}) for row in rows], int(total or 0)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def create_trigger_event(self, plan_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_trigger_event (
                            plan_id, endpoint_key, event_name, source, idempotency_key,
                            payload_json, status, received_at
                        )
                        VALUES (
                            :plan_id, :endpoint_key, :event_name, :source, :idempotency_key,
                            :payload_json, :status, CURRENT_TIMESTAMP
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "plan_id": int(plan_id),
                        "endpoint_key": clean_text(payload.get("endpoint_key")),
                        "event_name": clean_text(payload.get("event_name") or payload.get("event")),
                        "source": clean_text(payload.get("source")),
                        "idempotency_key": clean_text(payload.get("idempotency_key")),
                        "payload_json": _json_dumps(payload.get("payload_json") or payload.get("payload") or {}),
                        "status": clean_text(payload.get("status") or "accepted"),
                    },
                ).fetchone()
                return self._row_to_trigger_event(_as_mapping(row) or {})
        except IntegrityError:
            existing = self.find_trigger_event(int(plan_id), clean_text(payload.get("idempotency_key")))
            if existing:
                return existing
            raise
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def find_trigger_event(self, plan_id: int, idempotency_key: str) -> dict[str, Any] | None:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT *
                        FROM automation_group_ops_trigger_event
                        WHERE plan_id = :plan_id AND idempotency_key = :idempotency_key
                        LIMIT 1
                        """
                    ),
                    {"plan_id": int(plan_id), "idempotency_key": clean_text(idempotency_key)},
                ).fetchone()
                return self._row_to_trigger_event(_as_mapping(row)) if row else None
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def update_trigger_event(self, event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        updates = {
            "status": clean_text(payload.get("status") or "accepted"),
            "error_message": clean_text(payload.get("error_message")),
            "event_id": clean_text(event_id),
        }
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        UPDATE automation_group_ops_trigger_event
                        SET status = :status,
                            error_message = :error_message,
                            processed_at = CURRENT_TIMESTAMP
                        WHERE id = :event_id
                        RETURNING *
                        """
                    ),
                    updates,
                ).fetchone()
                if not row:
                    raise NotFoundError("group ops trigger event not found")
                return self._row_to_trigger_event(_as_mapping(row) or {})
        except NotFoundError:
            raise
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def create_execution_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        INSERT INTO automation_group_ops_execution_log (
                            trigger_event_id, plan_id, user_id, external_user_id, sender,
                            recipient, layer_key, action_type, action_ref_id, status,
                            error_message, idempotency_key, received_at, processed_at
                        )
                        VALUES (
                            :trigger_event_id, :plan_id, :user_id, :external_user_id, :sender,
                            :recipient, :layer_key, :action_type, :action_ref_id, :status,
                            :error_message, :idempotency_key, :received_at, CURRENT_TIMESTAMP
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "trigger_event_id": clean_text(payload.get("trigger_event_id")),
                        "plan_id": int(payload.get("plan_id") or 0),
                        "user_id": clean_text(payload.get("user_id")),
                        "external_user_id": clean_text(payload.get("external_user_id")),
                        "sender": _json_dumps(payload.get("sender") or {}),
                        "recipient": _json_dumps(payload.get("recipient") or {}),
                        "layer_key": clean_text(payload.get("layer_key")),
                        "action_type": clean_text(payload.get("action_type")),
                        "action_ref_id": clean_text(payload.get("action_ref_id")),
                        "status": clean_text(payload.get("status") or "success"),
                        "error_message": clean_text(payload.get("error_message")),
                        "idempotency_key": clean_text(payload.get("idempotency_key")),
                        "received_at": clean_text(payload.get("received_at")) or None,
                    },
                ).fetchone()
                return self._row_to_execution_log(_as_mapping(row) or {})
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc

    def list_execution_logs(self, plan_id: int, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        clauses = ["plan_id = :plan_id"]
        params = {
            "plan_id": int(plan_id),
            "trigger_event_id": clean_text(filters.get("trigger_event_id")),
            "status": clean_text(filters.get("status")),
            "action_type": clean_text(filters.get("action_type")),
            "layer_key": clean_text(filters.get("layer_key")),
            "recipient": f"%{clean_text(filters.get('recipient')).lower()}%",
            "limit": max(1, _int(filters.get("limit")) or 50),
            "offset": max(0, _int(filters.get("offset"))),
        }
        for key in ("trigger_event_id", "status", "action_type", "layer_key"):
            if params[key]:
                clauses.append(f"{key} = :{key}")
        if clean_text(filters.get("recipient")):
            clauses.append("(LOWER(external_user_id) LIKE :recipient OR LOWER(user_id) LIKE :recipient)")
        where = " AND ".join(clauses)
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(
                    text(f"SELECT * FROM automation_group_ops_execution_log WHERE {where} ORDER BY id DESC LIMIT :limit OFFSET :offset"), params
                ).fetchall()
                total = conn.execute(
                    text(f"SELECT COUNT(*) FROM automation_group_ops_execution_log WHERE {where}"),
                    {k: v for k, v in params.items() if k not in {"limit", "offset"}},
                ).scalar_one()
                return [self._row_to_execution_log(_as_mapping(row) or {}) for row in rows], int(total or 0)
        except SQLAlchemyError as exc:
            raise RepositoryProviderError(f"group ops repository unavailable: {exc}") from exc
