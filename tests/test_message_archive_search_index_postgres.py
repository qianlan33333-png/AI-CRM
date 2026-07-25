from __future__ import annotations

import json
import os

import psycopg


INDEX_NAME = "ix_archived_messages_content_trgm"
UNIONID = "union-message-search-growth"


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


def test_archived_message_search_trigram_index_is_ready(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        extension_installed = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')"
        ).fetchone()[0]
        index_state = connection.execute(
            """
            SELECT index_state.indisvalid, index_state.indisready
            FROM pg_index index_state
            JOIN pg_class index_relation ON index_relation.oid = index_state.indexrelid
            WHERE index_relation.relname = %s
            """,
            (INDEX_NAME,),
        ).fetchone()

    assert extension_installed is True
    assert index_state == (True, True)


def test_archived_message_growth_search_uses_trigram_index(next_pg_schema) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO archived_messages (
                seq,
                msgid,
                unionid,
                sender,
                msgtype,
                content,
                send_time
            )
            SELECT
                value,
                'message-search-growth-' || value::text,
                %s,
                'staff-search',
                'text',
                CASE
                    WHEN value %% 1000 = 0 THEN 'needle-growth-result-' || value::text
                    ELSE 'background-message-' || value::text
                END,
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """,
            (UNIONID,),
        )
        connection.execute("ANALYZE archived_messages")
        plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT message.id
            FROM archived_messages message
            WHERE message.unionid = %s
              AND message.content <> ''
              AND message.content LIKE %s
            ORDER BY message.send_time DESC, message.id DESC
            LIMIT 21 OFFSET 0
            """,
            (UNIONID, "%needle-growth%"),
        ).fetchone()[0]

    root, nodes = _plan_nodes(plan)
    index_names = {str(node.get("Index Name") or "") for node in nodes}
    assert INDEX_NAME in index_names
    assert not any(
        node.get("Node Type") == "Seq Scan"
        and node.get("Relation Name") == "archived_messages"
        for node in nodes
    )
    assert max(int(node.get("Actual Rows") or 0) for node in nodes) < 1000
    assert float(root.get("Execution Time") or 0.0) < 250.0
