from __future__ import annotations

import argparse
import json
from typing import Sequence

from aicrm_next.ai_audience_ops.hxc_projection import (
    HxcMemberUsageProjectionService,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or inspect the unionid-based HuangXiaoCan audience projection."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--full",
        action="store_true",
        help="Build a new generation and atomically mark it active.",
    )
    action.add_argument(
        "--status",
        action="store_true",
        help="Read the non-PII projection generation and freshness status.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = HxcMemberUsageProjectionService()
    result = service.refresh_full() if args.full else {"ok": True, "status": service.status()}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
