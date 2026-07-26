#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json, read_int_env
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json, read_int_env

ensure_repo_root_on_path()

from aicrm_next.platform_foundation.internal_events.config import DEFAULT_WORKER_BATCH_SIZE
from aicrm_next.platform_foundation.internal_events.worker import InternalEventWorker
from aicrm_next.internal_event_composition import build_internal_event_consumer_registry


def _csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _default_limit() -> int:
    return read_int_env(
        "AICRM_INTERNAL_EVENTS_WORKER_BATCH_SIZE",
        read_int_env("AICRM_INTERNAL_EVENT_WORKER_BATCH_SIZE", DEFAULT_WORKER_BATCH_SIZE),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AI-CRM Internal Event Queue worker.")
    parser.add_argument("--limit", "--batch-size", dest="limit", type=int, default=_default_limit())
    parser.add_argument("--event-types", default="")
    parser.add_argument("--consumer-names", default="")
    parser.add_argument(
        "--outbox-idempotency-key",
        default="",
        help="Relay and dispatch only the exact outbox key; requires --execute plus exactly one event type and consumer.",
    )
    parser.add_argument("--execute", action="store_true", default=False, help="Dispatch consumers. Without this flag the worker dry-runs.")
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be >= 1")
    if args.outbox_idempotency_key and not args.execute:
        parser.error("--outbox-idempotency-key requires --execute")
    if args.outbox_idempotency_key and len(_csv(args.event_types)) != 1:
        parser.error("--outbox-idempotency-key requires exactly one --event-types value")
    if args.outbox_idempotency_key and len(_csv(args.consumer_names)) != 1:
        parser.error("--outbox-idempotency-key requires exactly one --consumer-names value")
    return args


def run(
    *,
    limit: int | None = None,
    dry_run: bool = True,
    event_types: list[str] | None = None,
    consumer_names: list[str] | None = None,
    outbox_idempotency_key: str = "",
) -> dict:
    return InternalEventWorker(
        consumer_registry=build_internal_event_consumer_registry(),
        relay_role="owner",
    ).run_due(
        batch_size=int(limit or _default_limit()),
        dry_run=bool(dry_run),
        event_types=event_types,
        consumer_names=consumer_names,
        outbox_idempotency_key=str(outbox_idempotency_key or "").strip(),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = run(
        limit=int(args.limit),
        dry_run=not bool(args.execute),
        event_types=_csv(args.event_types),
        consumer_names=_csv(args.consumer_names),
        outbox_idempotency_key=args.outbox_idempotency_key,
    )
    print_json(payload)
    if payload.get("dry_run"):
        return 0
    return int(payload.get("exit_code") or (0 if payload.get("ok") else 1))


if __name__ == "__main__":
    sys.exit(main())
