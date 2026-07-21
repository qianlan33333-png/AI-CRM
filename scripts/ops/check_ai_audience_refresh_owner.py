#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.script_runtime import print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import print_json


ROOT = Path(__file__).resolve().parents[2]
LEGACY_SERVICE = ROOT / "deploy" / "openclaw-ai-audience-scheduler.service"
LEGACY_TIMER = ROOT / "deploy" / "openclaw-ai-audience-scheduler.timer"
DAILY_SERVICE = ROOT / "deploy" / "aicrm-ai-audience-daily-intent.service"
DAILY_TIMER = ROOT / "deploy" / "aicrm-ai-audience-daily-intent.timer"
RUNTIME_MANIFEST = ROOT / "deploy" / "production_runtime_units.json"
EVENTS = ROOT / "aicrm_next" / "ai_audience_ops" / "events.py"
INTENTS = ROOT / "aicrm_next" / "ai_audience_ops" / "refresh_intents.py"


def _code_checks() -> dict[str, bool]:
    legacy_service = LEGACY_SERVICE.read_text(encoding="utf-8")
    legacy_timer = LEGACY_TIMER.read_text(encoding="utf-8")
    daily_service = DAILY_SERVICE.read_text(encoding="utf-8")
    daily_timer = DAILY_TIMER.read_text(encoding="utf-8")
    manifest = json.loads(RUNTIME_MANIFEST.read_text(encoding="utf-8"))
    cutover = manifest.get("cutover_managed_legacy") or {}
    cutover_pairs = {
        (str(item.get("timer") or ""), str(item.get("service") or ""))
        for item in cutover.get("timers") or []
    }
    active_pairs = {
        (str(item.get("timer") or ""), str(item.get("service") or ""))
        for item in manifest.get("active_autostart") or []
    }
    replacement = manifest.get("cutover_replacement_autostart") or {}
    replacement_pairs = {
        (str(item.get("timer") or ""), str(item.get("service") or ""))
        for item in replacement.get("timers") or []
    }
    events = EVENTS.read_text(encoding="utf-8")
    intents = INTENTS.read_text(encoding="utf-8")
    return {
        "legacy_three_minute_owner_preserved": "--run-consumers --execute" in legacy_service and "*:0/3:00" in legacy_timer,
        "legacy_owner_cutover_managed": (
            (LEGACY_TIMER.name, LEGACY_SERVICE.name) in cutover_pairs
            and (LEGACY_TIMER.name, LEGACY_SERVICE.name) not in active_pairs
        ),
        "daily_clock_only": "--daily-only" in daily_service and "--run-consumers" not in daily_service and "--execute" not in daily_service,
        "daily_clock_is_distinct_cutover_replacement": (
            (DAILY_TIMER.name, DAILY_SERVICE.name) in replacement_pairs
            and (DAILY_TIMER.name, DAILY_SERVICE.name) not in active_pairs
            and "02:00:00" in daily_timer
            and "*:0/3:00" not in daily_timer
        ),
        "owner_precheck_installed": "check_ai_audience_refresh_owner.py --code-only" in daily_service,
        "single_package_consumer_registered": "REFRESH_REQUESTED_EVENT" in events and "refresh_intent_consumer" in events,
        "legacy_ticks_are_intent_only": "request_due_refreshes(" in events and "run_due(" not in events,
        "coalescing_intent_present": "ai_audience_refresh_intent" in intents and "claim_latest" in intents,
        "no_inline_provider_dispatch": "dispatch_one(" not in intents and ".dispatch(" not in intents,
    }


def _runtime_checks(database_url: str) -> dict[str, Any]:
    import psycopg

    normalized = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(normalized) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT active_generation, claim_enabled, rollout_mode
                FROM queue_runtime_control
                WHERE singleton = TRUE
                """
            )
            control = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM internal_event_consumer_run
                WHERE consumer_name IN (
                    'ai_audience_incremental_refresh_consumer',
                    'ai_audience_daily_refresh_consumer'
                )
                  AND status = 'running'
                """
            )
            legacy_running = int(cursor.fetchone()[0])
    active_generation = int(control[0]) if control else 0
    claim_enabled = bool(control[1]) if control else False
    rollout_mode = str(control[2]) if control else "missing"
    return {
        "active_generation": active_generation,
        "claim_enabled": claim_enabled,
        "rollout_mode": rollout_mode,
        "legacy_refresh_consumer_running_count": legacy_running,
        "runtime_owner_ready": active_generation > 0 and claim_enabled and rollout_mode in {"canary", "execute"} and legacy_running == 0,
        "legacy_owner_allowed": active_generation == 0 and not claim_enabled and rollout_mode == "standby",
    }


def assert_legacy_owner_allowed(database_url: str = "") -> dict[str, Any]:
    resolved = str(database_url or os.getenv("DATABASE_URL") or "").strip()
    if not resolved:
        raise RuntimeError("DATABASE_URL is required for the AI Audience legacy-owner guard")
    runtime = _runtime_checks(resolved)
    if not runtime["legacy_owner_allowed"]:
        raise RuntimeError("AI Audience legacy owner is forbidden after queue generation activation")
    return {
        "active_generation": runtime["active_generation"],
        "claim_enabled": runtime["claim_enabled"],
        "rollout_mode": runtime["rollout_mode"],
        "legacy_owner_allowed": True,
        "real_external_call_executed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed AI Audience refresh owner checker.")
    parser.add_argument("--code-only", action="store_true", help="Validate the release contract without reading live DB state.")
    parser.add_argument("--database-url", default="")
    args = parser.parse_args()

    checks = _code_checks()
    payload: dict[str, Any] = {
        "ok": all(checks.values()),
        "code_checks": checks,
        "activation_ready": False,
        "real_external_call_executed": False,
    }
    if not args.code_only:
        database_url = str(args.database_url or os.getenv("DATABASE_URL") or "").strip()
        if not database_url:
            payload["ok"] = False
            payload["error"] = "database_url_required_for_runtime_owner_check"
        else:
            runtime = _runtime_checks(database_url)
            payload["runtime"] = runtime
            payload["activation_ready"] = bool(payload["ok"] and runtime["runtime_owner_ready"])
            payload["ok"] = bool(payload["activation_ready"])
    print_json(payload, indent=2, sort_keys=True)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
