from __future__ import annotations

from scripts.ops import check_wecom_callback_objective_coverage as coverage


def test_objective_coverage_local_contract_is_ready_without_production_completion() -> None:
    assert coverage.REQUIRED_TEST_PROOFS["external_effect_stale_dispatching_quarantine"] == (
        "tests/test_external_effect_delivery_lease.py",
        "test_stale_post_provider_dispatch_is_quarantined_as_unknown",
    )
    payload = coverage.run([])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["checked"] is False
    assert payload["objective_requirements"]["generic_webhook_inbox"]["local_evidence_ok"] is True
    assert payload["objective_requirements"]["generic_webhook_inbox"]["production_evidence_ok"] is None
    assert payload["objective_requirements"]["generic_webhook_inbox"]["asset_evidence"]["webhook_inbox_models"] is True
    assert payload["objective_requirements"]["generic_webhook_inbox"]["asset_evidence"]["webhook_inbox_service"] is True
    assert payload["objective_requirements"]["generic_webhook_inbox"]["test_evidence"]["webhook_inbox_service_models"] is True
    assert payload["objective_requirements"]["generic_webhook_inbox"]["test_evidence"]["webhook_inbox_metrics_distribution"] is True
    assert payload["test_proofs"]["generated_sample_worker_roundtrip"]["ok"] is True
    assert payload["test_proofs"]["internal_event_worker_isolation_canary_gate"]["ok"] is True
    assert payload["test_proofs"]["reapply_cutover_after_rollback_gate"]["ok"] is True
    assert payload["objective_requirements"]["callback_http_ingress_only"]["test_evidence"]["generated_sample_worker_roundtrip"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["generated_sample_worker_roundtrip"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["worker_dry_run_preview_only"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["worker_entrypoint_dry_run_gate"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["worker_systemd_persistent_service"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["worker_dispatch_one"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["worker_dispatch_one_replay"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["admin_retry_skip"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["admin_dispatch_one"] is True
    assert payload["objective_requirements"]["worker_queue_processing"]["test_evidence"]["admin_run_due"] is True
    assert payload["test_proofs"]["channel_entry_effect_realtime_wakeup"]["ok"] is True
    assert (
        payload["objective_requirements"]["real_outbound_effect_boundary"]["test_evidence"][
            "channel_entry_effect_realtime_wakeup"
        ]
        is True
    )
    assert payload["test_proofs"]["external_effect_realtime_signal_only"]["ok"] is True
    assert (
        payload["objective_requirements"]["real_outbound_effect_boundary"]["test_evidence"][
            "external_effect_realtime_signal_only"
        ]
        is True
    )
    assert payload["test_proofs"]["external_effect_stale_dispatching_quarantine"]["ok"] is True
    assert (
        payload["objective_requirements"]["real_outbound_effect_boundary"]["test_evidence"][
            "external_effect_stale_dispatching_quarantine"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "internal_event_worker_isolation_canary_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "reapply_cutover_after_rollback_gate"
        ]
        is True
    )
    assert payload["test_proofs"]["quick_ack_route_level_detection"]["ok"] is True
    assert payload["test_proofs"]["quick_ack_dual_probe_detection"]["ok"] is True
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "quick_ack_route_level_detection"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "quick_ack_dual_probe_detection"
        ]
        is True
    )
    assert payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"]["admin_dispatch_one"] is True
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"][
            "reapply_cutover_after_rollback_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"][
            "quick_ack_dual_probe_detection"
        ]
        is True
    )
    assert payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"]["admin_retry_skip"] is True
    assert payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"]["admin_run_due"] is True
    assert payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"]["admin_page_hooks"] is True
    assert payload["assets"]["production_cutover_checklist_zh"]["ok"] is True
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["asset_evidence"][
            "production_cutover_checklist_zh"
        ]
        is True
    )
    assert payload["assets"]["production_restore_investigation_zh"]["ok"] is True
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["asset_evidence"][
            "production_restore_investigation_zh"
        ]
        is True
    )
    assert payload["test_proofs"]["production_cutover_checklist_rollback_reapply_gate"]["ok"] is True
    assert payload["test_proofs"]["production_cutover_checklist_final_readiness_gate"]["ok"] is True
    assert payload["test_proofs"]["production_restore_report_temporary_recovery_gate"]["ok"] is True
    assert payload["test_proofs"]["production_restore_report_permanent_gap_gate"]["ok"] is True
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"][
            "production_cutover_checklist_rollback_reapply_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"][
            "production_cutover_checklist_final_readiness_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"][
            "production_restore_report_temporary_recovery_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"][
            "production_restore_report_permanent_gap_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["operator_runbook_and_acceptance_report"]["test_evidence"][
            "webhook_inbox_metrics_distribution"
        ]
        is True
    )
    assert payload["objective_requirements"]["runtime_isolation_and_backpressure"]["production_evidence_ok"] is None
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["readiness_evidence"][
            "deploy_smoke_ok"
        ]
        is None
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "deploy_smoke_detail_route_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "deploy_smoke_distinct_base_url_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "deploy_smoke_json_api_shape_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "public_state_detail_route_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "public_state_json_api_shape_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "public_state_dual_callback_route_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "deploy_smoke_completion_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "deploy_smoke_ingress_callback_route_gate"
        ]
        is True
    )
    assert (
        payload["objective_requirements"]["runtime_isolation_and_backpressure"]["test_evidence"][
            "deploy_workflow_deploy_smoke_evidence"
        ]
        is True
    )
    assert any("production readiness JSON not provided" in warning for warning in payload["warnings"])


def test_objective_coverage_accepts_completion_readiness_file(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "public_state_evidence": {"ok": True},
                "deploy_smoke_evidence": {"ok": True},
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is True
    assert payload["ok"] is True
    assert payload["readiness"]["webhook_inbox_health_ok"] is True
    assert payload["readiness"]["webhook_ingestion_ok"] is True
    assert payload["readiness"]["webhook_processing_ok"] is True
    assert payload["readiness"]["same_sample_ok"] is True
    assert payload["readiness"]["admin_webhook_inbox_ok"] is True
    assert payload["readiness"]["admin_webhook_inbox_metrics_ok"] is True
    assert payload["readiness"]["admin_webhook_inbox_items_ok"] is True
    assert payload["readiness"]["admin_webhook_inbox_reconciliation_ok"] is True
    assert payload["readiness"]["worker_isolation_ok"] is True
    assert payload["readiness"]["internal_event_worker_isolation_ok"] is True
    assert payload["readiness"]["downstream_worker_isolation_ok"] is True
    assert payload["readiness"]["rollback_ok"] is True
    assert payload["readiness"]["public_state_ok"] is True
    assert payload["readiness"]["deploy_smoke_ok"] is True
    assert all(item["local_evidence_ok"] is True for item in payload["objective_requirements"].values())
    assert all(item["production_evidence_ok"] is True for item in payload["objective_requirements"].values())
    assert payload["warnings"] == []


def test_objective_coverage_rejects_completion_without_public_state_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["public state evidence not provided"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["public_state_ok"] is None
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_webhook_inbox_health(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": False},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["webhook_inbox health failed readiness targets"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["webhook_inbox_health_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_worker_isolation_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": False},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["worker isolation evidence failed readiness targets"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["worker_isolation_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_downstream_worker_isolation_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": False},
                "rollback_evidence": {"ok": True},
                "warnings": ["downstream worker isolation evidence failed readiness targets"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["downstream_worker_isolation_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_internal_event_worker_isolation_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": False},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["internal event worker isolation evidence failed readiness targets"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["internal_event_worker_isolation_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_webhook_ingestion_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": False},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["webhook_inbox ingestion evidence failed readiness targets"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["webhook_ingestion_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_webhook_processing_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": False},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["webhook_inbox processing evidence failed readiness targets"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["webhook_processing_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_same_sample_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": False},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["same-sample pressure/ingestion/processing evidence failed"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["same_sample_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_admin_webhook_inbox_page(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": False},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["admin webhook inbox page is not available"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["admin_webhook_inbox_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_admin_webhook_inbox_metrics_api(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": False},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["admin webhook inbox metrics API is not available"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["admin_webhook_inbox_metrics_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_admin_webhook_inbox_items_api(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": False},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["admin webhook inbox items API is not available"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["admin_webhook_inbox_items_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_admin_webhook_inbox_reconciliation_api(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": False},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": True},
                "warnings": ["admin webhook inbox reconciliation API is not available"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["admin_webhook_inbox_reconciliation_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_completion_without_rollback_evidence(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": True,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": True,
                "webhook_inbox_health": {"ok": True},
                "webhook_ingestion_evidence": {"ok": True},
                "webhook_processing_evidence": {"ok": True},
                "same_sample_evidence": {"ok": True},
                "admin_webhook_inbox": {"ok": True},
                "admin_webhook_inbox_metrics": {"ok": True},
                "admin_webhook_inbox_items": {"ok": True},
                "admin_webhook_inbox_reconciliation": {"ok": True},
                "worker_isolation_evidence": {"ok": True},
                "internal_event_worker_isolation_evidence": {"ok": True},
                "downstream_worker_isolation_evidence": {"ok": True},
                "rollback_evidence": {"ok": False},
                "warnings": ["rollback evidence failed readiness targets"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert payload["readiness"]["rollback_ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]


def test_objective_coverage_rejects_cutover_only_readiness_file(tmp_path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        coverage.json.dumps(
            {
                "ok": False,
                "ready_for_production_cutover": True,
                "ready_for_production_completion": False,
                "warnings": ["pressure evidence not provided"],
            }
        ),
        encoding="utf-8",
    )

    payload = coverage.run(["--readiness-file", str(readiness)])

    assert payload["local_contract_ready"] is True
    assert payload["production_completion_ready"] is False
    assert payload["ok"] is False
    assert "production readiness JSON does not prove completion" in payload["warnings"]
