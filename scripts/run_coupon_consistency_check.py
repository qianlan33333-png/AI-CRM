#!/usr/bin/env python3
from __future__ import annotations

import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.extensions.commerce.commerce.coupons.checker import run_coupon_consistency_check


def main() -> int:
    payload = run_coupon_consistency_check()
    print_json(payload)
    if payload.get("skipped"):
        return 0
    return 1 if int(payload.get("total") or 0) > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
