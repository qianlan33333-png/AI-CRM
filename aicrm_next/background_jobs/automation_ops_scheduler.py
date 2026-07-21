from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _skipped(component: str, reason: str) -> dict[str, str]:
    return {"component": component, "status": "skipped", "reason": reason}


def _run_group_ops(*, now: datetime, operator: str, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return _skipped("group_ops_scheduler", "dry_run")
    from aicrm_next.automation_engine.group_ops.scheduler import run_group_ops_due_scheduler

    return {"component": "group_ops_scheduler", "status": "ok", **run_group_ops_due_scheduler(now=now, operator=operator)}


def run_automation_ops_scheduler(
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    operator: str | None = None,
    group_ops_runner: Callable[..., dict[str, Any]] | None = None,
    media_refresh_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scanned_at = _utc(now)
    actor = (operator or os.getenv("AUTOMATION_OPS_SCHEDULER_OPERATOR", "automation_ops_scheduler")).strip() or "automation_ops_scheduler"
    components: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    try:
        runner = group_ops_runner or _run_group_ops
        group_ops = runner(now=scanned_at, operator=actor, dry_run=dry_run)
        components.append(group_ops)
        errors.extend(list(group_ops.get("errors") or []))
    except Exception as exc:
        errors.append({"scope": "group_ops_scheduler", "error": str(exc)})
        components.append({"component": "group_ops_scheduler", "status": "failed", "error": str(exc)})

    # Media preparation is now created on demand as a durable dependency of
    # the business effect. Keep this parameter for one compatibility release,
    # but a periodic timer must never invoke it again.
    del media_refresh_runner
    components.append(_skipped("wecom_media_lease_refresher", "retired_manual_repair_only"))

    return {
        "ok": not errors,
        "job": "automation_ops_scheduler",
        "dry_run": bool(dry_run),
        "scanned_at": scanned_at.isoformat(),
        "operator": actor,
        "components": components,
        "errors": errors,
    }
