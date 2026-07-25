#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.platform_foundation.background_jobs.catalog import JobCatalog


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the versioned AI-CRM scheduler job catalog.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--job-type", action="append", default=[])
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, object]:
    catalog = JobCatalog()
    requested = {str(item or "").strip() for item in args.job_type if str(item or "").strip()}
    candidates = [
        spec.to_dict()
        for spec in catalog.active_specs(runtime_role="scheduler")
        if not requested or spec.job_type in requested
    ]
    if args.execute:
        return {
            "ok": False,
            "error": "scheduler_cutover_not_activated",
            "candidate_count": len(candidates),
            "items": candidates,
            "real_external_call_executed": False,
        }
    return {
        "ok": True,
        "dry_run": True,
        "candidate_count": len(candidates),
        "items": candidates,
        "real_external_call_executed": False,
    }


def main(argv: list[str] | None = None) -> int:
    payload = run(_parse_args(argv))
    print_json(payload, sort_keys=True)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
