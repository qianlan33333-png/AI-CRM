from __future__ import annotations

from datetime import datetime, timezone
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from aicrm_next.admin_jobs.repository import PostgresAdminJobsRepository
from aicrm_next.platform_foundation.background_jobs.broadcast_job_write_port import (
    BroadcastJobCreate,
    build_broadcast_job_write_port,
)
from aicrm_next.background_jobs.broadcast_queue_worker import (
    PostgresBroadcastQueueRepository,
)


pytestmark = pytest.mark.usefixtures("next_pg_schema")


def _database_url() -> str:
    return str(os.environ.get("DATABASE_URL") or os.environ.get("AICRM_TEST_DATABASE_URL") or "")


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _request(*, status: str = "queued") -> BroadcastJobCreate:
    suffix = uuid4().hex
    return BroadcastJobCreate(
        source_type="manual",
        source_id=f"manual-{suffix}",
        source_table="owner_test",
        scheduled_for=datetime.now(timezone.utc),
        priority=100,
        batch_key=f"owner-{suffix}",
        idempotency_key=f"broadcast-owner-{suffix}",
        target_unionids=(f"union-{suffix}",),
        target_summary="one target",
        content_type="private_message",
        content_payload={"content_text": "hello"},
        content_summary="hello",
        trace_id=f"trace-{suffix}",
        created_by="pytest",
        business_domain="owner_test",
        channel="wecom_private",
        target_kind="unionid",
        status=status,
        requires_approval=status == "waiting_approval",
    )


def test_broadcast_owner_admin_approval_and_cancel(next_pg_schema) -> None:
    with _connect() as connection:
        created = build_broadcast_job_write_port().create_dbapi(
            connection,
            _request(status="waiting_approval"),
        )
    assert created is not None

    admin = PostgresAdminJobsRepository()
    approved = admin.approve_broadcast_job(int(created["id"]), approved_by="reviewer")
    cancelled = admin.cancel_broadcast_job(
        int(created["id"]),
        cancelled_by="reviewer",
        reason="owner boundary test",
    )

    assert approved is not None and approved["status"] == "queued"
    assert cancelled is not None and cancelled["status"] == "cancelled"
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT status, approved_by, cancelled_by, cancel_reason
            FROM broadcast_jobs
            WHERE id = %s
            """,
            (int(created["id"]),),
        ).fetchone()
    assert row == {
        "status": "cancelled",
        "approved_by": "reviewer",
        "cancelled_by": "reviewer",
        "cancel_reason": "owner boundary test",
    }


def test_broadcast_owner_worker_claim_dispatch_and_finalize(next_pg_schema) -> None:
    with _connect() as connection:
        created = build_broadcast_job_write_port().create_dbapi(connection, _request())
    assert created is not None
    repository = PostgresBroadcastQueueRepository()
    now = datetime.now(timezone.utc)

    claimed = repository.claim_due_jobs(
        limit=1,
        now=now,
        claim_token="owner-claim",
        lease_seconds=60,
    )
    assert [int(row["id"]) for row in claimed] == [int(created["id"])]
    dispatching = repository.begin_dispatch(
        int(created["id"]),
        claim_token="owner-claim",
        now=now,
    )
    assert dispatching is not None and dispatching["status"] == "dispatching"
    finalized = repository.finalize_dispatch(
        int(created["id"]),
        claim_token="owner-claim",
        outcome={
            "status": "simulated",
            "sent_count": 0,
            "failed_count": 0,
            "side_effect_executed": False,
            "provider_result_received": False,
            "request_payload": {"test": True},
            "response_payload": {"simulated": True},
        },
    )

    assert finalized is not None and finalized["status"] == "simulated"
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT status, claim_token, lease_expires_at, completed_at
            FROM broadcast_jobs
            WHERE id = %s
            """,
            (int(created["id"]),),
        ).fetchone()
    assert row["status"] == "simulated"
    assert row["claim_token"] == ""
    assert row["lease_expires_at"] is None
    assert row["completed_at"] is not None
