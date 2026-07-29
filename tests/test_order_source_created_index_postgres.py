from __future__ import annotations

import json
import os

import psycopg


INDEX_NAME = "ix_alipay_pay_orders_created_id"
WECHAT_INDEX_NAME = "idx_wechat_pay_orders_created"
WECHAT_SHOP_INDEX_NAME = "idx_wechat_shop_orders_provider_created"


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def _plan_nodes(payload: object) -> tuple[dict, list[dict]]:
    value = json.loads(payload) if isinstance(payload, str) else payload
    root = dict(value[0])
    nodes: list[dict] = []
    stack = [dict(root["Plan"])]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.get("Plans") or [])
    return root, nodes


def test_alipay_order_created_index_is_ready(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        index_state = connection.execute(
            """
            SELECT index_state.indisvalid,
                   index_state.indisready,
                   pg_get_indexdef(index_state.indexrelid)
            FROM pg_index index_state
            JOIN pg_class index_relation ON index_relation.oid = index_state.indexrelid
            WHERE index_relation.relname = %s
            """,
            (INDEX_NAME,),
        ).fetchone()

    assert index_state is not None
    assert index_state[:2] == (True, True)
    assert "(created_at DESC, id DESC)" in index_state[2]


def test_alipay_order_growth_page_uses_stable_index(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO alipay_pay_orders (
                out_trade_no,
                product_code,
                product_name,
                amount_total,
                status,
                trade_status,
                created_at
            )
            SELECT
                'ALIPAY-GROWTH-' || lpad(value::text, 6, '0'),
                'growth-product',
                'Growth product',
                9900,
                'paid',
                'TRADE_SUCCESS',
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """
        )
        connection.execute("ANALYZE alipay_pay_orders")
        plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT id, created_at
            FROM alipay_pay_orders
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """
        ).fetchone()[0]

    root, nodes = _plan_nodes(plan)
    index_names = {str(node.get("Index Name") or "") for node in nodes}
    assert INDEX_NAME in index_names
    assert not any(
        node.get("Node Type") == "Seq Scan"
        and node.get("Relation Name") == "alipay_pay_orders"
        for node in nodes
    )
    assert max(int(node.get("Actual Rows") or 0) for node in nodes) <= 50
    assert float(root.get("Execution Time") or 0.0) < 250.0


def test_bootstrap_order_source_sort_indexes_are_ready(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        states = connection.execute(
            """
            SELECT index_relation.relname,
                   index_state.indisvalid,
                   index_state.indisready,
                   pg_get_indexdef(index_state.indexrelid)
            FROM pg_index index_state
            JOIN pg_class index_relation ON index_relation.oid = index_state.indexrelid
            WHERE index_relation.relname IN (%s, %s)
            ORDER BY index_relation.relname
            """,
            (WECHAT_INDEX_NAME, WECHAT_SHOP_INDEX_NAME),
        ).fetchall()

    assert {row[0] for row in states} == {WECHAT_INDEX_NAME, WECHAT_SHOP_INDEX_NAME}
    assert all(row[1:3] == (True, True) for row in states)
    definitions = {row[0]: row[3] for row in states}
    assert "(created_at DESC, id DESC)" in definitions[WECHAT_INDEX_NAME]
    assert "(provider, created_at DESC, id DESC)" in definitions[WECHAT_SHOP_INDEX_NAME]


def test_bootstrap_order_source_growth_pages_use_sort_indexes(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO wechat_pay_orders (out_trade_no, created_at)
            SELECT
                'WECHAT-GROWTH-' || lpad(value::text, 6, '0'),
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """
        )
        connection.execute(
            """
            INSERT INTO wechat_shop_orders (order_id, provider, created_at)
            SELECT
                'SHOP-GROWTH-' || txid_current()::text || '-' || lpad(value::text, 6, '0'),
                'wechat_shop',
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """
        )
        connection.execute("ANALYZE wechat_pay_orders")
        connection.execute("ANALYZE wechat_shop_orders")
        wechat_plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT id, created_at
            FROM wechat_pay_orders
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """
        ).fetchone()[0]
        shop_plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT id, created_at
            FROM wechat_shop_orders
            WHERE provider = 'wechat_shop'
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """
        ).fetchone()[0]

    for plan, table_name, index_name in (
        (wechat_plan, "wechat_pay_orders", WECHAT_INDEX_NAME),
        (shop_plan, "wechat_shop_orders", WECHAT_SHOP_INDEX_NAME),
    ):
        root, nodes = _plan_nodes(plan)
        index_names = {str(node.get("Index Name") or "") for node in nodes}
        assert index_name in index_names
        assert not any(
            node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == table_name
            for node in nodes
        )
        assert max(int(node.get("Actual Rows") or 0) for node in nodes) <= 50
        assert float(root.get("Execution Time") or 0.0) < 250.0
