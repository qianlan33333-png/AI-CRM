from __future__ import annotations

import json
import sys
from typing import Any

from aicrm_next.platform.shared.runtime_settings import runtime_bool, runtime_setting

from .adapters import ExternalEffectAdapterRegistry
from .continuations import ExternalEffectContinuationRegistry
from .repo import ExternalEffectRepository
from .worker import ExternalEffectWorker


SCHEDULER_ENABLED_KEY = "AICRM_EXTERNAL_EFFECT_RUN_DUE_SCHEDULER_ENABLED"
SCHEDULER_INTERVAL_SECONDS_KEY = "AICRM_EXTERNAL_EFFECT_RUN_DUE_INTERVAL_SECONDS"
SCHEDULER_BATCH_SIZE_KEY = "AICRM_EXTERNAL_EFFECT_RUN_DUE_BATCH_SIZE"
TEST_EXECUTION_ONLY_KEY = "AICRM_EXTERNAL_EFFECT_TEST_EXECUTION_ONLY"


def _write_json_result(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _bounded_int(value: Any, *, default: int, minimum: int = 1, maximum: int = 500) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def external_effect_scheduler_state() -> dict[str, Any]:
    interval_seconds = _bounded_int(runtime_setting(SCHEDULER_INTERVAL_SECONDS_KEY, "60"), default=60, minimum=60, maximum=86400)
    batch_size = _bounded_int(runtime_setting(SCHEDULER_BATCH_SIZE_KEY, "20"), default=20, minimum=1, maximum=500)
    enabled = runtime_bool(SCHEDULER_ENABLED_KEY, default=False)
    test_only = runtime_bool(TEST_EXECUTION_ONLY_KEY, default=False)
    return {
        "enabled": enabled,
        "status": "enabled" if enabled else "disabled",
        "interval_seconds": interval_seconds,
        "interval_minutes": max(1, round(interval_seconds / 60)),
        "batch_size": batch_size,
        "mode": "test_only_due_scan" if test_only else "global_due_scan",
        "test_only": test_only,
        "description": "统一扫描所有到期 external_effect_job；能力开关只表示允许执行，不代表立即发送。",
    }


def run_scheduled_external_effects(
    *,
    dry_run: bool = True,
    limit: int | None = None,
    operator: str = "external_effect_scheduler",
    repository: ExternalEffectRepository | None = None,
    adapter_registry: ExternalEffectAdapterRegistry | None = None,
    continuation_registry: ExternalEffectContinuationRegistry | None = None,
) -> dict[str, Any]:
    scheduler = external_effect_scheduler_state()
    batch_size = _bounded_int(limit if limit is not None else scheduler["batch_size"], default=int(scheduler["batch_size"]), minimum=1, maximum=500)
    worker = ExternalEffectWorker(
        repository,
        adapter_registry,
        continuation_registry=continuation_registry,
        locked_by=f"external-effect-scheduler-{operator or 'system'}",
    )
    test_only = bool(scheduler["test_only"])
    scan_mode = "test_only_due_jobs" if test_only else "all_due_jobs"
    if dry_run:
        result = worker.preview_due(batch_size=batch_size, effect_types=None, test_only=test_only)
        result.update(
            {
                "job": "external_effect_queue_run_due",
                "scheduler": scheduler,
                "operator": operator,
                "dry_run": True,
                "mode": f"preview_{scan_mode}",
                "test_only": test_only,
            }
        )
        return result
    if not scheduler["enabled"]:
        return {
            "ok": True,
            "job": "external_effect_queue_run_due",
            "status": "skipped",
            "reason": "scheduler_disabled",
            "scheduler": scheduler,
            "operator": operator,
            "dry_run": False,
            "mode": f"execute_{scan_mode}",
            "test_only": test_only,
            "items": [],
            "counts": {
                "candidate_count": 0,
                "processed_count": 0,
                "succeeded_count": 0,
                "simulated_count": 0,
                "skipped_count": 0,
                "unknown_after_dispatch_count": 0,
                "failed_count": 0,
                "blocked_count": 0,
                "lost_lease_count": 0,
            },
            "real_external_call_executed": False,
        }

    items: list[dict[str, Any]] = []
    counts = {
        "candidate_count": 0,
        "processed_count": 0,
        "succeeded_count": 0,
        "simulated_count": 0,
        "skipped_count": 0,
        "unknown_after_dispatch_count": 0,
        "failed_count": 0,
        "blocked_count": 0,
        "lost_lease_count": 0,
    }
    real_external_call_executed = False
    ok = True
    for _index in range(batch_size):
        result = worker.run_due(batch_size=1, dry_run=False, effect_types=None, test_only=test_only)
        current = list(result.get("items") or [])
        current_counts = dict(result.get("counts") or {})
        for key in counts:
            counts[key] += int(current_counts.get(key) or 0)
        ok = bool(ok and result.get("ok"))
        real_external_call_executed = real_external_call_executed or bool(result.get("real_external_call_executed"))
        if not current:
            break
        items.extend(current)

    return {
        "ok": ok,
        "exit_code": 0 if ok else 1,
        "job": "external_effect_queue_run_due",
        "status": "ok" if ok else "failed",
        "scheduler": scheduler,
        "operator": operator,
        "dry_run": False,
        "mode": f"execute_{scan_mode}",
        "test_only": test_only,
        "items": items,
        "counts": counts,
        "real_external_call_executed": real_external_call_executed,
    }


def print_run_due_result(*, dry_run: bool, limit: int | None = None, operator: str = "cli") -> None:
    _write_json_result(run_scheduled_external_effects(dry_run=dry_run, limit=limit, operator=operator))


def complete_record_only_jobs(*, dry_run: bool = True, limit: int = 100, operator: str = "cli") -> dict[str, Any]:
    from .service import ExternalEffectService

    return ExternalEffectService().complete_record_only(dry_run=dry_run, limit=limit, operator=operator)


def print_complete_record_only_result(*, dry_run: bool, limit: int = 100, operator: str = "cli") -> None:
    _write_json_result(complete_record_only_jobs(dry_run=dry_run, limit=limit, operator=operator))
