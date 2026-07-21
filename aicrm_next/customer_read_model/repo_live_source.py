from __future__ import annotations


from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from aicrm_next.identity_contact.dto import ResolvePersonIdentityRequest
from aicrm_next.identity_contact.resolver import SQLAlchemyIdentityResolver, classify_identity_candidates
from aicrm_next.shared.typing import JsonDict

from .sql_dialect import is_sqlite_session, json_text_expression

from .repo import (
    _DEFAULT_LIVE_SOURCE_LIST_LIMIT,
    _apply_page,
    _iso,
    _json_dict,
    _json_list,
    _normalize_bool_filter,
)


def _external_userids_statement(session: Session, sql: str):
    """Bind one customer scope without expanding it past driver limits."""

    if is_sqlite_session(session):
        return text(sql).bindparams(bindparam("external_userids", expanding=True))
    return text(
        sql.replace(
            "IN :external_userids",
            "= ANY(CAST(:external_userids AS TEXT[]))",
        )
    )


class LiveSourceCustomerReadRepository:
    """Read live customer data from production source tables when projections are not ready."""

    source_name = "live_source"

    def __init__(self, session: Session) -> None:
        self._session = session

    def close(self) -> None:
        try:
            self._session.rollback()
        finally:
            self._session.close()

    def list_customers(
        self,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
        external_userids: set[str] | None = None,
    ) -> list[JsonDict]:
        effective_filters = dict(filters or {})
        normalized_external_userids = {str(item or "").strip() for item in (external_userids or set()) if str(item or "").strip()}
        if len(normalized_external_userids) == 1 and not effective_filters.get("external_userid"):
            effective_filters["external_userid"] = next(iter(normalized_external_userids))
        rows = self._customer_rows(effective_filters, limit=limit, offset=offset)
        if normalized_external_userids:
            rows = [row for row in rows if str(row.get("external_userid") or "").strip() in normalized_external_userids]
        return self._decorate_customer_rows(rows)

    def count_customers(self, filters: JsonDict | None = None) -> int:
        where, params = self._customer_where(filters or {})
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = self._customer_decorated_sql(where_sql, "COUNT(*) AS total")
        return int(self._session.execute(text(sql), params).scalar_one() or 0)

    def get_customer(self, external_userid: str) -> JsonDict | None:
        rows = self._customer_rows({"external_userid": str(external_userid or "").strip()}, limit=1, offset=0)
        customers = self._decorate_customer_rows(rows)
        return customers[0] if customers else None

    get_customer_detail = get_customer

    def get_customer_by_unionid(self, unionid: str) -> JsonDict | None:
        rows = self._customer_rows({"unionid": str(unionid or "").strip()}, limit=1, offset=0)
        customers = self._decorate_customer_rows(rows)
        return customers[0] if customers else None

    def list_timeline(
        self,
        external_userid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        event_type = str((filters or {}).get("event_type") or "").strip()
        messages = [
            {
                "event_id": f"message:{item.get('source_id') or item.get('msgid')}",
                "event_type": "message",
                "event_time": item.get("send_time"),
                "title": f"消息 · {item.get('msgtype') or 'unknown'}",
                "summary": item.get("content") or "",
                "source_table": "archived_messages",
                "source_id": str(item.get("source_id") or ""),
                "metadata": dict(item),
            }
            for item in self.list_recent_messages(external_userid, limit=(limit or 50) + offset)
        ]
        if event_type:
            messages = [item for item in messages if item.get("event_type") == event_type]
        return _apply_page(messages, limit=limit, offset=offset)

    get_customer_timeline = list_timeline

    def list_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]:
        identity = self._identity_by_external_userid(str(external_userid or "").strip())
        unionid = str(identity.get("unionid") or "").strip()
        if not unionid:
            return []
        return self.list_recent_messages_by_unionid(unionid, limit=limit)

    get_recent_messages = list_recent_messages

    def list_timeline_by_unionid(
        self,
        unionid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        event_type = str((filters or {}).get("event_type") or "").strip()
        messages = [
            {
                "event_id": f"message:{item.get('source_id') or item.get('msgid')}",
                "event_type": "message",
                "event_time": item.get("send_time"),
                "title": f"消息 · {item.get('msgtype') or 'unknown'}",
                "summary": item.get("content") or "",
                "source_table": "archived_messages",
                "source_id": str(item.get("source_id") or ""),
                "metadata": dict(item),
            }
            for item in self.list_recent_messages_by_unionid(unionid, limit=(limit or 50) + offset)
        ]
        if event_type:
            messages = [item for item in messages if item.get("event_type") == event_type]
        return _apply_page(messages, limit=limit, offset=offset)

    def list_recent_messages_by_unionid(self, unionid: str, *, limit: int | None = None) -> list[JsonDict]:
        rows = self._session.execute(
            text(
                """
                SELECT id, msgid, chat_type, unionid, owner_userid, sender, receiver,
                       msgtype, content, send_time, raw_payload, created_at
                FROM archived_messages
                WHERE unionid = :unionid
                ORDER BY send_time DESC, id DESC
                LIMIT :limit
                """
            ),
            {"unionid": str(unionid or "").strip(), "limit": max(1, int(limit or 20))},
        ).mappings()
        return [
            {
                "msgid": row.get("msgid") or "",
                "unionid": row.get("unionid") or "",
                "msgtype": row.get("msgtype") or "text",
                "content": row.get("content") or "",
                "send_time": _iso(row.get("send_time")),
                "owner_userid": row.get("owner_userid") or "",
                "chat_type": row.get("chat_type") or "single",
                "sender": row.get("sender") or "",
                "receiver": row.get("receiver") or "",
                "source_id": str(row.get("id") or ""),
                "raw_payload": row.get("raw_payload") or "",
            }
            for row in rows
        ]

    def snapshot_recent_messages_by_unionid(
        self,
        unionids: list[str],
        *,
        per_customer_limit: int = 100,
    ) -> dict[str, list[JsonDict]]:
        """Read a bounded recent-message snapshot in one production-safe query."""

        normalized = [str(value or "").strip() for value in unionids if str(value or "").strip()]
        if not normalized:
            return {}
        stmt = _external_userids_statement(
            self._session,
            """
            WITH ranked AS (
                SELECT id, msgid, chat_type, unionid, owner_userid, msgtype, content,
                       COALESCE(
                           NULLIF(CAST(send_time AS TEXT), ''),
                           CAST(created_at AS TEXT)
                       ) AS send_time,
                       ROW_NUMBER() OVER (
                           PARTITION BY unionid
                           ORDER BY COALESCE(
                               NULLIF(CAST(send_time AS TEXT), ''),
                               CAST(created_at AS TEXT)
                           ) DESC, id DESC
                       ) AS row_number
                FROM archived_messages
                WHERE unionid IN :external_userids
            )
            SELECT id, msgid, chat_type, unionid, owner_userid, msgtype, content, send_time
            FROM ranked
            WHERE row_number <= :per_customer_limit
            ORDER BY unionid ASC, send_time DESC, id DESC
            """,
        )
        rows = self._session.execute(
            stmt,
            {
                "external_userids": normalized,
                "per_customer_limit": max(1, min(int(per_customer_limit or 100), 500)),
            },
        ).mappings()
        result: dict[str, list[JsonDict]] = {}
        for row in rows:
            unionid = str(row.get("unionid") or "").strip()
            if not unionid:
                continue
            result.setdefault(unionid, []).append(
                {
                    "msgid": row.get("msgid") or "",
                    "unionid": unionid,
                    "msgtype": row.get("msgtype") or "text",
                    "content": row.get("content") or "",
                    "send_time": _iso(row.get("send_time")),
                    "owner_userid": row.get("owner_userid") or "",
                    "chat_type": row.get("chat_type") or "single",
                    "source_id": str(row.get("id") or ""),
                }
            )
        return result

    def snapshot_customer_activity_by_unionid(
        self,
        unionids: list[str],
        *,
        per_customer_limit: int = 500,
    ) -> dict[str, list[JsonDict]]:
        """Reconcile four business event types with one set-based source query."""

        normalized = [str(value or "").strip() for value in unionids if str(value or "").strip()]
        if not normalized:
            return {}
        sqlite = is_sqlite_session(self._session)
        statement_sql = """
            WITH __REQUESTED_UNIONIDS_CTE__ source_events AS (
                SELECT
                    'channel_entry:' || entry.event_log_id::text AS event_id,
                    entry.unionid,
                    'channel_entry'::text AS event_type,
                    entry.created_at AS event_time,
                    '扫码进入渠道' || CASE WHEN COALESCE(channel.channel_name, '') <> '' THEN ' · ' || channel.channel_name ELSE '' END AS title,
                    COALESCE(NULLIF(channel.channel_name, ''), NULLIF(entry.scene_value, ''), '通过渠道码进入') AS summary,
                    'automation_channel_entry_effect_log'::text AS source_table,
                    entry.event_log_id::text AS source_id,
                    jsonb_build_object(
                        'channel_name', COALESCE(channel.channel_name, ''),
                        'channel_code', COALESCE(NULLIF(channel.channel_code, ''), entry.scene_value, '')
                    ) AS metadata_json
                FROM (
                    SELECT DISTINCT ON (event_log_id, unionid)
                           event_log_id, unionid, channel_id, scene_value, created_at
                    FROM automation_channel_entry_effect_log
                    WHERE event_log_id IS NOT NULL
                      AND unionid __REQUESTED_UNIONIDS_FILTER__
                    ORDER BY event_log_id, unionid, created_at ASC, id ASC
                ) entry
                LEFT JOIN automation_channel channel ON channel.id = entry.channel_id

                UNION ALL

                SELECT
                    'questionnaire:' || submission.id::text,
                    submission.unionid,
                    'questionnaire_submitted',
                    COALESCE(submission.submitted_at, submission.created_at),
                    '提交问卷 · ' || COALESCE(NULLIF(questionnaire.title, ''), NULLIF(questionnaire.name, ''), '问卷'),
                    '已完成问卷提交',
                    'questionnaire_submissions',
                    submission.id::text,
                    jsonb_build_object(
                        'questionnaire_id', submission.questionnaire_id::text,
                        'questionnaire_title', COALESCE(NULLIF(questionnaire.title, ''), NULLIF(questionnaire.name, ''), '问卷')
                    )
                FROM questionnaire_submissions submission
                LEFT JOIN questionnaires questionnaire ON questionnaire.id = submission.questionnaire_id
                WHERE submission.unionid __REQUESTED_UNIONIDS_FILTER__

                UNION ALL

                SELECT
                    'product:payment:' || payment.out_trade_no,
                    payment.unionid,
                    'product_enrolled',
                    COALESCE(payment.paid_at, payment.updated_at, payment.created_at),
                    '报名或支付成功 · ' || COALESCE(NULLIF(payment.product_name, ''), NULLIF(payment.product_code, ''), '商品'),
                    '已完成商品报名或支付',
                    'wechat_pay_orders',
                    payment.out_trade_no,
                    jsonb_build_object(
                        'product_id', COALESCE(payment.product_code, ''),
                        'product_title', COALESCE(NULLIF(payment.product_name, ''), NULLIF(payment.product_code, ''), '商品'),
                        'product_type', 'standard_product'
                    )
                FROM wechat_pay_orders payment
                WHERE payment.unionid __REQUESTED_UNIONIDS_FILTER__
                  AND (payment.status = 'paid' OR payment.trade_state = 'SUCCESS')
                  AND COALESCE(payment.out_trade_no, '') <> ''

                UNION ALL

                SELECT
                    'product:wechat_shop:' || shop_order.order_id,
                    shop_order.unionid,
                    'product_enrolled',
                    COALESCE(shop_order.paid_at, shop_order.updated_at, shop_order.created_at),
                    '报名或支付成功 · ' || COALESCE(NULLIF(shop_order.product_name, ''), NULLIF(shop_order.product_code, ''), '微信小店商品'),
                    '已完成商品报名或支付',
                    'wechat_shop_orders',
                    shop_order.order_id,
                    jsonb_build_object(
                        'product_id', COALESCE(shop_order.product_code, ''),
                        'product_title', COALESCE(NULLIF(shop_order.product_name, ''), NULLIF(shop_order.product_code, ''), '微信小店商品'),
                        'product_type', 'wechat_shop'
                    )
                FROM wechat_shop_orders shop_order
                WHERE shop_order.unionid __REQUESTED_UNIONIDS_FILTER__
                  AND shop_order.deal_recorded IS TRUE
                  AND COALESCE(shop_order.order_id, '') <> ''

                UNION ALL

                SELECT
                    CASE WHEN COALESCE(period_event.out_trade_no, '') <> ''
                         THEN 'product:payment:' || period_event.out_trade_no
                         ELSE 'product:service_period:' || period_event.event_id END,
                    period_event.unionid,
                    'product_enrolled',
                    period_event.created_at,
                    '报名或支付成功 · ' || COALESCE(NULLIF(trade_product.name, ''), NULLIF(trade_product.product_code, ''), '周期商品'),
                    '已确认周期商品报名或激活',
                    'service_period_events',
                    period_event.event_id,
                    jsonb_build_object(
                        'product_id', period_event.trade_product_id::text,
                        'product_title', COALESCE(NULLIF(trade_product.name, ''), NULLIF(trade_product.product_code, ''), '周期商品'),
                        'product_type', 'service_period'
                    )
                FROM service_period_events period_event
                LEFT JOIN wechat_pay_products trade_product ON trade_product.id = period_event.trade_product_id
                WHERE period_event.unionid __REQUESTED_UNIONIDS_FILTER__
                  AND period_event.event_type IN ('activated', 'renewed', 'admin_adjusted')

                UNION ALL

                SELECT
                    'radar:' || click_event.id::text,
                    click_event.unionid,
                    'radar_opened',
                    click_event.created_at,
                    '打开雷达 · ' || COALESCE(NULLIF(radar.title, ''), '雷达内容'),
                    '已打开追踪链接',
                    'radar_click_events',
                    click_event.id::text,
                    jsonb_build_object(
                        'radar_id', click_event.link_id::text,
                        'radar_title', COALESCE(NULLIF(radar.title, ''), '雷达内容'),
                        'target_type', COALESCE(NULLIF(click_event.target_type_snapshot, ''), radar.target_type, 'link')
                    )
                FROM radar_click_events click_event
                JOIN radar_links radar ON radar.id = click_event.link_id
                WHERE click_event.unionid __REQUESTED_UNIONIDS_FILTER__
                  AND (
                    click_event.stage = 'authorized'
                    OR (click_event.stage = 'landing' AND COALESCE(click_event.unionid, '') <> '')
                  )
            ), ranked AS (
                SELECT source_events.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY unionid
                           ORDER BY event_time DESC, event_id DESC
                       ) AS row_number
                FROM source_events
            )
            SELECT event_id, unionid, event_type, event_time, title, summary,
                   source_table, source_id, metadata_json
            FROM ranked
            WHERE row_number <= :per_customer_limit
            ORDER BY unionid ASC, event_time DESC, event_id DESC
            """
        if sqlite:
            statement_sql = statement_sql.replace("__REQUESTED_UNIONIDS_CTE__", "").replace(
                "__REQUESTED_UNIONIDS_FILTER__",
                "IN :external_userids",
            )
            statement = text(statement_sql).bindparams(bindparam("external_userids", expanding=True))
        else:
            statement_sql = statement_sql.replace(
                "__REQUESTED_UNIONIDS_CTE__",
                """
                requested_unionids AS (
                    SELECT DISTINCT requested.unionid
                    FROM unnest(CAST(:external_userids AS TEXT[])) AS requested(unionid)
                ),
                """,
            ).replace(
                "__REQUESTED_UNIONIDS_FILTER__",
                "IN (SELECT unionid FROM requested_unionids)",
            )
            statement = text(statement_sql)
        rows = self._session.execute(
            statement,
            {
                "external_userids": normalized,
                "per_customer_limit": max(1, min(int(per_customer_limit or 500), 2_000)),
            },
        ).mappings()
        result: dict[str, list[JsonDict]] = {}
        for row in rows:
            unionid = str(row.get("unionid") or "").strip()
            if not unionid:
                continue
            result.setdefault(unionid, []).append(
                {
                    "event_id": str(row.get("event_id") or ""),
                    "unionid": unionid,
                    "event_type": str(row.get("event_type") or ""),
                    "event_time": _iso(row.get("event_time")),
                    "title": str(row.get("title") or ""),
                    "summary": str(row.get("summary") or ""),
                    "source_table": str(row.get("source_table") or ""),
                    "source_id": str(row.get("source_id") or ""),
                    "metadata": dict(row.get("metadata_json") or {}),
                }
            )
        return result

    def customer_exists(self, external_userid: str) -> bool:
        return self.get_customer(external_userid) is not None

    def customer_exists_by_unionid(self, unionid: str) -> bool:
        return self.get_customer_by_unionid(unionid) is not None

    def _customer_rows(self, filters: JsonDict, *, limit: int | None, offset: int) -> list[JsonDict]:
        where, params = self._customer_where(filters)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = self._customer_decorated_sql(where_sql, "*") + """
            ORDER BY sort_updated_at DESC, unionid DESC
            LIMIT :limit OFFSET :offset
        """
        effective_limit = _DEFAULT_LIVE_SOURCE_LIST_LIMIT if limit is None else int(limit)
        params.update({"limit": max(1, effective_limit), "offset": max(0, int(offset or 0))})
        return [dict(row) for row in self._session.execute(text(sql), params).mappings()]

    def _customer_decorated_sql(self, where_sql: str, select_sql: str) -> str:
        sqlite = is_sqlite_session(self._session)
        return f"""
            WITH scope AS (
                SELECT unionid FROM crm_user_identity WHERE COALESCE(identity_status, 'active') <> 'deleted'
                UNION
                SELECT unionid FROM wechat_pay_orders
                UNION
                SELECT unionid FROM questionnaire_submissions
                UNION
                SELECT unionid FROM archived_messages
                UNION
                SELECT unionid FROM contact_tags
                UNION
                SELECT unionid FROM class_user_status_current
                UNION
                SELECT unionid FROM automation_channel_contact
            ),
            latest_messages AS (
                SELECT unionid, MAX(send_time) AS last_message_at
                FROM archived_messages
                WHERE unionid IS NOT NULL AND unionid <> ''
                GROUP BY unionid
            ),
            decorated AS (
                SELECT
                    scope.unionid,
                    COALESCE(NULLIF(identity.primary_external_userid, ''), '') AS external_userid,
                    COALESCE(
                        NULLIF(class_status.owner_userid_snapshot, ''),
                        NULLIF(channel_contact.owner_staff_id, ''),
                        NULLIF(identity.primary_owner_userid, ''),
                        ''
                    ) AS owner_userid,
                    COALESCE(
                        NULLIF(class_status.customer_name_snapshot, ''),
                        NULLIF(CAST({json_text_expression("channel_contact.source_payload_json", "customer_name", sqlite=sqlite)} AS TEXT), ''),
                        NULLIF(CAST({json_text_expression("channel_contact.source_payload_json", "name", sqlite=sqlite)} AS TEXT), ''),
                        NULLIF(identity.customer_name, ''),
                        NULLIF(CAST({json_text_expression("identity.profile_json", "name", sqlite=sqlite)} AS TEXT), ''),
                        NULLIF(CAST({json_text_expression("identity.profile_json", "customer_name", sqlite=sqlite)} AS TEXT), ''),
                        NULLIF(identity.remark, ''),
                        NULLIF(identity.primary_external_userid, ''),
                        scope.unionid
                    ) AS customer_name,
                    COALESCE(NULLIF(identity.mobile, ''), '') AS mobile,
                    COALESCE(NULLIF(identity.remark, ''), NULLIF(CAST({json_text_expression("channel_contact.source_payload_json", "remark", sqlite=sqlite)} AS TEXT), ''), '') AS remark,
                    COALESCE(NULLIF(identity.description, ''), NULLIF(CAST({json_text_expression("channel_contact.source_payload_json", "description", sqlite=sqlite)} AS TEXT), ''), '') AS description,
                    COALESCE(class_status.signup_status, '') AS signup_status,
                    COALESCE(class_status.signup_label_name, '') AS signup_label_name,
                    COALESCE(CAST(class_status.status_flags_json AS TEXT), '{{}}') AS status_flags_json,
                    CASE WHEN COALESCE(NULLIF(identity.mobile, ''), NULLIF(identity.primary_external_userid, '')) IS NULL THEN 0 ELSE 1 END AS is_bound,
                    COALESCE(NULLIF(identity.legacy_person_id, ''), '') AS person_id,
                    '' AS third_party_user_id,
                    latest_payment_order.latest_paid_order_id AS latest_paid_order_id,
                    latest_payment_order.latest_paid_at AS latest_paid_at,
                    identity.updated_at AS contact_updated_at,
                    channel_contact.updated_at AS channel_contact_updated_at,
                    identity.updated_at AS binding_updated_at,
                    class_status.updated_at AS class_status_updated_at,
                    latest_messages.last_message_at AS last_message_at,
                    identity.primary_openid AS openid,
                    identity.identity_status AS identity_status,
                    identity.follow_users_json AS follow_users_json,
                    COALESCE(
                        CAST(class_status.updated_at AS TEXT),
                        CAST(channel_contact.updated_at AS TEXT),
                        CAST(latest_payment_order.latest_paid_at AS TEXT),
                        CAST(identity.updated_at AS TEXT),
                        CAST(latest_messages.last_message_at AS TEXT),
                        ''
                    ) AS sort_updated_at
                FROM scope
                JOIN crm_user_identity identity ON identity.unionid = scope.unionid
                LEFT JOIN (
                    SELECT *
                    FROM (
                        SELECT channel_contact.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY channel_contact.unionid
                                   ORDER BY channel_contact.updated_at DESC, channel_contact.id DESC
                               ) AS row_num
                        FROM automation_channel_contact channel_contact
                        WHERE channel_contact.unionid IS NOT NULL
                          AND channel_contact.unionid <> ''
                    ) ranked_channel_contact
                    WHERE ranked_channel_contact.row_num = 1
                ) channel_contact ON channel_contact.unionid = scope.unionid
                LEFT JOIN (
                    SELECT unionid,
                           MAX(id) AS latest_paid_order_id,
                           MAX(COALESCE(paid_at, updated_at, created_at)) AS latest_paid_at
                    FROM wechat_pay_orders
                    WHERE unionid IS NOT NULL
                      AND unionid <> ''
                      AND (status = 'paid' OR trade_state = 'SUCCESS')
                    GROUP BY unionid
                ) latest_payment_order ON latest_payment_order.unionid = scope.unionid
                LEFT JOIN class_user_status_current class_status ON class_status.unionid = scope.unionid
                LEFT JOIN latest_messages ON latest_messages.unionid = scope.unionid
                WHERE scope.unionid IS NOT NULL AND scope.unionid <> ''
            )
            SELECT {select_sql}
            FROM decorated
            {where_sql}
        """

    def _customer_where(self, filters: JsonDict) -> tuple[list[str], JsonDict]:
        where: list[str] = []
        params: JsonDict = {}
        unionid = str(filters.get("unionid") or "").strip()
        if unionid:
            where.append("decorated.unionid = :unionid")
            params["unionid"] = unionid
        external_userid = str(filters.get("external_userid") or "").strip()
        if external_userid:
            where.append("decorated.external_userid = :external_userid")
            params["external_userid"] = external_userid
        owner_userid = str(filters.get("owner_userid") or "").strip()
        if owner_userid:
            where.append(
                """
                (
                    decorated.owner_userid = :owner_userid
                    OR EXISTS (
                        SELECT 1
                        FROM owner_role_map owner_role
                        WHERE owner_role.userid = decorated.owner_userid
                          AND owner_role.display_name = :owner_userid
                    )
                )
                """
            )
            params["owner_userid"] = owner_userid
        tag = str(filters.get("tag") or "").strip()
        if tag:
            where.append(
                """
                (
                    decorated.signup_label_name = :tag
                    OR EXISTS (
                        SELECT 1
                        FROM contact_tags tag
                        WHERE tag.unionid = decorated.unionid
                          AND (tag.tag_id = :tag OR tag.tag_name = :tag)
                    )
                )
                """
            )
            params["tag"] = tag
        status = str(filters.get("status") or "").strip()
        if status:
            where.append(
                """
                (
                    decorated.signup_status = :status
                    OR (:status = 'bound' AND decorated.is_bound = 1)
                    OR (:status = 'unbound' AND decorated.is_bound = 0)
                )
                """
            )
            params["status"] = status
        is_bound = _normalize_bool_filter(filters.get("is_bound"))
        if is_bound is not None:
            where.append("decorated.is_bound = :is_bound")
            params["is_bound"] = 1 if is_bound else 0
        mobile = str(filters.get("mobile") or "").strip()
        if mobile:
            where.append("decorated.mobile LIKE :mobile")
            params["mobile"] = f"%{mobile}%"
        keyword = str(filters.get("keyword") or "").strip().lower()
        if keyword:
            where.append(
                """
                (
                    LOWER(decorated.unionid) LIKE :keyword
                    OR LOWER(decorated.external_userid) LIKE :keyword
                    OR LOWER(decorated.customer_name) LIKE :keyword
                    OR LOWER(decorated.owner_userid) LIKE :keyword
                    OR LOWER(decorated.remark) LIKE :keyword
                    OR LOWER(decorated.description) LIKE :keyword
                    OR LOWER(decorated.mobile) LIKE :keyword
                    OR LOWER(decorated.signup_status) LIKE :keyword
                    OR LOWER(decorated.signup_label_name) LIKE :keyword
                    OR EXISTS (
                        SELECT 1
                        FROM owner_role_map owner_role
                        WHERE owner_role.userid = decorated.owner_userid
                          AND LOWER(owner_role.display_name) LIKE :keyword
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM contact_tags tag
                        WHERE tag.unionid = decorated.unionid
                          AND (LOWER(tag.tag_id) LIKE :keyword OR LOWER(tag.tag_name) LIKE :keyword)
                    )
                )
                """
            )
            params["keyword"] = f"%{keyword}%"
        return where, params

    def _decorate_customer_rows(self, rows: list[JsonDict]) -> list[JsonDict]:
        unionids = [str(row.get("unionid") or "").strip() for row in rows if str(row.get("unionid") or "").strip()]
        tag_map = self._tag_map(unionids)
        owner_display_map = self._owner_display_map(
            [
                str(row.get("owner_userid") or "").strip()
                for row in rows
                if str(row.get("owner_userid") or "").strip()
            ]
        )
        customers: list[JsonDict] = []
        for row in rows:
            unionid = str(row.get("unionid") or "").strip()
            external_userid = str(row.get("external_userid") or "").strip()
            owner_userid = str(row.get("owner_userid") or "").strip()
            mobile = str(row.get("mobile") or "").strip() or None
            is_bound = bool(row.get("is_bound"))
            customer_name = row.get("customer_name") or external_userid or unionid
            class_user_status = {
                "current_status": row.get("signup_status") or "",
                "signup_status": row.get("signup_status") or "",
                "signup_label_name": row.get("signup_label_name") or "",
                "activation_bucket": _json_dict(row.get("status_flags_json")).get("activation_bucket", ""),
                "updated_at": _iso(row.get("class_status_updated_at")),
            }
            follow_users = _json_list(row.get("follow_users_json"))
            customers.append(
                {
                    "unionid": unionid,
                    "person_id": str(row.get("person_id") or ""),
                    "external_userid": external_userid,
                    "customer_name": customer_name,
                    "remark": row.get("remark") or "",
                    "description": row.get("description") or "",
                    "owner_userid": owner_userid,
                    "owner_display_name": owner_display_map.get(owner_userid) or owner_userid,
                    "mobile": mobile,
                    "tags": tag_map.get(unionid, []),
                    "class_user_status": class_user_status,
                    "last_message_at": _iso(row.get("last_message_at")),
                    "last_touch_at": _iso(row.get("class_status_updated_at") or row.get("contact_updated_at") or row.get("binding_updated_at")),
                    "updated_at": _iso(row.get("sort_updated_at")),
                    "created_at": _iso(row.get("contact_updated_at") or row.get("binding_updated_at") or row.get("class_status_updated_at")),
                    "binding": {
                        "is_bound": is_bound,
                        "mobile": mobile,
                        "binding_status": "bound" if is_bound else "unbound",
                        "person_id": row.get("person_id"),
                        "third_party_user_id": row.get("third_party_user_id") or "",
                    },
                    "identity": {
                        "person_id": row.get("person_id"),
                        "external_userid": external_userid,
                        "mobile": mobile,
                        "unionid": unionid,
                        "openid": row.get("openid") or "",
                        "status": row.get("identity_status") or "",
                    },
                    "follow_users": follow_users,
                    "marketing_summary": {},
                    "marketing_profile": {},
                    "contact": {
                        "external_userid": external_userid,
                        "name": customer_name,
                        "remark": row.get("remark") or "",
                        "description": row.get("description") or "",
                    },
                    "sidebar_context": {
                        "can_open_sidebar": bool(unionid),
                        "customer_profile_url": f"/admin/customers/{unionid}" if unionid else "",
                    },
                }
            )
        return customers

    def _tag_map(self, unionids: list[str]) -> dict[str, list[str]]:
        rows = self._execute_in_query(
            """
            SELECT unionid, COALESCE(NULLIF(tag_name, ''), tag_id) AS tag
            FROM contact_tags
            WHERE unionid IN :external_userids
            ORDER BY unionid ASC, tag ASC
            """,
            unionids,
        )
        result: dict[str, list[str]] = {}
        for row in rows:
            unionid = str(row.get("unionid") or "").strip()
            tag = str(row.get("tag") or "").strip()
            if unionid and tag and tag not in result.setdefault(unionid, []):
                result[unionid].append(tag)
        return result

    def _identity_by_external_userid(self, external_userid: str) -> JsonDict:
        normalized = str(external_userid or "").strip()
        if not normalized:
            return {}
        if is_sqlite_session(self._session):
            return self._identity_by_external_userid_sqlite_fixture_adapter(normalized)
        resolution = SQLAlchemyIdentityResolver(self._session).resolve(
            ResolvePersonIdentityRequest(external_userid=normalized)
        )
        identity = resolution.identity if resolution.status == "resolved" else None
        if identity is None:
            return {}
        return {
            "unionid": str(identity.unionid or ""),
            "primary_external_userid": str(identity.external_userid or ""),
            "primary_openid": str(identity.openid or ""),
            "identity_status": "active",
        }

    def _identity_by_external_userid_sqlite_fixture_adapter(self, external_userid: str) -> JsonDict:
        rows = self._session.execute(
            text(
                """
                SELECT unionid, primary_external_userid, external_userids_json, primary_openid, identity_status
                FROM crm_user_identity
                ORDER BY unionid
                """
            ),
        ).mappings().all()
        resolution = classify_identity_candidates(
            ResolvePersonIdentityRequest(external_userid=external_userid),
            rows,
        )
        identity = resolution.identity if resolution.status == "resolved" else None
        if identity is None:
            return {}
        return {
            "unionid": str(identity.unionid or ""),
            "primary_external_userid": str(identity.external_userid or ""),
            "primary_openid": str(identity.openid or ""),
            "identity_status": "active",
        }

    def _owner_display_map(self, userids: list[str]) -> dict[str, str]:
        rows = self._execute_in_query(
            """
            SELECT userid, display_name
            FROM owner_role_map
            WHERE userid IN :external_userids
            """,
            userids,
        )
        return {
            str(row.get("userid") or "").strip(): str(row.get("display_name") or "").strip()
            for row in rows
            if str(row.get("userid") or "").strip()
        }

    def _execute_in_query(self, sql: str, values: list[str]) -> list[JsonDict]:
        normalized = [str(value or "").strip() for value in values if str(value or "").strip()]
        if not normalized:
            return []
        stmt = _external_userids_statement(self._session, sql)
        return [dict(row) for row in self._session.execute(stmt, {"external_userids": normalized}).mappings()]
