from __future__ import annotations

from typing import Any

from .postgres_repo import (
    _as_mapping,
    _int,
    _iso,
    _json_dumps,
    _json_loads,
    clean_text,
    derive_node_scheduled_time,
    normalize_group_admin_userids,
    text,
)


class GroupOpsPostgresMappingMixin:
    def _get_plan_sql(self, conn: Any, plan_id: int) -> dict[str, Any] | None:
        row = conn.execute(
            text(
                """
                SELECT *
                FROM automation_group_ops_plans
                WHERE id = :plan_id
                  AND archived_at IS NULL
                LIMIT 1
                """
            ),
            {"plan_id": int(plan_id)},
        ).fetchone()
        return self._row_to_plan(conn, _as_mapping(row)) if row else None

    def _get_plan_group_sql(self, conn: Any, binding_id: int) -> dict[str, Any] | None:
        row = conn.execute(
            text("SELECT * FROM automation_group_ops_plan_groups WHERE id = :binding_id LIMIT 1"),
            {"binding_id": int(binding_id)},
        ).fetchone()
        return self._row_to_plan_group(_as_mapping(row)) if row else None

    def _get_node_sql(self, conn: Any, node_id: int) -> dict[str, Any] | None:
        row = conn.execute(
            text("SELECT * FROM automation_group_ops_plan_nodes WHERE id = :node_id LIMIT 1"),
            {"node_id": int(node_id)},
        ).fetchone()
        return self._row_to_node(_as_mapping(row)) if row else None

    def _get_event_sql(self, conn: Any, event_id: int) -> dict[str, Any] | None:
        row = conn.execute(
            text("SELECT * FROM automation_group_ops_webhook_events WHERE id = :event_id LIMIT 1"),
            {"event_id": int(event_id)},
        ).fetchone()
        return self._row_to_event(_as_mapping(row)) if row else None

    def _get_event_public(self, event_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            return self._get_event_sql(conn, int(event_id))

    def _owner_name_for_userid(self, conn: Any, owner_userid: str) -> str:
        if not owner_userid:
            return ""
        row = conn.execute(
            text(
                """
                SELECT owner_name
                FROM wecom_group_chat_snapshots
                WHERE owner_userid = :owner_userid
                  AND owner_name <> ''
                ORDER BY synced_at DESC, chat_id ASC
                LIMIT 1
                """
            ),
            {"owner_userid": owner_userid},
        ).fetchone()
        return clean_text((_as_mapping(row) or {}).get("owner_name")) if row else ""

    def _table_has_column(self, conn: Any, table_name: str, column_name: str) -> bool:
        if conn.dialect.name == "sqlite":
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            return any(clean_text((_as_mapping(row) or {}).get("name")) == column_name for row in rows)
        row = conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :table_name
                  AND column_name = :column_name
                LIMIT 1
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).fetchone()
        return bool(row)

    def _update_plan_extra_fields(self, conn: Any, plan_id: int, normalized: dict[str, Any]) -> None:
        extra_columns = [
            "default_action_type",
            "allow_no_sop",
            "allow_external_recipients",
            "description",
        ]
        if not all(self._table_has_column(conn, "automation_group_ops_plans", column) for column in extra_columns):
            return
        conn.execute(
            text(
                """
                UPDATE automation_group_ops_plans
                SET default_action_type = :default_action_type,
                    allow_no_sop = :allow_no_sop,
                    allow_external_recipients = :allow_external_recipients,
                    description = :description
                WHERE id = :plan_id
                """
            ),
            {
                "plan_id": int(plan_id),
                "default_action_type": clean_text(normalized.get("default_action_type") or "record_only"),
                "allow_no_sop": bool(normalized.get("allow_no_sop", True)),
                "allow_external_recipients": bool(normalized.get("allow_external_recipients", True)),
                "description": clean_text(normalized.get("description")),
            },
        )

    def _row_to_plan(self, conn: Any, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        owner_userid = clean_text(row.get("owner_userid"))
        return {
            "id": _int(row.get("id")),
            "plan_code": clean_text(row.get("plan_code")),
            "plan_name": clean_text(row.get("plan_name")),
            "plan_type": clean_text(row.get("plan_type")),
            "owner_userid": owner_userid,
            "owner_name": self._owner_name_for_userid(conn, owner_userid),
            "status": clean_text(row.get("status")),
            "default_action_type": clean_text(
                row.get("default_action_type") or ("enqueue" if clean_text(row.get("plan_type")) == "webhook" else "record_only")
            ),
            "allow_no_sop": bool(row.get("allow_no_sop", True)),
            "allow_external_recipients": bool(row.get("allow_external_recipients", True)),
            "description": clean_text(row.get("description")),
            "webhook_key": clean_text(row.get("webhook_key")),
            "created_by": clean_text(row.get("created_by")),
            "updated_by": clean_text(row.get("updated_by")),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
            "archived_at": _iso(row.get("archived_at")),
        }

    def _row_to_plan_group(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": _int(row.get("id")),
            "plan_id": _int(row.get("plan_id")),
            "chat_id": clean_text(row.get("chat_id")),
            "group_name_snapshot": clean_text(row.get("group_name_snapshot")),
            "owner_userid_snapshot": clean_text(row.get("owner_userid_snapshot")),
            "internal_member_count_snapshot": _int(row.get("internal_member_count_snapshot")),
            "external_member_count_snapshot": _int(row.get("external_member_count_snapshot")),
            "status": clean_text(row.get("status")),
            "created_at": _iso(row.get("created_at")),
            "removed_at": _iso(row.get("removed_at")),
        }

    def _row_to_group_asset(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        bound_plan_id = _int(row.get("bound_plan_id"))
        return {
            "chat_id": clean_text(row.get("chat_id")),
            "group_name": clean_text(row.get("group_name")),
            "owner_userid": clean_text(row.get("owner_userid")),
            "owner_name": clean_text(row.get("owner_name")),
            "admin_userids": normalize_group_admin_userids(row.get("admin_userids")),
            "internal_member_count": _int(row.get("internal_member_count")),
            "external_member_count": _int(row.get("external_member_count")),
            "synced_at": _iso(row.get("synced_at")),
            "status": clean_text(row.get("status")),
            "bound_plan_id": bound_plan_id,
            "plan_name": clean_text(row.get("plan_name")) if bound_plan_id else "",
            "bind_status": "bound" if bound_plan_id else "unbound",
        }

    def _row_to_node(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        scheduled_time = derive_node_scheduled_time(row) or "20:00"
        text_content = clean_text(row.get("text_content"))
        content_package = _json_loads(
            row.get("content_package_json"),
            {
                "content_text": text_content,
                "image_library_ids": [],
                "miniprogram_library_ids": [],
                "attachment_library_ids": [],
                "group_invite_library_ids": [],
            },
        )
        if isinstance(content_package, dict):
            has_material_ids = any(content_package.get(key) for key in ("image_library_ids", "miniprogram_library_ids", "attachment_library_ids", "group_invite_library_ids"))
            if text_content and not clean_text(content_package.get("content_text")) and not has_material_ids:
                content_package = {**content_package, "content_text": text_content}
        else:
            content_package = {
                "content_text": text_content,
                "image_library_ids": [],
                "miniprogram_library_ids": [],
                "attachment_library_ids": [],
                "group_invite_library_ids": [],
            }
        return {
            "id": _int(row.get("id")),
            "plan_id": _int(row.get("plan_id")),
            "day_index": _int(row.get("day_index")),
            "scheduled_time": scheduled_time,
            "trigger_time_label": clean_text(row.get("trigger_time_label")),
            "action_title": clean_text(row.get("action_title")),
            "text_content": text_content,
            "attachments": _json_loads(row.get("attachments_json"), []),
            "content_package_json": content_package,
            "sort_order": _int(row.get("sort_order")),
            "status": clean_text(row.get("status")),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
        }

    def _row_to_event(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": _int(row.get("id")),
            "plan_id": _int(row.get("plan_id")),
            "idempotency_key": clean_text(row.get("idempotency_key")),
            "request_payload": _json_loads(row.get("request_payload"), {}),
            "normalized_content_payload": _json_loads(row.get("normalized_content_payload"), {}),
            "scheduled_at": _iso(row.get("scheduled_at")),
            "status": clean_text(row.get("status")),
            "broadcast_job_ids": _json_loads(row.get("broadcast_job_ids_json"), []),
            "error_message": clean_text(row.get("error_message")),
            "created_at": _iso(row.get("created_at")),
        }

    def _row_to_plan_member(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": _int(row.get("id")),
            "plan_id": _int(row.get("plan_id")),
            "user_id": clean_text(row.get("user_id")),
            "external_user_id": clean_text(row.get("external_user_id")),
            "group_id": clean_text(row.get("group_id")),
            "layer_key": clean_text(row.get("layer_key")),
            "source_type": clean_text(row.get("source_type")),
            "source_ref_id": clean_text(row.get("source_ref_id")),
            "status": clean_text(row.get("status")),
            "joined_at": _iso(row.get("joined_at")),
            "updated_at": _iso(row.get("updated_at")),
        }

    def _row_to_segmentation(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "plan_id": _int(row.get("plan_id")),
            "segmentation_type": clean_text(row.get("segmentation_type")),
            "rule_key": clean_text(row.get("rule_key")),
            "rule_version": _int(row.get("rule_version")),
            "params": _json_loads(row.get("params_json"), {}),
            "layer_actions": _json_loads(row.get("layer_actions_json"), {}),
            "updated_at": _iso(row.get("updated_at")),
        }

    def _row_to_audience_rule(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": _int(row.get("id")),
            "rule_key": clean_text(row.get("rule_key")),
            "display_name": clean_text(row.get("display_name")),
            "description": clean_text(row.get("description")),
            "rule_type": clean_text(row.get("rule_type")),
            "owner": clean_text(row.get("owner")),
            "status": clean_text(row.get("status")),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
        }

    def _row_to_audience_rule_version(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": _int(row.get("id")),
            "rule_id": _int(row.get("rule_id")),
            "rule_key": clean_text(row.get("rule_key")),
            "version": _int(row.get("version")),
            "executor_type": clean_text(row.get("executor_type")),
            "code_or_sql": clean_text(row.get("code_or_sql")),
            "params_schema": _json_loads(row.get("params_schema"), {}),
            "output_schema": _json_loads(row.get("output_schema"), {}),
            "refresh_policy": _json_loads(row.get("refresh_policy"), {}),
            "status": clean_text(row.get("status")),
            "published_at": _iso(row.get("published_at")),
            "created_at": _iso(row.get("created_at")),
        }

    def _row_to_rule_result(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": _int(row.get("id")),
            "rule_key": clean_text(row.get("rule_key")),
            "rule_version": _int(row.get("rule_version")),
            "plan_id": _int(row.get("plan_id")),
            "user_id": clean_text(row.get("user_id")),
            "external_user_id": clean_text(row.get("external_user_id")),
            "layer_key": clean_text(row.get("layer_key")),
            "score": float(row.get("score") or 0),
            "reason": clean_text(row.get("reason")),
            "evidence_json": _json_loads(row.get("evidence_json"), {}),
            "computed_at": _iso(row.get("computed_at")),
        }

    def _row_to_trigger_event(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": clean_text(row.get("id")),
            "plan_id": _int(row.get("plan_id")),
            "endpoint_key": clean_text(row.get("endpoint_key")),
            "event_name": clean_text(row.get("event_name")),
            "source": clean_text(row.get("source")),
            "idempotency_key": clean_text(row.get("idempotency_key")),
            "payload_json": _json_loads(row.get("payload_json"), {}),
            "status": clean_text(row.get("status")),
            "received_at": _iso(row.get("received_at")),
            "processed_at": _iso(row.get("processed_at")),
            "error_message": clean_text(row.get("error_message")),
        }

    def _row_to_execution_log(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        return {
            "id": _int(row.get("id")),
            "trigger_event_id": clean_text(row.get("trigger_event_id")),
            "plan_id": _int(row.get("plan_id")),
            "event_name": clean_text(row.get("event_name")),
            "user_id": clean_text(row.get("user_id")),
            "external_user_id": clean_text(row.get("external_user_id")),
            "sender": _json_loads(row.get("sender"), {}),
            "recipient": _json_loads(row.get("recipient"), {}),
            "layer_key": clean_text(row.get("layer_key")),
            "action_type": clean_text(row.get("action_type")),
            "action_ref_id": clean_text(row.get("action_ref_id")),
            "status": clean_text(row.get("status")),
            "error_message": clean_text(row.get("error_message")),
            "idempotency_key": clean_text(row.get("idempotency_key")),
            "received_at": _iso(row.get("received_at")),
            "processed_at": _iso(row.get("processed_at")),
            "created_at": _iso(row.get("created_at")),
        }

    def _group_binding_params(self, *, group: dict[str, Any], binding_id: int | None = None) -> dict[str, Any]:
        params = {
            "group_name": clean_text(group.get("group_name")),
            "owner_userid": clean_text(group.get("owner_userid")),
            "internal_count": _int(group.get("internal_member_count")),
            "external_count": _int(group.get("external_member_count")),
        }
        if binding_id is not None:
            params["binding_id"] = int(binding_id)
        return params

    def _node_params(self, payload: dict[str, Any]) -> dict[str, Any]:
        scheduled_time = clean_text(payload.get("scheduled_time") or payload.get("trigger_time_label"))
        return {
            "day_index": _int(payload.get("day_index")) or 1,
            "trigger_time_label": scheduled_time,
            "action_title": clean_text(payload.get("action_title")),
            "text_content": clean_text(payload.get("text_content")),
            "attachments_json": _json_dumps(list(payload.get("attachments") or [])),
            "content_package_json": _json_dumps(payload.get("content_package_json") or {}),
            "sort_order": _int(payload.get("sort_order")),
            "status": clean_text(payload.get("status") or "active"),
        }
