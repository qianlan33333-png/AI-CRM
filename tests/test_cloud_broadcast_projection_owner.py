from __future__ import annotations

from pathlib import Path

from aicrm_next.platform.platform_foundation.background_jobs.cloud_broadcast_projection_write_port import (
    build_cloud_broadcast_projection_write_port,
)


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, row=None) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class _Executor:
    def __init__(self, rows=()) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, statement, params):
        self.calls.append((" ".join(str(statement).split()), tuple(params)))
        row = self._rows.pop(0) if self._rows else None
        return _Result(row)


def test_cloud_broadcast_projection_owner_covers_runtime_transitions() -> None:
    port = build_cloud_broadcast_projection_write_port()
    executor = _Executor()

    port.mark_dispatching_dbapi(executor, job_id=81)
    port.finalize_dispatch_dbapi(
        executor,
        job_id=81,
        status="sent",
        last_error="",
    )
    port.mark_unknown_after_dispatch_dbapi(
        executor,
        job_id=82,
        last_error="provider result persisted ambiguously",
    )

    statements = [statement for statement, _params in executor.calls]
    assert len(statements) == 6
    assert "send_status = 'dispatching'" in statements[0]
    assert "status = 'dispatching'" in statements[1]
    assert "send_status = %s" in statements[2]
    assert "sent_at = CASE WHEN %s = 'sent'" in statements[3]
    assert "send_status = 'unknown_after_dispatch'" in statements[4]
    assert "status = 'unknown_after_dispatch'" in statements[5]


def test_cloud_broadcast_projection_owner_covers_planning_and_review() -> None:
    port = build_cloud_broadcast_projection_write_port()
    executor = _Executor(
        rows=(
            {"id": 11, "approval_status": "pending"},
            None,
            {"id": 21},
            {"id": 11, "approval_status": "approved"},
            None,
            {"id": 11, "approval_status": "rejected"},
            {"id": 21, "content_text": "updated"},
        )
    )

    recipient = port.upsert_agent_recipient_dbapi(
        executor,
        plan_id="plan-1",
        unionid="union-1",
        owner_userid="owner-1",
        display_name="Customer 1",
        approval_status="pending",
    )
    message_id = port.upsert_agent_message_dbapi(
        executor,
        plan_id="plan-1",
        recipient_id=11,
        unionid="union-1",
        content_text="hello",
        content_payload_json='{"text":"hello"}',
    )
    approved = port.approve_recipient_dbapi(
        executor,
        recipient_id=11,
        approved_by="reviewer",
        job_id=81,
    )
    rejected = port.reject_recipient_dbapi(
        executor,
        recipient_id=11,
        rejected_by="reviewer",
        reason="cancel",
    )
    updated = port.update_recipient_message_dbapi(
        executor,
        message_id=21,
        content_text="updated",
        content_payload_json='{"text":"updated"}',
        day_offset=1,
        send_time="09:30",
    )

    assert recipient == {"id": 11, "approval_status": "pending"}
    assert message_id == 21
    assert approved == {"id": 11, "approval_status": "approved"}
    assert rejected == {"id": 11, "approval_status": "rejected"}
    assert updated == {"id": 21, "content_text": "updated"}


def test_cloud_broadcast_projection_write_sql_is_confined_to_owner_repository() -> None:
    owner = (
        "aicrm_next/platform/platform_foundation/background_jobs/"
        "cloud_broadcast_projection_write_repository.py"
    )
    markers = (
        "INSERT INTO cloud_broadcast_plan_recipients",
        "UPDATE cloud_broadcast_plan_recipients",
        "DELETE FROM cloud_broadcast_plan_recipients",
        "INSERT INTO cloud_broadcast_plan_recipient_messages",
        "UPDATE cloud_broadcast_plan_recipient_messages",
        "DELETE FROM cloud_broadcast_plan_recipient_messages",
    )
    offenders: list[str] = []
    for path in sorted((ROOT / "aicrm_next").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not any(marker in source for marker in markers):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative != owner:
            offenders.append(relative)

    assert offenders == []
