from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aicrm_next.automation.background_jobs.broadcast_queue_worker as worker
from aicrm_next.automation.background_jobs.broadcast_queue_worker import run_broadcast_queue_worker


class FakeRepo:
    def __init__(self, jobs: list[dict[str, Any]] | None = None) -> None:
        self.jobs = jobs or []
        self.sent: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []

    def claim_due_jobs(self, *, limit: int, now: datetime, claim_token: str, lease_seconds: int) -> list[dict[str, Any]]:
        return self.jobs[:limit]

    def begin_dispatch(self, job_id: int, *, claim_token: str, now: datetime) -> dict[str, Any] | None:
        return next(({**job, "status": "dispatching"} for job in self.jobs if int(job["id"]) == job_id), None)

    def finalize_dispatch(self, job_id: int, *, claim_token: str, outcome: dict[str, Any]) -> dict[str, Any]:
        record = {"job_id": job_id, "claim_token": claim_token, **outcome}
        if outcome["status"] == "sent":
            self.sent.append(record)
        else:
            self.failed.append(record)
        return record

    def mark_unknown_after_dispatch(
        self,
        job_id: int,
        *,
        claim_token: str,
        error: str,
        side_effect_executed: bool,
        provider_result_received: bool,
    ) -> dict[str, Any]:
        record = {"job_id": job_id, "claim_token": claim_token, "error": error}
        self.failed.append(record)
        return record


class SuccessDispatcher:
    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "outbound_task_id": 555, "sent_count": 3}


class FailureDispatcher:
    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": "wecom api 401 invalid token"}


def _job() -> dict[str, Any]:
    return {"id": 1, "source_type": "manual", "target_unionids_json": ["union_a", "union_b", "union_c"]}


def test_worker_dispatches_due_job_and_marks_sent() -> None:
    repo = FakeRepo([_job()])

    summary = run_broadcast_queue_worker(
        limit=10,
        repo=repo,
        dispatcher=SuccessDispatcher(),
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    assert summary["claimed"] == 1
    assert summary["sent_ok"] == 1
    assert summary["sent_failed"] == 0
    assert repo.sent[0]["job_id"] == 1
    assert repo.sent[0]["claim_token"]


def test_worker_marks_failed_when_dispatch_fails() -> None:
    repo = FakeRepo([_job()])

    summary = run_broadcast_queue_worker(limit=10, repo=repo, dispatcher=FailureDispatcher())

    assert summary["sent_ok"] == 0
    assert summary["sent_failed"] == 1
    assert "401" in repo.failed[0]["error"]


def test_known_provider_rejection_is_retryable_without_becoming_unknown() -> None:
    outcome = worker._normalize_dispatch_outcome(
        _job(),
        {
            "ok": False,
            "failure_type": "external_call_failed_known",
            "error": "not external contact",
            "side_effect_executed": True,
            "provider_result_received": True,
            "response_payload": {"result": {"errcode": 40096}},
        },
    )

    assert outcome["status"] == "failed_retryable"
    assert outcome["side_effect_executed"] is True
    assert outcome["provider_result_received"] is True


def test_worker_no_due_jobs_returns_empty_summary() -> None:
    summary = run_broadcast_queue_worker(limit=10, repo=FakeRepo([]), dispatcher=SuccessDispatcher())

    assert summary["claimed"] == 0
    assert summary["sent_ok"] == 0
    assert summary["sent_failed"] == 0
    assert summary["results"] == []
