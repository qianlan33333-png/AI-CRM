#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.extensions.commerce.commerce.order_reconciliation import WeChatPayOrderReconciliationWorker
from aicrm_next.extensions.commerce.public_product.h5_wechat_pay import _apply_transaction


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WeChat Pay unpaid-order reconciliation.")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--execute", action="store_true", default=False)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--ttl-hours", type=int, default=0)
    parser.add_argument("--window-hours", type=int, default=24)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dry_run = not bool(args.execute)
    if args.dry_run:
        dry_run = True
    payload = WeChatPayOrderReconciliationWorker(transaction_applier=_apply_transaction).run_once(
        limit=int(args.limit or 100),
        ttl_hours=int(args.ttl_hours or 0) or None,
        window_hours=int(args.window_hours or 24),
        dry_run=dry_run,
    )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
