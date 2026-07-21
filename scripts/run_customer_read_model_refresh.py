#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()

from aicrm_next.customer_read_model.refresh_intents import (  # noqa: E402
    CustomerReadModelRefreshIntentRepository,
    CustomerReadModelRefreshIntentService,
)


def wait_for_refresh_completion(
    repository: CustomerReadModelRefreshIntentRepository,
    *,
    target_generation: int,
    timeout_seconds: float,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last: dict[str, Any] = {}
    while True:
        last = dict(repository.get() or {})
        dirty = int(last.get("dirty_generation") or 0)
        completed = int(last.get("completed_generation") or 0)
        status = str(last.get("status") or "missing")
        if completed >= max(int(target_generation), dirty) and status == "idle":
            return {
                "ok": True,
                "target_generation": int(target_generation),
                "dirty_generation": dirty,
                "completed_generation": completed,
                "status": status,
            }
        if status == "blocked":
            return {
                "ok": False,
                "reason": "customer_read_model_refresh_blocked",
                "target_generation": int(target_generation),
                "dirty_generation": dirty,
                "completed_generation": completed,
                "status": status,
            }
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "reason": "customer_read_model_refresh_wait_timeout",
                "target_generation": int(target_generation),
                "dirty_generation": dirty,
                "completed_generation": completed,
                "status": status,
            }
        time.sleep(max(0.01, float(poll_seconds)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a customer read-model refresh intent; never rebuild inline.")
    parser.add_argument("--execute", action="store_true", default=False, help="Deprecated compatibility flag; still writes intent only.")
    parser.add_argument("--max-customers", type=int, default=None, help="Deprecated; retained for CLI compatibility and ignored.")
    parser.add_argument("--source-key", default="")
    parser.add_argument(
        "--release-refresh",
        action="store_true",
        default=False,
        help="Allow a verified deploy to supersede one blocked local projection intent.",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        help="Wait for the persistent queue worker to complete the durable intent; never rebuild inline.",
    )
    args = parser.parse_args(argv)
    if args.wait_seconds < 0 or args.wait_seconds > 900:
        parser.error("--wait-seconds must be between 0 and 900")
    try:
        now = datetime.now(timezone.utc)
        bucket = now.strftime("%Y-%m-%dT%H:%M")
        source_event_key = str(args.source_key or f"compatibility_clock:{bucket}").strip()
        repository = CustomerReadModelRefreshIntentRepository()
        recover_blocked = False
        expected_row_version: int | None = None
        source_event_type = "customer_read_model.compatibility_clock"
        if args.release_refresh:
            if not args.execute:
                raise RuntimeError("customer_read_model_release_refresh_execute_required")
            if os.getenv("AICRM_CUSTOMER_READ_MODEL_RELEASE_REFRESH_AUTHORIZED", "").strip() != "1":
                raise RuntimeError("customer_read_model_release_refresh_not_authorized")
            if not source_event_key.startswith("deploy_runtime:"):
                raise RuntimeError("customer_read_model_release_refresh_source_key_required")
            current = dict(repository.get() or {})
            if str(current.get("status") or "").strip() == "blocked":
                expected_row_version = int(current.get("row_version") or 0)
                if expected_row_version <= 0:
                    raise RuntimeError("customer_read_model_release_refresh_row_version_missing")
                recover_blocked = True
            source_event_type = "customer_read_model.release_refresh"
        result = CustomerReadModelRefreshIntentService(repository=repository).request_refresh(
            source_event_key=source_event_key,
            source_event_type=source_event_type,
            recover_blocked=recover_blocked,
            expected_row_version=expected_row_version,
        )
        wait_result: dict[str, Any] | None = None
        if args.wait_seconds:
            target_generation = int(result.get("generation") or (result.get("intent") or {}).get("dirty_generation") or 0)
            if target_generation <= 0:
                raise RuntimeError("customer_read_model_refresh_generation_missing")
            wait_result = wait_for_refresh_completion(
                repository,
                target_generation=target_generation,
                timeout_seconds=args.wait_seconds,
            )
            if not wait_result.get("ok"):
                print_json(
                    {
                        "ok": False,
                        "accepted": bool(result.get("ok")),
                        "reason": str(wait_result.get("reason") or "customer_read_model_refresh_wait_failed"),
                        "wait": wait_result,
                        "inline_refresh_executed": False,
                    }
                )
                return 1
    except Exception as exc:
        message = str(exc)
        reason = (
            message
            if isinstance(exc, RuntimeError) and message.startswith("customer_read_model_")
            else "customer_read_model_refresh_failed"
        )
        print_json({"ok": False, "error": type(exc).__name__, "reason": reason})
        return 1
    payload = {
        "accepted": bool(result.get("ok")),
        **result,
        "release_refresh": bool(args.release_refresh),
        "inline_refresh_executed": False,
    }
    if wait_result is not None:
        payload["wait"] = wait_result
    print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
