#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aicrm_next.deployment_profile import RUNTIME_ROLES
from aicrm_next.platform_foundation.background_jobs.catalog import JOB_SPECS, validate_job_catalog


DEFAULT_ROLE_CATALOG = ROOT / "deploy" / "runtime_role_catalog.json"
DEFAULT_RUNTIME_UNITS = ROOT / "deploy" / "production_runtime_units.json"


def validate_job_catalog_scheduler_manifest(
    path: Path = DEFAULT_RUNTIME_UNITS,
) -> list[str]:
    errors: list[str] = []
    raw = json.loads(path.read_text(encoding="utf-8"))
    scheduler = dict(raw.get("job_catalog_scheduler") or {})
    active_timers = {
        str(item.get("timer") or ""): str(item.get("service") or "")
        for item in raw.get("active_autostart") or []
    }
    safe_jobs = {
        spec.job_type
        for spec in JOB_SPECS
        if spec.runtime_role == "scheduler"
        and spec.scheduler_execution == "safe_command"
    }
    observe_only_jobs = {
        spec.job_type
        for spec in JOB_SPECS
        if spec.runtime_role == "scheduler"
        and spec.scheduler_execution == "observe_only"
    }
    if set(scheduler.get("safe_job_types") or []) != safe_jobs:
        errors.append("runtime manifest safe scheduler jobs must match the job catalog")
    if set(scheduler.get("observe_only_job_types") or []) != observe_only_jobs:
        errors.append("runtime manifest observe-only jobs must match the job catalog")
    timer = str(scheduler.get("timer") or "")
    service = str(scheduler.get("service") or "")
    if not timer or active_timers.get(timer) != service:
        errors.append("job catalog scheduler timer must be active and own its service")
        return errors
    service_path = ROOT / "deploy" / service
    if not service_path.exists():
        errors.append("job catalog scheduler service file is missing")
        return errors
    service_body = service_path.read_text(encoding="utf-8")
    mode = str(scheduler.get("activation_mode") or "")
    if mode == "observe":
        if "AICRM_JOB_CATALOG_SCHEDULER_EXECUTE=0" not in service_body:
            errors.append("observer scheduler must keep the execute environment gate closed")
        if "run_job_catalog_scheduler.py --dry-run" not in service_body or "--execute" in service_body:
            errors.append("observer scheduler service must be dry-run only")
        if scheduler.get("legacy_units_remain_authoritative") is not True:
            errors.append("observer scheduler must leave predecessor units authoritative")
    elif mode == "enforce":
        if "AICRM_JOB_CATALOG_SCHEDULER_EXECUTE=1" not in service_body:
            errors.append("enforced scheduler must explicitly open the execute environment gate")
        if "run_job_catalog_scheduler.py --execute" not in service_body:
            errors.append("enforced scheduler service must use execute mode")
        if scheduler.get("legacy_units_remain_authoritative") is not False:
            errors.append("enforced scheduler cannot leave predecessor units authoritative")
    else:
        errors.append("job catalog scheduler activation_mode must be observe or enforce")
    predecessor_timers = dict(scheduler.get("predecessor_timers") or {})
    if not set(predecessor_timers).issubset(safe_jobs):
        errors.append("scheduler predecessor mappings must reference safe catalog jobs")
    replacement_timers = {
        str(item.get("timer") or "")
        for item in dict(raw.get("cutover_replacement_autostart") or {}).get("timers") or []
    }
    if mode == "observe" and not set(predecessor_timers.values()).issubset(replacement_timers):
        errors.append("observer scheduler predecessors must remain replacement timers")
    return errors


def validate_runtime_role_catalog(path: Path = DEFAULT_ROLE_CATALOG) -> list[str]:
    errors = list(validate_job_catalog())
    errors.extend(validate_job_catalog_scheduler_manifest())
    raw = json.loads(path.read_text(encoding="utf-8"))
    roles = list(raw.get("roles") or [])
    role_names = [str(item.get("role") or "") for item in roles]
    if set(role_names) != set(RUNTIME_ROLES) or len(role_names) != len(set(role_names)):
        errors.append("runtime role catalog must declare the five unique deployment roles")
    provider_roles = {str(item.get("role") or "") for item in roles if item.get("may_call_real_provider") is True}
    if provider_roles != {"external_worker"}:
        errors.append("external_worker must be the only role allowed to call a real provider")
    if raw.get("artifact_policy") != "same_commit_same_artifact":
        errors.append("runtime roles must use the same commit and artifact")
    cutover = dict(raw.get("cutover_policy") or {})
    if cutover.get("activate_new_units") is not False:
        errors.append("candidate runtime roles must not activate units before successor parity")
    if cutover.get("requires_successor_parity") is not True:
        errors.append("runtime cutover must require successor parity")
    catalog_roles = {spec.runtime_role for spec in JOB_SPECS}
    if not catalog_roles <= set(role_names):
        errors.append("job catalog references an undeclared runtime role")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the versioned job and runtime role catalogs.")
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLE_CATALOG)
    args = parser.parse_args(argv)
    errors = validate_runtime_role_catalog(args.roles)
    if errors:
        print("Job catalog check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Job catalog OK: {len(JOB_SPECS)} jobs across five runtime roles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
