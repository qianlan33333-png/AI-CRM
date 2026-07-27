from __future__ import annotations

import json
from typing import Any

from aicrm_next.shared.repository_provider import RepositoryProviderError

from .repo import HxcDashboardBroadcastRepository, _build_audience_preview, connect_hxc_dashboard_broadcast_db, new_task_id


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


class PostgresHxcDashboardBroadcastRepository(HxcDashboardBroadcastRepository):
    source_status = "production_postgres_hxc_dashboard"

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self):
        return connect_hxc_dashboard_broadcast_db(self._database_url)

    def preview_audience(
        self,
        *,
        selected_customer_ids: list[str],
        audience_filter: dict[str, Any],
        sender_userid: str,
    ) -> dict[str, Any]:
        selected = [str(item or "").strip() for item in selected_customer_ids if str(item or "").strip()]
        projection = """
            WITH snapshot_identity AS (
                SELECT
                    identity.unionid,
                    COALESCE(identity.primary_external_userid, '') AS resolved_external_userid,
                    s.owner_userid,
                    s.funnel_state,
                    s.phone_match_key,
                    s.refreshed_at
                FROM user_ops_hxc_dashboard_snapshot s
                JOIN crm_user_identity identity ON identity.unionid = s.unionid
                WHERE COALESCE(s.unionid, '') <> ''
                  AND identity.identity_status = 'active'
            )
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    if selected:
                        cur.execute(
                            projection
                            + """
                            SELECT unionid, resolved_external_userid AS external_userid, owner_userid, funnel_state,
                                   EXISTS (
                                       SELECT 1
                                       FROM user_ops_do_not_disturb_next dnd
                                       WHERE dnd.is_active = TRUE
                                         AND dnd.unionid <> ''
                                         AND dnd.unionid = s.unionid
                                   ) AS do_not_disturb
                            FROM snapshot_identity s
                            WHERE s.unionid = ANY(%s)
                            """,
                            (selected,),
                        )
                    else:
                        cur.execute(
                            projection
                            + """
                            SELECT unionid, resolved_external_userid AS external_userid, owner_userid, funnel_state,
                                   EXISTS (
                                       SELECT 1
                                       FROM user_ops_do_not_disturb_next dnd
                                       WHERE dnd.is_active = TRUE
                                         AND dnd.unionid <> ''
                                         AND dnd.unionid = s.unionid
                                   ) AS do_not_disturb
                            FROM snapshot_identity s
                            ORDER BY refreshed_at DESC
                            LIMIT 5000
                            """
                        )
                    rows = [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            raise RepositoryProviderError(f"HXC 群发生产人群预览不可用：{exc}") from exc
        return _build_audience_preview(rows)

    def get_task_by_key(self, *, source_type: str, source_id: str, idempotency_key: str) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM hxc_dashboard_broadcast_tasks
                        WHERE source_type = %s
                          AND source_id = %s
                          AND idempotency_key = %s
                        LIMIT 1
                        """,
                        (source_type, source_id, idempotency_key),
                    )
                    row = cur.fetchone()
        except Exception as exc:
            raise RepositoryProviderError(f"HXC 群发任务表不可用：{exc}") from exc
        return self._row_to_task(dict(row)) if row else None

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = new_task_id()
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO hxc_dashboard_broadcast_tasks (
                            task_id, source_type, source_id, idempotency_key, sender_userid,
                            audience_filter, selected_customer_ids, content_package,
                            audience_total, eligible_count, skipped_count, skipped_by_reason,
                            status, dispatch_status
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, %s::jsonb,
                            %s, %s, %s, %s::jsonb,
                            %s, %s
                        )
                        ON CONFLICT (source_type, source_id, idempotency_key)
                        DO NOTHING
                        RETURNING *
                        """,
                        (
                            task_id,
                            payload["source_type"],
                            payload["source_id"],
                            payload["idempotency_key"],
                            payload["sender_userid"],
                            _json_dumps(payload.get("audience_filter") or {}),
                            _json_dumps(payload.get("selected_customer_ids") or []),
                            _json_dumps(payload.get("content_package") or {}),
                            int(payload.get("audience_total") or 0),
                            int(payload.get("eligible_count") or 0),
                            int(payload.get("skipped_count") or 0),
                            _json_dumps(payload.get("skipped_by_reason") or {}),
                            "created",
                            "pending_external_dispatch",
                        ),
                    )
                    row = cur.fetchone()
                    conn.commit()
            if row:
                return self._row_to_task(dict(row))
            existing = self.get_task_by_key(
                source_type=payload["source_type"],
                source_id=payload["source_id"],
                idempotency_key=payload["idempotency_key"],
            )
            if existing:
                return existing
        except Exception as exc:
            raise RepositoryProviderError(f"HXC 群发任务创建失败：{exc}") from exc
        raise RepositoryProviderError("HXC 群发任务创建失败：未返回任务记录")

    def _row_to_task(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": str(row.get("task_id") or row.get("id") or ""),
            "status": str(row.get("status") or "created"),
            "dispatch_status": str(row.get("dispatch_status") or "pending_external_dispatch"),
            "source_status": self.source_status,
            "source_type": str(row.get("source_type") or ""),
            "source_id": str(row.get("source_id") or ""),
            "idempotency_key": str(row.get("idempotency_key") or ""),
            "sender_userid": str(row.get("sender_userid") or ""),
            "audience_filter": _json_loads(row.get("audience_filter"), {}),
            "selected_customer_ids": _json_loads(row.get("selected_customer_ids"), []),
            "content_package": _json_loads(row.get("content_package"), {}),
            "audience_total": int(row.get("audience_total") or 0),
            "eligible_count": int(row.get("eligible_count") or 0),
            "skipped_count": int(row.get("skipped_count") or 0),
            "skipped_by_reason": _json_loads(row.get("skipped_by_reason"), {}),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }
