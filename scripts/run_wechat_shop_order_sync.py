from __future__ import annotations

import argparse
import sys
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json, read_int_env
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json, read_int_env

ensure_repo_root_on_path()

from aicrm_next.extensions.commerce.commerce.wechat_shop_service import (  # noqa: E402
    sanitize_wechat_shop_error,
    sync_wechat_shop_orders_backfill,
    sync_wechat_shop_orders_incremental,
    sync_wechat_shop_orders_window,
)


DEFAULT_LOOKBACK_MINUTES = 120
DEFAULT_OVERLAP_MINUTES = 15
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 200
DEFAULT_WINDOW_DAYS = 7


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.mode == "incremental":
        return sync_wechat_shop_orders_incremental(
            lookback_minutes=args.lookback_minutes,
            overlap_minutes=args.overlap_minutes,
            page_size=args.page_size,
            max_pages=args.max_pages,
            dry_run=args.dry_run,
            operator=args.operator,
        )
    if args.mode == "window":
        return sync_wechat_shop_orders_window(
            args.start_time,
            args.end_time,
            mode=args.time_mode,
            page_size=args.page_size,
            max_pages=args.max_pages,
            sync_type="manual_window",
            dry_run=args.dry_run,
            operator=args.operator,
        )
    return sync_wechat_shop_orders_backfill(
        start_time=args.start_time,
        end_time=args.end_time,
        window_days=args.window_days,
        page_size=args.page_size,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
        operator=args.operator,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync WeChat Shop orders into AI-CRM.")
    parser.add_argument("--mode", choices=["incremental", "window", "backfill"], default="incremental")
    parser.add_argument("--start-time", default="")
    parser.add_argument("--end-time", default="")
    parser.add_argument("--time-mode", choices=["create_time", "update_time"], default="update_time")
    parser.add_argument("--lookback-minutes", type=int, default=read_int_env("WECHAT_SHOP_SYNC_LOOKBACK_MINUTES", DEFAULT_LOOKBACK_MINUTES))
    parser.add_argument("--overlap-minutes", type=int, default=read_int_env("WECHAT_SHOP_SYNC_OVERLAP_MINUTES", DEFAULT_OVERLAP_MINUTES))
    parser.add_argument("--window-days", type=int, default=read_int_env("WECHAT_SHOP_BACKFILL_WINDOW_DAYS", DEFAULT_WINDOW_DAYS))
    parser.add_argument("--page-size", type=int, default=read_int_env("WECHAT_SHOP_SYNC_PAGE_SIZE", DEFAULT_PAGE_SIZE))
    parser.add_argument("--max-pages", type=int, default=read_int_env("WECHAT_SHOP_SYNC_MAX_PAGES", DEFAULT_MAX_PAGES))
    parser.add_argument("--operator", default="wechat_shop_order_sync_timer")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode in {"window", "backfill"} and not args.start_time:
        print_json({"ok": False, "error_code": "start_time_required", "source_status": "next_wechat_shop_order_sync_script"})
        return 2
    if args.mode == "window" and not args.end_time:
        print_json({"ok": False, "error_code": "end_time_required", "source_status": "next_wechat_shop_order_sync_script"})
        return 2
    try:
        payload = run(args)
    except Exception as exc:
        print_json(
            {
                "ok": False,
                "error_code": "wechat_shop_order_sync_failed",
                "message": sanitize_wechat_shop_error(exc),
                "source_status": "next_wechat_shop_order_sync_script",
                "route_owner": "ai_crm_next",
                "fallback_used": False,
            }
        )
        return 1
    print_json(payload)
    return 0 if payload.get("ok") is not False else 1


if __name__ == "__main__":
    sys.exit(main())
