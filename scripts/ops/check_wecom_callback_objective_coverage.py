#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

try:
    from scripts.script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


REQUIRED_ASSETS = {
    "webhook_inbox_migration": "migrations/versions/0054_webhook_inbox.py",
    "webhook_inbox_models": "aicrm_next/platform_foundation/webhook_inbox/models.py",
    "webhook_inbox_repository": "aicrm_next/platform_foundation/webhook_inbox/repository.py",
    "webhook_inbox_service": "aicrm_next/platform_foundation/webhook_inbox/service.py",
    "callback_fast_ack_route": "aicrm_next/channel_entry/api.py",
    "callback_ingress_module": "aicrm_next/channel_entry/callback_ingress.py",
    "callback_inbox_worker": "aicrm_next/channel_entry/inbox.py",
    "callback_worker_module": "aicrm_next/channel_entry/callback_worker.py",
    "callback_processor_module": "aicrm_next/channel_entry/callback_processor.py",
    "isolated_ingress_runtime": "aicrm_next/channel_entry/ingress_app.py",
    "callback_worker_entrypoint": "scripts/run_wecom_callback_inbox_worker.py",
    "callback_ingress_entrypoint": "scripts/run_wecom_callback_ingress.py",
    "callback_ingress_systemd_unit": "deploy/openclaw-wecom-callback-ingress.service",
    "callback_worker_systemd_unit": "deploy/openclaw-wecom-callback-inbox-worker.service",
    "typed_wecom_runtime": "aicrm_next/integration_gateway/wecom_runtime.py",
    "canonical_web_systemd_unit": "deploy/openclaw-wecom-postgres.service",
    "canonical_wecom_ingress_systemd_unit": "deploy/aicrm-wecom-ingress.service",
    "canonical_callback_worker_systemd_unit": "deploy/aicrm-wecom-callback-worker.service",
    "canonical_internal_event_worker_systemd_unit": "deploy/aicrm-internal-event-worker.service",
    "canonical_external_effect_worker_systemd_unit": "deploy/aicrm-external-effect-worker.service",
    "callback_nginx_cutover_template": "deploy/nginx-wecom-callback-ingress.conf.example",
    "production_deploy_workflow": ".github/workflows/deploy.yml",
    "webhook_ingestion_checker": "scripts/ops/check_wecom_callback_ingestion_evidence.py",
    "webhook_processing_checker": "scripts/ops/check_wecom_callback_processing_evidence.py",
    "rollback_evidence_checker": "scripts/ops/check_wecom_callback_rollback_evidence.py",
    "readiness_checker": "scripts/ops/check_wecom_callback_permanent_fix_readiness.py",
    "public_state_checker": "scripts/ops/check_wecom_callback_public_state.py",
    "deploy_smoke_checker": "scripts/ops/check_wecom_callback_deploy_smoke.py",
    "pressure_probe": "scripts/ops/probe_wecom_callback_pressure.py",
    "callback_sample_generator": "scripts/ops/generate_wecom_callback_sample.py",
    "cutover_plan": "scripts/ops/prepare_wecom_callback_ingress_cutover.py",
    "runbook": "docs/runbooks/wecom_callback_storm.md",
    "production_cutover_checklist_zh": "docs/runbooks/wecom_callback_production_cutover_zh.md",
    "production_restore_investigation_zh": "docs/reports/production_page_restore_investigation_20260627_zh.md",
    "acceptance_audit": "docs/reports/wecom_callback_permanent_fix_acceptance_audit_20260627.md",
}

REQUIRED_TEST_PROOFS = {
    "hard_ack_rules": ("tests/test_wecom_callback_inbox.py", "test_callback_post_returns_503_when_inbox_write_fails"),
    "callback_ingress_module_boundary": ("tests/test_wecom_callback_inbox.py", "test_callback_ingress_decrypts_and_ingests_webhook_inbox"),
    "callback_worker_processor_boundary": ("tests/test_wecom_callback_inbox.py", "test_wecom_callback_worker_alias_and_processor_boundary"),
    "duplicate_collapse": ("tests/test_wecom_callback_inbox.py", "test_ingest_wecom_callback_deduplicates_by_event_key"),
    "worker_retry_dead_letter": ("tests/test_wecom_callback_inbox.py", "test_wecom_callback_inbox_worker_retries_then_dead_letters"),
    "worker_dry_run_preview_only": (
        "tests/test_wecom_callback_inbox.py",
        "test_wecom_callback_inbox_worker_dry_run_only_previews_due_rows",
    ),
    "worker_entrypoint_dry_run_gate": (
        "tests/test_wecom_callback_inbox.py",
        "test_wecom_callback_worker_entrypoint_defaults_to_dry_run_and_requires_execute",
    ),
    "worker_systemd_persistent_service": (
        "tests/test_deploy_workflow_contract.py",
        "test_wecom_callback_inbox_worker_systemd_units_are_deployable",
    ),
    "stale_processing_reclaim": ("tests/test_wecom_callback_inbox.py", "test_wecom_callback_worker_reclaims_stale_processing_after_crash"),
    "worker_dispatch_one": ("tests/test_wecom_callback_inbox.py", "test_wecom_callback_inbox_worker_dispatch_one_by_id"),
    "worker_dispatch_one_replay": ("tests/test_wecom_callback_inbox.py", "test_wecom_callback_inbox_worker_dispatch_one_replays_dead_letter"),
    "runtime_isolation": ("tests/test_wecom_callback_ingress_runtime.py", "test_wecom_callback_ingress_runtime_only_exposes_callback_and_health_routes"),
    "nginx_backpressure_gate": ("tests/test_wecom_callback_ingress_runtime.py", "test_wecom_callback_ingress_cutover_check_ignores_commented_backpressure"),
    "invalid_callback_not_plain_success": ("tests/test_wecom_callback_ingress_runtime.py", "test_wecom_callback_ingress_cutover_probe_rejects_plain_success"),
    "quick_ack_route_level_detection": (
        "tests/test_wecom_callback_ingress_runtime.py",
        "test_callback_quick_ack_state_ignores_comments_and_unrelated_success_returns",
    ),
    "quick_ack_dual_probe_detection": (
        "tests/test_wecom_callback_ingress_runtime.py",
        "test_callback_quick_ack_state_probes_env_callback_urls",
    ),
    "public_state_checker_gate": (
        "tests/test_wecom_callback_public_state.py",
        "test_public_state_reports_current_emergency_quick_ack_shape",
    ),
    "public_state_admin_server_error_gate": (
        "tests/test_wecom_callback_public_state.py",
        "test_public_state_rejects_admin_webhook_inbox_server_error",
    ),
    "public_state_invalid_callback_upstream_error_gate": (
        "tests/test_wecom_callback_public_state.py",
        "test_public_state_rejects_invalid_callback_upstream_error",
    ),
    "public_state_detail_route_gate": (
        "tests/test_wecom_callback_public_state.py",
        "test_public_state_rejects_generic_404_for_webhook_inbox_detail_route",
    ),
    "public_state_json_api_shape_gate": (
        "tests/test_wecom_callback_public_state.py",
        "test_public_state_rejects_json_api_200_without_required_shape",
    ),
    "public_state_dual_callback_route_gate": (
        "tests/test_wecom_callback_public_state.py",
        "test_public_state_rejects_secondary_callback_route_quick_ack",
    ),
    "deploy_smoke_admin_route_gate": (
        "tests/test_wecom_callback_deploy_smoke.py",
        "test_deploy_smoke_rejects_missing_webhook_inbox_admin_routes",
    ),
    "deploy_smoke_ingress_gate": (
        "tests/test_wecom_callback_deploy_smoke.py",
        "test_deploy_smoke_rejects_missing_isolated_ingress_runtime",
    ),
    "deploy_smoke_distinct_base_url_gate": (
        "tests/test_wecom_callback_deploy_smoke.py",
        "test_deploy_smoke_rejects_same_web_and_ingress_base_url",
    ),
    "deploy_smoke_detail_route_gate": (
        "tests/test_wecom_callback_deploy_smoke.py",
        "test_deploy_smoke_rejects_missing_webhook_inbox_detail_route",
    ),
    "deploy_smoke_json_api_shape_gate": (
        "tests/test_wecom_callback_deploy_smoke.py",
        "test_deploy_smoke_rejects_json_api_200_without_required_shape",
    ),
    "deploy_smoke_ingress_callback_route_gate": (
        "tests/test_wecom_callback_deploy_smoke.py",
        "test_deploy_smoke_rejects_missing_ingress_callback_route",
    ),
    "pressure_probe_gate": ("tests/test_wecom_callback_pressure_probe.py", "test_pressure_probe_rejects_page_sample_failures"),
    "pressure_sample_validation_gate": ("tests/test_wecom_callback_pressure_probe.py", "test_pressure_probe_requires_valid_sample_before_sending_requests"),
    "callback_sample_generator_gate": ("tests/test_wecom_callback_sample_generator.py", "test_callback_sample_generator_outputs_valid_encrypted_callback"),
    "generated_sample_worker_roundtrip": ("tests/test_wecom_callback_sample_generator.py", "test_generated_callback_sample_round_trips_through_inbox_worker"),
    "webhook_ingestion_evidence_gate": ("tests/test_wecom_callback_ingestion_evidence.py", "test_ingestion_evidence_accepts_recent_webhook_inbox_row"),
    "webhook_processing_evidence_gate": ("tests/test_wecom_callback_processing_evidence.py", "test_processing_evidence_accepts_succeeded_noop_canary_row"),
    "rollback_evidence_gate": ("tests/test_wecom_callback_rollback_evidence.py", "test_rollback_evidence_accepts_complete_production_drill_payload"),
    "worker_isolation_canary_gate": ("tests/test_wecom_callback_cutover_plan.py", "worker_isolation_canary"),
    "downstream_worker_isolation_canary_gate": ("tests/test_wecom_callback_cutover_plan.py", "downstream_worker_isolation_canary"),
    "internal_event_worker_isolation_canary_gate": ("tests/test_wecom_callback_cutover_plan.py", "internal_event_worker_isolation_canary"),
    "reapply_cutover_after_rollback_gate": ("tests/test_wecom_callback_cutover_plan.py", "reapply_cutover_after_rollback"),
    "readiness_completion_gate": ("tests/test_wecom_callback_permanent_fix_readiness.py", "test_readiness_accepts_cutover_state_with_pressure_evidence"),
    "webhook_inbox_health_gate": ("tests/test_wecom_callback_permanent_fix_readiness.py", "test_readiness_rejects_unhealthy_webhook_inbox_metrics"),
    "public_state_completion_gate": (
        "tests/test_wecom_callback_permanent_fix_readiness.py",
        "test_readiness_rejects_missing_public_state_evidence",
    ),
    "deploy_smoke_completion_gate": (
        "tests/test_wecom_callback_permanent_fix_readiness.py",
        "test_readiness_rejects_missing_deploy_smoke_evidence",
    ),
    "deploy_workflow_callback_services": (
        "tests/test_deploy_workflow_contract.py",
        "test_production_deploy_installs_callback_ingress_and_worker_isolated_runtime",
    ),
    "deploy_workflow_deploy_smoke_evidence": (
        "tests/test_deploy_workflow_contract.py",
        "tee /tmp/wecom-callback-deploy-smoke.json",
    ),
    "canonical_runtime_isolation_systemd_units": (
        "tests/test_deploy_workflow_contract.py",
        "test_aicrm_canonical_runtime_isolation_systemd_units_are_deployable",
    ),
    "external_effect_boundary": ("tests/test_wecom_callback_external_effect_boundary.py", "test_channel_entry_real_wecom_actions_are_planned_as_external_effect_jobs"),
    "channel_entry_effect_realtime_wakeup": (
        "tests/test_next_channel_entry_orchestrator.py",
        "test_active_channel_baseline_emits_only_channel_entry_without_program_admission",
    ),
    "external_effect_realtime_signal_only": (
        "tests/test_external_effects_realtime.py",
        "test_realtime_signal_never_dispatches_provider_or_creates_attempt",
    ),
    "external_effect_stale_dispatching_quarantine": (
        "tests/test_external_effect_delivery_lease.py",
        "test_stale_post_provider_dispatch_is_quarantined_as_unknown",
    ),
    "schema_contract": ("tests/test_webhook_inbox_migration_contract.py", "test_webhook_inbox_migration_locks_status_and_idempotency_contracts"),
    "webhook_inbox_service_models": (
        "tests/test_webhook_inbox_repository.py",
        "test_webhook_inbox_service_item_model_exposes_operational_fields",
    ),
    "webhook_inbox_metrics_distribution": (
        "tests/test_webhook_inbox_repository.py",
        "test_webhook_inbox_metrics_include_distribution_and_recent_errors",
    ),
    "admin_replay_detail": ("tests/test_webhook_inbox_admin_api.py", "test_webhook_inbox_admin_detail_returns_processing_chain"),
    "admin_retry_skip": ("tests/test_webhook_inbox_admin_api.py", "test_webhook_inbox_admin_retry_and_skip_require_token"),
    "admin_dispatch_one": ("tests/test_webhook_inbox_admin_api.py", "test_webhook_inbox_admin_dispatch_one_requires_token_and_versioned_command"),
    "admin_run_due": ("tests/test_webhook_inbox_admin_api.py", "test_webhook_inbox_admin_run_due_defaults_to_dry_run"),
    "admin_page_hooks": ("tests/test_webhook_inbox_admin_api.py", "test_webhook_inbox_admin_page_renders_shell_and_api_hooks"),
    "admin_incident_window_filter": (
        "tests/test_webhook_inbox_admin_api.py",
        "test_webhook_inbox_admin_filters_incident_window_pending_failed_rows",
    ),
    "production_cutover_checklist_rollback_reapply_gate": (
        "docs/runbooks/wecom_callback_production_cutover_zh.md",
        "reapply_cutover_after_rollback",
    ),
    "production_cutover_checklist_final_readiness_gate": (
        "docs/runbooks/wecom_callback_production_cutover_zh.md",
        "ready_for_production_completion=true",
    ),
    "production_restore_report_temporary_recovery_gate": (
        "docs/reports/production_page_restore_investigation_20260627_zh.md",
        "页面/侧边栏已临时恢复",
    ),
    "production_restore_report_permanent_gap_gate": (
        "docs/reports/production_page_restore_investigation_20260627_zh.md",
        "企微 callback 永久修复尚未完成",
    ),
}

OBJECTIVE_REQUIREMENTS = {
    "generic_webhook_inbox": {
        "description": "Generic webhook_inbox schema, repository, idempotency, and duplicate collapse exist.",
        "assets": ["webhook_inbox_migration", "webhook_inbox_models", "webhook_inbox_repository", "webhook_inbox_service"],
        "tests": ["schema_contract", "duplicate_collapse", "webhook_inbox_service_models", "webhook_inbox_metrics_distribution"],
        "readiness": [],
    },
    "callback_http_ingress_only": {
        "description": "Callback HTTP route verifies/decrypts/enqueues/ACKs and does not run business processing inline.",
        "assets": ["callback_fast_ack_route", "callback_ingress_module", "webhook_ingestion_checker", "callback_sample_generator"],
        "tests": [
            "hard_ack_rules",
            "callback_ingress_module_boundary",
            "pressure_sample_validation_gate",
            "callback_sample_generator_gate",
            "generated_sample_worker_roundtrip",
            "webhook_ingestion_evidence_gate",
        ],
        "readiness": ["webhook_ingestion_ok", "same_sample_ok"],
    },
    "worker_queue_processing": {
        "description": "Webhook worker claims inbox rows, retries failures, dead-letters terminal rows, and exposes replay detail.",
        "assets": [
            "callback_inbox_worker",
            "callback_worker_module",
            "callback_processor_module",
            "callback_worker_entrypoint",
            "callback_worker_systemd_unit",
            "typed_wecom_runtime",
            "webhook_processing_checker",
        ],
        "tests": [
            "worker_retry_dead_letter",
            "worker_dry_run_preview_only",
            "worker_entrypoint_dry_run_gate",
            "worker_systemd_persistent_service",
            "stale_processing_reclaim",
            "callback_worker_processor_boundary",
            "worker_dispatch_one",
            "worker_dispatch_one_replay",
            "generated_sample_worker_roundtrip",
            "webhook_processing_evidence_gate",
            "admin_replay_detail",
            "admin_retry_skip",
            "admin_dispatch_one",
            "admin_run_due",
        ],
        "readiness": ["worker_isolation_ok", "webhook_processing_ok", "same_sample_ok"],
    },
    "real_outbound_effect_boundary": {
        "description": "Real outbound effects are planned through external_effect_job and realtime wakeup remains gated.",
        "assets": [],
        "tests": [
            "external_effect_boundary",
            "channel_entry_effect_realtime_wakeup",
            "external_effect_realtime_signal_only",
            "external_effect_stale_dispatching_quarantine",
        ],
        "readiness": ["downstream_worker_isolation_ok"],
    },
    "runtime_isolation_and_backpressure": {
        "description": "Callback ingress runs separately from web/admin/sidebar runtime with nginx backpressure and pressure evidence.",
        "assets": [
            "isolated_ingress_runtime",
            "callback_ingress_entrypoint",
            "callback_ingress_systemd_unit",
            "canonical_web_systemd_unit",
            "canonical_wecom_ingress_systemd_unit",
            "canonical_callback_worker_systemd_unit",
            "canonical_internal_event_worker_systemd_unit",
            "canonical_external_effect_worker_systemd_unit",
            "callback_nginx_cutover_template",
            "production_deploy_workflow",
            "pressure_probe",
            "readiness_checker",
            "public_state_checker",
            "deploy_smoke_checker",
            "cutover_plan",
        ],
        "tests": [
            "runtime_isolation",
            "nginx_backpressure_gate",
            "invalid_callback_not_plain_success",
            "quick_ack_route_level_detection",
            "quick_ack_dual_probe_detection",
            "public_state_checker_gate",
            "public_state_admin_server_error_gate",
            "public_state_invalid_callback_upstream_error_gate",
            "public_state_detail_route_gate",
            "public_state_json_api_shape_gate",
            "public_state_dual_callback_route_gate",
            "deploy_smoke_admin_route_gate",
            "deploy_smoke_ingress_gate",
            "deploy_smoke_distinct_base_url_gate",
            "deploy_smoke_detail_route_gate",
            "deploy_smoke_json_api_shape_gate",
            "deploy_smoke_ingress_callback_route_gate",
            "pressure_probe_gate",
            "worker_isolation_canary_gate",
            "downstream_worker_isolation_canary_gate",
            "internal_event_worker_isolation_canary_gate",
            "reapply_cutover_after_rollback_gate",
            "readiness_completion_gate",
            "webhook_inbox_health_gate",
            "public_state_completion_gate",
            "deploy_smoke_completion_gate",
            "deploy_workflow_callback_services",
            "deploy_workflow_deploy_smoke_evidence",
            "canonical_runtime_isolation_systemd_units",
        ],
        "readiness": [
            "ready_for_production_cutover",
            "ready_for_production_completion",
            "webhook_inbox_health_ok",
            "internal_event_worker_isolation_ok",
            "public_state_ok",
            "deploy_smoke_ok",
        ],
    },
    "operator_runbook_and_acceptance_report": {
        "description": "Runbook and acceptance audit identify rollback, canaries, pressure evidence, and remaining production gaps.",
        "assets": [
            "runbook",
            "production_cutover_checklist_zh",
            "production_restore_investigation_zh",
            "acceptance_audit",
            "rollback_evidence_checker",
        ],
        "tests": [
            "rollback_evidence_gate",
            "reapply_cutover_after_rollback_gate",
            "quick_ack_dual_probe_detection",
            "admin_retry_skip",
            "admin_dispatch_one",
            "admin_run_due",
            "admin_page_hooks",
            "admin_incident_window_filter",
            "webhook_inbox_metrics_distribution",
            "deploy_smoke_completion_gate",
            "production_cutover_checklist_rollback_reapply_gate",
            "production_cutover_checklist_final_readiness_gate",
            "production_restore_report_temporary_recovery_gate",
            "production_restore_report_permanent_gap_gate",
        ],
        "readiness": [
            "ready_for_production_completion",
            "admin_webhook_inbox_ok",
            "admin_webhook_inbox_metrics_ok",
            "admin_webhook_inbox_items_ok",
            "admin_webhook_inbox_reconciliation_ok",
            "rollback_ok",
            "public_state_ok",
            "deploy_smoke_ok",
        ],
    },
}


def _asset_checks() -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for key, relative in REQUIRED_ASSETS.items():
        path = REPO_ROOT / relative
        checks[key] = {"path": relative, "ok": path.exists(), "size_bytes": path.stat().st_size if path.exists() else 0}
    return checks


def _test_checks() -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for key, (relative, marker) in REQUIRED_TEST_PROOFS.items():
        path = REPO_ROOT / relative
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        checks[key] = {"path": relative, "marker": marker, "ok": path.exists() and marker in text}
    return checks


def _readiness_check(path: str) -> dict[str, Any]:
    readiness_path = str(path or "").strip()
    if not readiness_path:
        return {"checked": False, "ok": None, "path": "", "error": "readiness file not provided"}
    try:
        payload = json.loads(Path(readiness_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": readiness_path, "error": str(exc)}
    inbox_health = payload.get("webhook_inbox_health") if isinstance(payload.get("webhook_inbox_health"), dict) else {}
    webhook_ingestion = payload.get("webhook_ingestion_evidence") if isinstance(payload.get("webhook_ingestion_evidence"), dict) else {}
    webhook_processing = payload.get("webhook_processing_evidence") if isinstance(payload.get("webhook_processing_evidence"), dict) else {}
    same_sample = payload.get("same_sample_evidence") if isinstance(payload.get("same_sample_evidence"), dict) else {}
    admin_webhook_inbox = payload.get("admin_webhook_inbox") if isinstance(payload.get("admin_webhook_inbox"), dict) else {}
    admin_webhook_inbox_metrics = payload.get("admin_webhook_inbox_metrics") if isinstance(payload.get("admin_webhook_inbox_metrics"), dict) else {}
    admin_webhook_inbox_items = payload.get("admin_webhook_inbox_items") if isinstance(payload.get("admin_webhook_inbox_items"), dict) else {}
    admin_webhook_inbox_reconciliation = (
        payload.get("admin_webhook_inbox_reconciliation")
        if isinstance(payload.get("admin_webhook_inbox_reconciliation"), dict)
        else {}
    )
    worker_isolation = payload.get("worker_isolation_evidence") if isinstance(payload.get("worker_isolation_evidence"), dict) else {}
    internal_event_worker_isolation = payload.get("internal_event_worker_isolation_evidence") if isinstance(payload.get("internal_event_worker_isolation_evidence"), dict) else {}
    downstream_worker_isolation = payload.get("downstream_worker_isolation_evidence") if isinstance(payload.get("downstream_worker_isolation_evidence"), dict) else {}
    rollback_evidence = payload.get("rollback_evidence") if isinstance(payload.get("rollback_evidence"), dict) else {}
    public_state_evidence = payload.get("public_state_evidence") if isinstance(payload.get("public_state_evidence"), dict) else {}
    deploy_smoke_evidence = payload.get("deploy_smoke_evidence") if isinstance(payload.get("deploy_smoke_evidence"), dict) else {}
    completion_proven = (
        payload.get("ready_for_production_completion") is True
        and payload.get("ok") is True
        and inbox_health.get("ok") is True
        and webhook_ingestion.get("ok") is True
        and webhook_processing.get("ok") is True
        and same_sample.get("ok") is True
        and admin_webhook_inbox.get("ok") is True
        and admin_webhook_inbox_metrics.get("ok") is True
        and admin_webhook_inbox_items.get("ok") is True
        and admin_webhook_inbox_reconciliation.get("ok") is True
        and worker_isolation.get("ok") is True
        and internal_event_worker_isolation.get("ok") is True
        and downstream_worker_isolation.get("ok") is True
        and rollback_evidence.get("ok") is True
        and public_state_evidence.get("ok") is True
        and deploy_smoke_evidence.get("ok") is True
    )
    return {
        "checked": True,
        "ok": completion_proven,
        "path": readiness_path,
        "ready_for_production_cutover": payload.get("ready_for_production_cutover"),
        "ready_for_production_completion": payload.get("ready_for_production_completion"),
        "webhook_inbox_health_ok": inbox_health.get("ok"),
        "webhook_ingestion_ok": webhook_ingestion.get("ok"),
        "webhook_processing_ok": webhook_processing.get("ok"),
        "same_sample_ok": same_sample.get("ok"),
        "admin_webhook_inbox_ok": admin_webhook_inbox.get("ok"),
        "admin_webhook_inbox_metrics_ok": admin_webhook_inbox_metrics.get("ok"),
        "admin_webhook_inbox_items_ok": admin_webhook_inbox_items.get("ok"),
        "admin_webhook_inbox_reconciliation_ok": admin_webhook_inbox_reconciliation.get("ok"),
        "worker_isolation_ok": worker_isolation.get("ok"),
        "internal_event_worker_isolation_ok": internal_event_worker_isolation.get("ok"),
        "downstream_worker_isolation_ok": downstream_worker_isolation.get("ok"),
        "rollback_ok": rollback_evidence.get("ok"),
        "public_state_ok": public_state_evidence.get("ok"),
        "deploy_smoke_ok": deploy_smoke_evidence.get("ok"),
        "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
        "error": "" if completion_proven else "production readiness does not prove completion",
    }


def _requirement_checks(
    assets: dict[str, dict[str, Any]],
    tests: dict[str, dict[str, Any]],
    readiness: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for key, config in OBJECTIVE_REQUIREMENTS.items():
        asset_keys = list(config.get("assets") or [])
        test_keys = list(config.get("tests") or [])
        readiness_keys = list(config.get("readiness") or [])
        asset_state = {asset_key: bool(assets.get(asset_key, {}).get("ok")) for asset_key in asset_keys}
        test_state = {test_key: bool(tests.get(test_key, {}).get("ok")) for test_key in test_keys}
        readiness_state = {readiness_key: readiness.get(readiness_key) for readiness_key in readiness_keys}
        local_evidence_ok = all(asset_state.values()) and all(test_state.values())
        if readiness.get("checked") is False:
            production_evidence_ok = None
        elif not readiness_keys:
            production_evidence_ok = bool(readiness.get("ok") is True)
        else:
            production_evidence_ok = all(value is True for value in readiness_state.values())
        checks[key] = {
            "description": config.get("description"),
            "local_evidence_ok": local_evidence_ok,
            "production_evidence_ok": production_evidence_ok,
            "asset_evidence": asset_state,
            "test_evidence": test_state,
            "readiness_evidence": readiness_state,
        }
    return checks


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    assets = _asset_checks()
    tests = _test_checks()
    readiness = _readiness_check(str(args.readiness_file))
    objective_requirements = _requirement_checks(assets, tests, readiness)
    local_contract_ready = all(item["ok"] for item in assets.values()) and all(item["ok"] for item in tests.values())
    production_completion_ready = readiness.get("ok") is True
    missing_assets = [key for key, item in assets.items() if not item["ok"]]
    missing_tests = [key for key, item in tests.items() if not item["ok"]]
    warnings: list[str] = []
    if missing_assets:
        warnings.append("local assets missing: " + ", ".join(missing_assets))
    if missing_tests:
        warnings.append("local test proofs missing: " + ", ".join(missing_tests))
    if readiness.get("checked") is False:
        warnings.append("production readiness JSON not provided; production completion remains unproven")
    elif not production_completion_ready:
        warnings.append("production readiness JSON does not prove completion")
    return {
        "ok": bool(local_contract_ready and production_completion_ready),
        "local_contract_ready": local_contract_ready,
        "production_completion_ready": production_completion_ready,
        "assets": assets,
        "test_proofs": tests,
        "objective_requirements": objective_requirements,
        "readiness": readiness,
        "warnings": warnings,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Map the WeCom callback permanent-fix objective to current local and production evidence.")
    parser.add_argument("--readiness-file", default="", help="JSON output from check_wecom_callback_permanent_fix_readiness.py after production cutover and pressure evidence.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
