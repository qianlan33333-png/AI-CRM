from __future__ import annotations

import json
import os

import psycopg


INDEX_NAME = "ix_wechat_pay_order_events_trade_created_id"


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


def test_wechat_payment_event_lookup_index_is_ready(next_pg_schema) -> None:
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
    assert "(out_trade_no, created_at DESC, id DESC)" in index_state[2]


def test_wechat_payment_event_growth_lookup_uses_stable_index(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO wechat_pay_order_events (
                out_trade_no,
                event_type,
                transaction_id,
                trade_state,
                payload_json,
                headers_json,
                created_at
            )
            SELECT
                'PAY-GROWTH-' || lpad(((value - 1) % 1000)::text, 4, '0'),
                'payment_success',
                'transaction-' || value::text,
                'SUCCESS',
                '{}'::jsonb,
                '{}'::jsonb,
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """
        )
        connection.execute("ANALYZE wechat_pay_order_events")
        plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT id, event_type, created_at
            FROM wechat_pay_order_events
            WHERE out_trade_no = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """,
            ("PAY-GROWTH-0001",),
        ).fetchone()[0]

    root, nodes = _plan_nodes(plan)
    index_names = {str(node.get("Index Name") or "") for node in nodes}
    assert INDEX_NAME in index_names
    assert not any(
        node.get("Node Type") == "Seq Scan"
        and node.get("Relation Name") == "wechat_pay_order_events"
        for node in nodes
    )
    assert max(int(node.get("Actual Rows") or 0) for node in nodes) < 1000
    assert float(root.get("Execution Time") or 0.0) < 250.0
