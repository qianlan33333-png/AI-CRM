from __future__ import annotations

from datetime import datetime, timezone

from aicrm_next.platform_foundation.background_jobs.scheduler_runtime import (
    JobCatalogScheduler,
    schedule_is_due,
)
from scripts import run_job_catalog_scheduler


AT_ALL_INTERVALS = datetime(2026, 7, 26, 6, 15, tzinfo=timezone.utc)


def _handler(payload: dict | None = None):
    def run(**_kwargs):
        return {
            "ok": True,
            "candidate_count": 2,
            "secret_payload": payload or {"secret": "must-not-escape"},
            "real_external_call_executed": False,
        }

    return run


def _safe_handlers():
    return {
        "external_effect.reconcile": _handler(),
        "campaign.plan": _handler(),
        "group_ops.plan": _handler(),
        "ai_audience.refresh": _handler(),
    }


def test_minute_schedule_grammar_is_bounded_and_deterministic() -> None:
    assert schedule_is_due("* * * * *", now=AT_ALL_INTERVALS) is True
    assert schedule_is_due("*/3 * * * *", now=AT_ALL_INTERVALS) is True
    assert schedule_is_due("*/5 * * * *", now=AT_ALL_INTERVALS) is True
    assert schedule_is_due("*/5 * * * *", now=AT_ALL_INTERVALS.replace(minute=16)) is False


def test_dry_run_previews_only_safe_due_commands_and_redacts_handler_payloads() -> None:
    report = JobCatalogScheduler(handlers=_safe_handlers()).run(
        dry_run=True,
        now=AT_ALL_INTERVALS,
    )

    assert report["ok"] is True
    assert report["candidate_count"] == 5
    assert report["due_count"] == 5
    assert report["previewed_count"] == 4
    assert report["executed_count"] == 0
    assert next(item for item in report["items"] if item["job_type"] == "payment.reconcile")[
        "status"
    ] == "observe_only"
    assert "must-not-escape" not in str(report)
    assert report["real_external_call_executed"] is False


def test_execute_runs_safe_commands_but_explicit_observe_only_job_fails_closed() -> None:
    scheduler = JobCatalogScheduler(handlers=_safe_handlers())
    safe = scheduler.run(
        dry_run=False,
        requested_job_types=("campaign.plan",),
        now=AT_ALL_INTERVALS,
    )
    blocked = scheduler.run(
        dry_run=False,
        requested_job_types=("payment.reconcile",),
        now=AT_ALL_INTERVALS,
    )

    assert safe["ok"] is True
    assert safe["executed_count"] == 1
    assert blocked["ok"] is False
    assert blocked["errors"] == [
        {"job_type": "payment.reconcile", "error": "job_is_observe_only"}
    ]


def test_provider_boundary_signal_fails_the_scheduler_without_returning_payload() -> None:
    handlers = _safe_handlers()
    handlers["group_ops.plan"] = lambda **_kwargs: {
        "ok": True,
        "provider_response": {"access_token": "do-not-log"},
        "real_external_call_executed": True,
    }

    report = JobCatalogScheduler(handlers=handlers).run(
        dry_run=False,
        requested_job_types=("group_ops.plan",),
        now=AT_ALL_INTERVALS,
    )

    assert report["ok"] is False
    assert report["errors"] == [
        {"job_type": "group_ops.plan", "error": "provider_boundary_violation"}
    ]
    assert "do-not-log" not in str(report)
    assert report["real_external_call_executed"] is False


def test_cli_execute_requires_environment_gate_and_exact_confirmation() -> None:
    scheduler = JobCatalogScheduler(handlers=_safe_handlers())
    args = run_job_catalog_scheduler._parse_args(
        ["--execute", "--job-type", "campaign.plan"]
    )
    closed = run_job_catalog_scheduler.run(
        args,
        environ={},
        now=AT_ALL_INTERVALS,
        scheduler=scheduler,
    )
    mismatch = run_job_catalog_scheduler.run(
        args,
        environ={run_job_catalog_scheduler.EXECUTE_ENV: "1"},
        now=AT_ALL_INTERVALS,
        scheduler=scheduler,
    )
    confirmed_args = run_job_catalog_scheduler._parse_args(
        [
            "--execute",
            "--job-type",
            "campaign.plan",
            "--confirmation",
            run_job_catalog_scheduler.EXECUTE_CONFIRMATION,
        ]
    )
    executed = run_job_catalog_scheduler.run(
        confirmed_args,
        environ={run_job_catalog_scheduler.EXECUTE_ENV: "1"},
        now=AT_ALL_INTERVALS,
        scheduler=scheduler,
    )

    assert closed["error"] == "scheduler_execute_gate_closed"
    assert mismatch["error"] == "scheduler_execute_confirmation_mismatch"
    assert executed["ok"] is True
    assert executed["executed_count"] == 1
