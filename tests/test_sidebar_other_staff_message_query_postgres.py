from __future__ import annotations

import json
import os

import psycopg

from aicrm_next.customer_read_model.sidebar_v2 import SidebarV2SqlRepository
from aicrm_next.shared.db_session import get_engine


EXTERNAL_ALIAS = "external-other-staff-alias"
CANONICAL_EXTERNAL_USERID = "external-other-staff-primary"
CURRENT_USERID = "staff-current"
UNIONID = "union-other-staff-message"


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def _plan_nodes(payload: object) -> list[dict]:
    value = json.loads(payload) if isinstance(payload, str) else payload
    root = dict(value[0]["Plan"])
    nodes: list[dict] = []
    stack = [root]
    while stack:
        node = stack.pop()
        nodes.append(node)
        stack.extend(node.get("Plans") or [])
    return nodes


def test_other_staff_repository_filters_before_returning_latest_limit_plus_one(
    next_pg_schema,
) -> None:
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO crm_user_identity (
                unionid,
                primary_external_userid,
                external_userids_json,
                primary_owner_userid,
                identity_status
            ) VALUES (
                %s,
                %s,
                jsonb_build_array(jsonb_build_object('external_userid', CAST(%s AS text))),
                %s,
                'active'
            )
            """,
            (
                UNIONID,
                CANONICAL_EXTERNAL_USERID,
                EXTERNAL_ALIAS,
                CURRENT_USERID,
            ),
        )
        connection.execute(
            """
            INSERT INTO archived_messages (
                msgid, unionid, sender, msgtype, content, send_time
            ) VALUES
                ('eligible-old', %s, 'staff-old', 'text', 'old', CURRENT_TIMESTAMP - interval '60 seconds'),
                ('current-staff', %s, %s, 'text', 'current', CURRENT_TIMESTAMP - interval '50 seconds'),
                ('customer-alias', %s, %s, 'text', 'alias', CURRENT_TIMESTAMP - interval '40 seconds'),
                ('customer-primary', %s, %s, 'text', 'primary', CURRENT_TIMESTAMP - interval '30 seconds'),
                ('unsupported-type', %s, 'staff-file', 'file', 'file', CURRENT_TIMESTAMP - interval '20 seconds'),
                ('eligible-middle', %s, 'staff-middle', 'image', 'image', CURRENT_TIMESTAMP - interval '10 seconds'),
                ('eligible-new', %s, 'staff-new', 'text', 'new', CURRENT_TIMESTAMP),
                ('eligible-undated', %s, 'staff-undated', 'text', 'undated', NULL)
            """,
            (
                UNIONID,
                UNIONID,
                CURRENT_USERID,
                UNIONID,
                EXTERNAL_ALIAS,
                UNIONID,
                CANONICAL_EXTERNAL_USERID,
                UNIONID,
                UNIONID,
                UNIONID,
                UNIONID,
            ),
        )

    repository = SidebarV2SqlRepository(
        get_engine(database_url=_database_url())
    )
    rows = repository.list_other_staff_messages(
        EXTERNAL_ALIAS,
        current_userid=CURRENT_USERID,
        limit=2,
    )

    assert [row["msgid"] for row in rows] == [
        "eligible-new",
        "eligible-middle",
        "eligible-old",
    ]
    assert {row["msgtype"] for row in rows} <= {"text", "image"}
    assert {
        CURRENT_USERID,
        EXTERNAL_ALIAS,
        CANONICAL_EXTERNAL_USERID,
        "staff-undated",
    }.isdisjoint({row["sender"] for row in rows})


def test_other_staff_growth_query_uses_ordered_unionid_index(next_pg_schema) -> None:
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
                'growth-message-' || value::text,
                %s,
                CASE
                    WHEN value %% 11 = 0 THEN %s
                    WHEN value %% 13 = 0 THEN %s
                    ELSE 'staff-' || (value %% 17)::text
                END,
                CASE WHEN value %% 7 = 0 THEN 'file' ELSE 'text' END,
                'growth',
                CURRENT_TIMESTAMP - (value * interval '1 millisecond')
            FROM generate_series(1, 100000) AS value
            """,
            (UNIONID, CURRENT_USERID, CANONICAL_EXTERNAL_USERID),
        )
        connection.execute("ANALYZE archived_messages")
        plan = connection.execute(
            """
            EXPLAIN (ANALYZE, FORMAT JSON)
            SELECT message.id
            FROM archived_messages message
            WHERE message.unionid = %s
              AND message.unionid <> ''
              AND message.send_time IS NOT NULL
              AND message.sender <> ''
              AND message.sender <> %s
              AND message.sender <> %s
              AND message.sender <> %s
              AND message.msgtype IN ('text', 'image')
            ORDER BY message.send_time DESC, message.id DESC
            LIMIT 21
            """,
            (
                UNIONID,
                EXTERNAL_ALIAS,
                CANONICAL_EXTERNAL_USERID,
                CURRENT_USERID,
            ),
        ).fetchone()[0]

    nodes = _plan_nodes(plan)
    index_names = {str(node.get("Index Name") or "") for node in nodes}
    assert "ix_archived_messages_unionid_send_time" in index_names
    assert not any(
        node.get("Node Type") == "Seq Scan"
        and node.get("Relation Name") == "archived_messages"
        for node in nodes
    )
    assert not any(node.get("Node Type") == "Sort" for node in nodes)
    assert max(int(node.get("Actual Rows") or 0) for node in nodes) <= 21
    assert sum(int(node.get("Rows Removed by Filter") or 0) for node in nodes) < 1000
