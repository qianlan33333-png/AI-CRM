from __future__ import annotations

import argparse
import sys

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.extensions.commerce.service_period.huangyoucan_usage_sync import (  # noqa: E402
    sanitize_huangyoucan_usage_error,
    sync_huangyoucan_usage,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync readonly HuangYouCan usage into the AI-CRM service-period projection.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--operator", default="huangyoucan_usage_timer")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = sync_huangyoucan_usage(dry_run=args.dry_run, trigger_source=args.operator)
    except Exception as exc:
        print_json(
            {
                "ok": False,
                "error_code": "huangyoucan_usage_sync_failed",
                "message": sanitize_huangyoucan_usage_error(exc),
                "source_status": "next_huangyoucan_usage_sync",
                "route_owner": "ai_crm_next",
                "fallback_used": False,
            }
        )
        return 1
    print_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
