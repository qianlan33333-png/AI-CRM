from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

import aicrm_next.background_jobs.broadcast_queue_worker as worker
from aicrm_next.background_jobs import broadcast_effect_repository
from aicrm_next.background_jobs.broadcast_queue_worker import (
    PostgresBroadcastQueueRepository,
    SafeSkippedBroadcastDispatcher,
)
from aicrm_next.cloud_orchestrator.repository import PostgresCloudPlanRepository
from aicrm_next.platform_foundation.external_effects.repo import SQLAlchemyExternalEffectRepository


def _seed_cloud_job(*, status: str = "queued", claim_token: str = "") -> dict[str, int | str]:
    plan_id = f"r10-plan-{uuid4().hex}"
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute(
            """
            INSERT INTO cloud_broadcast_plans (plan_id, trace_id, session_id, operator, intent)
            VALUES (%s, %s, 'r10-session', 'r10-test', 'state machine')
            """,
            (plan_id, plan_id),
        )
        recipient = conn.execute(
            """
            INSERT INTO cloud_broadcast_plan_recipients (
                plan_id, unionid, owner_userid, display_name,
                planned_message_count, approval_status, send_status
            )
            VALUES (%s, 'union_r10', 'owner_r10', 'R10', 1, 'approved', 'queued')
            RETURNING id
            """,
            (plan_id,),
        ).fetchone()
        recipient_id = int(recipient["id"])
        message = conn.execute(
            """
            INSERT INTO cloud_broadcast_plan_recipient_messages (
                plan_id, recipient_id, unionid, content_text, status
            )
            VALUES (%s, %s, 'union_r10', 'hello', 'queued')
            RETURNING id
            """,
            (plan_id, recipient_id),
        ).fetchone()
        job = conn.execute(
            """
            INSERT INTO broadcast_jobs (
                source_type, source_id, source_table, scheduled_for, status,
                business_domain, idempotency_key, channel, target_kind,
                target_unionids_json, target_count, content_type, content_payload,
                claim_token, lease_expires_at
            )
            VALUES (
                'cloud_plan', %s, 'cloud_broadcast_plan_recipients', CURRENT_TIMESTAMP - INTERVAL '1 minute', %s,
                'ai_assistant', %s, 'wecom_private', 'unionid',
                '["union_r10"]'::jsonb, 1, 'cloud_plan', CAST(%s AS jsonb),
                %s, CASE WHEN %s = '' THEN NULL ELSE CURRENT_TIMESTAMP + INTERVAL '5 minutes' END
            )
            RETURNING id
            """,
            (
                f"{plan_id}:{recipient_id}",
                status,
                f"r10:{plan_id}:{recipient_id}",
                json.dumps(
                    {
                        "plan_id": plan_id,
                        "recipient_id": recipient_id,
                        "message_mode": "recipient_messages",
                        "owner_userid": "owner_r10",
                    }
                ),
                claim_token,
                claim_token,
            ),
        ).fetchone()
        job_id = int(job["id"])
        conn.execute(
            "UPDATE cloud_broadcast_plan_recipients SET broadcast_job_id = %s WHERE id = %s",
            (job_id, recipient_id),
        )
        conn.commit()
    return {
        "plan_id": plan_id,
        "recipient_id": recipient_id,
        "message_id": int(message["id"]),
        "job_id": job_id,
    }


def _row(sql: str, params: tuple = ()) -> dict:
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        return dict(conn.execute(sql, params).fetchone())


def _delegated_outcome(monkeypatch, job: dict) -> dict:
    monkeypatch.setattr(
        worker,
        "_resolve_private_targets_by_unionid",
        lambda unionids: ([f"wm_{item.removeprefix('union_')}" for item in unionids], []),
    )
    outcome = SafeSkippedBroadcastDispatcher().dispatch(job)
    assert outcome["status"] == "delegated"
    assert outcome["external_effect_job_ids"] == []
    assert len(outcome["effect_plan_requests"]) == 1
    return outcome


def test_broadcast_owner_link_and_external_effect_commit_atomically(next_pg_schema, monkeypatch) -> None:
    seeded = _seed_cloud_job()
    repo = PostgresBroadcastQueueRepository()
    now = datetime.now(timezone.utc)
    claimed = repo.claim_due_jobs(limit=1, now=now, claim_token="delegate-owner", lease_seconds=300)
    dispatching = repo.begin_dispatch(int(seeded["job_id"]), claim_token="delegate-owner", now=now)

    assert [int(item["id"]) for item in claimed] == [seeded["job_id"]]
    assert dispatching is not None
    outcome = _delegated_outcome(monkeypatch, dispatching)
    finalized = repo.finalize_dispatch(
        int(seeded["job_id"]),
        claim_token="delegate-owner",
        outcome=outcome,
    )

    assert finalized is not None
    job = _row(
        "SELECT status, execution_owner, external_effect_job_id, completed_at FROM broadcast_jobs WHERE id = %s",
        (seeded["job_id"],),
    )
    effect = _row(
        "SELECT id, business_type, business_id, status, lane, attempt_count FROM external_effect_job WHERE business_type = 'broadcast_job' AND business_id = %s",
        (str(seeded["job_id"]),),
    )
    assert job == {
        "status": "delegated",
        "execution_owner": "external_effect_job",
        "external_effect_job_id": effect["id"],
        "completed_at": None,
    }
    assert effect["business_type"] == "broadcast_job"
    assert effect["business_id"] == str(seeded["job_id"])
    assert effect["status"] in {"blocked", "queued"}
    assert effect["lane"] == "wecom_bulk"
    assert effect["attempt_count"] == 0


def test_broadcast_effect_insert_rolls_back_when_owner_link_finalization_fails(next_pg_schema, monkeypatch) -> None:
    seeded = _seed_cloud_job()

    def fail_before_outbound_task(stage: str) -> None:
        if stage == "before_outbound_task":
            raise RuntimeError("injected_owner_link_failure")

    repo = PostgresBroadcastQueueRepository(fault_injector=fail_before_outbound_task)
    now = datetime.now(timezone.utc)
    repo.claim_due_jobs(limit=1, now=now, claim_token="rollback-owner", lease_seconds=300)
    dispatching = repo.begin_dispatch(int(seeded["job_id"]), claim_token="rollback-owner", now=now)
    assert dispatching is not None
    outcome = _delegated_outcome(monkeypatch, dispatching)

    with pytest.raises(RuntimeError, match="injected_owner_link_failure"):
        repo.finalize_dispatch(
            int(seeded["job_id"]),
            claim_token="rollback-owner",
            outcome=outcome,
        )

    job = _row(
        "SELECT status, execution_owner, external_effect_job_id FROM broadcast_jobs WHERE id = %s",
        (seeded["job_id"],),
    )
    count = _row(
        "SELECT COUNT(*)::int AS count FROM external_effect_job WHERE business_type = 'broadcast_job' AND business_id = %s",
        (str(seeded["job_id"]),),
    )
    assert job == {
        "status": "dispatching",
        "execution_owner": "legacy_frozen",
        "external_effect_job_id": None,
    }
    assert count["count"] == 0


def test_terminal_delegated_effect_closes_broadcast_and_recipient_projections(next_pg_schema, monkeypatch) -> None:
    seeded = _seed_cloud_job()
    repo = PostgresBroadcastQueueRepository()
    now = datetime.now(timezone.utc)
    repo.claim_due_jobs(limit=1, now=now, claim_token="terminal-owner", lease_seconds=300)
    dispatching = repo.begin_dispatch(int(seeded["job_id"]), claim_token="terminal-owner", now=now)
    assert dispatching is not None
    outcome = _delegated_outcome(monkeypatch, dispatching)
    repo.finalize_dispatch(int(seeded["job_id"]), claim_token="terminal-owner", outcome=outcome)
    effect = _row(
        "SELECT id FROM external_effect_job WHERE business_type = 'broadcast_job' AND business_id = %s",
        (str(seeded["job_id"]),),
    )
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        connection.execute(
            """
            UPDATE external_effect_job
            SET status = 'blocked', last_error_code = 'broadcast_policy_blocked',
                completed_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (effect["id"],),
        )
        connection.commit()
    effect_job = SQLAlchemyExternalEffectRepository().get_job(int(effect["id"]))
    assert effect_job is not None

    result = broadcast_effect_repository._project(effect_job, None)

    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute(
            "UPDATE cloud_broadcast_plans SET candidate_count = 1 WHERE plan_id = %s",
            (seeded["plan_id"],),
        )
        connection.commit()

    assert result["aggregate_status"] == "blocked"
    job = _row(
        "SELECT status, failed_count, reconciliation_required FROM broadcast_jobs WHERE id = %s",
        (seeded["job_id"],),
    )
    recipient = _row(
        "SELECT send_status, last_error FROM cloud_broadcast_plan_recipients WHERE id = %s",
        (seeded["recipient_id"],),
    )
    message = _row(
        "SELECT status, last_error FROM cloud_broadcast_plan_recipient_messages WHERE id = %s",
        (seeded["message_id"],),
    )
    stats = PostgresCloudPlanRepository().plan_stats(str(seeded["plan_id"]))
    failed_recipients, failed_total = PostgresCloudPlanRepository().list_recipients(
        str(seeded["plan_id"]),
        status="failed",
    )
    assert job == {"status": "blocked", "failed_count": 1, "reconciliation_required": False}
    assert recipient == {"send_status": "failed", "last_error": "external_effect_blocked"}
    assert message == {"status": "failed", "last_error": "external_effect_blocked"}
    assert stats["failed_count"] == 1
    assert failed_total == 1
    assert [item["recipient_id"] for item in failed_recipients] == [seeded["recipient_id"]]


def test_claim_begin_and_atomic_success_finalization_keep_all_projections_aligned(next_pg_schema) -> None:
    seeded = _seed_cloud_job()
    repo = PostgresBroadcastQueueRepository()
    now = datetime.now(timezone.utc)
    claimed = repo.claim_due_jobs(limit=10, now=now, claim_token="claim-r10", lease_seconds=300)

    assert [int(item["id"]) for item in claimed] == [seeded["job_id"]]
    dispatching = repo.begin_dispatch(int(seeded["job_id"]), claim_token="claim-r10", now=now)
    assert dispatching is not None
    assert dispatching["status"] == "dispatching"

    finalized = repo.finalize_dispatch(
        int(seeded["job_id"]),
        claim_token="claim-r10",
        outcome={
            "status": "sent",
            "sent_count": 1,
            "failed_count": 0,
            "side_effect_executed": True,
            "provider_result_received": True,
            "request_payload": {"target_count": 1},
            "response_payload": {"wecom_msgid": "msg-r10-success"},
            "task_type": "broadcast_job/wecom_private",
            "wecom_msgid": "msg-r10-success",
        },
    )

    assert finalized is not None
    assert finalized["status"] == "sent"
    job = _row(
        """
        SELECT status, claim_token, side_effect_executed, provider_result_received,
               reconciliation_required, outbound_task_id, completed_at
        FROM broadcast_jobs WHERE id = %s
        """,
        (seeded["job_id"],),
    )
    recipient = _row(
        "SELECT send_status, last_error FROM cloud_broadcast_plan_recipients WHERE id = %s",
        (seeded["recipient_id"],),
    )
    message = _row(
        "SELECT status, sent_at, last_error FROM cloud_broadcast_plan_recipient_messages WHERE id = %s",
        (seeded["message_id"],),
    )
    outbound = _row(
        "SELECT broadcast_job_id, status, wecom_task_id FROM outbound_tasks WHERE id = %s",
        (job["outbound_task_id"],),
    )

    assert job["status"] == "sent"
    assert job["claim_token"] == ""
    assert job["side_effect_executed"] is True
    assert job["provider_result_received"] is True
    assert job["reconciliation_required"] is False
    assert job["completed_at"] is not None
    assert recipient == {"send_status": "sent", "last_error": ""}
    assert message["status"] == "sent"
    assert message["sent_at"] is not None
    assert message["last_error"] == ""
    assert outbound == {
        "broadcast_job_id": seeded["job_id"],
        "status": "sent",
        "wecom_task_id": "msg-r10-success",
    }


def test_finalizer_requires_dispatching_state_and_matching_claim_token(next_pg_schema) -> None:
    seeded = _seed_cloud_job(status="claimed", claim_token="owner-r10")
    repo = PostgresBroadcastQueueRepository()
    repo.begin_dispatch(int(seeded["job_id"]), claim_token="owner-r10", now=datetime.now(timezone.utc))

    result = repo.finalize_dispatch(
        int(seeded["job_id"]),
        claim_token="wrong-owner",
        outcome={"status": "sent", "side_effect_executed": True, "provider_result_received": True},
    )

    assert result is None
    row = _row("SELECT status, claim_token, outbound_task_id FROM broadcast_jobs WHERE id = %s", (seeded["job_id"],))
    assert row == {"status": "dispatching", "claim_token": "owner-r10", "outbound_task_id": None}


def test_claim_query_never_reclaims_dispatching_or_unknown_jobs(next_pg_schema) -> None:
    dispatching = _seed_cloud_job(status="dispatching", claim_token="dispatch-owner")
    unknown = _seed_cloud_job(status="unknown_after_dispatch")
    queued = _seed_cloud_job(status="queued")
    repo = PostgresBroadcastQueueRepository()

    claimed = repo.claim_due_jobs(
        limit=10,
        now=datetime.now(timezone.utc) + timedelta(hours=1),
        claim_token="new-owner",
        lease_seconds=300,
    )

    claimed_ids = {int(item["id"]) for item in claimed}
    assert int(queued["job_id"]) in claimed_ids
    assert int(dispatching["job_id"]) not in claimed_ids
    assert int(unknown["job_id"]) not in claimed_ids


def test_retryable_failure_at_max_attempts_becomes_terminal_and_is_not_reclaimed(next_pg_schema) -> None:
    seeded = _seed_cloud_job(status="queued")
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute(
            "UPDATE broadcast_jobs SET max_attempts = 1 WHERE id = %s",
            (seeded["job_id"],),
        )
        conn.commit()

    repo = PostgresBroadcastQueueRepository()
    now = datetime.now(timezone.utc)
    claimed = repo.claim_due_jobs(
        limit=1,
        now=now,
        claim_token="max-attempt-owner",
        lease_seconds=300,
    )
    assert [int(item["id"]) for item in claimed] == [seeded["job_id"]]
    assert int(claimed[0]["attempt_count"]) == 1

    dispatching = repo.begin_dispatch(
        int(seeded["job_id"]),
        claim_token="max-attempt-owner",
        now=now,
    )
    assert dispatching is not None
    finalized = repo.finalize_dispatch(
        int(seeded["job_id"]),
        claim_token="max-attempt-owner",
        outcome={
            "status": "failed_retryable",
            "failure_type": "provider_unavailable_before_call",
            "error": "temporary provider outage",
            "side_effect_executed": False,
            "provider_result_received": False,
        },
    )

    assert finalized is not None
    assert finalized["status"] == "failed_terminal"
    row = _row(
        "SELECT status, attempt_count, max_attempts, next_retry_at FROM broadcast_jobs WHERE id = %s",
        (seeded["job_id"],),
    )
    assert row == {
        "status": "failed_terminal",
        "attempt_count": 1,
        "max_attempts": 1,
        "next_retry_at": None,
    }
    assert (
        repo.claim_due_jobs(
            limit=1,
            now=now + timedelta(hours=1),
            claim_token="must-not-reclaim",
            lease_seconds=300,
        )
        == []
    )
