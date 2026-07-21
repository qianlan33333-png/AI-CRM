#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.script_runtime import print_json
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import print_json


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _code_checks() -> dict[str, bool]:
    application = _read("aicrm_next/channel_entry/application.py")
    callback_body = application.split("def process_wecom_external_contact_event(", 1)[1].split("def diagnose_channel_runtime", 1)[0]
    identity_worker = _read("aicrm_next/channel_entry/identity_resolution_worker.py")
    customer_script = _read("scripts/run_customer_read_model_refresh.py")
    adapter = _read("aicrm_next/platform_foundation/external_effects/adapters.py")
    completion = _read("aicrm_next/platform_foundation/external_effects/completion_events.py")
    models = _read("aicrm_next/platform_foundation/external_effects/models.py")
    public_attempt_model = models.split("class ExternalEffectAttempt:", 1)[1].split("class ExternalEffectTestReceipt:", 1)[0]
    identity_adapter = adapter.split("class WeComExternalContactDetailAdapter", 1)[1].split("class WeChatPaymentAdapter", 1)[0]
    channel_repo = _read("aicrm_next/channel_entry/repo.py")
    customer_intents = _read("aicrm_next/customer_read_model/refresh_intents.py")
    migration = _read("migrations/versions/0129_identity_customer_event_driven.py")
    return {
        "callback_has_no_inline_identity_provider": (
            "_sync_identity_best_effort(" not in callback_body
            and "get_external_contact_detail(" not in callback_body
            and "_plan_identity_resolution_for_event(" in callback_body
        ),
        "linked_identity_rows_excluded_from_legacy_worker": (
            "external_effect_job_id IS NULL" in identity_worker
            and "identity_external_effect_job_id IS NULL" in identity_worker
        ),
        "customer_cli_is_intent_only": (
            "CustomerReadModelRefreshIntentService" in customer_script
            and "CustomerReadModelRefreshService" not in customer_script
            and ".run(" not in customer_script
        ),
        "provider_detail_has_canonical_private_storage": (
            "provider_result=provider_detail" in adapter
            and '"provider_detail": provider_detail' not in adapter
            and "get_attempt_provider_result" in completion
        ),
        "provider_detail_is_absent_from_public_attempt_contract": (
            "provider_result_json" not in public_attempt_model
            and "provider_result_hash" not in public_attempt_model
            and '"target_id": job.target_id' not in identity_adapter
        ),
        "channel_runtime_queue_effect_share_transaction": (
            "enqueue_channel_entry_identity_resolution_in_connection(" in channel_repo
            and "enqueue_identity_resolution=True" in application
        ),
        "identity_effect_is_lane_owned": (
            'WECOM_EXTERNAL_CONTACT_DETAIL_FETCH = "wecom.external_contact.detail.fetch"'
            in _read("aicrm_next/platform_foundation/external_effects/models.py")
            and 'lane="wecom_interactive"' in _read("aicrm_next/identity_contact/resolution_effects.py")
        ),
        "customer_generation_consumer_registered": (
            "register_customer_read_model_event_consumers(registry)"
            in _read("aicrm_next/internal_event_composition.py")
        ),
        "customer_sources_and_dirty_during_run_are_explicit": (
            all(
                event_type in customer_intents
                for event_type in (
                    "channel_entry.entered",
                    "customer.phone_bound",
                    "identity.resolved",
                    "message_archive.batch_ingested",
                    "payment.succeeded",
                    "questionnaire.submitted",
                )
            )
            and "has_continuation = dirty > int(generation)" in customer_intents
        ),
        "archive_source_change_uses_transactional_outbox": (
            "archive_source_change_recorder_required" in _read("aicrm_next/message_archive/repo.py")
            and "message_archive.batch_ingested" in _read("aicrm_next/admin_jobs_archive_sync_gateway.py")
            and "enqueue_transactional_internal_event_outbox(" in _read("aicrm_next/admin_jobs_archive_sync_gateway.py")
            and "admin_jobs_archive_sync_gateway import execute_archive_sync" in _read("scripts/run_incremental_archive_sync.py")
        ),
        "deploy_preflight_waits_on_durable_customer_intent": (
            "scripts/run_customer_read_model_refresh.py" in _read("scripts/ops/deploy_id_validation_remote.sh")
            and "--wait-seconds 180" in _read("scripts/ops/deploy_id_validation_remote.sh")
        ),
        "historical_identity_work_is_held_not_replayed": (
            "pre_event_driven_cutover_requires_manual_classification" in migration
            and "WHERE status IN ('pending', 'polling')" in migration
        ),
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
            control = cursor.fetchone() or (0, False, "missing")
            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE status = 'pending' AND external_effect_job_id IS NULL
                    ) AS pending_without_effect,
                    COUNT(*) FILTER (
                        WHERE status = 'held'
                          AND hold_reason = 'pre_event_driven_cutover_requires_manual_classification'
                    ) AS historical_hold_count
                FROM crm_user_identity_resolution_queue
                """
            )
            identity = cursor.fetchone() or (0, 0)
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM external_effect_attempt
                WHERE provider_result_json <> '{}'::jsonb
                  AND provider_result_consumed_at IS NOT NULL
                """
            )
            consumed_payload_leak = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT dirty_generation, completed_generation, signal_generation,
                       running_generation, status
                FROM customer_read_model_refresh_intent
                WHERE singleton_id = 1
                """
            )
            customer = cursor.fetchone() or (0, 0, 0, 0, "missing")
    return {
        "active_generation": int(control[0]),
        "claim_enabled": bool(control[1]),
        "rollout_mode": str(control[2]),
        "pending_identity_without_effect_count": int(identity[0]),
        "historical_identity_hold_count": int(identity[1]),
        "consumed_provider_payload_leak_count": consumed_payload_leak,
        "customer_intent": {
            "dirty_generation": int(customer[0]),
            "completed_generation": int(customer[1]),
            "signal_generation": int(customer[2]),
            "running_generation": int(customer[3]),
            "status": str(customer[4]),
        },
        "standby_safe": (
            not bool(control[1])
            and int(identity[0]) == 0
            and consumed_payload_leak == 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only identity/customer event-driven owner checker.")
    parser.add_argument("--code-only", action="store_true")
    parser.add_argument("--database-url", default="")
    parser.add_argument("--expect-standby", action="store_true")
    args = parser.parse_args()
    code_checks = _code_checks()
    payload: dict[str, Any] = {
        "ok": all(code_checks.values()),
        "code_checks": code_checks,
        "real_external_call_executed": False,
    }
    if not args.code_only:
        database_url = str(args.database_url or os.getenv("DATABASE_URL") or "").strip()
        if not database_url:
            payload["ok"] = False
            payload["error"] = "database_url_required"
        else:
            runtime = _runtime_checks(database_url)
            payload["runtime"] = runtime
            if args.expect_standby:
                payload["ok"] = bool(payload["ok"] and runtime["standby_safe"])
    print_json(payload, indent=2, sort_keys=True)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
