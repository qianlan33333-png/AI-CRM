#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.internal_event_composition import build_internal_event_consumer_registry
from aicrm_next.platform.platform_foundation.internal_events.reconciliation import InternalEventOutboxReconciliationService


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose or repair internal event outbox gaps without executing consumers.")
    parser.add_argument("--repair", action="store_true", help="Apply idempotent technical outbox/event/run repairs. Default is count-only dry-run.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be >= 1")
    return args


def run(*, repair: bool = False, limit: int = 100) -> dict:
    service = InternalEventOutboxReconciliationService(
        consumer_registry=build_internal_event_consumer_registry(),
    )
    return service.repair(dry_run=not repair, limit=limit) if repair else service.diagnose()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = run(repair=bool(args.repair), limit=int(args.limit))
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
