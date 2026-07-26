#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.background_jobs.automation_ops_scheduler import run_automation_ops_scheduler
from aicrm_next.automation_engine.group_ops.scheduler import run_group_ops_due_scheduler


def _run_group_ops(*, now, operator, dry_run):
    if dry_run:
        return {"component": "group_ops_scheduler", "status": "skipped", "reason": "dry_run"}
    return {
        "component": "group_ops_scheduler",
        "status": "ok",
        **run_group_ops_due_scheduler(now=now, operator=operator),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Next-native automation ops scheduler.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--operator", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = run_automation_ops_scheduler(
        dry_run=bool(args.dry_run),
        operator=str(args.operator or "") or None,
        group_ops_runner=_run_group_ops,
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
