#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json, read_int_env
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json, read_int_env

ensure_repo_root_on_path()

from aicrm_next.automation.background_jobs.broadcast_queue_worker import run_broadcast_queue_worker


def run(*, batch_size: int | None = None, limit: int | None = None, dry_run: bool = False) -> dict:
    """Backward-compatible module entrypoint for existing smoke tests."""
    selected_limit = limit if limit is not None else batch_size
    return run_broadcast_queue_worker(limit=int(selected_limit or 50), dry_run=bool(dry_run))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Next-native broadcast queue worker.")
    parser.add_argument("--limit", type=int, default=read_int_env("BROADCAST_QUEUE_BATCH_SIZE", 50))
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = run_broadcast_queue_worker(limit=int(args.limit), dry_run=bool(args.dry_run))
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
