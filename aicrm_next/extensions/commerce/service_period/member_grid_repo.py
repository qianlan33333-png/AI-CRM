from __future__ import annotations

import json
from typing import Any, Protocol

from aicrm_next.shared.errors import ContractError, NotFoundError

from .domain import TENANT_ID, isoformat, text, utcnow
from .huangyoucan_usage import huangyoucan_usage_match_joins, huangyoucan_usage_select_fields
from .member_grid import (
    DEFAULT_PAGE_SIZE,
    FIELD_MAP,
    MAX_PAGE_SIZE,
    MemberViewConflictError,
    clone_config,
    decode_cursor,
    empty_view_config,
    encode_cursor,
    normalize_view_config,
    normalize_view_name,
    order_values_for_row,
    public_grid_row,
    query_in_memory_rows,
    sql_filter_clause,
    sql_keyset_clause,
    sql_order_clause,
)


EFFECTIVE_RENEWAL_EVENT_TYPES = ("activated", "renewed", "admin_adjusted")
REFUND_RELATED_ORDER_STATUSES = (
    "requested",
    "processing",
    "refund_processing",
    "partial_refunded",
    "full_refunded",
)


def effective_renewal_count_from_events(
    events: list[dict[str, Any]],
    *,
    service_product_id: str,
    unionid: str,
) -> int:
    """Return valid paid enrollment orders minus the first enrollment."""

    normalized_product_id = text(service_product_id)
    normalized_unionid = text(unionid)
    eligible_orders: set[str] = set()
    refunded_orders: set[str] = set()
    for event in events:
        if text(event.get("service_product_id")) != normalized_product_id:
            continue
        if text(event.get("unionid")) != normalized_unionid:
            continue
        out_trade_no = text(event.get("out_trade_no"))
        if not out_trade_no:
            continue
        event_type = text(event.get("event_type"))
        if event_type == "refunded":
            refunded_orders.add(out_trade_no)
            continue
        if event_type not in EFFECTIVE_RENEWAL_EVENT_TYPES:
            continue
        payload = event.get("payload_json") if isinstance(event.get("payload_json"), dict) else {}
        order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
        if order:
            is_paid = text(order.get("status")).lower() == "paid" or text(order.get("trade_state")).upper() == "SUCCESS"
            if not is_paid:
                continue
            try:
                refunded_amount = int(order.get("refunded_amount_total") or 0)
            except (TypeError, ValueError):
                refunded_amount = 0
            if refunded_amount > 0 or text(order.get("refund_status")).lower() in REFUND_RELATED_ORDER_STATUSES:
                continue
        eligible_orders.add(out_trade_no)
    return max(len(eligible_orders - refunded_orders) - 1, 0)


class MemberGridRepositoryProtocol(Protocol):
    def list_member_views(self, service_product_id: str) -> dict[str, Any]: ...
    def create_member_view(self, service_product_id: str, *, name: str, config: dict[str, Any], actor: str) -> dict[str, Any]: ...
    def update_member_view(self, service_product_id: str, view_id: str, *, name: str, config: dict[str, Any], expected_version: int, actor: str) -> dict[str, Any]: ...
    def delete_member_view(self, service_product_id: str, view_id: str, *, expected_version: int) -> dict[str, Any]: ...
    def query_member_grid(self, service_product_id: str, *, config: dict[str, Any], limit: int, cursor: str) -> dict[str, Any]: ...


def _jsonb(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(
        value if isinstance(value, (dict, list)) else {},
        dumps=lambda data: json.dumps(data, ensure_ascii=False, default=str),
    )


def _serialize_member_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": text(row.get("id")),
        "service_product_id": text(row.get("service_product_id")),
        "name": text(row.get("name")),
        "position": int(row.get("position") or 0),
        "is_default": bool(row.get("is_default")),
        "schema_version": int(row.get("schema_version") or 1),
        "config": clone_config(row.get("config_json") or {}),
        "version": int(row.get("version") or 1),
        "created_by": text(row.get("created_by")),
        "updated_by": text(row.get("updated_by")),
        "created_at": isoformat(row.get("created_at")),
        "updated_at": isoformat(row.get("updated_at")),
    }


class InMemoryMemberGridRepositoryMixin:
    _member_views: list[dict[str, Any]]
    _entitlements: list[dict[str, Any]]
    _next_member_view_id: int

    def list_member_views(self, service_product_id: str) -> dict[str, Any]:
        if not self._find_product(service_product_id):
            raise NotFoundError("service period product not found")
        items = [
            _serialize_member_view(row)
            for row in self._member_views
            if text(row.get("service_product_id")) == text(service_product_id)
        ]
        items.sort(key=lambda item: (int(item.get("position") or 0), text(item.get("id"))))
        return {"ok": True, "items": items}

    def create_member_view(
        self,
        service_product_id: str,
        *,
        name: str,
        config: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        if not self._find_product(service_product_id):
            raise NotFoundError("service period product not found")
        normalized_name = normalize_view_name(name)
        normalized_config = normalize_view_config(config)
        if any(
            text(row.get("service_product_id")) == text(service_product_id)
            and text(row.get("name")).casefold() == normalized_name.casefold()
            for row in self._member_views
        ):
            raise MemberViewConflictError("视图名称已存在")
        positions = [
            int(row.get("position") or 0)
            for row in self._member_views
            if text(row.get("service_product_id")) == text(service_product_id)
        ]
        now = utcnow().isoformat()
        row = {
            "id": f"spv_{self._next_member_view_id:03d}",
            "tenant_id": TENANT_ID,
            "service_product_id": text(service_product_id),
            "name": normalized_name,
            "position": max(positions, default=-1) + 1,
            "is_default": False,
            "schema_version": 1,
            "config_json": clone_config(normalized_config),
            "version": 1,
            "created_by": text(actor) or "system",
            "updated_by": text(actor) or "system",
            "created_at": now,
            "updated_at": now,
        }
        self._next_member_view_id += 1
        self._member_views.append(row)
        return {"ok": True, "view": _serialize_member_view(row)}

    def update_member_view(
        self,
        service_product_id: str,
        view_id: str,
        *,
        name: str,
        config: dict[str, Any],
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        row = self._find_member_view(service_product_id, view_id)
        if not row:
            raise NotFoundError("member view not found")
        if int(row.get("version") or 0) != int(expected_version or 0):
            raise MemberViewConflictError("视图已被其他管理员更新")
        normalized_name = normalize_view_name(name)
        if any(
            other is not row
            and text(other.get("service_product_id")) == text(service_product_id)
            and text(other.get("name")).casefold() == normalized_name.casefold()
            for other in self._member_views
        ):
            raise MemberViewConflictError("视图名称已存在")
        row.update(
            {
                "name": normalized_name,
                "config_json": clone_config(config),
                "schema_version": 1,
                "version": int(row.get("version") or 0) + 1,
                "updated_by": text(actor) or "system",
                "updated_at": utcnow().isoformat(),
            }
        )
        return {"ok": True, "view": _serialize_member_view(row)}

    def delete_member_view(self, service_product_id: str, view_id: str, *, expected_version: int) -> dict[str, Any]:
        row = self._find_member_view(service_product_id, view_id)
        if not row:
            raise NotFoundError("member view not found")
        if bool(row.get("is_default")):
            raise ContractError("默认视图不能删除")
        if int(row.get("version") or 0) != int(expected_version or 0):
            raise MemberViewConflictError("视图已被其他管理员更新")
        self._member_views = [item for item in self._member_views if item is not row]
        return {"ok": True, "deleted": True, "view_id": text(view_id)}

    def query_member_grid(
        self,
        service_product_id: str,
        *,
        config: dict[str, Any],
        limit: int,
        cursor: str,
    ) -> dict[str, Any]:
        if not self._find_product(service_product_id):
            raise NotFoundError("service period product not found")
        now = utcnow()
        members: list[dict[str, Any]] = []
        for index, row in enumerate(self._entitlements, start=1):
            if text(row.get("service_product_id")) != text(service_product_id):
                continue
            item = self._member_payload(row, now=now)
            try:
                item["record_id"] = int(text(row.get("id")).rsplit("_", 1)[-1])
            except (TypeError, ValueError):
                item["record_id"] = index
            members.append(item)
        return query_in_memory_rows(members, config=config, limit=limit, cursor=cursor)

    def _find_member_view(self, service_product_id: Any, view_id: Any) -> dict[str, Any] | None:
        for row in self._member_views:
            if text(row.get("service_product_id")) == text(service_product_id) and text(row.get("id")) == text(view_id):
                return row
        return None

    def _append_default_member_view(self, service_product_id: str, *, actor: str) -> dict[str, Any]:
        now = utcnow().isoformat()
        row = {
            "id": f"spv_{self._next_member_view_id:03d}",
            "tenant_id": TENANT_ID,
            "service_product_id": text(service_product_id),
            "name": "表格",
            "position": 0,
            "is_default": True,
            "schema_version": 1,
            "config_json": empty_view_config(),
            "version": 1,
            "created_by": text(actor) or "system",
            "updated_by": text(actor) or "system",
            "created_at": now,
            "updated_at": now,
        }
        self._next_member_view_id += 1
        self._member_views.append(row)
        return row

    def _delete_member_views(self, service_product_id: str) -> None:
        self._member_views = [
            item
            for item in self._member_views
            if text(item.get("service_product_id")) != text(service_product_id)
        ]


class PostgresMemberGridRepositoryMixin:
    def _insert_default_member_view(self, conn: Any, service_product_id: Any, *, actor: str) -> None:
        conn.execute(
            """
            INSERT INTO service_period_member_views (
                tenant_id, service_product_id, name, position, is_default,
                schema_version, config_json, version, created_by, updated_by,
                created_at, updated_at
            )
            VALUES (
                'aicrm', %s, '表格', 0, TRUE,
                1, %s::jsonb, 1, %s, %s,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (int(service_product_id), _jsonb(empty_view_config()), text(actor) or "system", text(actor) or "system"),
        )

    def list_member_views(self, service_product_id: str) -> dict[str, Any]:
        if not self.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM service_period_member_views
                WHERE tenant_id = 'aicrm'
                  AND service_product_id::text = %s
                ORDER BY position ASC, id ASC
                """,
                (text(service_product_id),),
            ).fetchall()
        return {"ok": True, "items": [_serialize_member_view(dict(row)) for row in rows]}

    def create_member_view(
        self,
        service_product_id: str,
        *,
        name: str,
        config: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        if not self.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        normalized_name = normalize_view_name(name)
        normalized_config = normalize_view_config(config)
        with self._connect() as conn:
            try:
                row = conn.execute(
                    """
                    INSERT INTO service_period_member_views (
                        tenant_id, service_product_id, name, position, is_default,
                        schema_version, config_json, version, created_by, updated_by,
                        created_at, updated_at
                    )
                    SELECT
                        'aicrm', %s, %s, COALESCE(MAX(position), -1) + 1,
                        FALSE, 1, %s::jsonb, 1, %s, %s,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM service_period_member_views
                    WHERE tenant_id = 'aicrm'
                      AND service_product_id::text = %s
                    RETURNING *
                    """,
                    (
                        int(service_product_id),
                        normalized_name,
                        _jsonb(normalized_config),
                        text(actor) or "system",
                        text(actor) or "system",
                        text(service_product_id),
                    ),
                ).fetchone()
                conn.commit()
            except Exception as exc:
                conn.rollback()
                if text(getattr(exc, "sqlstate", "")) == "23505":
                    raise MemberViewConflictError("视图名称已存在") from exc
                raise
        return {"ok": True, "view": _serialize_member_view(dict(row))}

    def update_member_view(
        self,
        service_product_id: str,
        view_id: str,
        *,
        name: str,
        config: dict[str, Any],
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        normalized_name = normalize_view_name(name)
        normalized_config = normalize_view_config(config)
        with self._connect() as conn:
            current = conn.execute(
                """
                SELECT *
                FROM service_period_member_views
                WHERE tenant_id = 'aicrm'
                  AND service_product_id::text = %s
                  AND id::text = %s
                FOR UPDATE
                """,
                (text(service_product_id), text(view_id)),
            ).fetchone()
            if not current:
                raise NotFoundError("member view not found")
            if int(current.get("version") or 0) != int(expected_version or 0):
                raise MemberViewConflictError("视图已被其他管理员更新")
            try:
                row = conn.execute(
                    """
                    UPDATE service_period_member_views
                    SET name = %s,
                        schema_version = 1,
                        config_json = %s::jsonb,
                        version = version + 1,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE tenant_id = 'aicrm'
                      AND service_product_id::text = %s
                      AND id::text = %s
                      AND version = %s
                    RETURNING *
                    """,
                    (
                        normalized_name,
                        _jsonb(normalized_config),
                        text(actor) or "system",
                        text(service_product_id),
                        text(view_id),
                        int(expected_version),
                    ),
                ).fetchone()
                if not row:
                    raise MemberViewConflictError("视图已被其他管理员更新")
                conn.commit()
            except MemberViewConflictError:
                conn.rollback()
                raise
            except Exception as exc:
                conn.rollback()
                if text(getattr(exc, "sqlstate", "")) == "23505":
                    raise MemberViewConflictError("视图名称已存在") from exc
                raise
        return {"ok": True, "view": _serialize_member_view(dict(row))}

    def delete_member_view(self, service_product_id: str, view_id: str, *, expected_version: int) -> dict[str, Any]:
        with self._connect() as conn:
            current = conn.execute(
                """
                SELECT id, is_default, version
                FROM service_period_member_views
                WHERE tenant_id = 'aicrm'
                  AND service_product_id::text = %s
                  AND id::text = %s
                FOR UPDATE
                """,
                (text(service_product_id), text(view_id)),
            ).fetchone()
            if not current:
                raise NotFoundError("member view not found")
            if bool(current.get("is_default")):
                raise ContractError("默认视图不能删除")
            if int(current.get("version") or 0) != int(expected_version or 0):
                raise MemberViewConflictError("视图已被其他管理员更新")
            deleted = conn.execute(
                """
                DELETE FROM service_period_member_views
                WHERE tenant_id = 'aicrm'
                  AND service_product_id::text = %s
                  AND id::text = %s
                  AND version = %s
                RETURNING id
                """,
                (text(service_product_id), text(view_id), int(expected_version)),
            ).fetchone()
            if not deleted:
                conn.rollback()
                raise MemberViewConflictError("视图已被其他管理员更新")
            conn.commit()
        return {"ok": True, "deleted": True, "view_id": text(view_id)}

    def query_member_grid(
        self,
        service_product_id: str,
        *,
        config: dict[str, Any],
        limit: int,
        cursor: str,
    ) -> dict[str, Any]:
        if not self.get_product(service_product_id):
            raise NotFoundError("service period product not found")
        normalized_config = normalize_view_config(config)
        decoded = decode_cursor(cursor, config=normalized_config) if cursor else {}
        snapshot_at = decoded.get("snapshot_at") or utcnow()
        page_size = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
        filter_clause, filter_params = sql_filter_clause(normalized_config)
        cursor_keys = decoded.get("keys") or []
        keyset_clause, keyset_params = (
            sql_keyset_clause(normalized_config, cursor_keys) if cursor_keys else ("TRUE", [])
        )
        group_count_columns: list[str] = []
        group_aliases: list[str] = []
        for index, group in enumerate(normalized_config.get("groups") or []):
            group_aliases.append(FIELD_MAP[group["field"]].group_partition_alias)
            group_count_columns.append(
                f"COUNT(*) OVER (PARTITION BY {', '.join(group_aliases)}) AS _group_count_{index + 1}"
            )
        group_count_sql = ",\n                    " + ",\n                    ".join(group_count_columns) if group_count_columns else ""
        order_clause = sql_order_clause(normalized_config)
        matched = "raw.huangyoucan_match_status IN ('matched_unionid', 'matched_mobile')"
        progress_state = f"""
            CASE
                WHEN NOT ({matched}) THEN 'unmatched'
                WHEN raw.huangyoucan_learning_plan_current IS NULL
                  OR raw.huangyoucan_learning_plan_total IS NULL
                  OR raw.huangyoucan_learning_plan_total <= 0 THEN 'no_plan'
                WHEN raw.huangyoucan_learning_plan_current <= 0 THEN 'not_started'
                WHEN raw.huangyoucan_learning_plan_current >= raw.huangyoucan_learning_plan_total THEN 'complete'
                ELSE 'in_progress'
            END
        """.strip()
        last_open = f"CASE WHEN {matched} THEN raw.huangyoucan_last_open_at ELSE NULL END"
        params: list[Any] = [
            text(service_product_id),
            list(EFFECTIVE_RENEWAL_EVENT_TYPES),
            list(REFUND_RELATED_ORDER_STATUSES),
            text(service_product_id),
            snapshot_at,
            snapshot_at,
            *filter_params,
            *keyset_params,
            page_size + 1,
        ]
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                WITH effective_order_counts AS MATERIALIZED (
                    SELECT
                        source.service_product_id,
                        source.unionid,
                        GREATEST(
                            COUNT(DISTINCT COALESCE(NULLIF(paid_order.out_trade_no, ''), paid_order.id::text)) - 1,
                            0
                        )::integer AS renewal_count
                    FROM service_period_events source
                    JOIN wechat_pay_orders paid_order
                      ON paid_order.id = source.order_id
                      OR (
                          source.order_id IS NULL
                          AND source.out_trade_no <> ''
                          AND paid_order.out_trade_no = source.out_trade_no
                      )
                    WHERE source.tenant_id = 'aicrm'
                      AND source.service_product_id::text = %s
                      AND source.event_type = ANY(%s)
                      AND source.unionid <> ''
                      AND source.out_trade_no <> ''
                      AND (paid_order.status = 'paid' OR paid_order.trade_state = 'SUCCESS')
                      AND COALESCE(paid_order.refunded_amount_total, 0) = 0
                      AND NOT (LOWER(COALESCE(paid_order.refund_status, '')) = ANY(%s))
                      AND NOT EXISTS (
                          SELECT 1
                          FROM service_period_events refunded
                          WHERE refunded.tenant_id = source.tenant_id
                            AND refunded.service_product_id = source.service_product_id
                            AND refunded.event_type = 'refunded'
                            AND refunded.out_trade_no = source.out_trade_no
                      )
                    GROUP BY source.service_product_id, source.unionid
                ),
                raw_members AS (
                    SELECT
                        e.id AS record_id,
                        e.unionid,
                        e.end_at,
                        COALESCE(effective_orders.renewal_count, 0)::integer AS renewal_count,
                        COALESCE(
                            NULLIF(c.remark, ''),
                            NULLIF(wfu.remark, ''),
                            NULLIF(NULLIF(c.customer_name, ''), '问卷提交用户'),
                            NULLIF(NULLIF(c.profile_json->>'name', ''), '问卷提交用户'),
                            NULLIF(wim.name, ''),
                            NULLIF(c.customer_name, ''),
                            NULLIF(e.metadata_json->>'payer_name', ''),
                            NULLIF(o.payer_name_snapshot, ''),
                            e.unionid
                        ) AS display_name,
                        COALESCE(
                            NULLIF(e.external_userid_snapshot, ''),
                            NULLIF(c.primary_external_userid, ''),
                            NULLIF(wim.external_userid, '')
                        ) AS external_userid,
                        COALESCE(NULLIF(c.mobile, ''), NULLIF(c.mobile_normalized, '')) AS mobile,
                        COALESCE(NULLIF(e.metadata_json->>'admin_remark', ''), NULLIF(e.metadata_json->>'remark', ''), '') AS remark,
                        COALESCE(NULLIF(e.metadata_json->>'admin_alliance', ''), '') AS alliance,
                        {huangyoucan_usage_select_fields()}
                    FROM service_period_entitlements e
                    LEFT JOIN effective_order_counts effective_orders
                      ON effective_orders.service_product_id = e.service_product_id
                     AND effective_orders.unionid = e.unionid
                    LEFT JOIN wechat_pay_orders o ON o.id = e.last_order_id
                    LEFT JOIN crm_user_identity c ON c.unionid = e.unionid
                    LEFT JOIN LATERAL (
                        SELECT im.external_userid, im.name
                        FROM wecom_external_contact_identity_map im
                        WHERE im.unionid = e.unionid
                        ORDER BY im.updated_at DESC NULLS LAST, im.id DESC
                        LIMIT 1
                    ) wim ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT fu.remark
                        FROM wecom_external_contact_follow_users fu
                        WHERE fu.external_userid = COALESCE(
                            NULLIF(e.external_userid_snapshot, ''),
                            NULLIF(c.primary_external_userid, ''),
                            NULLIF(wim.external_userid, '')
                        )
                          AND COALESCE(fu.relation_status, 'active') = 'active'
                        ORDER BY fu.is_primary DESC NULLS LAST, fu.updated_at DESC NULLS LAST, fu.id DESC
                        LIMIT 1
                    ) wfu ON TRUE
                    {huangyoucan_usage_match_joins(unionid_sql="e.unionid", mobile_sql="COALESCE(NULLIF(c.mobile, ''), NULLIF(c.mobile_normalized, ''))")}
                    WHERE e.tenant_id = 'aicrm'
                      AND e.service_product_id::text = %s
                      AND e.created_at <= %s::timestamptz
                ),
                member_rows AS (
                    SELECT
                        raw.*,
                        LOWER(BTRIM(COALESCE(raw.display_name, raw.unionid))) AS member_sort,
                        NULLIF(LOWER(BTRIM(COALESCE(raw.display_name, raw.unionid))), '') AS member_group,
                        LOWER(CONCAT_WS(' ', raw.display_name, raw.external_userid, raw.unionid)) AS member_search,
                        GREATEST(
                            0,
                            CEIL(EXTRACT(EPOCH FROM (raw.end_at - %s::timestamptz)) / 86400.0)
                        )::integer AS remaining_days,
                        CASE WHEN {matched}
                            THEN CASE WHEN raw.huangyoucan_formally_logged_in THEN 'yes' ELSE 'no' END
                            ELSE 'unmatched'
                        END AS formally_logged_in,
                        CASE WHEN {matched}
                            THEN CASE WHEN raw.huangyoucan_formally_logged_in THEN 0 ELSE 1 END
                            ELSE 2
                        END AS formally_logged_in_rank,
                        CASE WHEN {matched}
                            THEN CASE WHEN raw.huangyoucan_has_token_usage THEN 'yes' ELSE 'no' END
                            ELSE 'unmatched'
                        END AS token_usage,
                        CASE WHEN {matched}
                            THEN CASE WHEN raw.huangyoucan_has_token_usage THEN 0 ELSE 1 END
                            ELSE 2
                        END AS token_usage_rank,
                        {progress_state} AS progress_state,
                        CASE {progress_state}
                            WHEN 'unmatched' THEN 0
                            WHEN 'no_plan' THEN 1
                            WHEN 'not_started' THEN 2
                            WHEN 'in_progress' THEN 3
                            WHEN 'complete' THEN 4
                            ELSE 5
                        END AS progress_state_rank,
                        CASE
                            WHEN {matched}
                              AND raw.huangyoucan_learning_plan_current IS NOT NULL
                              AND raw.huangyoucan_learning_plan_total > 0
                            THEN ROUND(
                                LEAST(
                                    100.0,
                                    GREATEST(
                                        0.0,
                                        raw.huangyoucan_learning_plan_current::numeric
                                        / NULLIF(raw.huangyoucan_learning_plan_total, 0)::numeric
                                        * 100.0
                                    )
                                ),
                                4
                            )
                            ELSE NULL
                        END AS progress_ratio,
                        raw.huangyoucan_learning_plan_current AS progress_current,
                        raw.huangyoucan_learning_plan_total AS progress_total,
                        CASE WHEN {matched} THEN COALESCE(raw.huangyoucan_open_count_7d, 0) ELSE NULL END AS open_count_7d,
                        {last_open} AS last_open_at,
                        ({last_open} AT TIME ZONE 'Asia/Shanghai')::date AS last_open_date,
                        NULLIF(LOWER(BTRIM(raw.remark)), '') AS remark_sort,
                        NULLIF(LOWER(BTRIM(raw.remark)), '') AS remark_group,
                        LOWER(COALESCE(raw.remark, '')) AS remark_search,
                        NULLIF(LOWER(BTRIM(raw.alliance)), '') AS alliance_sort,
                        NULLIF(LOWER(BTRIM(raw.alliance)), '') AS alliance_group,
                        LOWER(COALESCE(raw.alliance, '')) AS alliance_search
                    FROM raw_members raw
                ),
                filtered AS (
                    SELECT *
                    FROM member_rows
                    WHERE {filter_clause}
                ),
                ranked AS (
                    SELECT
                        filtered.*
                        {group_count_sql}
                    FROM filtered
                )
                SELECT *
                FROM ranked
                WHERE {keyset_clause}
                ORDER BY {order_clause}
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        page = [dict(row) for row in rows[:page_size]]
        has_more = len(rows) > page_size
        next_cursor = ""
        if has_more and page:
            next_cursor = encode_cursor(
                config=normalized_config,
                snapshot_at=snapshot_at,
                keys=order_values_for_row(page[-1], normalized_config),
            )
        return {
            "ok": True,
            "rows": [public_grid_row(row, normalized_config) for row in page],
            "total": None,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "snapshot_at": snapshot_at.isoformat(),
            "page_size": page_size,
        }
