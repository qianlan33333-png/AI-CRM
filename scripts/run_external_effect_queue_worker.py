#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.external_effect_composition import (
    build_external_effect_adapter_registry,
    build_external_effect_continuation_registry,
)
from aicrm_next.platform.platform_foundation.external_effects.jobs import run_scheduled_external_effects


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run External Effect Queue due jobs through the unified scheduler.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--execute", action="store_true", default=False)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--operator", default="external_effect_queue_worker")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dry_run = not bool(args.execute)
    if args.dry_run:
        dry_run = True
    payload = run_scheduled_external_effects(
        dry_run=dry_run,
        limit=int(args.limit or 0) or None,
        operator=str(args.operator or "").strip() or "external_effect_queue_worker",
        adapter_registry=build_external_effect_adapter_registry(),
        continuation_registry=build_external_effect_continuation_registry(),
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
