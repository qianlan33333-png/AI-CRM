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
from aicrm_next.platform.platform_foundation.background_jobs.catalog import JOB_SPECS, validate_job_catalog


DEFAULT_ROLE_CATALOG = ROOT / "deploy" / "runtime_role_catalog.json"
DEFAULT_RUNTIME_UNITS = ROOT / "deploy" / "production_runtime_units.json"
INTERNAL_WORKER_OBSERVER = "aicrm-internal-worker-observer.service"
INTERNAL_WORKER_SERVICE = "aicrm-internal-worker.service"
INTERNAL_WORKER_PREDECESSORS = {
    "aicrm-internal-queue-runtime.service",
    "aicrm-inbox-queue-runtime.service",
}
INTERNAL_WORKER_HEARTBEATS = {
    "aicrm-internal_event-runtime",
    "aicrm-internal_outbox-runtime",
    "aicrm-webhook_inbox-runtime",
}
SCHEDULER_PREDECESSORS = {
    "aicrm-next-broadcast-delegation.timer": "aicrm-next-broadcast-delegation.service",
    "aicrm-next-group-ops-planning.timer": "aicrm-next-group-ops-planning.service",
    "aicrm-ai-audience-daily-intent.timer": "aicrm-ai-audience-daily-intent.service",
}
SCHEDULER_EXECUTE_COMMAND = (
    "python scripts/run_job_catalog_scheduler.py --execute "
    "--confirmation EXECUTE_SAFE_JOB_CATALOG_SCHEDULER"
)


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
        for token in (
            "--confirmation EXECUTE_SAFE_JOB_CATALOG_SCHEDULER",
            "EnvironmentFile=-/home/ubuntu/.aicrm-queue-runtime-generation.env",
        ):
            if token not in service_body:
                errors.append(f"enforced scheduler service is missing parity token: {token}")
        if "AICRM_GROUP_OPS_MATERIAL_UPLOAD_MODE=real" in service_body:
            errors.append("enforced scheduler must not own synchronous WeCom material upload")
        if scheduler.get("legacy_units_remain_authoritative") is not False:
            errors.append("enforced scheduler cannot leave predecessor units authoritative")
    else:
        errors.append("job catalog scheduler activation_mode must be observe or enforce")
    predecessor_timers = dict(scheduler.get("predecessor_timers") or {})
    if not set(predecessor_timers).issubset(safe_jobs):
        errors.append("scheduler predecessor mappings must reference safe catalog jobs")
    if set(predecessor_timers.values()) != set(SCHEDULER_PREDECESSORS):
        errors.append("scheduler must declare the three reviewed predecessor timers")
    replacement_timers = {
        str(item.get("timer") or "")
        for item in dict(raw.get("cutover_replacement_autostart") or {}).get("timers") or []
    }
    if mode == "observe" and not set(predecessor_timers.values()).issubset(replacement_timers):
        errors.append("observer scheduler predecessors must remain replacement timers")
    if mode == "enforce":
        retired = set(raw.get("retired_forbidden") or [])
        retired_files = set(raw.get("retired_unit_files") or [])
        predecessor_units = set(SCHEDULER_PREDECESSORS) | set(SCHEDULER_PREDECESSORS.values())
        if not predecessor_units.issubset(retired & retired_files):
            errors.append("enforced scheduler must retire every predecessor timer and service")
        if set(SCHEDULER_PREDECESSORS) & replacement_timers:
            errors.append("enforced scheduler predecessors cannot remain cutover replacements")
        residue = sorted(
            unit for unit in predecessor_units if (ROOT / "deploy" / unit).exists()
        )
        if residue:
            errors.append("enforced scheduler predecessor unit files remain deployable: " + ", ".join(residue))
    return errors


def validate_internal_worker_consolidation_manifest(
    path: Path = DEFAULT_RUNTIME_UNITS,
) -> list[str]:
    errors: list[str] = []
    raw = json.loads(path.read_text(encoding="utf-8"))
    consolidation = dict(raw.get("internal_worker_consolidation") or {})
    active_services = {
        str(item.get("service") or ""): dict(item)
        for item in raw.get("active_services") or []
    }
    if consolidation.get("runtime_role") != "internal_worker":
        errors.append("internal_worker consolidation must bind the stable internal_worker role")
    if set(consolidation.get("predecessor_services") or []) != INTERNAL_WORKER_PREDECESSORS:
        errors.append("internal_worker consolidation must declare both predecessor services")
    if set(consolidation.get("heartbeat_service_names") or []) != INTERNAL_WORKER_HEARTBEATS:
        errors.append("internal_worker consolidation must declare all three heartbeat contracts")
    if consolidation.get("worker_id_namespace") != ":role:internal_worker:":
        errors.append("internal_worker consolidation must use the isolated role worker-id namespace")
    if consolidation.get("requires_successor_parity") is not True:
        errors.append("internal_worker consolidation must require successor parity")
    if consolidation.get("real_external_calls_allowed") is not False:
        errors.append("internal_worker consolidation must forbid real external calls")

    mode = str(consolidation.get("activation_mode") or "")
    if mode == "observe":
        if consolidation.get("observer_service") != INTERNAL_WORKER_OBSERVER:
            errors.append("internal_worker observe mode must declare the reviewed observer service")
        observer = active_services.get(INTERNAL_WORKER_OBSERVER)
        if not observer or observer.get("stop_for_migration") is not True:
            errors.append("internal_worker observer must be an active migration-safe service")
        if not INTERNAL_WORKER_PREDECESSORS.issubset(active_services):
            errors.append("internal_worker predecessors must remain active during observer parity")
        if consolidation.get("legacy_units_remain_authoritative") is not True:
            errors.append("internal_worker observe mode must leave predecessors authoritative")
        service_name = INTERNAL_WORKER_OBSERVER
        required_command = "scripts/run_execution_runtime.py --role internal_worker --standby"
    elif mode == "enforce":
        if consolidation.get("service") != INTERNAL_WORKER_SERVICE:
            errors.append("internal_worker enforce mode must declare the canonical owner service")
        owner = active_services.get(INTERNAL_WORKER_SERVICE)
        if not owner or owner.get("stop_for_migration") is not True:
            errors.append("internal_worker owner must be an active migration-safe service")
        if INTERNAL_WORKER_PREDECESSORS & set(active_services):
            errors.append("internal_worker predecessors cannot remain active after owner cutover")
        retired = set(raw.get("retired_forbidden") or [])
        retired_files = set(raw.get("retired_unit_files") or [])
        expected_retired = INTERNAL_WORKER_PREDECESSORS | {INTERNAL_WORKER_OBSERVER}
        if not expected_retired.issubset(retired & retired_files):
            errors.append("internal_worker predecessors and observer must be retired unit files")
        if consolidation.get("legacy_units_remain_authoritative") is not False:
            errors.append("internal_worker enforce mode cannot leave predecessors authoritative")
        service_name = INTERNAL_WORKER_SERVICE
        required_command = "scripts/run_execution_runtime.py --role internal_worker"
    else:
        errors.append("internal_worker activation_mode must be observe or enforce")
        return errors

    service_path = ROOT / "deploy" / service_name
    if not service_path.exists():
        errors.append(f"internal_worker service file is missing: {service_name}")
        return errors
    body = service_path.read_text(encoding="utf-8")
    for token in (
        required_command,
        "EnvironmentFile=-/home/ubuntu/.aicrm-queue-runtime-generation.env",
        "Restart=always",
    ):
        if token not in body:
            errors.append(f"internal_worker service is missing fail-closed token: {token}")
    if mode == "observe" and "--execute" in body:
        errors.append("internal_worker observer must never enable execution mode")
    if mode == "enforce" and "--standby" in body:
        errors.append("internal_worker owner cannot remain in standby mode")
    return errors


def validate_runtime_role_catalog(path: Path = DEFAULT_ROLE_CATALOG) -> list[str]:
    errors = list(validate_job_catalog())
    errors.extend(validate_job_catalog_scheduler_manifest())
    errors.extend(validate_internal_worker_consolidation_manifest())
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
    runtime_manifest = json.loads(DEFAULT_RUNTIME_UNITS.read_text(encoding="utf-8"))
    internal_mode = str(
        dict(runtime_manifest.get("internal_worker_consolidation") or {}).get("activation_mode")
        or ""
    )
    scheduler_mode = str(
        dict(runtime_manifest.get("job_catalog_scheduler") or {}).get("activation_mode") or ""
    )
    expected_role_activation = {
        "web": "enforce",
        "callback": "enforce",
        "internal_worker": internal_mode,
        "external_worker": "enforce",
        "scheduler": scheduler_mode,
    }
    if dict(raw.get("role_activation") or {}) != expected_role_activation:
        errors.append("runtime role activation must match the production owner manifest")
    expected_activation_mode = (
        "enforce"
        if set(expected_role_activation.values()) == {"enforce"}
        else "mixed"
    )
    if raw.get("activation_mode") != expected_activation_mode:
        errors.append(
            "runtime role catalog activation mode must match staged role activation"
        )
    cutover = dict(raw.get("cutover_policy") or {})
    if cutover.get("activate_new_units") is not True:
        errors.append("runtime cutover must activate the reviewed internal_worker owner")
    if cutover.get("requires_successor_parity") is not True:
        errors.append("runtime cutover must require successor parity")
    if cutover.get("observer_unit_active") is not False:
        errors.append("runtime cutover must retire the claimless internal_worker observer")
    if cutover.get("internal_worker_predecessors_authoritative") is not False:
        errors.append("runtime cutover must retire internal_worker predecessor owners")
    expected_scheduler_predecessors = scheduler_mode == "observe"
    if cutover.get("scheduler_predecessors_authoritative") is not expected_scheduler_predecessors:
        errors.append("runtime cutover scheduler predecessor authority must match scheduler mode")
    internal_worker = next(
        (item for item in roles if str(item.get("role") or "") == "internal_worker"),
        {},
    )
    if internal_worker.get("command") != "python scripts/run_execution_runtime.py --role internal_worker":
        errors.append("internal_worker role command must bind the enforced combined owner")
    scheduler = next(
        (item for item in roles if str(item.get("role") or "") == "scheduler"),
        {},
    )
    expected_scheduler_command = (
        SCHEDULER_EXECUTE_COMMAND
        if scheduler_mode == "enforce"
        else "python scripts/run_job_catalog_scheduler.py --dry-run"
    )
    if scheduler.get("command") != expected_scheduler_command:
        errors.append("scheduler role command must match its activation mode")
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
