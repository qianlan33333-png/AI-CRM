from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.platform.platform_foundation.background_jobs.cloud_broadcast_projection_write_port import (
    build_cloud_broadcast_projection_write_port,
)


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def test_cloud_broadcast_projection_owner_planning_review_and_edit(next_pg_schema) -> None:
    plan_id = f"projection-owner-{uuid4().hex}"
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        connection.execute(
            """
            INSERT INTO cloud_broadcast_plans (
                plan_id, trace_id, session_id, operator, intent, review_status, status
            ) VALUES (%s, %s, 'pytest', 'pytest', 'owner boundary', 'approved', 'draft')
            """,
            (plan_id, plan_id),
        )
        port = build_cloud_broadcast_projection_write_port()
        recipient = port.upsert_agent_recipient_dbapi(
            connection,
            plan_id=plan_id,
            unionid="union_projection_owner",
            owner_userid="owner_projection",
            display_name="Projection Owner",
            approval_status="pending",
        )
        assert recipient is not None
        recipient_id = int(recipient["id"])
        message_id = port.upsert_agent_message_dbapi(
            connection,
            plan_id=plan_id,
            recipient_id=recipient_id,
            unionid="union_projection_owner",
            content_text="hello",
            content_payload_json='{"text":"hello"}',
        )
        edited = port.update_recipient_message_dbapi(
            connection,
            message_id=message_id,
            content_text="updated",
            content_payload_json='{"text":"updated"}',
            day_offset=2,
            send_time="10:30",
        )
        approved = port.approve_recipient_dbapi(
            connection,
            recipient_id=recipient_id,
            approved_by="reviewer",
            job_id=None,
        )
        rejected = port.reject_recipient_dbapi(
            connection,
            recipient_id=recipient_id,
            rejected_by="reviewer",
            reason="owner boundary",
        )
        connection.commit()

    assert edited is not None and edited["content_text"] == "updated"
    assert edited["day_offset"] == 2 and edited["send_time"] == "10:30"
    assert approved is not None and approved["approval_status"] == "approved"
    assert rejected is not None and rejected["approval_status"] == "rejected"
    assert rejected["send_status"] == "cancelled"
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        message = connection.execute(
            """
            SELECT status, content_text, day_offset, send_time
            FROM cloud_broadcast_plan_recipient_messages
            WHERE id = %s
            """,
            (message_id,),
        ).fetchone()
    assert message == {
        "status": "queued",
        "content_text": "updated",
        "day_offset": 2,
        "send_time": "10:30",
    }
