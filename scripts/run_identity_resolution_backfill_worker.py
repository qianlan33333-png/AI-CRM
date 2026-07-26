#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.channel_entry.identity_resolution_worker import IdentityResolutionBackfillWorker
from aicrm_next.channel_entry_composition import configure_channel_crm_dependencies


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CRM user identity resolution backfill worker.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--execute", action="store_true", default=False)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--operator", default="identity_resolution_worker")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    configure_channel_crm_dependencies()
    dry_run = not bool(args.execute)
    if args.dry_run:
        dry_run = True
    payload = IdentityResolutionBackfillWorker(locked_by=str(args.operator or "identity_resolution_worker")).run_due(
        limit=int(args.limit or 100),
        max_attempts=int(args.max_attempts or 5),
        dry_run=dry_run,
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
