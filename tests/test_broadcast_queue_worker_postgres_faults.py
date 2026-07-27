from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aicrm_next.automation.background_jobs.broadcast_queue_worker import (
    PostgresBroadcastQueueRepository,
    run_broadcast_queue_worker,
)
from tests.test_broadcast_queue_worker_postgres_state_machine import _row, _seed_cloud_job


class _ProviderSuccessDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, _job):
        self.calls += 1
        return {
            "ok": True,
            "status": "sent",
            "sent_count": 1,
            "failed_count": 0,
            "side_effect_executed": True,
            "provider_result_received": True,
            "request_payload": {"target_count": 1},
            "response_payload": {"wecom_msgid": "msg-after-provider"},
            "task_type": "broadcast_job/wecom_private",
            "wecom_msgid": "msg-after-provider",
        }


class _PreProviderRetryableDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(self, _job):
        self.calls += 1
        return {
            "ok": False,
            "error": "provider credentials temporarily unavailable before call",
            "failure_type": "provider_unavailable_before_call",
            "side_effect_executed": False,
            "provider_result_received": False,
            "request_payload": {"target_count": 1},
            "response_payload": {},
            "task_type": "broadcast_job/wecom_private",
        }


@pytest.mark.parametrize(
    "failure_stage",
    ("before_outbound_task", "after_outbound_task", "after_projection_updates", "before_commit"),
)
def test_finalization_fault_rolls_back_outbound_evidence_and_terminal_projections(
    next_pg_schema,
    failure_stage: str,
) -> None:
    seeded = _seed_cloud_job(status="claimed", claim_token="fault-owner")

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError("injected projection finalization failure")

    repo = PostgresBroadcastQueueRepository(fault_injector=fail)
    repo.begin_dispatch(int(seeded["job_id"]), claim_token="fault-owner", now=datetime.now(timezone.utc))

    with pytest.raises(RuntimeError, match="injected projection finalization failure"):
        repo.finalize_dispatch(
            int(seeded["job_id"]),
            claim_token="fault-owner",
            outcome={
                "status": "sent",
                "side_effect_executed": True,
                "provider_result_received": True,
                "request_payload": {"target_count": 1},
                "response_payload": {"wecom_msgid": "msg-fault"},
                "wecom_msgid": "msg-fault",
            },
        )

    job = _row("SELECT status, outbound_task_id FROM broadcast_jobs WHERE id = %s", (seeded["job_id"],))
    recipient = _row("SELECT send_status FROM cloud_broadcast_plan_recipients WHERE id = %s", (seeded["recipient_id"],))
    message = _row("SELECT status FROM cloud_broadcast_plan_recipient_messages WHERE id = %s", (seeded["message_id"],))
    assert job == {"status": "dispatching", "outbound_task_id": None}
    assert recipient["send_status"] == "dispatching"
    assert message["status"] == "dispatching"


def test_classified_pre_provider_failure_is_retryable_without_provider_ambiguity(next_pg_schema) -> None:
    seeded = _seed_cloud_job()
    dispatcher = _PreProviderRetryableDispatcher()

    first = run_broadcast_queue_worker(
        repo=PostgresBroadcastQueueRepository(),
        dispatcher=dispatcher,
        limit=10,
    )
    second = run_broadcast_queue_worker(
        repo=PostgresBroadcastQueueRepository(),
        dispatcher=dispatcher,
        limit=10,
    )

    job = _row(
        """
        SELECT status, reconciliation_required, side_effect_executed,
               provider_result_received, next_retry_at
        FROM broadcast_jobs WHERE id = %s
        """,
        (seeded["job_id"],),
    )
    recipient = _row(
        "SELECT send_status FROM cloud_broadcast_plan_recipients WHERE id = %s",
        (seeded["recipient_id"],),
    )
    message = _row(
        "SELECT status FROM cloud_broadcast_plan_recipient_messages WHERE id = %s",
        (seeded["message_id"],),
    )

    assert first["ok"] is True
    assert first["sent_failed"] == 1
    assert first["unknown_after_dispatch"] == 0
    assert second["claimed"] == 0
    assert dispatcher.calls == 1
    assert job["status"] == "failed_retryable"
    assert job["reconciliation_required"] is False
    assert job["side_effect_executed"] is False
    assert job["provider_result_received"] is False
    assert job["next_retry_at"] is not None
    assert recipient["send_status"] == "failed_retryable"
    assert message["status"] == "failed_retryable"


def test_worker_marks_post_provider_finalization_failure_unknown_and_never_resends(next_pg_schema) -> None:
    seeded = _seed_cloud_job()
    dispatcher = _ProviderSuccessDispatcher()

    def fail(stage: str) -> None:
        if stage == "after_outbound_task":
            raise RuntimeError("injected outbound evidence failure")

    repo = PostgresBroadcastQueueRepository(fault_injector=fail)
    first = run_broadcast_queue_worker(repo=repo, dispatcher=dispatcher, limit=10)
    second = run_broadcast_queue_worker(repo=PostgresBroadcastQueueRepository(), dispatcher=dispatcher, limit=10)

    job = _row(
        """
        SELECT status, reconciliation_required, side_effect_executed,
               provider_result_received, outbound_task_id, claim_token
        FROM broadcast_jobs WHERE id = %s
        """,
        (seeded["job_id"],),
    )
    recipient = _row("SELECT send_status FROM cloud_broadcast_plan_recipients WHERE id = %s", (seeded["recipient_id"],))
    message = _row("SELECT status FROM cloud_broadcast_plan_recipient_messages WHERE id = %s", (seeded["message_id"],))

    assert first["unknown_after_dispatch"] == 1
    assert first["ok"] is False
    assert second["claimed"] == 0
    assert dispatcher.calls == 1
    assert job == {
        "status": "unknown_after_dispatch",
        "reconciliation_required": True,
        "side_effect_executed": True,
        "provider_result_received": True,
        "outbound_task_id": None,
        "claim_token": "",
    }
    assert recipient["send_status"] == "unknown_after_dispatch"
    assert message["status"] == "unknown_after_dispatch"
