from __future__ import annotations

import os

import psycopg

from aicrm_next.crm.customer_tags.projection_port import build_customer_tag_projection_port


def test_channel_snapshot_upsert_is_single_owner_and_idempotent(next_pg_schema) -> None:
    database_url = os.environ["DATABASE_URL"]
    port = build_customer_tag_projection_port()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            first = port.save_channel_snapshot_dbapi(
                cursor,
                unionid="union-contact-tag-owner",
                owner_userid="owner-contact-tag-owner",
                tag_ids=["tag-contact-owner"],
                tag_names={"tag-contact-owner": "首次同步"},
            )
            second = port.save_channel_snapshot_dbapi(
                cursor,
                unionid="union-contact-tag-owner",
                owner_userid="owner-contact-tag-owner",
                tag_ids=["tag-contact-owner"],
                tag_names={"tag-contact-owner": "再次同步"},
            )
        connection.commit()

        row = connection.execute(
            """
            SELECT unionid, userid, tag_id, tag_name
            FROM contact_tags
            WHERE unionid = %s AND userid = %s AND tag_id = %s
            """,
            ("union-contact-tag-owner", "owner-contact-tag-owner", "tag-contact-owner"),
        ).fetchone()

    assert first == {"updated_count": 0, "inserted_count": 1}
    assert second == {"updated_count": 1, "inserted_count": 0}
    assert row == (
        "union-contact-tag-owner",
        "owner-contact-tag-owner",
        "tag-contact-owner",
        "再次同步",
    )
