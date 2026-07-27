from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from aicrm_next.platform_foundation.admin_audit import (
    AdminAuditRecord,
    build_admin_audit_port,
)
from aicrm_next.shared.runtime import raw_database_url
from aicrm_next.shared.runtime_settings import startup_environment_setting


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (table_name,))
        row = cur.fetchone()
        return bool(row and row.get("exists"))


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(FALSE))
                  AND table_name = %s
                  AND column_name = %s
            ) AS exists
            """,
            (table_name, column_name),
        )
        row = cur.fetchone()
        return bool(row and row.get("exists"))


def _row_count(conn, query: str, params: tuple[Any, ...]) -> int:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return int((row or {}).get("count") or 0)


def _returning_external_userids(conn, query: str, params: tuple[Any, ...]) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return [str(row.get("external_userid") or "").strip() for row in cur.fetchall()]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _external_scope_clause(external_userids: list[str] | None) -> tuple[str, tuple[Any, ...]]:
    if external_userids is None:
        return "", ()
    values = [str(item or "").strip() for item in external_userids if str(item or "").strip()]
    if not values:
        return " AND FALSE", ()
    return " AND external_userid = ANY(%s::text[])", (values,)


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def _iso_value(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


class FixtureOwnerMigrationRepository:
    source_status = "local_contract_probe"

    def preview_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        external_userids: list[str] | None = None,
    ) -> dict[str, Any]:
        del external_userids
        return {
            "source_status": self.source_status,
            "candidate_count": 0,
            "all_external_userids": [],
            "sample_external_userids": [],
            "surface_counts": {},
            "pending_review": {},
            "notes": ["DATABASE_URL is not PostgreSQL; owner migration is available only against production data."],
        }

    def execute_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        operator: str,
        external_userids: list[str] | None = None,
        target_owner_display_name: str | None = None,
    ) -> dict[str, Any]:
        del target_owner_display_name
        return {
            **self.preview_owner_migration(
                source_owner_userid=source_owner_userid,
                target_owner_userid=target_owner_userid,
            ),
            "update_counts": {},
            "executed": False,
        }

    def resolve_operation_members(self, userids: list[str]) -> dict[str, dict[str, Any]]:
        return {
            str(userid): {"user_id": str(userid), "display_name": str(userid), "status": "active"}
            for userid in userids
            if str(userid or "").strip()
        }

    def lookup_customer_owners(self, external_userids: list[str]) -> dict[str, dict[str, Any]]:
        del external_userids
        return {}

    def save_import_session(self, session: dict[str, Any]) -> None:
        store = self._load_store()
        store.setdefault("sessions", {})[session["session_id"]] = session
        self._save_store(store)

    def get_import_session(self, session_id: str) -> dict[str, Any] | None:
        return self._load_store().get("sessions", {}).get(session_id)

    def save_preview(self, preview: dict[str, Any]) -> None:
        store = self._load_store()
        store.setdefault("previews", {})[preview["preview_token"]] = preview
        self._save_store(store)

    def get_preview(self, preview_token: str) -> dict[str, Any] | None:
        return self._load_store().get("previews", {}).get(preview_token)

    def get_latest_preview_by_session(self, session_id: str) -> dict[str, Any] | None:
        previews = [
            preview
            for preview in self._load_store().get("previews", {}).values()
            if str(preview.get("session_id") or "") == session_id
        ]
        return sorted(previews, key=lambda item: str(item.get("created_at") or ""), reverse=True)[0] if previews else None

    def mark_preview_executed(self, preview_token: str, result_id: str) -> None:
        store = self._load_store()
        preview = store.setdefault("previews", {}).get(preview_token)
        if preview:
            preview["executed_result_id"] = result_id
        self._save_store(store)

    def save_result(self, result: dict[str, Any]) -> None:
        store = self._load_store()
        store.setdefault("results", {})[result["result_id"]] = result
        self._save_store(store)

    def get_result(self, result_id: str) -> dict[str, Any] | None:
        return self._load_store().get("results", {}).get(result_id)

    def audit_owner_migration_event(self, event_type: str, payload: dict[str, Any]) -> None:
        store = self._load_store()
        store.setdefault("audit", []).append({"event_type": event_type, "payload": payload})
        self._save_store(store)

    def _store_path(self) -> Path:
        configured = startup_environment_setting("OWNER_MIGRATION_FIXTURE_STORE").strip()
        return Path(configured) if configured else Path(tempfile.gettempdir()) / "aicrm_owner_migration_fixture_store.json"

    def _load_store(self) -> dict[str, Any]:
        path = self._store_path()
        if not path.exists():
            return {"sessions": {}, "previews": {}, "results": {}, "audit": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"sessions": {}, "previews": {}, "results": {}, "audit": []}
        return payload if isinstance(payload, dict) else {"sessions": {}, "previews": {}, "results": {}, "audit": []}

    def _save_store(self, store: dict[str, Any]) -> None:
        path = self._store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(store, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)


class PostgresOwnerMigrationRepository:
    source_status = "production_postgres"

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(raw_database_url(), row_factory=dict_row)

    def preview_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        external_userids: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            candidates = self._candidate_rows(conn, source_owner_userid, external_userids=external_userids)
            return {
                "source_status": self.source_status,
                "candidate_count": len(candidates),
                "all_external_userids": [row["external_userid"] for row in candidates],
                "sample_external_userids": [row["external_userid"] for row in candidates[:20]],
                "surface_counts": self._surface_counts(conn, source_owner_userid, external_userids=external_userids),
                "pending_review": self._pending_review_counts(conn, source_owner_userid),
                "notes": [
                    "Execution calls WeCom customer transfer first, then updates CRM rows for successful external_userids.",
                    "Pending jobs and historical messages are reported for review but are not rewritten automatically.",
                ],
            }

    def execute_owner_migration(
        self,
        *,
        source_owner_userid: str,
        target_owner_userid: str,
        operator: str,
        external_userids: list[str] | None = None,
        target_owner_display_name: str | None = None,
    ) -> dict[str, Any]:
        owner_display_name = str(target_owner_display_name or "").strip() or target_owner_userid
        with self._connect() as conn:
            before = self.preview_owner_migration(
                source_owner_userid=source_owner_userid,
                target_owner_userid=target_owner_userid,
                external_userids=external_userids,
            )
            update_counts: dict[str, int] = {}
            touched: list[str] = []
            scope_clause, scope_params = _external_scope_clause(external_userids)
            if _table_exists(conn, "external_contact_bindings"):
                values = _returning_external_userids(
                    conn,
                    f"""
                    UPDATE external_contact_bindings
                    SET last_owner_userid = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE last_owner_userid = %s AND COALESCE(external_userid, '') <> ''
                    {scope_clause}
                    RETURNING external_userid
                    """,
                    (target_owner_userid, source_owner_userid, *scope_params),
                )
                update_counts["external_contact_bindings"] = len(values)
                touched.extend(values)
            if _table_exists(conn, "wecom_external_contact_identity_map"):
                values = _returning_external_userids(
                    conn,
                    f"""
                    UPDATE wecom_external_contact_identity_map
                    SET follow_user_userid = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE follow_user_userid = %s AND COALESCE(external_userid, '') <> ''
                    {scope_clause}
                    RETURNING external_userid
                    """,
                    (target_owner_userid, source_owner_userid, *scope_params),
                )
                update_counts["wecom_external_contact_identity_map"] = len(values)
                touched.extend(values)
            if _table_exists(conn, "wecom_external_contact_follow_users"):
                inserted = _returning_external_userids(
                    conn,
                    f"""
                    WITH source_rows AS (
                        SELECT *
                        FROM wecom_external_contact_follow_users
                        WHERE user_id = %s
                          AND COALESCE(external_userid, '') <> ''
                          AND COALESCE(relation_status, 'active') = 'active'
                          {scope_clause}
                    )
                    INSERT INTO wecom_external_contact_follow_users (
                        corp_id, external_userid, user_id, relation_status, is_primary,
                        remark, description, add_way, state, oper_userid, createtime,
                        raw_follow_user, first_seen_at, last_seen_at, created_at, updated_at
                    )
                    SELECT
                        corp_id, external_userid, %s, 'active', TRUE,
                        remark, description, add_way, state, oper_userid, createtime,
                        raw_follow_user, first_seen_at, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM source_rows
                    ON CONFLICT (corp_id, external_userid, user_id) DO UPDATE SET
                        relation_status = 'active',
                        is_primary = TRUE,
                        updated_at = CURRENT_TIMESTAMP,
                        last_seen_at = CURRENT_TIMESTAMP
                    RETURNING external_userid
                    """,
                    (source_owner_userid, *scope_params, target_owner_userid),
                )
                closed = _returning_external_userids(
                    conn,
                    f"""
                    UPDATE wecom_external_contact_follow_users
                    SET relation_status = 'transferred', is_primary = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s
                      AND COALESCE(external_userid, '') <> ''
                      AND COALESCE(relation_status, 'active') = 'active'
                      {scope_clause}
                    RETURNING external_userid
                    """,
                    (source_owner_userid, *scope_params),
                )
                update_counts["wecom_external_contact_follow_users_target_active"] = len(inserted)
                update_counts["wecom_external_contact_follow_users_source_transferred"] = len(closed)
                touched.extend(inserted)
                touched.extend(closed)
            if _table_exists(conn, "customer_list_index_next"):
                values = _returning_external_userids(
                    conn,
                    f"""
                    UPDATE customer_list_index_next
                    SET owner_userid = %s,
                        owner_display_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE owner_userid = %s AND COALESCE(external_userid, '') <> ''
                    {scope_clause}
                    RETURNING external_userid
                    """,
                    (target_owner_userid, owner_display_name, source_owner_userid, *scope_params),
                )
                update_counts["customer_list_index_next"] = len(values)
                touched.extend(values)
            if _table_exists(conn, "customer_detail_snapshot_next"):
                values = _returning_external_userids(
                    conn,
                    f"""
                    UPDATE customer_detail_snapshot_next
                    SET customer_json = jsonb_set(
                            jsonb_set(customer_json::jsonb, '{{owner_userid}}', to_jsonb(%s::text), TRUE),
                            '{{owner_display_name}}', to_jsonb(%s::text), TRUE
                        )::json,
                        binding_json = jsonb_set(
                            binding_json::jsonb, '{{last_owner_userid}}', to_jsonb(%s::text), TRUE
                        )::json,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE customer_json::jsonb ->> 'owner_userid' = %s
                      AND COALESCE(external_userid, '') <> ''
                      {scope_clause}
                    RETURNING external_userid
                    """,
                    (target_owner_userid, owner_display_name, target_owner_userid, source_owner_userid, *scope_params),
                )
                update_counts["customer_detail_snapshot_next"] = len(values)
                touched.extend(values)
            touched_external_userids = _unique(touched)
            after = {
                "update_counts": update_counts,
                "touched_count": len(touched_external_userids),
                "touched_external_userids": touched_external_userids,
                "sample_external_userids": touched_external_userids[:20],
                "scoped_to_external_userids": external_userids is not None,
            }
            self._insert_audit(
                conn,
                operator=operator,
                source_owner_userid=source_owner_userid,
                target_owner_userid=target_owner_userid,
                before=before,
                after=after,
            )
            conn.commit()
            return {
                **self.preview_owner_migration(
                    source_owner_userid=source_owner_userid,
                    target_owner_userid=target_owner_userid,
                    external_userids=external_userids,
                ),
                "executed": True,
                **after,
            }

    def _candidate_rows(self, conn, source_owner_userid: str, *, external_userids: list[str] | None = None) -> list[dict[str, Any]]:
        unions: list[str] = []
        params: list[Any] = []
        scope_clause, scope_params = _external_scope_clause(external_userids)
        surfaces = {
            "external_contact_bindings": f"SELECT external_userid, 'external_contact_bindings' AS source_table FROM external_contact_bindings WHERE last_owner_userid = %s AND COALESCE(external_userid, '') <> '' {scope_clause}",
            "wecom_external_contact_identity_map": f"SELECT external_userid, 'wecom_external_contact_identity_map' AS source_table FROM wecom_external_contact_identity_map WHERE follow_user_userid = %s AND COALESCE(external_userid, '') <> '' {scope_clause}",
            "wecom_external_contact_follow_users": f"SELECT external_userid, 'wecom_external_contact_follow_users' AS source_table FROM wecom_external_contact_follow_users WHERE user_id = %s AND COALESCE(relation_status, 'active') = 'active' AND COALESCE(external_userid, '') <> '' {scope_clause}",
            "customer_list_index_next": f"SELECT external_userid, 'customer_list_index_next' AS source_table FROM customer_list_index_next WHERE owner_userid = %s AND COALESCE(external_userid, '') <> '' {scope_clause}",
            "customer_detail_snapshot_next": f"SELECT external_userid, 'customer_detail_snapshot_next' AS source_table FROM customer_detail_snapshot_next WHERE customer_json::jsonb ->> 'owner_userid' = %s AND COALESCE(external_userid, '') <> '' {scope_clause}",
        }
        for table_name, query in surfaces.items():
            if _table_exists(conn, table_name):
                unions.append(query)
                params.append(source_owner_userid)
                params.extend(scope_params)
        if not unions:
            return []
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH candidates AS ({' UNION ALL '.join(unions)})
                SELECT external_userid, ARRAY_AGG(DISTINCT source_table ORDER BY source_table) AS source_tables
                FROM candidates
                GROUP BY external_userid
                ORDER BY external_userid ASC
                """,
                tuple(params),
            )
            return [dict(row) for row in cur.fetchall()]

    def _surface_counts(self, conn, source_owner_userid: str, *, external_userids: list[str] | None = None) -> dict[str, int]:
        scope_clause, scope_params = _external_scope_clause(external_userids)
        count_queries = {
            "external_contact_bindings": (
                f"SELECT COUNT(*) AS count FROM external_contact_bindings WHERE last_owner_userid = %s {scope_clause}",
                (source_owner_userid, *scope_params),
            ),
            "wecom_external_contact_identity_map": (
                f"SELECT COUNT(*) AS count FROM wecom_external_contact_identity_map WHERE follow_user_userid = %s {scope_clause}",
                (source_owner_userid, *scope_params),
            ),
            "wecom_external_contact_follow_users": (
                f"SELECT COUNT(*) AS count FROM wecom_external_contact_follow_users WHERE user_id = %s AND COALESCE(relation_status, 'active') = 'active' {scope_clause}",
                (source_owner_userid, *scope_params),
            ),
            "customer_list_index_next": (
                f"SELECT COUNT(*) AS count FROM customer_list_index_next WHERE owner_userid = %s {scope_clause}",
                (source_owner_userid, *scope_params),
            ),
            "customer_detail_snapshot_next": (
                f"SELECT COUNT(*) AS count FROM customer_detail_snapshot_next WHERE customer_json::jsonb ->> 'owner_userid' = %s {scope_clause}",
                (source_owner_userid, *scope_params),
            ),
        }
        return {
            table_name: _row_count(conn, query, params)
            for table_name, (query, params) in count_queries.items()
            if _table_exists(conn, table_name)
        }

    def resolve_operation_members(self, userids: list[str]) -> dict[str, dict[str, Any]]:
        from aicrm_next.common_operation_members import list_operation_member_rows
        from aicrm_next.shared.operation_members import candidate_from_row

        wanted = {str(userid or "").strip() for userid in userids if str(userid or "").strip()}
        resolved: dict[str, dict[str, Any]] = {}
        for row in list_operation_member_rows():
            candidate = candidate_from_row(row)
            if candidate is None or candidate.user_id not in wanted:
                continue
            item = candidate.to_item()
            item["userid"] = candidate.user_id
            item["name"] = item.get("display_name") or candidate.user_id
            resolved[candidate.user_id] = item
        return resolved

    def lookup_customer_owners(self, external_userids: list[str]) -> dict[str, dict[str, Any]]:
        values = [str(item or "").strip() for item in external_userids if str(item or "").strip()]
        if not values:
            return {}
        with self._connect() as conn:
            unions: list[str] = []
            params: list[Any] = []
            surfaces = {
                "external_contact_bindings": "SELECT external_userid, last_owner_userid AS owner_userid, '' AS customer_name FROM external_contact_bindings WHERE external_userid = ANY(%s::text[]) AND COALESCE(external_userid, '') <> ''",
                "wecom_external_contact_identity_map": "SELECT external_userid, follow_user_userid AS owner_userid, COALESCE(name, '') AS customer_name FROM wecom_external_contact_identity_map WHERE external_userid = ANY(%s::text[]) AND COALESCE(external_userid, '') <> ''",
                "wecom_external_contact_follow_users": "SELECT external_userid, user_id AS owner_userid, COALESCE(remark, '') AS customer_name FROM wecom_external_contact_follow_users WHERE external_userid = ANY(%s::text[]) AND COALESCE(relation_status, 'active') = 'active' AND COALESCE(external_userid, '') <> ''",
                "customer_list_index_next": "SELECT external_userid, owner_userid, COALESCE(customer_name, '') AS customer_name FROM customer_list_index_next WHERE external_userid = ANY(%s::text[]) AND COALESCE(external_userid, '') <> ''",
                "customer_detail_snapshot_next": "SELECT external_userid, customer_json::jsonb ->> 'owner_userid' AS owner_userid, COALESCE(customer_json::jsonb ->> 'customer_name', customer_json::jsonb ->> 'name', '') AS customer_name FROM customer_detail_snapshot_next WHERE external_userid = ANY(%s::text[]) AND COALESCE(external_userid, '') <> ''",
            }
            for table_name, query in surfaces.items():
                if _table_exists(conn, table_name):
                    unions.append(query)
                    params.append(values)
            if not unions:
                return {}
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH owners AS ({' UNION ALL '.join(unions)})
                    SELECT
                        external_userid,
                        ARRAY_AGG(DISTINCT owner_userid) FILTER (WHERE COALESCE(owner_userid, '') <> '') AS owner_userids,
                        MAX(customer_name) FILTER (WHERE COALESCE(customer_name, '') <> '') AS customer_name
                    FROM owners
                    GROUP BY external_userid
                    """,
                    tuple(params),
                )
                return {
                    str(row.get("external_userid") or ""): {
                        "owner_userids": list(row.get("owner_userids") or []),
                        "customer_name": str(row.get("customer_name") or ""),
                    }
                    for row in cur.fetchall()
                }

    def save_import_session(self, session: dict[str, Any]) -> None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO owner_migration_import_sessions (
                        session_id, file_name, file_hash, source_owner_userid, target_owner_userid,
                        include_wecom_transfer, transfer_welcome_msg, rows_json, row_stats_json, operator, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))
                    ON CONFLICT (session_id) DO UPDATE SET
                        file_name = EXCLUDED.file_name,
                        file_hash = EXCLUDED.file_hash,
                        rows_json = EXCLUDED.rows_json,
                        row_stats_json = EXCLUDED.row_stats_json
                    """,
                    (
                        session.get("session_id"),
                        session.get("file_name"),
                        session.get("file_hash"),
                        session.get("source_owner_userid"),
                        session.get("target_owner_userid"),
                        bool(session.get("include_wecom_transfer")),
                        session.get("transfer_welcome_msg"),
                        json.dumps(session.get("rows") or [], ensure_ascii=False),
                        json.dumps(session.get("row_stats") or {}, ensure_ascii=False),
                        session.get("operator"),
                        session.get("created_at"),
                    ),
                )
            conn.commit()

    def get_import_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM owner_migration_import_sessions WHERE session_id = %s", (session_id,))
                row = cur.fetchone()
        return self._session_from_row(row) if row else None

    def save_preview(self, preview: dict[str, Any]) -> None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO owner_migration_previews (
                        preview_token, preview_hash, scope_type, session_id, file_hash, source_owner_userid,
                        target_owner_userid, source_owner_display_name, target_owner_display_name,
                        include_wecom_transfer, transfer_welcome_msg, eligible_external_userids_json,
                        rows_json, row_stats_json, surface_counts_json, pending_review_json,
                        confirm_phrase, operator, created_at, expires_at, executed_result_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), %s::timestamptz, %s)
                    ON CONFLICT (preview_token) DO UPDATE SET executed_result_id = EXCLUDED.executed_result_id
                    """,
                    (
                        preview.get("preview_token"),
                        preview.get("preview_hash"),
                        preview.get("scope_type"),
                        preview.get("session_id"),
                        preview.get("file_hash"),
                        preview.get("source_owner_userid"),
                        preview.get("target_owner_userid"),
                        preview.get("source_owner_display_name"),
                        preview.get("target_owner_display_name"),
                        bool(preview.get("include_wecom_transfer")),
                        preview.get("transfer_welcome_msg"),
                        json.dumps(preview.get("eligible_external_userids") or [], ensure_ascii=False),
                        json.dumps(preview.get("rows") or [], ensure_ascii=False),
                        json.dumps(preview.get("row_stats") or {}, ensure_ascii=False),
                        json.dumps(preview.get("surface_counts") or {}, ensure_ascii=False),
                        json.dumps(preview.get("pending_review") or {}, ensure_ascii=False),
                        preview.get("confirm_phrase"),
                        preview.get("operator"),
                        preview.get("created_at"),
                        preview.get("expires_at"),
                        preview.get("executed_result_id") or "",
                    ),
                )
            conn.commit()

    def get_preview(self, preview_token: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM owner_migration_previews WHERE preview_token = %s", (preview_token,))
                row = cur.fetchone()
        return self._preview_from_row(row) if row else None

    def get_latest_preview_by_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM owner_migration_previews WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                )
                row = cur.fetchone()
        return self._preview_from_row(row) if row else None

    def mark_preview_executed(self, preview_token: str, result_id: str) -> None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE owner_migration_previews SET executed_result_id = %s WHERE preview_token = %s AND COALESCE(executed_result_id, '') = ''",
                    (result_id, preview_token),
                )
            conn.commit()

    def save_result(self, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO owner_migration_results (
                        result_id, job_id, preview_token, scope_type, session_id, file_hash,
                        source_owner_userid, target_owner_userid, source_owner_display_name,
                        target_owner_display_name, operator, preview_hash, total_rows,
                        eligible_count, wecom_success, wecom_failed, crm_updated,
                        include_wecom_transfer, transfer_welcome_msg, rows_json, stats_json,
                        created_at, executed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))
                    ON CONFLICT (result_id) DO NOTHING
                    """,
                    (
                        result.get("result_id"),
                        result.get("job_id"),
                        result.get("preview_token"),
                        result.get("scope_type"),
                        result.get("session_id"),
                        result.get("file_hash"),
                        result.get("source_owner_userid"),
                        result.get("target_owner_userid"),
                        result.get("source_owner_display_name"),
                        result.get("target_owner_display_name"),
                        result.get("operator"),
                        result.get("preview_hash"),
                        int(result.get("total_rows") or 0),
                        int(result.get("eligible_count") or 0),
                        int(result.get("wecom_success") or 0),
                        int(result.get("wecom_failed") or 0),
                        int(result.get("crm_updated") or 0),
                        bool(result.get("include_wecom_transfer")),
                        result.get("transfer_welcome_msg"),
                        json.dumps(result.get("rows") or [], ensure_ascii=False),
                        json.dumps(result, ensure_ascii=False),
                        result.get("created_at"),
                        result.get("executed_at"),
                    ),
                )
            conn.commit()

    def get_result(self, result_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            self._ensure_state_tables(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM owner_migration_results WHERE result_id = %s", (result_id,))
                row = cur.fetchone()
        if not row:
            return None
        payload = row.get("stats_json") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload.setdefault("rows", row.get("rows_json") or [])
        return dict(payload)

    def audit_owner_migration_event(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            if _table_exists(conn, "admin_operation_logs"):
                build_admin_audit_port().append_dbapi(
                    conn,
                    record=AdminAuditRecord(
                        operator=str(payload.get("operator") or "crm_console"),
                        action_type=event_type,
                        target_type="owner_migration",
                        target_id=str(
                            payload.get("session_id")
                            or payload.get("result_id")
                            or payload.get("preview_token")
                            or ""
                        ),
                        before={},
                        after=payload,
                    )
                )
            conn.commit()

    def _ensure_state_tables(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_migration_import_sessions (
                    session_id TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL DEFAULT '',
                    file_hash TEXT NOT NULL DEFAULT '',
                    source_owner_userid TEXT NOT NULL,
                    target_owner_userid TEXT NOT NULL,
                    include_wecom_transfer BOOLEAN NOT NULL DEFAULT TRUE,
                    transfer_welcome_msg TEXT NOT NULL DEFAULT '',
                    rows_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    row_stats_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    operator TEXT NOT NULL DEFAULT 'crm_console',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_migration_previews (
                    preview_token TEXT PRIMARY KEY,
                    preview_hash TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    file_hash TEXT NOT NULL DEFAULT '',
                    source_owner_userid TEXT NOT NULL,
                    target_owner_userid TEXT NOT NULL,
                    source_owner_display_name TEXT NOT NULL DEFAULT '',
                    target_owner_display_name TEXT NOT NULL DEFAULT '',
                    include_wecom_transfer BOOLEAN NOT NULL DEFAULT TRUE,
                    transfer_welcome_msg TEXT NOT NULL DEFAULT '',
                    eligible_external_userids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    rows_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    row_stats_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    surface_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    pending_review_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    confirm_phrase TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT 'crm_console',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMPTZ NOT NULL,
                    executed_result_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS ix_owner_migration_previews_session ON owner_migration_previews (session_id, created_at DESC)")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_migration_results (
                    result_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL DEFAULT '',
                    preview_token TEXT NOT NULL DEFAULT '',
                    scope_type TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    file_hash TEXT NOT NULL DEFAULT '',
                    source_owner_userid TEXT NOT NULL,
                    target_owner_userid TEXT NOT NULL,
                    source_owner_display_name TEXT NOT NULL DEFAULT '',
                    target_owner_display_name TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT 'crm_console',
                    preview_hash TEXT NOT NULL DEFAULT '',
                    total_rows INTEGER NOT NULL DEFAULT 0,
                    eligible_count INTEGER NOT NULL DEFAULT 0,
                    wecom_success INTEGER NOT NULL DEFAULT 0,
                    wecom_failed INTEGER NOT NULL DEFAULT 0,
                    crm_updated INTEGER NOT NULL DEFAULT 0,
                    include_wecom_transfer BOOLEAN NOT NULL DEFAULT TRUE,
                    transfer_welcome_msg TEXT NOT NULL DEFAULT '',
                    rows_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                    stats_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    executed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS ix_owner_migration_results_preview ON owner_migration_results (preview_token)")

    def _session_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": row.get("session_id"),
            "file_name": row.get("file_name"),
            "file_hash": row.get("file_hash"),
            "source_owner_userid": row.get("source_owner_userid"),
            "target_owner_userid": row.get("target_owner_userid"),
            "include_wecom_transfer": bool(row.get("include_wecom_transfer")),
            "transfer_welcome_msg": row.get("transfer_welcome_msg") or "",
            "rows": _json_value(row.get("rows_json"), []),
            "row_stats": _json_value(row.get("row_stats_json"), {}),
            "operator": row.get("operator") or "crm_console",
            "created_at": _iso_value(row.get("created_at")),
        }

    def _preview_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        eligible_external_userids = _json_value(row.get("eligible_external_userids_json"), [])
        return {
            "ok": True,
            "mode": "preview",
            "preview_token": row.get("preview_token"),
            "preview_hash": row.get("preview_hash"),
            "scope_type": row.get("scope_type"),
            "session_id": row.get("session_id") or "",
            "file_hash": row.get("file_hash") or "",
            "source_owner_userid": row.get("source_owner_userid"),
            "target_owner_userid": row.get("target_owner_userid"),
            "source_owner_display_name": row.get("source_owner_display_name") or row.get("source_owner_userid"),
            "target_owner_display_name": row.get("target_owner_display_name") or row.get("target_owner_userid"),
            "include_wecom_transfer": bool(row.get("include_wecom_transfer")),
            "transfer_welcome_msg": row.get("transfer_welcome_msg") or "",
            "eligible_external_userids": eligible_external_userids,
            "all_external_userids": eligible_external_userids,
            "sample_external_userids": eligible_external_userids[:20],
            "rows": _json_value(row.get("rows_json"), []),
            "row_stats": _json_value(row.get("row_stats_json"), {}),
            "surface_counts": _json_value(row.get("surface_counts_json"), {}),
            "pending_review": _json_value(row.get("pending_review_json"), {}),
            "confirm_phrase": row.get("confirm_phrase") or "",
            "operator": row.get("operator") or "crm_console",
            "created_at": _iso_value(row.get("created_at")),
            "expires_at": _iso_value(row.get("expires_at")),
            "executed_result_id": row.get("executed_result_id") or "",
        }

    def _pending_review_counts(self, conn, source_owner_userid: str) -> dict[str, int]:
        count_queries: dict[str, tuple[str, str, str, tuple[Any, ...]]] = {
            "pending_broadcast_jobs": (
                "broadcast_jobs",
                "owner_userid",
                "SELECT COUNT(*) AS count FROM broadcast_jobs WHERE owner_userid = %s AND status IN ('pending', 'queued', 'running', 'draft')",
                (source_owner_userid,),
            ),
            "pending_outbound_tasks": (
                "outbound_tasks",
                "request_payload",
                "SELECT COUNT(*) AS count FROM outbound_tasks WHERE request_payload LIKE %s AND status IN ('pending', 'created', 'queued')",
                (f"%{source_owner_userid}%",),
            ),
        }
        counts: dict[str, int] = {}
        for key, (table_name, owner_column, query, params) in count_queries.items():
            if not _table_exists(conn, table_name) or not _column_exists(conn, table_name, owner_column):
                continue
            counts[key] = _row_count(conn, query, params)
        return counts

    def _insert_audit(
        self,
        conn,
        *,
        operator: str,
        source_owner_userid: str,
        target_owner_userid: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        if not _table_exists(conn, "admin_operation_logs"):
            return
        build_admin_audit_port().append_dbapi(
            conn,
            record=AdminAuditRecord(
                operator=operator,
                action_type="owner_migration_execute",
                target_type="owner_migration",
                target_id=f"{source_owner_userid}->{target_owner_userid}",
                before=before,
                after=after,
            )
        )
