from __future__ import annotations

import os

import psycopg

from aicrm_next.extensions.growth.cloud_orchestrator.campaign_step_media_port import build_campaign_step_media_reference_port


def test_campaign_step_media_cleanup_preserves_payload_and_removes_all_matching_ids(next_pg_schema) -> None:
    database_url = os.environ["DATABASE_URL"]
    port = build_campaign_step_media_reference_port()

    with psycopg.connect(database_url) as connection:
        step_id = connection.execute(
            """
            INSERT INTO campaign_steps (
                campaign_id,
                campaign_segment_id,
                step_index,
                content_payload_json
            ) VALUES (%s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                910042,
                920042,
                0,
                '{"image_library_ids":["42","7","42"],"text":"keep-me"}',
            ),
        ).fetchone()[0]

        with connection.cursor() as cursor:
            changed = port.clear_image_references_dbapi(cursor, image_library_id=42)

        payload = connection.execute(
            "SELECT content_payload_json FROM campaign_steps WHERE id = %s",
            (step_id,),
        ).fetchone()[0]
        connection.rollback()

    assert changed == 1
    assert payload == {"image_library_ids": ["7"], "text": "keep-me"}
