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


def validate_runtime_role_catalog(path: Path = DEFAULT_ROLE_CATALOG) -> list[str]:
    errors = list(validate_job_catalog())
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
