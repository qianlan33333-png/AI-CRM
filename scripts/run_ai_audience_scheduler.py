#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json, read_int_env
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json, read_int_env

ensure_repo_root_on_path()

from aicrm_next.ai_audience_ops import register_ai_audience_event_consumers
from aicrm_next.ai_audience_ops.scheduler import (
    DEFAULT_DAILY_REFRESH_TIME,
    DEFAULT_DAILY_TICK_WINDOW_MINUTES,
    emit_due_ticks,
    run_due_ai_audience_consumers,
)
from scripts.ops.check_ai_audience_refresh_owner import assert_legacy_owner_allowed

register_ai_audience_event_consumers()


def _default_batch_size() -> int:
    return read_int_env("AICRM_AI_AUDIENCE_SCHEDULER_BATCH_SIZE", 20)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write durable AI audience clock intents; preserve the guarded old owner until PR-3 cutover.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--incremental-only", action="store_true", help="Write compatibility incremental intents without executing them.")
    mode.add_argument("--daily-only", action="store_true", help="Write the daily intent; this is the production timer mode.")
    parser.add_argument(
        "--run-consumers",
        action="store_true",
        help="PR-3 cutover-only compatibility for the already-installed three-minute owner.",
    )
    parser.add_argument("--execute", action="store_true", help="Execute consumers on the guarded legacy-owner path.")
    parser.add_argument("--batch-size", type=int, default=_default_batch_size())
    parser.add_argument("--daily-at", default=DEFAULT_DAILY_REFRESH_TIME)
    parser.add_argument("--daily-window-minutes", type=int, default=DEFAULT_DAILY_TICK_WINDOW_MINUTES)
    args = parser.parse_args()

    if args.execute and not args.run_consumers:
        parser.error("--execute is only valid with the guarded --run-consumers legacy-owner path")

    legacy_owner = None
    if args.run_consumers:
        legacy_owner = assert_legacy_owner_allowed()
        include_incremental = not args.daily_only
        include_daily = not args.incremental_only
    else:
        include_incremental = bool(args.incremental_only)
        include_daily = not args.incremental_only
    payload = {
        "ticks": emit_due_ticks(
            include_daily=include_daily,
            include_incremental=include_incremental,
            daily_refresh_time=args.daily_at,
            daily_window_minutes=args.daily_window_minutes,
            force_daily=bool(args.daily_only and not args.run_consumers),
        )
    }
    if args.run_consumers:
        run_daily_consumers = include_daily and bool(payload["ticks"].get("daily_tick_due"))
        payload["legacy_owner_guard"] = legacy_owner
        payload["consumers"] = run_due_ai_audience_consumers(
            dry_run=not args.execute,
            batch_size=args.batch_size,
            include_incremental_refresh=include_incremental,
            include_daily_refresh=run_daily_consumers,
        )
    print_json(payload, indent=2)
    return 0 if payload["ticks"].get("ok") and payload.get("consumers", {"ok": True}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
