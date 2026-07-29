from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.extensions.growth.cloud_orchestrator.repository import (
    PostgresCloudPlanRepository,
)


def _seed_identity(*, unionid: str, external_userid: str) -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute(
            """
            INSERT INTO crm_user_identity (
                unionid, primary_external_userid, external_userids_json,
                identity_status, created_at, updated_at
            ) VALUES (
                %s, %s, jsonb_build_array(%s::text),
                'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """,
            (unionid, external_userid, external_userid),
        )


def _row(sql: str, params: tuple = ()) -> dict:
    with psycopg.connect(
        os.environ["DATABASE_URL"], row_factory=dict_row
    ) as connection:
        row = connection.execute(sql, params).fetchone()
    return dict(row or {})


def _create_review_plan(repo: PostgresCloudPlanRepository) -> dict:
    suffix = uuid4().hex
    external_userid = f"wm_immediate_{suffix}"
    _seed_identity(
        unionid=f"union_immediate_{suffix}",
        external_userid=external_userid,
    )
    return repo.create_or_reuse_agent_send_plan(
        external_event_id=f"agent_immediate_{suffix}",
        package_key="pytest_immediate",
        external_userid=external_userid,
        owner_userid="owner_immediate",
        content_package={"content_text": "one recipient, one message"},
        operator="pytest",
        requires_review=True,
    )


def test_agent_plan_approval_materializes_effect_and_owner_link_atomically(
    next_pg_schema,
) -> None:
    repo = PostgresCloudPlanRepository()
    created = _create_review_plan(repo)
    repo.approve_plan(created["plan_id"], operator="pytest")

    result = repo.create_or_reuse_recipient_broadcast_jobs(
        created["plan_id"],
        operator="pytest",
    )

    assert result["immediate_effect_count"] == 1
    assert result["immediate_not_materialized"] == []
    job = _row(
        """
        SELECT status, execution_owner, external_effect_job_id, outbound_task_id
        FROM broadcast_jobs WHERE id = %s
        """,
        (result["broadcast_job_id"],),
    )
    effect = _row(
        """
        SELECT id, lane, ordering_key, fairness_key, business_type, business_id
        FROM external_effect_job WHERE id = %s
        """,
        (result["immediate_effect_job_ids"][0],),
    )
    recipient = _row(
        "SELECT send_status FROM cloud_broadcast_plan_recipients WHERE id = %s",
        (created["recipient_id"],),
    )
    message = _row(
        "SELECT status FROM cloud_broadcast_plan_recipient_messages WHERE id = %s",
        (created["message_id"],),
    )

    assert job["status"] == "delegated"
    assert job["execution_owner"] == "external_effect_job"
    assert job["external_effect_job_id"] == effect["id"]
    assert job["outbound_task_id"] is not None
    assert effect["lane"] == "wecom_ai_assistant_bulk"
    assert effect["ordering_key"].startswith("external_contact:wm_immediate_")
    assert effect["fairness_key"].startswith("broadcast:owner_immediate:")
    assert effect["business_type"] == "broadcast_job"
    assert effect["business_id"] == str(result["broadcast_job_id"])
    assert recipient["send_status"] == "delegated"
    assert message["status"] == "delegated"

    duplicate = repo.create_or_reuse_recipient_broadcast_jobs(
        created["plan_id"],
        operator="pytest",
    )
    count = _row(
        "SELECT COUNT(*)::int AS count FROM external_effect_job WHERE business_type = 'broadcast_job' AND business_id = %s",
        (str(result["broadcast_job_id"]),),
    )
    assert duplicate["immediate_effect_job_ids"] == result["immediate_effect_job_ids"]
    assert count["count"] == 1


def test_effect_insert_rolls_back_if_broadcast_owner_link_fails(
    next_pg_schema,
    monkeypatch,
) -> None:
    repo = PostgresCloudPlanRepository()
    created = _create_review_plan(repo)
    repo.approve_plan(created["plan_id"], operator="pytest")

    class FailingPort:
        def delegate_external_effect_dbapi(self, *_args, **_kwargs):
            raise RuntimeError("injected owner-link failure")

    monkeypatch.setattr(
        "aicrm_next.platform.platform_foundation.background_jobs.immediate_broadcast_delegate.build_broadcast_job_write_port",
        lambda: FailingPort(),
    )

    with pytest.raises(RuntimeError, match="injected owner-link failure"):
        repo.create_or_reuse_recipient_broadcast_jobs(
            created["plan_id"],
            operator="pytest",
        )

    counts = _row(
        """
        SELECT
            (SELECT COUNT(*) FROM broadcast_jobs WHERE source_id LIKE %s)::int AS jobs,
            (SELECT COUNT(*) FROM external_effect_job WHERE source_command_id <> '' AND trace_id = %s)::int AS effects
        """,
        (f"{created['plan_id']}:%", created["plan_id"]),
    )
    assert counts == {"jobs": 0, "effects": 0}
