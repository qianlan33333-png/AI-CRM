from __future__ import annotations

from datetime import datetime, timezone

from aicrm_next.automation.background_jobs.broadcast_queue_worker import run_broadcast_queue_worker


class EmptyRepo:
    def claim_due_jobs(self, *, limit: int, now: datetime, claim_token: str, lease_seconds: int) -> list[dict]:
        return []

    def begin_dispatch(self, job_id: int, *, claim_token: str, now: datetime) -> None:
        raise AssertionError("no job should begin dispatch")

    def finalize_dispatch(self, job_id: int, *, claim_token: str, outcome: dict) -> None:
        raise AssertionError("no job should finalize")

    def mark_unknown_after_dispatch(
        self,
        job_id: int,
        *,
        claim_token: str,
        error: str,
        side_effect_executed: bool,
        provider_result_received: bool,
    ) -> None:
        raise AssertionError("no job should require reconciliation")


def test_send_task_worker_empty_queue_is_successful_noop() -> None:
    result = run_broadcast_queue_worker(
        limit=10,
        repo=EmptyRepo(),
        dispatcher=object(),
        now=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    assert result["ok"] is True
    assert result["claimed"] == 0
    assert result["sent_ok"] == 0
    assert result["errors"] == []
