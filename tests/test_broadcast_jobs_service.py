from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from aicrm_next.background_jobs import broadcast_queue_worker
from aicrm_next.background_jobs.broadcast_queue_worker import PostgresBroadcastQueueRepository, run_broadcast_queue_worker


class FakeBroadcastRepo:
    def __init__(self) -> None:
        self.jobs = [
            {
                "id": 101,
                "source_type": "manual",
                "target_unionids_json": ["union_a", "union_b"],
                "scheduled_for": datetime.now(timezone.utc) - timedelta(minutes=1),
            }
        ]
        self.sent: list[dict[str, Any]] = []
        self.simulated: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.unknown: list[dict[str, Any]] = []
        self.claims: list[dict[str, Any]] = []

    def claim_due_jobs(self, *, limit: int, now: datetime, claim_token: str, lease_seconds: int) -> list[dict[str, Any]]:
        self.claims.append({"limit": limit, "now": now, "claim_token": claim_token, "lease_seconds": lease_seconds})
        return self.jobs[:limit]

    def begin_dispatch(self, job_id: int, *, claim_token: str, now: datetime) -> dict[str, Any] | None:
        return next(({**job, "status": "dispatching"} for job in self.jobs if int(job["id"]) == job_id), None)

    def finalize_dispatch(self, job_id: int, *, claim_token: str, outcome: dict[str, Any]) -> dict[str, Any]:
        record = {"job_id": job_id, "claim_token": claim_token, **outcome}
        if outcome["status"] == "sent":
            self.sent.append(record)
        elif outcome["status"] == "simulated":
            self.simulated.append(record)
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
        record = {
            "job_id": job_id,
            "claim_token": claim_token,
            "error": error,
            "side_effect_executed": side_effect_executed,
            "provider_result_received": provider_result_received,
        }
        self.unknown.append(record)
        return record


class FakeDispatcher:
    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "outbound_task_id": 8801, "sent_count": len(job["target_unionids_json"])}


class SkippedDispatcher:
    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "status": "skipped", "reason": "adapter_disabled"}


class SimulatedDispatcher:
    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "status": "simulated", "outbound_task_id": 8803, "target_count": len(job["target_unionids_json"]), "side_effect_executed": False}


class SometimesBrokenDispatcher:
    def dispatch(self, job: dict[str, Any]) -> dict[str, Any]:
        if int(job["id"]) == 101:
            raise RuntimeError("temporary wecom failure")
        return {"ok": True, "outbound_task_id": 8802, "sent_count": len(job["target_unionids_json"])}


def test_broadcast_worker_claims_due_jobs_and_marks_sent() -> None:
    repo = FakeBroadcastRepo()

    result = run_broadcast_queue_worker(limit=10, repo=repo, dispatcher=FakeDispatcher())

    assert result["ok"] is True
    assert result["claimed"] == 1
    assert result["sent_ok"] == 1
    assert result["sent_failed"] == 0
    assert {key: repo.sent[0][key] for key in ("job_id", "sent_count", "failed_count", "claim_token")} == {
        "job_id": 101,
        "sent_count": 2,
        "failed_count": 0,
        "claim_token": repo.claims[0]["claim_token"],
    }
    assert repo.claims[0]["lease_seconds"] > 0


def test_broadcast_worker_records_structured_skip_without_external_send() -> None:
    repo = FakeBroadcastRepo()

    result = run_broadcast_queue_worker(limit=10, repo=repo, dispatcher=SkippedDispatcher())

    assert result["ok"] is True
    assert result["skipped"] == 1
    assert result["sent_failed"] == 1
    assert repo.failed[0]["job_id"] == 101
    assert repo.failed[0]["error"] == "adapter_disabled"
    assert repo.failed[0]["failure_type"] == "next_native_dispatch_skipped"


def test_broadcast_worker_persists_simulation_without_marking_sent() -> None:
    repo = FakeBroadcastRepo()

    result = run_broadcast_queue_worker(limit=10, repo=repo, dispatcher=SimulatedDispatcher())

    assert result["ok"] is True
    assert result["simulated"] == 1
    assert result["sent_ok"] == 0
    assert repo.sent == []
    assert repo.simulated[0]["job_id"] == 101
    assert repo.simulated[0]["claim_token"] == repo.claims[0]["claim_token"]
    assert result["results"] == [{"id": 101, "status": "simulated", "target_count": 2, "side_effect_executed": False}]


def test_broadcast_worker_continues_after_per_job_dispatch_exception() -> None:
    repo = FakeBroadcastRepo()
    repo.jobs.append(
        {
            "id": 102,
            "source_type": "manual",
            "target_unionids_json": ["union_c"],
            "scheduled_for": datetime.now(timezone.utc) - timedelta(minutes=1),
        }
    )

    result = run_broadcast_queue_worker(limit=10, repo=repo, dispatcher=SometimesBrokenDispatcher())

    assert result["ok"] is False
    assert result["claimed"] == 2
    assert result["sent_failed"] == 1
    assert result["sent_ok"] == 1
    assert repo.unknown[0]["job_id"] == 101
    assert repo.unknown[0]["error"] == "temporary wecom failure"
    assert repo.unknown[0]["side_effect_executed"] is True
    assert repo.sent[0]["job_id"] == 102
    assert repo.sent[0]["sent_count"] == 1


def test_postgres_broadcast_claim_reclaims_expired_and_retryable_jobs(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            calls.append({"sql": sql, "params": params})
            return SimpleNamespace(fetchall=lambda: [])

    monkeypatch.setattr(broadcast_queue_worker, "connect", lambda: FakeConn())

    PostgresBroadcastQueueRepository().claim_due_jobs(
        limit=5,
        now=datetime(2026, 7, 4, tzinfo=timezone.utc),
        claim_token="claim-token",
        lease_seconds=900,
    )

    sql = calls[0]["sql"]
    assert "status = 'queued'" in sql
    assert "status = 'claimed'" in sql
    assert "lease_expires_at <= %s" in sql
    assert "status = 'failed_retryable'" in sql
    assert "next_retry_at IS NULL OR next_retry_at <= %s" in sql
    assert "attempt_count < max_attempts" in sql


def test_broadcast_worker_rejects_invalid_limit() -> None:
    result = run_broadcast_queue_worker(limit=0, repo=FakeBroadcastRepo(), dispatcher=FakeDispatcher())

    assert result["ok"] is False
    assert result["errors"][0]["code"] == "invalid_limit"
