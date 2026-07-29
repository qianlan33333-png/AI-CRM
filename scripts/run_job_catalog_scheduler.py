#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from datetime import datetime

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.platform.platform_foundation.background_jobs.scheduler_runtime import (
    JobCatalogScheduler,
)


EXECUTE_ENV = "AICRM_JOB_CATALOG_SCHEDULER_EXECUTE"
EXECUTE_CONFIRMATION = "EXECUTE_SAFE_JOB_CATALOG_SCHEDULER"


def _external_effect_reconcile(*, dry_run, operator, limit, now):
    del now
    from aicrm_next.platform.platform_foundation.external_effects.jobs import (
        complete_record_only_jobs,
    )

    return complete_record_only_jobs(
        dry_run=bool(dry_run),
        limit=int(limit),
        operator=str(operator),
    )


def _campaign_plan(*, dry_run, operator, limit, now):
    del operator
    from aicrm_next.campaign_preparation_composition import configure_campaign_preparation_dependencies
    from aicrm_next.automation.background_jobs.broadcast_queue_worker import (
        run_broadcast_queue_worker,
    )

    configure_campaign_preparation_dependencies()
    return run_broadcast_queue_worker(
        dry_run=bool(dry_run),
        limit=int(limit),
        now=now,
    )


def _group_ops_plan(*, dry_run, operator, limit, now):
    del limit
    from aicrm_next.automation.automation_engine.group_ops.scheduler import (
        run_group_ops_due_scheduler,
    )
    from aicrm_next.automation.background_jobs.automation_ops_scheduler import (
        run_automation_ops_scheduler,
    )

    def group_ops_runner(**kwargs):
        if bool(kwargs.get("dry_run")):
            return {
                "component": "group_ops_scheduler",
                "status": "skipped",
                "reason": "dry_run",
            }
        return {
            "component": "group_ops_scheduler",
            "status": "ok",
            **run_group_ops_due_scheduler(
                now=kwargs.get("now"),
                operator=str(kwargs.get("operator") or operator),
            ),
        }

    return run_automation_ops_scheduler(
        dry_run=bool(dry_run),
        now=now,
        operator=str(operator),
        group_ops_runner=group_ops_runner,
    )


def _ai_audience_refresh(*, dry_run, operator, limit, now):
    del operator, limit
    if bool(dry_run):
        return {
            "ok": True,
            "dry_run": True,
            "would_emit_incremental_tick": True,
            "would_check_daily_tick": True,
            "real_external_call_executed": False,
        }
    from aicrm_next.extensions.ai.ai_audience_ops.scheduler import emit_due_ticks

    return emit_due_ticks(
        include_incremental=True,
        include_daily=True,
        now=now,
    )


def _scheduler_handlers():
    return {
        "external_effect.reconcile": _external_effect_reconcile,
        "campaign.plan": _campaign_plan,
        "group_ops.plan": _group_ops_plan,
        "ai_audience.refresh": _ai_audience_refresh,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the provider-free commands in the versioned AI-CRM scheduler job catalog."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--job-type", action="append", default=[])
    parser.add_argument("--operator", default="job_catalog_scheduler")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 500:
        parser.error("--limit must be between 1 and 500")
    return args


def run(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
    scheduler: JobCatalogScheduler | None = None,
) -> dict[str, object]:
    environment = environ if environ is not None else os.environ
    if args.execute:
        if str(environment.get(EXECUTE_ENV, "")).strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return {
                "ok": False,
                "error": "scheduler_execute_gate_closed",
                "real_external_call_executed": False,
            }
        if str(args.confirmation or "") != EXECUTE_CONFIRMATION:
            return {
                "ok": False,
                "error": "scheduler_execute_confirmation_mismatch",
                "real_external_call_executed": False,
            }
    return (scheduler or JobCatalogScheduler(handlers=_scheduler_handlers())).run(
        dry_run=not bool(args.execute),
        requested_job_types=tuple(args.job_type),
        now=now,
        operator=str(args.operator or "").strip() or "job_catalog_scheduler",
        limit=int(args.limit),
    )


def main(argv: list[str] | None = None) -> int:
    payload = run(_parse_args(argv))
    print_json(payload, sort_keys=True)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
