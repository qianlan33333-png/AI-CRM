from __future__ import annotations

import os

import psycopg


UNIONID = "union-message-search-query"
EXTERNAL_USERID = "external-message-search-query"


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


def _insert_identity(connection: psycopg.Connection) -> None:
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
            'staff-message-search',
            'active'
        )
        """,
        (UNIONID, EXTERNAL_USERID, EXTERNAL_USERID),
    )


def test_search_route_finds_match_older_than_the_previous_200_message_window(
    next_client,
    next_pg_schema,
) -> None:
    with psycopg.connect(_database_url()) as connection:
        _insert_identity(connection)
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
                'message-search-window-' || value::text,
                %s,
                'staff-message-search',
                'text',
                CASE
                    WHEN value = 1 THEN 'deep-history-needle'
                    ELSE 'background-message-' || value::text
                END,
                TIMESTAMPTZ '2026-01-01 00:00:00+00' + (value * interval '1 second')
            FROM generate_series(1, 250) AS value
            """,
            (UNIONID,),
        )

    response = next_client.get(
        "/api/messages/search",
        params={
            "external_userid": EXTERNAL_USERID,
            "keyword": "deep-history-needle",
            "limit": 20,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["source_status"] == "message_archive_read_model"
    assert payload["read_model_status"] == "primary"
    assert payload["count"] == 1
    assert [row["msgid"] for row in payload["messages"]] == ["message-search-window-1"]
    assert payload["items"] == payload["messages"]


def test_search_route_treats_like_wildcards_as_literals_and_pages_database_matches(
    next_client,
    next_pg_schema,
) -> None:
    with psycopg.connect(_database_url()) as connection:
        _insert_identity(connection)
        connection.execute(
            """
            INSERT INTO archived_messages (
                seq,
                msgid,
                chat_type,
                unionid,
                sender,
                msgtype,
                content,
                send_time
            ) VALUES
                (1, 'literal-old', 'single', %s, 'staff-message-search', 'text',
                 E'prefix 100%%_match\\\\done suffix', TIMESTAMPTZ '2026-01-01 00:00:01+00'),
                (2, 'literal-group', 'group', %s, 'staff-message-search', 'text',
                 E'prefix 100%%_match\\\\done suffix', TIMESTAMPTZ '2026-01-01 00:00:02+00'),
                (3, 'wildcard-decoy', 'private', %s, 'staff-message-search', 'text',
                 E'prefix 100AXmatch\\\\done suffix', TIMESTAMPTZ '2026-01-01 00:00:03+00'),
                (4, 'literal-new', 'private', %s, 'staff-message-search', 'text',
                 E'prefix 100%%_match\\\\done suffix', TIMESTAMPTZ '2026-01-01 00:00:04+00')
            """,
            (UNIONID, UNIONID, UNIONID, UNIONID),
        )

    response = next_client.get(
        "/api/messages/search",
        params={
            "external_userid": EXTERNAL_USERID,
            "keyword": r"100%_match\done",
            "chat_type": "private",
            "limit": 1,
            "offset": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["filters"] == {"chat_type": "private"}
    assert [row["msgid"] for row in payload["messages"]] == ["literal-old"]
    assert payload["messages"][0]["chat_scene"] == "private"
