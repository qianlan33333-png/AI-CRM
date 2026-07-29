from __future__ import annotations

import argparse
import json
from typing import Sequence

from aicrm_next.extensions.ai.ai_audience_ops.hxc_projection import (
    HxcMemberUsageProjectionService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or inspect the unionid-based HuangXiaoCan audience projection.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--full",
        action="store_true",
        help="Build a new generation and atomically mark it active.",
    )
    action.add_argument(
        "--incremental",
        action="store_true",
        help="Refresh at most one bounded batch in the active generation.",
    )
    action.add_argument(
        "--status",
        action="store_true",
        help="Read the non-PII projection generation and freshness status.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5_000,
        help="Incremental source rows to scan; clamped to 1..5000.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = HxcMemberUsageProjectionService()
    if args.full:
        result = service.refresh_full()
    elif args.incremental:
        result = service.refresh_incremental(batch_size=args.batch_size)
    else:
        result = {"ok": True, "status": service.status()}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
