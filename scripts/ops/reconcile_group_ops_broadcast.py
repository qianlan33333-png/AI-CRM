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

from aicrm_next.automation.background_jobs.broadcast_reconciliation import (
    GroupOpsBroadcastReconciliationService,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count R10 Group Ops/broadcast delivery gaps without repair or provider execution."
    )
    return parser.parse_args(argv)


def run() -> dict:
    return GroupOpsBroadcastReconciliationService().diagnose()


def main(argv: list[str] | None = None) -> int:
    _parse_args(argv)
    payload = run()
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
