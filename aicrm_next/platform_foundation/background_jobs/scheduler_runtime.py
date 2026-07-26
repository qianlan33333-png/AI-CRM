from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .catalog import JobCatalog, JobSpec


SchedulerHandler = Callable[..., dict[str, Any]]


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def schedule_is_due(schedule: str, *, now: datetime | None = None) -> bool:
    """Evaluate the intentionally small minute-based catalog schedule grammar."""

    parts = str(schedule or "").strip().split()
    if len(parts) != 5 or any(part != "*" for part in parts[1:]):
        raise ValueError("scheduler catalog supports only minute-based UTC schedules")
    minute = _utc(now).minute
    minute_spec = parts[0]
    if minute_spec == "*":
        return True
    if minute_spec.startswith("*/"):
        try:
            interval = int(minute_spec[2:])
        except ValueError as exc:
            raise ValueError("invalid scheduler minute interval") from exc
        if not 1 <= interval <= 60:
            raise ValueError("scheduler minute interval must be between 1 and 60")
        return minute % interval == 0
    try:
        selected_minute = int(minute_spec)
    except ValueError as exc:
        raise ValueError("invalid scheduler minute") from exc
    if not 0 <= selected_minute <= 59:
        raise ValueError("scheduler minute must be between 0 and 59")
    return minute == selected_minute


_PUBLIC_SCALARS = {
    "ok",
    "dry_run",
    "status",
    "reason",
    "candidate_count",
    "completed_count",
    "claimed",
    "delegated",
    "sent_ok",
    "simulated",
    "sent_failed",
    "unknown_after_dispatch",
    "skipped",
    "group_ops_scanned_plans",
    "group_ops_due_nodes",
    "group_ops_enqueued_jobs",
    "group_ops_external_effect_jobs",
    "group_ops_reused_external_effect_jobs",
    "group_ops_skipped_future",
    "group_ops_skipped_duplicate",
    "daily_tick_due",
    "daily_tick_window_due",
    "daily_tick_forced",
    "would_emit_incremental_tick",
    "would_check_daily_tick",
    "real_external_call_executed",
}


def _redacted_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in sorted(_PUBLIC_SCALARS):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if key in payload:
                result[key] = value
    counts = payload.get("counts")
    if isinstance(counts, Mapping):
        result["counts"] = {
            str(key): int(value)
            for key, value in sorted(counts.items())
            if isinstance(value, int) and not isinstance(value, bool)
        }
    errors = payload.get("errors")
    if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
        result["error_count"] = len(errors)
    items = payload.get("items")
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
        result["item_count"] = len(items)
    components = payload.get("components")
    if isinstance(components, Sequence) and not isinstance(components, (str, bytes)):
        result["component_count"] = len(components)
        group_ops = next(
            (
                item
                for item in components
                if isinstance(item, Mapping)
                and str(item.get("component") or "") == "group_ops_scheduler"
            ),
            None,
        )
        if isinstance(group_ops, Mapping):
            result["group_ops"] = _redacted_result(group_ops)
    result.setdefault("real_external_call_executed", False)
    return result


class JobCatalogScheduler:
    def __init__(
        self,
        *,
        catalog: JobCatalog | None = None,
        handlers: Mapping[str, SchedulerHandler] | None = None,
    ) -> None:
        self._catalog = catalog or JobCatalog()
        self._handlers = dict(handlers or {})

    def run(
        self,
        *,
        dry_run: bool,
        requested_job_types: Sequence[str] = (),
        now: datetime | None = None,
        operator: str = "job_catalog_scheduler",
        limit: int = 100,
    ) -> dict[str, Any]:
        scanned_at = _utc(now)
        requested = tuple(
            dict.fromkeys(
                str(item or "").strip()
                for item in requested_job_types
                if str(item or "").strip()
            )
        )
        available = {
            spec.job_type: spec
            for spec in self._catalog.active_specs(runtime_role="scheduler")
        }
        unknown = sorted(set(requested) - set(available))
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = [
            {"job_type": job_type, "error": "job_not_active_for_scheduler"}
            for job_type in unknown
        ]
        selected = [
            spec
            for job_type, spec in sorted(available.items())
            if not requested or job_type in requested
        ]
        due_specs: list[JobSpec] = []
        for spec in selected:
            try:
                due = schedule_is_due(spec.schedule, now=scanned_at)
            except ValueError:
                due = False
                errors.append(
                    {"job_type": spec.job_type, "error": "invalid_catalog_schedule"}
                )
            if due:
                due_specs.append(spec)
            else:
                items.append(
                    {
                        "job_type": spec.job_type,
                        "status": "not_due",
                        "schedule": spec.schedule,
                        "execution_policy": spec.scheduler_execution,
                    }
                )

        executed_count = 0
        previewed_count = 0
        for spec in due_specs:
            if spec.scheduler_execution != "safe_command":
                item = {
                    "job_type": spec.job_type,
                    "status": "observe_only",
                    "schedule": spec.schedule,
                    "execution_policy": spec.scheduler_execution,
                }
                items.append(item)
                if not dry_run and spec.job_type in requested:
                    errors.append(
                        {"job_type": spec.job_type, "error": "job_is_observe_only"}
                    )
                continue
            handler = self._handlers.get(spec.job_type)
            if handler is None:
                items.append(
                    {
                        "job_type": spec.job_type,
                        "status": "handler_missing",
                        "schedule": spec.schedule,
                        "execution_policy": spec.scheduler_execution,
                    }
                )
                errors.append(
                    {"job_type": spec.job_type, "error": "safe_handler_missing"}
                )
                continue
            try:
                raw_result = dict(
                    handler(
                        dry_run=dry_run,
                        operator=operator,
                        limit=limit,
                        now=scanned_at,
                    )
                    or {}
                )
            except Exception as exc:
                items.append(
                    {
                        "job_type": spec.job_type,
                        "status": "failed",
                        "schedule": spec.schedule,
                        "execution_policy": spec.scheduler_execution,
                        "error_class": exc.__class__.__name__,
                    }
                )
                errors.append(
                    {"job_type": spec.job_type, "error": "handler_failed"}
                )
                continue
            if bool(raw_result.get("real_external_call_executed")):
                items.append(
                    {
                        "job_type": spec.job_type,
                        "status": "blocked",
                        "schedule": spec.schedule,
                        "execution_policy": spec.scheduler_execution,
                        "error": "provider_boundary_violation",
                    }
                )
                errors.append(
                    {
                        "job_type": spec.job_type,
                        "error": "provider_boundary_violation",
                    }
                )
                continue
            result_ok = raw_result.get("ok") is not False
            item = {
                "job_type": spec.job_type,
                "status": "previewed" if dry_run else ("executed" if result_ok else "failed"),
                "schedule": spec.schedule,
                "execution_policy": spec.scheduler_execution,
                "result": _redacted_result(raw_result),
            }
            items.append(item)
            if dry_run:
                previewed_count += 1
            else:
                executed_count += 1
            if not result_ok:
                errors.append(
                    {"job_type": spec.job_type, "error": "handler_reported_failure"}
                )

        return {
            "ok": not errors,
            "dry_run": bool(dry_run),
            "profile_id": self._catalog.summary()["profile_id"],
            "activation_mode": self._catalog.summary()["activation_mode"],
            "scanned_at": scanned_at.isoformat(),
            "candidate_count": len(selected),
            "due_count": len(due_specs),
            "previewed_count": previewed_count,
            "executed_count": executed_count,
            "items": items,
            "errors": errors,
            "real_external_call_executed": False,
        }


__all__ = [
    "JobCatalogScheduler",
    "SchedulerHandler",
    "schedule_is_due",
]
