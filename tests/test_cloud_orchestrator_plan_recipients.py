from __future__ import annotations

import os
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from aicrm_next.cloud_orchestrator.campaigns_read import reset_campaign_read_fixture_state
from aicrm_next.cloud_orchestrator.repository import PostgresCloudPlanRepository
from aicrm_next.cloud_orchestrator.run_due import reset_run_due_fixture_state
from aicrm_next.main import create_app


def test_cloud_orchestrator_run_due_lists_recipients_without_dispatch(monkeypatch) -> None:
    monkeypatch.delenv("AICRM_NEXT_FORCE_PRODUCTION_DATA", raising=False)
    monkeypatch.delenv("AICRM_NEXT_ENABLE_LEGACY_PRODUCTION_FACADE", raising=False)
    reset_campaign_read_fixture_state()
    reset_run_due_fixture_state()
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.post(
        "/api/admin/cloud-orchestrator/campaigns/run-due/preview",
        json={"batch_size": 10},
        headers={"Idempotency-Key": "cloud-run-due-recipient-preview", "Authorization": "Bearer timer-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["candidate_count"] >= 1
    assert body["estimated_actions"]["planned_message_count"] == body["candidate_count"]
    assert body["real_external_call_executed"] is False
    assert body["wecom_send_executed"] is False


def test_postgres_recipient_broadcast_jobs_keep_independent_stable_execution_ids(next_pg_schema) -> None:
    plan_id = f"execution-plan-{uuid4().hex}"
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.execute(
            """
            INSERT INTO cloud_broadcast_plans (
                plan_id, trace_id, session_id, operator, intent, display_name,
                candidate_count, review_status, run_status, status
            ) VALUES (%s, %s, 'pytest', 'pytest', 'execution identity',
                      'execution identity', 2, 'approved', 'draft', 'draft')
            """,
            (plan_id, plan_id),
        )
        for suffix in ("a", "b"):
            recipient = conn.execute(
                """
                INSERT INTO cloud_broadcast_plan_recipients (
                    plan_id, unionid, owner_userid, display_name,
                    planned_message_count, approval_status, send_status
                ) VALUES (%s, %s, 'owner', %s, 1, 'approved', 'pending')
                RETURNING id
                """,
                (plan_id, f"union_{suffix}", suffix.upper()),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO cloud_broadcast_plan_recipient_messages (
                    plan_id, recipient_id, unionid, content_text, status
                ) VALUES (%s, %s, %s, 'hello', 'pending')
                """,
                (plan_id, int(recipient["id"]), f"union_{suffix}"),
            )
        conn.commit()

    repository = PostgresCloudPlanRepository(database_url)
    first = repository.create_or_reuse_recipient_broadcast_jobs(plan_id, operator="pytest")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        initial_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, execution_id
                FROM broadcast_jobs
                WHERE source_type = 'cloud_plan' AND content_payload->>'plan_id' = %s
                ORDER BY id
                """,
                (plan_id,),
            ).fetchall()
        ]
        conn.execute("UPDATE broadcast_jobs SET execution_id = '' WHERE id = %s", (initial_rows[0]["id"],))
        conn.commit()

    second = repository.create_or_reuse_recipient_broadcast_jobs(plan_id, operator="pytest")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        replay_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, execution_id
                FROM broadcast_jobs
                WHERE source_type = 'cloud_plan' AND content_payload->>'plan_id' = %s
                ORDER BY id
                """,
                (plan_id,),
            ).fetchall()
        ]

    assert first["created_count"] == 2
    assert second["reused_count"] == 2
    assert len(initial_rows) == len(replay_rows) == 2
    assert [row["execution_id"] for row in replay_rows] == [row["execution_id"] for row in initial_rows]
    assert all(row["execution_id"].startswith("exe_broadcast_") for row in replay_rows)
    assert len({row["execution_id"] for row in replay_rows}) == 2
