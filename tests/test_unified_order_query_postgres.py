from __future__ import annotations

import base64
import json
import os
from typing import Any

import psycopg

from aicrm_next.extensions.commerce.commerce import admin_transaction_detail
from aicrm_next.extensions.commerce.commerce.admin_unified_orders import list_orders
from aicrm_next.extensions.commerce.commerce.repo import connect_commerce_db


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def _legacy_cursor(offset: int) -> str:
    payload = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _seed_cross_provider_orders(suffix: str) -> dict[str, str]:
    names = {
        "wechat_tie": f"WX-TIE-{suffix}",
        "wechat_older": f"WX-OLDER-{suffix}",
        "alipay_tie": f"ALI-TIE-{suffix}",
        "shop_tie": f"SHOP-TIE-{suffix}",
    }
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO wechat_pay_orders (
                out_trade_no, transaction_id, product_code, product_name,
                amount_total, status, trade_state, paid_at, created_at
            ) VALUES
                (%s, %s, 'course', 'Course', 9900, 'paid', 'SUCCESS',
                 '2026-07-25 12:00:00+00', '2026-07-25 12:00:00+00'),
                (%s, %s, 'course', 'Course', 9900, 'paid', 'SUCCESS',
                 '2026-07-25 11:59:00+00', '2026-07-25 11:59:00+00')
            """,
            (
                names["wechat_tie"],
                f"WX-TX-TIE-{suffix}",
                names["wechat_older"],
                f"WX-TX-OLDER-{suffix}",
            ),
        )
        connection.execute(
            """
            INSERT INTO alipay_pay_orders (
                out_trade_no, trade_no, product_code, product_name,
                amount_total, status, trade_status, paid_at, created_at
            ) VALUES (
                %s, %s, 'course', 'Course', 9900, 'paid', 'TRADE_SUCCESS',
                '2026-07-25 12:00:00+00', '2026-07-25 12:00:00+00'
            )
            """,
            (names["alipay_tie"], f"ALI-TX-TIE-{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO wechat_shop_orders (
                order_id, provider, transaction_id, product_code, product_name,
                amount_total, business_status, deal_recorded, paid_at, created_at
            ) VALUES (
                %s, 'wechat_shop', %s, 'course', 'Course',
                9900, 'deal', TRUE, '2026-07-25 12:00:00+00', '2026-07-25 12:00:00+00'
            )
            """,
            (names["shop_tie"], f"SHOP-TX-TIE-{suffix}"),
        )
    return names


def test_unified_order_query_uses_cross_provider_stable_keyset(next_pg_schema, monkeypatch) -> None:
    names = _seed_cross_provider_orders("keyset")
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            "UPDATE wechat_pay_orders SET notify_payload_json = %s::jsonb WHERE out_trade_no = %s",
            (json.dumps({"zeta": 1, "alpha": 2}), names["wechat_tie"]),
        )
    monkeypatch.setattr(
        admin_transaction_detail,
        "close_expired_wechat_pay_orders",
        lambda **_kwargs: {"ok": True, "closed_count": 0},
    )

    first = list_orders(provider="all", limit=2)

    assert [item["order_no"] for item in first["items"]] == [names["wechat_tie"], names["alipay_tie"]]
    assert first["items"][0]["callback_summary"]["notify_payload_present"] is True
    assert first["items"][0]["callback_summary"]["payload_keys"] == ["alpha", "zeta"]
    assert first["total"] == 4
    assert first["has_more"] is True
    assert first["next_cursor"]
    decoded_payload = json.loads(
        base64.urlsafe_b64decode(
            (first["next_cursor"] + "=" * (-len(first["next_cursor"]) % 4)).encode("ascii")
        ).decode("utf-8")
    )
    assert decoded_payload["v"] == 1
    assert decoded_payload["position"] == 2

    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO wechat_shop_orders (
                order_id, provider, transaction_id, product_code, product_name,
                amount_total, business_status, deal_recorded, paid_at, created_at
            ) VALUES (
                'SHOP-NEW-AFTER-PAGE-ONE-KEYSET', 'wechat_shop', 'SHOP-TX-NEW-KEYSET', 'course', 'Course',
                9900, 'deal', TRUE, '2026-07-25 12:01:00+00', '2026-07-25 12:01:00+00'
            )
            """
        )

    second = list_orders(provider="all", limit=2, cursor=first["next_cursor"])

    assert [item["order_no"] for item in second["items"]] == [names["shop_tie"], names["wechat_older"]]
    assert not ({item["order_no"] for item in first["items"]} & {item["order_no"] for item in second["items"]})
    assert "SHOP-NEW-AFTER-PAGE-ONE-KEYSET" not in {item["order_no"] for item in second["items"]}
    assert second["offset"] == 2
    assert second["total"] == 5
    assert second["has_more"] is False


def test_unified_order_query_keeps_legacy_offset_cursor(next_pg_schema, monkeypatch) -> None:
    names = _seed_cross_provider_orders("legacy")
    monkeypatch.setattr(
        admin_transaction_detail,
        "close_expired_wechat_pay_orders",
        lambda **_kwargs: {"ok": True, "closed_count": 0},
    )

    page = list_orders(provider="all", limit=2, cursor=_legacy_cursor(2))

    assert page["offset"] == 2
    assert [item["order_no"] for item in page["items"]] == [names["shop_tie"], names["wechat_older"]]


def test_unified_order_query_executes_one_count_and_one_union_list(next_pg_schema, monkeypatch) -> None:
    _seed_cross_provider_orders("shape")
    statements: list[tuple[str, Any]] = []

    class CapturingConnection:
        def __init__(self) -> None:
            self.connection = connect_commerce_db()

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self.connection.__exit__(exc_type, exc, tb)

        def execute(self, sql: str, params: Any = None):
            statements.append((sql, params))
            return self.connection.execute(sql, params)

        def commit(self) -> None:
            self.connection.commit()

    monkeypatch.setattr(admin_transaction_detail, "_connect", CapturingConnection)
    monkeypatch.setattr(
        admin_transaction_detail,
        "close_expired_wechat_pay_orders",
        lambda **_kwargs: {"ok": True, "closed_count": 0},
    )

    payload = list_orders(provider="all", filters={"product_code": "course"}, limit=2)

    assert payload["items"]
    assert len(statements) == 2
    count_sql = statements[0][0].lower()
    list_sql = statements[1][0].lower()
    assert count_sql.count("union all") == 2
    assert list_sql.count("union all") == 2
    assert "o.provider = 'wechat_shop'" in count_sql
    assert "o.provider = 'wechat_shop'" in list_sql
    assert "order by sort_created_at desc, provider_rank desc, source_id desc" in list_sql
    assert "select *" not in list_sql


def test_unified_order_growth_list_uses_all_three_sort_indexes(next_pg_schema, monkeypatch) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO wechat_pay_orders (
                out_trade_no, product_code, product_name, amount_total,
                status, trade_state, paid_at, created_at
            )
            SELECT
                'UNION-WX-' || lpad(value::text, 6, '0'),
                'growth-course', 'Growth course', 9900,
                'paid', 'SUCCESS',
                CURRENT_TIMESTAMP - (value * interval '1 millisecond'),
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """
        )
        connection.execute(
            """
            INSERT INTO alipay_pay_orders (
                out_trade_no, product_code, product_name, amount_total,
                status, trade_status, paid_at, created_at
            )
            SELECT
                'UNION-ALI-' || lpad(value::text, 6, '0'),
                'growth-course', 'Growth course', 9900,
                'paid', 'TRADE_SUCCESS',
                CURRENT_TIMESTAMP - (value * interval '1 millisecond'),
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """
        )
        connection.execute(
            """
            INSERT INTO wechat_shop_orders (
                order_id, provider, product_code, product_name, amount_total,
                business_status, deal_recorded, paid_at, created_at
            )
            SELECT
                'UNION-SHOP-' || lpad(value::text, 6, '0'),
                'wechat_shop', 'growth-course', 'Growth course', 9900,
                'deal', TRUE,
                CURRENT_TIMESTAMP - (value * interval '1 millisecond'),
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """
        )
        connection.execute("ANALYZE wechat_pay_orders")
        connection.execute("ANALYZE alipay_pay_orders")
        connection.execute("ANALYZE wechat_shop_orders")

    statements: list[tuple[str, Any]] = []

    class CapturingConnection:
        def __init__(self) -> None:
            self.connection = connect_commerce_db()

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self.connection.__exit__(exc_type, exc, tb)

        def execute(self, sql: str, params: Any = None):
            statements.append((sql, params))
            return self.connection.execute(sql, params)

        def commit(self) -> None:
            self.connection.commit()

    monkeypatch.setattr(admin_transaction_detail, "_connect", CapturingConnection)
    monkeypatch.setattr(
        admin_transaction_detail,
        "close_expired_wechat_pay_orders",
        lambda **_kwargs: {"ok": True, "closed_count": 0},
    )

    payload = list_orders(provider="all", limit=50)

    assert len(payload["items"]) == 50
    assert payload["total"] == 300000
    list_sql, list_params = statements[1]
    with psycopg.connect(_database_url()) as connection:
        plan_payload = connection.execute(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {list_sql}",
            list_params,
        ).fetchone()[0]

    root = dict(plan_payload[0])
    nodes: list[dict[str, Any]] = []
    stack = [dict(root["Plan"])]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.get("Plans") or [])
    index_names = {str(node.get("Index Name") or "") for node in nodes}
    assert {
        "idx_wechat_pay_orders_created",
        "ix_alipay_pay_orders_created_id",
        "idx_wechat_shop_orders_provider_created",
    } <= index_names
    order_scan_nodes = [
        node
        for node in nodes
        if node.get("Relation Name")
        in {"wechat_pay_orders", "alipay_pay_orders", "wechat_shop_orders"}
    ]
    assert order_scan_nodes
    assert not any(node.get("Node Type") == "Seq Scan" for node in order_scan_nodes)
    assert max(int(node.get("Actual Rows") or 0) for node in order_scan_nodes) <= 51
    assert float(root.get("Execution Time") or 0.0) < 250.0
