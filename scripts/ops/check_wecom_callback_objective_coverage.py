#!/usr/bin/env python3
"""Map the current WeCom callback implementation to local and production proof.

Local proof is derived only from the compact current-code test system. Production
completion remains fail-closed and still requires the readiness, rollback,
public-state, deploy-smoke, isolation, and same-sample evidence chain.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script_runtime import REPO_ROOT, ensure_repo_root_on_path, print_json

ensure_repo_root_on_path()


REQUIRED_ASSETS = {
    "webhook_inbox_migration": "migrations/versions/0054_webhook_inbox.py",
    "webhook_inbox_models": "aicrm_next/platform/platform_foundation/webhook_inbox/models.py",
    "webhook_inbox_repository": "aicrm_next/platform/platform_foundation/webhook_inbox/repository.py",
    "webhook_inbox_service": "aicrm_next/platform/platform_foundation/webhook_inbox/service.py",
    "callback_fast_ack_route": "aicrm_next/channels/channel_entry/api.py",
    "callback_ingress_module": "aicrm_next/channels/channel_entry/callback_ingress.py",
    "callback_inbox_worker": "aicrm_next/channels/channel_entry/inbox.py",
    "callback_worker_module": "aicrm_next/channels/channel_entry/callback_worker.py",
    "callback_processor_module": "aicrm_next/channels/channel_entry/callback_processor.py",
    "isolated_ingress_runtime": "aicrm_next/channels/channel_entry/ingress_app.py",
    "callback_worker_entrypoint": "scripts/run_wecom_callback_inbox_worker.py",
    "callback_ingress_entrypoint": "scripts/run_wecom_callback_ingress.py",
    "callback_ingress_systemd_unit": "deploy/openclaw-wecom-callback-ingress.service",
    "callback_worker_systemd_unit": "deploy/openclaw-wecom-callback-inbox-worker.service",
    "typed_wecom_runtime": "aicrm_next/channels/integration_gateway/wecom_runtime.py",
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

REQUIRED_TEST_PROOFS: dict[str, dict[str, object]] = {
    "current_callback_behavior_inventory": {
        "path": "docs/ci/current_behavior_inventory.json",
        "markers": ("channels_wecom_callback_welcome_and_gateway", "tests/high_risk/test_wecom_callback.py"),
    },
    "callback_crypto_fail_closed": {
        "path": "tests/unit/test_channel_rules.py",
        "markers": ("test_callback_timestamp_and_xml_fail_closed", "test_wecom_crypto_round_trip_and_signature"),
    },
    "callback_durable_idempotent_ack": {
        "path": "tests/high_risk/test_wecom_callback.py",
        "markers": ("test_callback_ack_is_durable_idempotent_and_does_not_process_inline", "durable_inbox_only"),
    },
    "callback_retry_dead_letter_recovery": {
        "path": "tests/high_risk/test_wecom_callback.py",
        "markers": ("test_callback_worker_retries_dead_letters_and_recovers_without_provider_io", "dead_letter"),
    },
    "route_owner_auth_contract": {
        "path": "tests/contracts/test_routes_and_auth.py",
        "markers": (
            "test_route_manifest_is_the_single_auth_and_permission_contract",
            "test_current_openapi_exposes_request_and_response_contracts",
        ),
    },
    "provider_effect_boundary_contract": {
        "path": "tests/contracts/test_provider_boundaries.py",
        "markers": ("test_current_provider_and_effect_boundary_checker", "check_external_effects_boundary.py"),
    },
    "webhook_postgres_contract": {
        "path": "tests/postgres/test_current_schema.py",
        "markers": ("test_identity_and_queue_columns_match_current_runtime_contract", "webhook_inbox"),
    },
    "external_effect_idempotency_contract": {
        "path": "tests/high_risk/test_external_effects.py",
        "markers": (
            "test_effect_planning_is_idempotent_and_approval_gated",
            "test_unregistered_adapter_records_a_terminal_non_execution",
        ),
    },
    "release_delivery_contract": {
        "path": "tests/release/test_workflow_contract.py",
        "markers": ("test_promotion_and_deploy_preserve_exact_sha_lock_health_and_rollback", "x-aicrm-release-sha"),
    },
    "runtime_owner_health_contract": {
        "path": "tests/release/test_application_startup.py",
        "markers": ("test_current_application_starts_and_exposes_exact_sha_health", "test_runtime_route_map_has_no_legacy_owner"),
    },
    "no_real_provider_io_contract": {
        "path": "tests/high_risk/conftest.py",
        "markers": ("real network access is forbidden", "AICRM_WECOM_EXECUTION_MODE"),
    },
    "production_completion_evidence_contract": {
        "path": "scripts/ops/check_wecom_callback_permanent_fix_readiness.py",
        "markers": ("and rollback_ok", "and public_state_ok", "and deploy_smoke_ok"),
    },
    "rollback_reapply_runbook_contract": {
        "path": "docs/runbooks/wecom_callback_production_cutover_zh.md",
        "markers": ("reapply_cutover_after_rollback", "ready_for_production_completion=true"),
    },
}


OBJECTIVE_REQUIREMENTS = {
    "generic_webhook_inbox": {
        "description": "Current webhook inbox schema, repository, idempotency, and duplicate collapse exist.",
        "assets": ["webhook_inbox_migration", "webhook_inbox_models", "webhook_inbox_repository", "webhook_inbox_service"],
        "tests": ["current_callback_behavior_inventory", "callback_durable_idempotent_ack", "webhook_postgres_contract"],
        "readiness": [],
    },
    "callback_http_ingress_only": {
        "description": "Callback HTTP verifies, decrypts, durably enqueues, and ACKs without inline business processing.",
        "assets": ["callback_fast_ack_route", "callback_ingress_module", "webhook_ingestion_checker", "callback_sample_generator"],
        "tests": ["callback_crypto_fail_closed", "callback_durable_idempotent_ack", "route_owner_auth_contract"],
        "readiness": ["webhook_ingestion_ok", "same_sample_ok"],
    },
    "worker_queue_processing": {
        "description": "Current worker previews, retries, dead-letters, and explicitly recovers an inbox item.",
        "assets": [
            "callback_inbox_worker",
            "callback_worker_module",
            "callback_processor_module",
            "callback_worker_entrypoint",
            "callback_worker_systemd_unit",
            "typed_wecom_runtime",
            "webhook_processing_checker",
        ],
        "tests": ["callback_retry_dead_letter_recovery", "webhook_postgres_contract", "route_owner_auth_contract"],
        "readiness": ["worker_isolation_ok", "webhook_processing_ok", "same_sample_ok"],
    },
    "real_outbound_effect_boundary": {
        "description": "Outbound work stays approval-gated and idempotent behind fake or disabled adapters in tests.",
        "assets": [],
        "tests": ["provider_effect_boundary_contract", "external_effect_idempotency_contract", "no_real_provider_io_contract"],
        "readiness": ["downstream_worker_isolation_ok"],
    },
    "runtime_isolation_and_backpressure": {
        "description": "Callback ingress is isolated and production completion requires pressure and deploy evidence.",
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
            "release_delivery_contract",
            "runtime_owner_health_contract",
            "route_owner_auth_contract",
            "production_completion_evidence_contract",
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
        "description": "Current runbook retains rollback, reapply, public-state, deploy-smoke, and completion gates.",
        "assets": [
            "runbook",
            "production_cutover_checklist_zh",
            "production_restore_investigation_zh",
            "acceptance_audit",
            "rollback_evidence_checker",
        ],
        "tests": ["release_delivery_contract", "production_completion_evidence_contract", "rollback_reapply_runbook_contract"],
        "readiness": [
            "ready_for_production_completion",
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
    for key, proof in REQUIRED_TEST_PROOFS.items():
        relative = str(proof.get("path") or "")
        markers = tuple(str(marker) for marker in proof.get("markers", ()) if str(marker))
        path = REPO_ROOT / relative
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        checks[key] = {
            "path": relative,
            "markers": list(markers),
            "ok": path.exists() and bool(markers) and all(marker in text for marker in markers),
        }
    return checks


def _readiness_check(path: str) -> dict[str, Any]:
    readiness_path = str(path or "").strip()
    if not readiness_path:
        return {"checked": False, "ok": None, "path": "", "error": "readiness file not provided"}
    try:
        payload = json.loads(Path(readiness_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"checked": True, "ok": False, "path": readiness_path, "error": str(exc)}

    def evidence(name: str) -> dict[str, Any]:
        value = payload.get(name)
        return value if isinstance(value, dict) else {}

    inbox_health = evidence("webhook_inbox_health")
    webhook_ingestion = evidence("webhook_ingestion_evidence")
    webhook_processing = evidence("webhook_processing_evidence")
    same_sample = evidence("same_sample_evidence")
    admin_metrics = evidence("admin_webhook_inbox_metrics")
    admin_items = evidence("admin_webhook_inbox_items")
    admin_reconciliation = evidence("admin_webhook_inbox_reconciliation")
    worker_isolation = evidence("worker_isolation_evidence")
    internal_event_worker_isolation = evidence("internal_event_worker_isolation_evidence")
    downstream_worker_isolation = evidence("downstream_worker_isolation_evidence")
    rollback_evidence = evidence("rollback_evidence")
    public_state_evidence = evidence("public_state_evidence")
    deploy_smoke_evidence = evidence("deploy_smoke_evidence")

    completion_proven = (
        payload.get("ready_for_production_completion") is True
        and payload.get("ok") is True
        and inbox_health.get("ok") is True
        and webhook_ingestion.get("ok") is True
        and webhook_processing.get("ok") is True
        and same_sample.get("ok") is True
        and admin_metrics.get("ok") is True
        and admin_items.get("ok") is True
        and admin_reconciliation.get("ok") is True
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
        "admin_webhook_inbox_metrics_ok": admin_metrics.get("ok"),
        "admin_webhook_inbox_items_ok": admin_items.get("ok"),
        "admin_webhook_inbox_reconciliation_ok": admin_reconciliation.get("ok"),
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
    parser = argparse.ArgumentParser(description="Map the current WeCom callback objective to local and production evidence.")
    parser.add_argument(
        "--readiness-file",
        default="",
        help="JSON from check_wecom_callback_permanent_fix_readiness.py after cutover and pressure evidence.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    payload = run(argv)
    print_json(payload, indent=2)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
