#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.platform.platform_foundation.external_effects.reconciliation import (
    ExternalEffectDispatchReconciliationService,
)


def run() -> dict:
    return ExternalEffectDispatchReconciliationService().diagnose()


def main() -> int:
    payload = run()
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
