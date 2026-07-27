#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from typing import Any

try:
    from scripts.script_runtime import ensure_repo_root_on_path, print_json, read_int_env
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from script_runtime import ensure_repo_root_on_path, print_json, read_int_env

ensure_repo_root_on_path()

from aicrm_next.channels.channel_entry.callback_worker import WeComCallbackWorker
from aicrm_next.channel_entry_composition import build_wecom_callback_inbox_worker_factory
from aicrm_next.external_effect_composition import build_external_effect_adapter_registry


EXECUTE_ENV = "AICRM_WECOM_CALLBACK_INBOX_WORKER_EXECUTE"
MAX_EXECUTE_LIMIT_ENV = "AICRM_WECOM_CALLBACK_INBOX_WORKER_MAX_EXECUTE_BATCH_SIZE"
POLL_INTERVAL_ENV = "AICRM_WECOM_CALLBACK_INBOX_WORKER_POLL_INTERVAL_SECONDS"


def _default_limit() -> int:
    return read_int_env("AICRM_WECOM_CALLBACK_INBOX_WORKER_BATCH_SIZE", 50)


def _max_execute_limit() -> int:
    return read_int_env(MAX_EXECUTE_LIMIT_ENV, 20)


def _poll_interval() -> float:
    try:
        value = float(os.getenv(POLL_INTERVAL_ENV, "0.25") or "0.25")
    except (TypeError, ValueError):
        value = 0.25
    return max(0.05, min(value, 60.0))


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _execute_gate(limit: int) -> dict:
    max_limit = max(1, int(_max_execute_limit()))
    if not _truthy_env(EXECUTE_ENV):
        return {
            "ok": False,
            "error": "wecom_callback_worker_execute_not_enabled",
            "message": f"--execute requires {EXECUTE_ENV}=1",
            "limit": int(limit),
            "max_execute_limit": max_limit,
        }
    if int(limit) > max_limit:
        return {
            "ok": False,
            "error": "wecom_callback_worker_execute_limit_exceeded",
            "message": f"--execute limit must be <= {max_limit}; set {MAX_EXECUTE_LIMIT_ENV} intentionally to raise it",
            "limit": int(limit),
            "max_execute_limit": max_limit,
        }
    return {"ok": True, "limit": int(limit), "max_execute_limit": max_limit}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run due WeCom callback webhook inbox jobs.")
    parser.add_argument("--limit", "--batch-size", dest="limit", type=int, default=_default_limit())
    parser.add_argument("--execute", action="store_true", default=False, help="Process claimed inbox rows. Without this flag the worker dry-runs.")
    parser.add_argument("--loop", action="store_true", default=False, help="Run as a persistent, signal-aware worker. Requires --execute.")
    parser.add_argument("--poll-interval", type=float, default=_poll_interval(), help="Idle poll interval in seconds (0.05-60).")
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be >= 1")
    if args.loop and not args.execute:
        parser.error("--loop requires --execute")
    if not 0.05 <= args.poll_interval <= 60:
        parser.error("--poll-interval must be between 0.05 and 60 seconds")
    return args


def run(*, limit: int | None = None, dry_run: bool = True) -> dict:
    return _build_worker().run_due(limit=int(limit or _default_limit()), dry_run=bool(dry_run))


def _build_worker() -> WeComCallbackWorker:
    factory = build_wecom_callback_inbox_worker_factory(
        external_effect_adapter_registry=build_external_effect_adapter_registry(),
    )
    return factory()


def run_loop(
    *,
    limit: int,
    poll_interval: float,
    stop_event: threading.Event,
    worker: Any | None = None,
) -> dict[str, Any]:
    callback_worker = worker or _build_worker()
    totals = {
        "batch_count": 0,
        "claimed_count": 0,
        "succeeded_count": 0,
        "failed_retryable_count": 0,
        "failed_terminal_count": 0,
        "dead_letter_count": 0,
    }
    while not stop_event.is_set():
        payload = callback_worker.run_due(limit=int(limit), dry_run=False)
        totals["batch_count"] += 1
        for key in tuple(totals):
            if key != "batch_count":
                totals[key] += int(payload.get(key) or 0)
        if not payload.get("ok"):
            return {"ok": False, "mode": "persistent", "error": "callback_worker_batch_failed", **totals}
        if int(payload.get("claimed_count") or 0) == 0:
            stop_event.wait(float(poll_interval))
    return {"ok": True, "mode": "persistent", "stopped_by_signal": True, **totals}


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.execute:
        gate = _execute_gate(int(args.limit))
        if not gate.get("ok"):
            print_json({"ok": False, "dry_run": False, **gate})
            return 1
    try:
        if args.loop:
            stop_event = threading.Event()
            _install_signal_handlers(stop_event)
            payload = run_loop(
                limit=int(args.limit),
                poll_interval=float(args.poll_interval),
                stop_event=stop_event,
            )
        else:
            payload = run(limit=int(args.limit), dry_run=not bool(args.execute))
    except Exception as exc:
        print_json({"ok": False, "error": "callback_worker_runtime_failed", "error_type": exc.__class__.__name__})
        return 1
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
