from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aicrm_next.platform_foundation.background_jobs.broadcast_job_write_port import (
    BroadcastJobCreate,
    build_broadcast_job_write_port,
)


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, *, row=None, rows=None) -> None:
        self.row = row
        self.rows = list(rows or [])

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _Executor:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), tuple(params)))
        return self.result


def _row(status: str = "queued") -> dict:
    return {
        "id": 81,
        "source_type": "manual",
        "status": status,
        "idempotency_key": "broadcast-owner-81",
    }


def _request() -> BroadcastJobCreate:
    return BroadcastJobCreate(
        source_type="manual",
        source_id="manual-81",
        source_table="owner_test",
        scheduled_for=datetime.now(timezone.utc),
        priority=100,
        batch_key="owner-test",
        idempotency_key="broadcast-owner-81",
        target_unionids=("union-81",),
        target_summary="one target",
        content_type="private_message",
        content_payload={"content_text": "hello"},
        content_summary="hello",
        trace_id="trace-81",
        created_by="pytest",
        business_domain="owner_test",
        channel="wecom_private",
        target_kind="unionid",
    )


def test_broadcast_owner_supports_create_and_admin_commands() -> None:
    port = build_broadcast_job_write_port()
    create = _Executor(_Result(row=_row()))
    callback_calls: list[tuple[str, tuple]] = []

    def execute_returning(statement: str, params: tuple):
        callback_calls.append((" ".join(statement.split()), params))
        return _row("cancelled")

    created = port.create_dbapi(create, _request())
    approved = port.approve_via(execute_returning, job_id=81, approved_by="reviewer")
    cancelled = port.cancel_via(
        execute_returning,
        job_id=81,
        cancelled_by="reviewer",
        reason="cancel test",
    )

    assert created == _row()
    assert "ON CONFLICT (idempotency_key)" in create.calls[0][0]
    assert "execution_id" in create.calls[0][0]
    assert approved == _row("cancelled")
    assert cancelled == _row("cancelled")
    assert "status = 'queued'" in callback_calls[0][0]
    assert "status = 'cancelled'" in callback_calls[1][0]


def test_broadcast_owner_supports_worker_state_machine_without_commit() -> None:
    port = build_broadcast_job_write_port()
    claim = _Executor(_Result(rows=[_row("claimed")]))
    begin = _Executor(_Result(row=_row("dispatching")))
    finalize = _Executor(_Result(row=_row("sent")))
    now = datetime.now(timezone.utc)

    claimed = port.claim_due_dbapi(
        claim,
        limit=1,
        now=now,
        claim_token="claim-81",
        lease_expires_at=now,
    )
    dispatching = port.begin_dispatch_dbapi(
        begin,
        job_id=81,
        claim_token="claim-81",
        now=now,
    )
    finalized = port.finalize_dispatch_dbapi(
        finalize,
        job_id=81,
        claim_token="claim-81",
        final_status="sent",
        outbound_task_id=91,
        sent_count=1,
        failed_count=0,
        failure_type="",
        error_text="",
        side_effect_executed=True,
        provider_result_received=True,
        result_summary_json='{"status":"sent"}',
        reconciliation_required=False,
        external_effect_job_id=101,
        retry_delay_seconds=300,
    )

    assert claimed == [_row("claimed")]
    assert dispatching == _row("dispatching")
    assert finalized == _row("sent")
    assert "FOR UPDATE SKIP LOCKED" in claim.calls[0][0]
    assert "status = 'dispatching'" in begin.calls[0][0]
    assert "execution_owner" in finalize.calls[0][0]
    assert not hasattr(claim, "commit")


def test_broadcast_job_write_sql_is_confined_to_owner_repository() -> None:
    offenders: list[str] = []
    markers = (
        "INSERT INTO broadcast_jobs",
        "UPDATE broadcast_jobs",
        "DELETE FROM broadcast_jobs",
    )
    owner = "aicrm_next/platform_foundation/background_jobs/broadcast_job_write_repository.py"
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in markers):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative != owner:
            offenders.append(relative)

    assert offenders == []
