from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "ci" / "select_test_scope.py"
MANIFEST = ROOT / "docs" / "ci" / "test_scope_manifest.yml"


def _select(
    *changed_files: str,
    inherit_ci_event: bool = False,
    deleted_files: tuple[str, ...] = (),
) -> dict:
    command = [sys.executable, str(SELECTOR), "--json"]
    for changed_file in changed_files:
        command.extend(["--changed-file", changed_file])
    for deleted_file in deleted_files:
        command.extend(["--deleted-file", deleted_file])
    env = os.environ.copy()
    env.pop("AICRM_FORCE_FULL_CI", None)
    if not inherit_ci_event:
        for key in ("GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH"):
            env.pop(key, None)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        check=True,
        capture_output=True,
    )
    return json.loads(completed.stdout)


def test_media_library_change_runs_small_no_pg_slice() -> None:
    result = _select("aicrm_next/extensions/commerce/commerce/templates/wechat_products.html")

    assert "commerce" in result["matched_scopes"]
    assert "media_library" in result["matched_scopes"]
    assert "tests/test_image_upload_client_static.py" in result["python_tests"]
    assert "tests/test_image_library_template.py" in result["python_tests"]
    assert "tests/test_wechat_products_admin_page_contract.py" in result["python_tests"]
    assert result["frontend_tests"] == []
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_qr_jpg_and_shared_download_contracts_have_permanent_scopes() -> None:
    result = _select(
        "tests/test_admin_download_frontend_contract.py",
        "tests/test_share_qr_jpeg.py",
        "tests/test_wecom_qrcode_image_client.py",
    )

    assert result["unmatched_files"] == []
    assert "tests/test_admin_download_frontend_contract.py" in result["python_tests"]
    assert "tests/test_share_qr_jpeg.py" in result["python_tests"]
    assert "tests/test_wecom_qrcode_image_client.py" in result["python_tests"]


def test_every_runtime_python_change_runs_import_graph_architecture_gate() -> None:
    result = _select("aicrm_next/engagement/media_library/variants.py")

    assert result["matched_scopes"] == ["media_library"]
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_dependency_audit_is_limited_to_supply_chain_changes() -> None:
    ordinary_business_change = _select("aicrm_next/engagement/media_library/variants.py")

    assert ordinary_business_change["needs_dependency_audit"] is False
    for changed_file in (
        "requirements.txt",
        "requirements-dev.txt",
        "requirements.lock",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        ".github/workflows/ci-fast.yml",
        "scripts/ci/check_dependency_security.py",
        "scripts/ci/check_github_action_pins.py",
        "docs/security/dependency_risk_acceptance.yml",
    ):
        assert _select(changed_file)["needs_dependency_audit"] is True, changed_file


def test_physical_engagement_package_migration_forces_full_ci() -> None:
    result = _select(
        "aicrm_next/engagement/__init__.py",
        deleted_files=(
            "aicrm_next/media_library/api.py",
            "aicrm_next/send_content/application.py",
            "aicrm_next/send_targets/resolver.py",
        ),
    )

    assert result["unmatched_files"] == []
    assert result["unmapped_deleted_files"] == []
    assert "physical_engagement_package_migration" in result["matched_scopes"]
    assert "tests/test_runtime_contract_inventory.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_physical_automation_package_migration_forces_full_ci() -> None:
    result = _select(
        "aicrm_next/automation/__init__.py",
        deleted_files=(
            "aicrm_next/automation_engine/api.py",
            "aicrm_next/background_jobs/broadcast_queue_worker.py",
            "aicrm_next/ops_enrollment/api.py",
        ),
    )

    assert result["unmatched_files"] == []
    assert result["unmapped_deleted_files"] == []
    assert "physical_automation_package_migration" in result["matched_scopes"]
    assert "tests/test_runtime_contract_inventory.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_physical_insights_package_migration_forces_full_ci() -> None:
    result = _select(
        "aicrm_next/insights/__init__.py",
        deleted_files=(
            "aicrm_next/admin_read_model/application.py",
            "aicrm_next/data_health/checks.py",
            "aicrm_next/delivery_lineage/api.py",
        ),
    )

    assert result["unmatched_files"] == []
    assert result["unmapped_deleted_files"] == []
    assert "physical_insights_package_migration" in result["matched_scopes"]
    assert "tests/test_runtime_contract_inventory.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_unmapped_deleted_path_forces_full_ci_without_blocking_retirement() -> None:
    retired_path = "aicrm_next/retired_context/api.py"
    result = _select(deleted_files=(retired_path,))

    assert result["changed_files"] == [retired_path]
    assert result["unmatched_files"] == []
    assert result["unmapped_deleted_files"] == [retired_path]
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_runtime_security_contract_has_a_permanent_scope() -> None:
    result = _select("tests/test_p1_runtime_security.py")

    assert "runtime_readiness" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_p1_runtime_security.py" in result["python_tests"]
    assert result["needs_full_ci"] is True


def test_runtime_configuration_cutover_has_a_permanent_control_plane_scope() -> None:
    changed_paths = (
        "aicrm_next/runtime_configuration.py",
        "tools/check_runtime_configuration_contract.py",
        "tests/test_runtime_configuration_contract.py",
    )

    result = _select(*changed_paths)

    assert "config_release_control_plane" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_runtime_configuration_contract.py" in result["python_tests"]
    assert "tests/test_config_releases.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_import_graph_governance_changes_force_mapped_full_ci() -> None:
    result = _select(
        "aicrm_next/extensions/ai/automation_agents/__init__.py",
        "tools/check_import_graph.py",
        "docs/architecture/import_graph_baseline.yml",
        "tests/test_import_graph_guard.py",
        "tests/test_zero_runtime_import_scc.py",
    )

    assert "import_graph_governance" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_import_graph_guard.py" in result["python_tests"]
    assert "tests/test_zero_runtime_import_scc.py" in result["python_tests"]
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_zero_scc_runtime_composition_files_force_full_postgres_ci() -> None:
    result = _select(
        "aicrm_next/channel_entry_composition.py",
        "aicrm_next/integration_ports.py",
        "aicrm_next/mcp_composition.py",
        "aicrm_next/read_model_composition.py",
        "aicrm_next/platform/shared/admin_action_runtime.py",
        "aicrm_next/platform/shared/outbound_https/security.py",
        "aicrm_next/platform/shared/outbound_https/transport.py",
        "aicrm_next/platform/shared/product_code_aliases.py",
        "aicrm_next/platform/shared/wecom_runtime.py",
        "scripts/run_wechat_pay_order_reconciliation_worker.py",
        "tests/test_order_reconciliation_worker.py",
        "tests/test_internal_events_ops_shadow.py",
        "tests/test_integration_ports.py",
    )

    assert "zero_scc_runtime_composition" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_zero_runtime_import_scc.py" in result["python_tests"]
    assert "tests/test_internal_event_registry_composition.py" in result["python_tests"]
    assert "tests/test_integration_ports.py" in result["python_tests"]
    assert "tests/test_order_reconciliation_worker.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_admin_read_model_runtime_files_have_a_permanent_ci_scope() -> None:
    result = _select(
        "aicrm_next/insights/admin_read_model/application.py",
        "aicrm_next/app/admin_console/admin_real_data.py",
    )

    assert "admin_read_model" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_admin_read_model_boundary.py" in result["python_tests"]
    assert "tests/test_admin_pages_real_data_binding.py" in result["python_tests"]
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_operation_cycles_changes_select_full_postgres_scope() -> None:
    result = _select(
        "aicrm_next/extensions/hxc/operation_cycles/api.py",
        "aicrm_next/app/admin_console/templates/admin_shell/operation_cycles_run.html",
        "fixtures/operation_cycles/hxc_monday_20260713_snapshot.json",
        "migrations/versions/0113_operation_cycles.py",
        "scripts/build_hxc_monday_operation_cycle_snapshot.py",
        "tests/test_operation_cycles_repository.py",
    )

    assert result["unmatched_files"] == []
    assert "operation_cycles" in result["matched_scopes"]
    assert "tests/test_operation_cycles_api.py" in result["python_tests"]
    assert "tests/test_operation_cycles_frontend_contract.py" in result["python_tests"]
    assert "tests/test_operation_cycles_migration.py" in result["python_tests"]
    assert "tests/test_operation_cycles_repository.py" in result["python_tests"]
    assert "tests/test_admin_read_pages_smoke.py" in result["python_tests"]
    assert "tests/test_internal_oauth_client_purpose.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_root_design_qa_artifact_is_mapped_to_docs_only_scope() -> None:
    result = _select("design-qa.md")

    assert result["unmatched_files"] == []
    assert result["matched_scopes"] == ["docs_only"]
    assert result["python_tests"] == []
    assert result["frontend_tests"] == []
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "none"
    assert result["needs_full_ci"] is False


def test_duration_baseline_only_change_reuses_full_regression_provenance_without_repeating_it() -> None:
    result = _select("docs/ci/pytest_duration_baseline.json")

    assert result["matched_scopes"] == ["pytest_duration_baseline"]
    assert result["unmatched_files"] == []
    assert result["python_tests"] == [
        "tests/test_pytest_duration_baseline_builder.py",
        "tests/test_pytest_duration_baseline_pr_validation.py",
        "tests/test_pytest_shard_selector.py",
        "tests/test_test_inventory_audit.py",
        "tests/test_ci_workflow_contract.py",
    ]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_live_runtime_readiness_replacement_has_permanent_full_ci_scope() -> None:
    result = _select(
        "tools/check_live_runtime_readiness.py",
        "tools/check_next_production_runtime_gaps.py",
        "tools/check_next_production_cutover_readiness.py",
        "aicrm_next/insights/admin_read_model/projections.py",
        "tests/test_live_runtime_readiness.py",
        "tests/test_retired_timer_readiness_cleanup.py",
    )

    assert result["unmatched_files"] == []
    assert "live_runtime_readiness" in result["matched_scopes"]
    assert "tests/test_runtime_readiness.py" in result["python_tests"]
    assert "tests/test_admin_read_model_boundary.py" in result["python_tests"]
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_expression_length_guard_has_permanent_full_ci_scope() -> None:
    result = _select(
        "scripts/ci/check_github_actions_expression_length.py",
        "tests/test_github_actions_expression_length.py",
    )

    assert result["unmatched_files"] == []
    assert "ci_deploy" in result["matched_scopes"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert "tests/test_ci_workflow_contract.py" in result["python_tests"]
    assert "tests/test_ai_audience_runtime_hotfixes.py" in result["python_tests"]
    assert "tests/test_identity_cutover_reconciliation_contract.py" in result["python_tests"]
    assert "tests/test_github_actions_expression_length.py" in result["python_tests"]
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_production_queue_operations_have_permanent_postgres_full_ci_scope() -> None:
    changed_paths = (
        "scripts/ops/manage_queue_runtime_soak.py",
        "scripts/ops/recover_all_scope_contact_detail.py",
        "scripts/ops/transition_queue_runtime_scope.py",
        "scripts/ops/validate_queue_all_scope_cutover.py",
        "tests/test_queue_all_scope_cutover.py",
        "tests/test_queue_runtime_cutover.py",
        "tests/test_queue_runtime_cutover_postgres.py",
    )

    result = _select(*changed_paths)

    assert result["unmatched_files"] == []
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert set(changed_paths[4:]) <= set(result["python_tests"])
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_h5_wechat_pay_mobile_projection_test_selects_commerce_scope() -> None:
    result = _select("tests/test_h5_wechat_pay_mobile_projection.py")

    assert result["matched_scopes"] == ["commerce"]
    assert "tests/test_h5_wechat_pay_mobile_projection.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_wechat_shop_mobile_projection_tests_select_commerce_scope() -> None:
    result = _select(
        "tests/test_wechat_shop_mobile_projection.py",
        "tests/test_wechat_shop_mobile_projection_migration.py",
    )

    assert result["matched_scopes"] == ["commerce", "migration_db"]
    assert "tests/test_wechat_shop_mobile_projection.py" in result["python_tests"]
    assert "tests/test_wechat_shop_mobile_projection_migration.py" in result["python_tests"]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "db"
    assert result["needs_full_ci"] is False


def test_public_pay_landing_test_selects_commerce_scope() -> None:
    result = _select("tests/test_public_pay_landing.py")

    assert result["matched_scopes"] == ["commerce"]
    assert "tests/test_public_pay_landing.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_commerce_admin_order_tests_select_commerce_scope() -> None:
    result = _select(
        "aicrm_next/extensions/commerce/commerce/templates/admin_orders.html",
        "tests/test_admin_p0_commerce_api.py",
        "tests/test_commerce_admin_transaction_detail.py",
    )

    assert result["matched_scopes"] == ["commerce"]
    assert "tests/test_admin_p0_commerce_api.py" in result["python_tests"]
    assert "tests/test_commerce_admin_transaction_detail.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_service_period_change_selects_service_period_slice() -> None:
    result = _select(
        "aicrm_next/extensions/commerce/service_period/api.py",
        "aicrm_next/extensions/commerce/service_period/templates/service_period_products.html",
        "tests/test_service_period_frontend_contract.py",
    )

    assert result["matched_scopes"] == ["service_period"]
    assert "tests/test_service_period_application.py" in result["python_tests"]
    assert "tests/test_service_period_h5_payment.py" in result["python_tests"]
    assert "tests/test_service_period_frontend_contract.py" in result["python_tests"]
    assert "tests/test_service_period_member_grid.py" in result["python_tests"]
    assert "tests/test_service_period_schema.py" in result["python_tests"]
    assert "tests/test_router_registry_contract.py" in result["python_tests"]
    assert "tests/frontend/service_period_member_grid.test.mjs" in result["frontend_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_huangyoucan_usage_projection_has_permanent_full_pg_scope() -> None:
    result = _select(
        "scripts/run_huangyoucan_usage_sync.py",
        "tests/test_huangyoucan_usage_sync.py",
    )

    assert result["matched_scopes"] == ["huangyoucan_usage_projection"]
    assert result["unmatched_files"] == []
    assert "tests/test_huangyoucan_usage_sync.py" in result["python_tests"]
    assert "tests/test_sidebar_v2_api.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_questionnaire_mobile_change_selects_questionnaire_and_commerce_slices() -> None:
    result = _select(
        "aicrm_next/extensions/forms/questionnaire/domain.py",
        "aicrm_next/extensions/forms/questionnaire/h5_write.py",
        "aicrm_next/app/admin_console/templates/questionnaire_h5_page.html",
        "aicrm_next/platform/shared/mobile.py",
        "aicrm_next/extensions/commerce/commerce/application.py",
        "tests/test_questionnaire_h5_submit_validation.py",
        "tests/test_questionnaire_mobile_normalization.py",
        "tests/test_checkout_api_contract.py",
    )

    assert "questionnaire" in result["matched_scopes"]
    assert "commerce" in result["matched_scopes"]
    assert "mobile_validation" in result["matched_scopes"]
    assert "tests/test_questionnaire_h5_submit_validation.py" in result["python_tests"]
    assert "tests/test_questionnaire_mobile_normalization.py" in result["python_tests"]
    assert "tests/test_checkout_api_contract.py" in result["python_tests"]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_identity_contact_change_selects_access_pg_and_full_architecture_gate() -> None:
    result = _select("aicrm_next/crm/identity_contact/application.py")

    assert "identity_contact" in result["matched_scopes"]
    assert "sidebar_questionnaire_access" in result["matched_scopes"]
    assert "tests/test_identity_application_contract.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_wecom_callback_ops_change_selects_identity_contact_slice() -> None:
    result = _select(
        "scripts/ops/check_wecom_callback_deploy_smoke.py",
        "scripts/ops/check_wecom_callback_permanent_fix_readiness.py",
        "scripts/ops/prepare_wecom_callback_ingress_cutover.py",
        "tests/test_wecom_callback_deploy_smoke.py",
        "tests/test_wecom_callback_ingress_runtime.py",
        "tests/test_wecom_callback_permanent_fix_readiness.py",
    )

    assert result["matched_scopes"] == ["identity_contact"]
    assert "tests/test_wecom_callback_inbox.py" in result["python_tests"]
    assert "tests/test_wecom_callback_deploy_smoke.py" in result["python_tests"]
    assert "tests/test_wecom_callback_ingress_runtime.py" in result["python_tests"]
    assert "tests/test_wecom_callback_permanent_fix_readiness.py" in result["python_tests"]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_identity_worker_deadlock_recovery_has_permanent_deploy_and_identity_scope() -> None:
    result = _select("scripts/ops/recover_identity_resolution_worker_deadlock.py")

    assert {"ci_deploy", "identity_contact"} <= set(result["matched_scopes"])
    assert result["unmatched_files"] == []
    assert "tests/test_identity_resolution_backfill_worker.py" in result["python_tests"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_r05_callback_runtime_files_are_mapped_to_full_pg_ci() -> None:
    result = _select(
        "scripts/ops/reconcile_wecom_callback_runtime.py",
        "scripts/run_wecom_callback_inbox_worker.py",
        "tests/test_push_capabilities_config.py",
        "tests/test_r05_wecom_callback_architecture.py",
    )

    assert {"identity_contact", "admin_config"} <= set(result["matched_scopes"])
    assert result["unmatched_files"] == []
    assert "tests/test_wecom_callback_inbox.py" in result["python_tests"]
    assert "tests/test_push_capabilities_config.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_r06_internal_event_outbox_files_force_full_postgres_ci() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/internal_events/outbox.py",
        "aicrm_next/platform/platform_foundation/internal_events/reconciliation/outbox.py",
        "aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py",
        "migrations/versions/0099_internal_event_outbox_and_consumer_lease.py",
        "migrations/versions/0122_internal_event_fanout_manifest.py",
        "migrations/versions/0123_required_physical_schema_repair.py",
        "scripts/ops/reconcile_internal_event_outbox.py",
        "tests/test_internal_event_outbox.py",
        "tests/test_internal_event_worker_exit.py",
    )

    assert "internal_event_outbox_reliability" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_internal_event_outbox.py" in result["python_tests"]
    assert "tests/test_internal_event_worker_exit.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_automation_agent_audit_schema_repair_routes_to_full_postgres_contracts() -> None:
    result = _select(
        "migrations/versions/0124_automation_agent_audit_tables.py",
        "docs/architecture/data_table_lifecycle_manifest.yml",
        "tests/test_database_bootstrap.py",
        "tests/test_deploy_workflow_contract.py",
    )

    assert {"migration_db", "ci_deploy"} <= set(result["matched_scopes"])
    assert result["unmatched_files"] == []
    assert "tests/test_alembic_revision_chain.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert "tests/test_data_table_lifecycle_guard.py" in result["python_tests"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_r07_external_effect_delivery_files_force_full_postgres_ci() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/external_effects/worker.py",
        "aicrm_next/platform/platform_foundation/external_effects/reconciliation.py",
        "aicrm_next/automation/background_jobs/broadcast_queue_worker.py",
        "aicrm_next/platform/external_push/service.py",
        "aicrm_next/insights/delivery_lineage/application.py",
        "aicrm_next/channels/integration_gateway/wecom_private_adapter.py",
        "migrations/versions/0100_external_effect_delivery_lease.py",
        "scripts/run_external_effect_queue_worker.py",
        "scripts/ops/reconcile_external_effect_dispatch.py",
        "deploy/openclaw-external-effect-worker.timer",
        "docs/architecture/external_effect_delivery_state_machine.md",
        "docs/runbooks/external_effect_delivery_reconciliation.md",
        "tests/test_external_effect_delivery_lease.py",
        "tests/test_external_effect_reconciliation.py",
        "tests/test_broadcast_jobs_wecom_private_dispatch.py",
    )

    assert "external_effect_delivery_reliability" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_external_effect_delivery_lease.py" in result["python_tests"]
    assert "tests/test_external_effect_reconciliation.py" in result["python_tests"]
    assert "tests/test_broadcast_jobs_wecom_private_dispatch.py" in result["python_tests"]
    assert "tests/test_external_push_next_native_service.py" in result["python_tests"]
    assert "tests/test_delivery_lineage_api.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_external_effect_continuation_composition_has_a_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/external_effect_composition.py",
        "aicrm_next/extensions/ai/automation_agents/external_effect_continuation.py",
        "aicrm_next/extensions/ai/automation_agents/internal_webhook_adapter.py",
        "aicrm_next/platform/shared/automation_agent_webhook_contract.py",
        "aicrm_next/channels/channel_entry/identity_external_effect.py",
        "aicrm_next/platform/external_push/external_effect_continuation.py",
        "aicrm_next/internal_event_composition.py",
        "aicrm_next/extensions/forms/questionnaire/external_effect_continuation.py",
        "aicrm_next/platform/platform_foundation/external_effects/completion_events.py",
        "aicrm_next/platform/platform_foundation/external_effects/continuations.py",
        "aicrm_next/platform/platform_foundation/external_effects/provider_result_repository.py",
        "aicrm_next/platform/platform_foundation/external_effects/repo_contract.py",
        "aicrm_next/platform/platform_foundation/external_effects/repo_memory.py",
        "deploy/openclaw-internal-event-worker.service",
        "migrations/versions/0131_external_effect_continuation_fanout.py",
        "tests/test_external_effect_completion_event.py",
        "tests/test_automation_agent_internal_webhook_adapter.py",
        "tests/test_external_effect_continuation_composition.py",
    )

    assert "external_effect_continuation_composition" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_external_effect_continuation_composition.py" in result["python_tests"]
    assert "tests/test_external_effect_completion_event.py" in result["python_tests"]
    assert "tests/test_internal_event_registry_composition.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert "tests/test_alembic_revision_chain.py" in result["python_tests"]
    assert "tests/test_external_effects_mvp.py" in result["python_tests"]
    assert "tests/test_questionnaire_h5_final_tags_real_wecom.py" in result["python_tests"]
    assert "tests/test_automation_agents_webhook_execution.py" in result["python_tests"]
    assert "tests/test_automation_agent_internal_webhook_adapter.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_welcome_media_dependency_migration_has_a_permanent_postgres_scope() -> None:
    result = _select(
        "aicrm_next/channels/channel_entry/welcome_media_effects_repository.py",
        "migrations/versions/0130_welcome_media_dependencies.py",
        "scripts/ci/check_welcome_media_effect_ownership.py",
        "tests/test_channel_welcome_media_dependencies_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "identity_contact" in result["matched_scopes"]
    assert "tests/test_channel_welcome_media_dependencies_postgres.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_execution_runtime_policy_and_timeline_migrations_have_a_permanent_postgres_scope() -> None:
    result = _select(
        "migrations/versions/0132_external_claim_scope_policy.py",
        "migrations/versions/0134_execution_timeline_graph_indexes.py",
        "migrations/versions/0135_queue_scope_transition_audit.py",
        "migrations/versions/0136_queue_runtime_validation_soak.py",
        "scripts/ci/check_queue_runtime_cutover_kernel.py",
        "tests/test_execution_timeline_graph_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert "tests/test_execution_runtime_postgres.py" in result["python_tests"]
    assert "tests/test_execution_timeline_graph_postgres.py" in result["python_tests"]
    assert "tests/test_queue_runtime_cutover_postgres.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert "tests/test_alembic_revision_chain.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_admin_queue_commands_have_a_permanent_postgres_runtime_scope() -> None:
    result = _select(
        "tests/test_admin_queue_command_api.py",
        "tests/test_admin_queue_command_boundary.py",
    )

    assert result["unmatched_files"] == []
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert "tests/test_admin_queue_command_api.py" in result["python_tests"]
    assert "tests/test_admin_queue_command_boundary.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_webhook_inbox_runtime_owner_has_permanent_postgres_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/webhook_inbox/runtime_write_port.py",
        "aicrm_next/platform/platform_foundation/webhook_inbox/runtime_write_repository.py",
        "tests/test_webhook_inbox_runtime_owner_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert "tests/test_webhook_inbox_runtime_owner.py" in result["python_tests"]
    assert "tests/test_webhook_inbox_runtime_owner_postgres.py" in result["python_tests"]
    assert "tests/test_execution_runtime_postgres.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_internal_event_consumer_run_owner_has_permanent_postgres_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/internal_events/consumer_run_write_port.py",
        "aicrm_next/platform/platform_foundation/internal_events/consumer_run_write_repository.py",
        "tests/test_internal_event_consumer_run_owner_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "internal_event_outbox_reliability" in result["matched_scopes"]
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert "tests/test_internal_event_consumer_run_owner.py" in result["python_tests"]
    assert "tests/test_internal_event_consumer_run_owner_postgres.py" in result["python_tests"]
    assert "tests/test_execution_runtime_postgres.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_internal_event_outbox_runtime_owner_has_permanent_postgres_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/internal_events/outbox_runtime_write_port.py",
        "aicrm_next/platform/platform_foundation/internal_events/outbox_runtime_write_repository.py",
        "tests/test_internal_event_outbox_runtime_owner_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "internal_event_outbox_reliability" in result["matched_scopes"]
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert "tests/test_internal_event_outbox_runtime_owner.py" in result["python_tests"]
    assert "tests/test_internal_event_outbox_runtime_owner_postgres.py" in result["python_tests"]
    assert "tests/test_execution_runtime_postgres.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_external_effect_runtime_owner_has_permanent_postgres_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/external_effects/claim_policy.py",
        "aicrm_next/platform/platform_foundation/external_effects/runtime_write_port.py",
        "aicrm_next/platform/platform_foundation/external_effects/runtime_write_repository.py",
        "tests/test_external_effect_runtime_owner_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert "tests/test_external_effect_runtime_owner.py" in result["python_tests"]
    assert "tests/test_external_effect_runtime_owner_postgres.py" in result["python_tests"]
    assert "tests/test_execution_runtime_postgres.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_prod_remediation_security_and_heading_files_have_permanent_scopes() -> None:
    result = _select(
        "aicrm_next/extensions/ai/automation_agents/templates/admin_console/automation_agent_list.html",
        "aicrm_next/crm/customer_tags/templates/admin_console/config_wecom_tags.html",
        "aicrm_next/app/admin_console/templates/admin_console/hxc_send_config.html",
        "aicrm_next/app/admin_console/templates/admin_console/setup_wizard.html",
        "aicrm_next/extensions/archive/message_archive/archive_sdk.py",
        "aicrm_next/extensions/archive/message_archive/sdk_subprocess.py",
        "scripts/ops/ensure_runtime_environment.py",
        "tests/test_admin_heading_deduplication.py",
        "tests/test_archive_sdk_isolation.py",
        "tests/test_runtime_browser_security.py",
    )

    assert result["unmatched_files"] == []
    assert {"admin_config", "admin_read_pages", "security_hardening"} <= set(result["matched_scopes"])
    assert "tests/test_admin_heading_deduplication.py" in result["python_tests"]
    assert "tests/test_archive_sdk_isolation.py" in result["python_tests"]
    assert "tests/test_runtime_browser_security.py" in result["python_tests"]
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_ai_audience_e2e_composition_has_a_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/extensions/ai/ai_audience_e2e_composition.py",
        "aicrm_next/automation/ops_enrollment/ai_audience_e2e_gateway.py",
        "tests/test_ai_audience_e2e_composition.py",
    )

    assert "ai_audience_e2e_composition" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_ai_audience_e2e_composition.py" in result["python_tests"]
    assert "tests/test_ai_audience_real_e2e_runner.py" in result["python_tests"]
    assert "tests/test_ai_audience_external_api.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_runtime_module_size_governance_has_a_permanent_full_ci_scope() -> None:
    result = _select(
        "tools/check_runtime_module_sizes.py",
        "docs/architecture/runtime_module_size_baseline.yml",
        "tests/test_runtime_module_size_guard.py",
        "tests/test_runtime_module_split_contract.py",
    )

    assert "runtime_module_size_governance" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_runtime_module_size_guard.py" in result["python_tests"]
    assert "tests/test_runtime_module_split_contract.py" in result["python_tests"]
    assert "tests/test_internal_events_mvp.py" in result["python_tests"]
    assert "tests/test_questionnaire_application_contract.py" in result["python_tests"]
    assert "tests/test_admin_config_next.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_admin_jobs_archive_gateway_has_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/extensions/archive/admin_jobs_archive_sync_gateway.py",
        "tests/test_admin_jobs_archive_sync_gateway.py",
    )

    assert result["unmatched_files"] == []
    assert "admin_jobs_archive_sync_gateway" in result["matched_scopes"]
    assert "tests/test_archive_sync_next.py" in result["python_tests"]
    assert "tests/test_import_graph_guard.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_wecom_payload_contract_has_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/platform/shared/wecom_payload_contract.py",
        "tests/test_wecom_payload_contract.py",
    )

    assert result["unmatched_files"] == []
    assert "wecom_payload_contract" in result["matched_scopes"]
    assert "tests/test_group_ops_domain.py" in result["python_tests"]
    assert "tests/test_import_graph_guard.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_send_content_media_gateway_has_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/engagement/send_content_media_repository_gateway.py",
        "tests/test_send_content_media_gateway.py",
    )

    assert result["unmatched_files"] == []
    assert "send_content_media_gateway" in result["matched_scopes"]
    assert "tests/test_next_material_picker_api.py" in result["python_tests"]
    assert "tests/test_import_graph_guard.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_customer_read_model_refresh_has_permanent_full_postgres_scope() -> None:
    result = _select(
        "aicrm_next/crm/customer_read_model/refresh.py",
        "aicrm_next/crm/customer_read_model/refresh_scope_repository.py",
        "aicrm_next/crm/customer_read_model/repo.py",
        "scripts/run_customer_read_model_refresh.py",
        "deploy/openclaw-customer-read-model-refresh.service",
        "deploy/openclaw-customer-read-model-refresh.timer",
        "migrations/versions/0108_customer_read_model_refresh_and_retired_workspace_drop.py",
        "migrations/versions/0153_customer_read_model_generation_slots.py",
        "tests/test_customer_live_source_repository.py",
        "tests/test_customer_read_model_refresh.py",
        "tests/test_customer_read_model_incremental_writer.py",
    )

    assert result["unmatched_files"] == []
    assert "customer_read_model_refresh" in result["matched_scopes"]
    assert "tests/test_customer_live_source_repository.py" in result["python_tests"]
    assert "tests/test_customer_read_model_refresh.py" in result["python_tests"]
    assert "tests/test_customer_read_model_incremental_writer.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_questionnaire_auto_execute_cutover_has_permanent_full_postgres_scope() -> None:
    result = _select(
        "aicrm_next/platform/shared/release_cutovers.py",
        "migrations/versions/0109_questionnaire_continuation_auto_execute.py",
        "deploy/openclaw-internal-event-worker.service",
        "tests/test_questionnaire_auto_execute_cutover.py",
    )

    assert result["unmatched_files"] == []
    assert "questionnaire_radar_reliability" in result["matched_scopes"]
    assert "tests/test_questionnaire_auto_execute_cutover.py" in result["python_tests"]
    assert "tests/test_questionnaire_radar_reconciliation.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_internal_event_registry_composition_has_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/internal_event_composition.py",
        "aicrm_next/platform/platform_foundation/internal_events/consumer_registry.py",
        "scripts/ci/runtime_contract_inventory.py",
        "tests/test_internal_event_registry_composition.py",
    )

    assert result["unmatched_files"] == []
    assert "internal_event_registry_composition" in result["matched_scopes"]
    assert "tests/test_internal_events_mvp.py" in result["python_tests"]
    assert "tests/test_internal_event_worker_exit.py" in result["python_tests"]
    assert "tests/test_runtime_contract_inventory.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_group_provider_diagnostics_have_full_postgres_scope() -> None:
    result = _select("tests/test_wecom_group_provider_diagnostics.py")

    assert result["unmatched_files"] == []
    assert "external_effect_delivery_reliability" in result["matched_scopes"]
    assert "tests/test_wecom_group_provider_diagnostics.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_questionnaire_editor_asset_split_has_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/extensions/forms/questionnaire/templates/admin_questionnaires.html",
        "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.css",
        "aicrm_next/extensions/forms/questionnaire/static/admin_questionnaire_editor.js",
        "tests/test_questionnaire_editor_asset_split.py",
        "tests/test_architecture_size_budgets.py",
    )

    assert result["unmatched_files"] == []
    assert "questionnaire_editor_asset_split" in result["matched_scopes"]
    assert "tests/test_questionnaire_admin_pages_next_native.py" in result["python_tests"]
    assert "tests/test_admin_pages_real_data_binding.py" in result["python_tests"]
    assert "tests/test_architecture_size_budgets.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_runtime_readiness_has_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/readiness.py",
        "tests/test_runtime_readiness.py",
    )

    assert result["unmatched_files"] == []
    assert "runtime_readiness" in result["matched_scopes"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert "tests/test_p1_runtime_security.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_cloud_repository_split_modules_keep_permanent_postgres_coverage() -> None:
    result = _select(
        "aicrm_next/extensions/growth/cloud_orchestrator/repository_legacy.py",
        "aicrm_next/extensions/growth/cloud_orchestrator/repository_memory.py",
    )

    assert result["unmatched_files"] == []
    assert "cloud_plan_repository_split" in result["matched_scopes"]
    assert "tests/test_ops_plan_broadcast_planner_consumer.py" in result["python_tests"]
    assert result["needs_full_ci"] is True
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"


def test_r08_commerce_fulfillment_files_force_full_postgres_ci() -> None:
    result = _select(
        "aicrm_next/extensions/commerce/commerce/fulfillment_reconciliation.py",
        "aicrm_next/platform/platform_foundation/external_effects/transactional.py",
        "aicrm_next/platform/platform_foundation/internal_events/refund.py",
        "aicrm_next/extensions/commerce/service_period/refund_consumer.py",
        "migrations/versions/0101_commerce_fulfillment_invariants.py",
        "scripts/run_external_push_worker.py",
        "scripts/ops/reconcile_commerce_fulfillment.py",
        "deploy/production_runtime_units.json",
        "tests/test_r08_commerce_fulfillment_postgres.py",
    )

    assert "commerce_fulfillment_reliability" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_r08_commerce_fulfillment_postgres.py" in result["python_tests"]
    assert "tests/test_commerce_fulfillment_reconciliation.py" in result["python_tests"]
    assert "tests/test_internal_events_refund_slice.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_unionid_identity_cutover_changes_force_pg_full_ci_without_unmapped_files() -> None:
    result = _select(
        "aicrm_next/extensions/ai/automation_agents/repository.py",
        "aicrm_next/crm/customer_tags/local_projection.py",
        "aicrm_next/crm/customer_tags/projection_port.py",
        "aicrm_next/crm/customer_tags/projection_repository.py",
        "aicrm_next/extensions/archive/message_archive/application.py",
        "scripts/ops/check_unionid_identity_cutover.py",
        "scripts/run_identity_mobile_bridge_backfill.py",
        "tests/test_external_questionnaire_submissions_api.py",
        "tests/test_internal_events_shadow_emit.py",
        "tests/test_next_hxc_broadcast_repo.py",
        "tests/test_customer_tag_projection_port.py",
        "tests/test_customer_tag_projection_port_postgres.py",
        "tests/test_unionid_identity_contract_gate.py",
        "tests/test_wecom_tag_live_mutation_callers_contract.py",
    )

    assert result["matched_scopes"] == ["unionid_identity_cutover"]
    assert result["unmatched_files"] == []
    assert "tests/test_identity_resolver_postgres.py" in result["python_tests"]
    assert "tests/test_customer_tag_projection_port_postgres.py" in result["python_tests"]
    assert "tests/test_service_period_payment_consumer.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_sidebar_write_change_selects_write_command_regression() -> None:
    result = _select("aicrm_next/crm/sidebar_write/repo.py")

    assert "customer_read_model_sidebar" in result["matched_scopes"]
    assert "sidebar_questionnaire_access" in result["matched_scopes"]
    assert "tests/test_sidebar_write_commands.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_db_session_runtime_audit_keeps_customer_repository_scope() -> None:
    result = _select("tests/test_db_session_runtime_audit.py")

    assert result["unmatched_files"] == []
    assert "customer_read_model_sidebar" in result["matched_scopes"]
    assert "tests/test_db_session_runtime_audit.py" in result["python_tests"]


def test_signed_session_change_selects_sidebar_shared_runtime_slice() -> None:
    result = _select("aicrm_next/platform/shared/signed_session.py")

    assert result["matched_scopes"] == [
        "sidebar_questionnaire_access",
        "shared_sidebar_runtime",
    ]
    assert "tests/test_sidebar_jssdk_adapter.py" in result["python_tests"]
    assert "tests/test_shared_flask_config_retirement.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_ai_assist_external_campaign_change_selects_focused_python_slice() -> None:
    result = _select("aicrm_next/extensions/ai/ai_assist/external_campaigns.py")

    assert "ai_assist_external_campaigns" in result["matched_scopes"]
    assert "tests/test_ai_assist_external_campaigns.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"


def test_shared_send_target_change_selects_ai_assist_campaign_slice() -> None:
    result = _select("aicrm_next/engagement/send_targets/resolver.py")

    assert result["unmatched_files"] == []
    assert "ai_assist_external_campaigns" in result["matched_scopes"]
    assert "tests/test_ai_assist_external_campaigns.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"


def test_campaign_step_media_owner_selects_cloud_plan_postgres_scope() -> None:
    result = _select(
        "aicrm_next/extensions/growth/cloud_orchestrator/campaign_step_media_port.py",
        "aicrm_next/extensions/growth/cloud_orchestrator/campaign_step_media_repository.py",
        "tests/test_campaign_step_media_reference_port.py",
        "tests/test_campaign_step_media_reference_port_postgres.py",
    )

    assert "cloud_plan_repository" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_campaign_step_media_reference_port_postgres.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_user_ops_change_selects_batch_send_contract_slice() -> None:
    result = _select("aicrm_next/automation/ops_enrollment/application.py")

    assert "user_ops" in result["matched_scopes"]
    assert "tests/test_user_ops_api.py" in result["python_tests"]
    assert "tests/test_user_ops_external_effect_enqueue.py" in result["python_tests"]
    assert "tests/test_user_ops_send_record_projection.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"


def test_admin_read_override_selects_focused_slice_without_pg() -> None:
    result = _select(
        "aicrm_next/extensions/ai/ai_audience_ops/admin_api.py",
        "aicrm_next/automation/automation_engine/group_ops/application.py",
        "aicrm_next/automation/ops_enrollment/api.py",
    )

    assert result["matched_scopes"] == ["admin_read_pages"]
    assert "tests/test_ai_audience_admin_pages.py" in result["python_tests"]
    assert "tests/test_group_ops_plans_api.py" in result["python_tests"]
    assert "tests/test_user_ops_api.py" in result["python_tests"]
    assert "tests/test_ai_audience_ops.py" not in result["python_tests"]
    assert "tests/test_group_ops_queue_contract.py" not in result["python_tests"]
    assert "tests/test_user_ops_application_contract.py" not in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_admin_read_smoke_test_file_selects_admin_read_scope() -> None:
    result = _select("tests/test_admin_read_pages_smoke.py")

    assert result["matched_scopes"] == ["admin_read_pages"]
    assert "tests/test_admin_read_pages_smoke.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"


def test_static_admin_shell_contract_and_contributors_select_admin_read_scope() -> None:
    result = _select(
        "aicrm_next/app/admin_console/__init__.py",
        "aicrm_next/admin_shell_contract.py",
        "aicrm_next/automation/automation_engine/channel_admin_pages.py",
        "aicrm_next/crm/customer_tags/admin_pages.py",
        "aicrm_next/extensions/hxc/hxc_dashboard/api.py",
        "aicrm_next/crm/owner_migration/api.py",
    )

    assert "admin_read_pages" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_admin_shell_native.py" in result["python_tests"]
    assert result["architecture_gate"] == "fast"


def test_admin_config_page_change_selects_config_scope() -> None:
    result = _select(
        "aicrm_next/mcp_tool_catalog.py",
        "aicrm_next/platform/admin_config/api.py",
        "aicrm_next/app/admin_console/static/admin_console/config_center.js",
        "aicrm_next/app/admin_console/templates/admin_console/config_admin_access_detail.html",
        "tests/test_admin_config_next.py",
        "tests/test_support_admin_pages_native.py",
    )

    assert result["matched_scopes"] == ["admin_config", "frontend_static"]
    assert result["unmatched_files"] == []
    assert "tests/test_admin_config_next.py" in result["python_tests"]
    assert "tests/test_operation_member_picker_frontend.py" in result["python_tests"]
    assert "tests/test_admin_auth_login_pages.py" in result["python_tests"]
    assert "tests/test_admin_pages_real_data_binding.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"


def test_admin_audit_owner_change_selects_postgres_and_full_ci() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/admin_audit/port.py",
        "aicrm_next/platform/platform_foundation/admin_audit/repository.py",
        "tests/test_admin_audit_owner_postgres.py",
    )

    assert "admin_audit_owner" in result["matched_scopes"]
    assert result["unmatched_files"] == []
    assert "tests/test_admin_audit_owner.py" in result["python_tests"]
    assert "tests/test_admin_audit_owner_postgres.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_operation_member_picker_static_assets_select_admin_config_scope() -> None:
    result = _select(
        "aicrm_next/app/admin_console/static/admin_console/admin_console.css",
        "aicrm_next/app/admin_console/static/admin_console/operation_member_picker.js",
        "tests/test_operation_member_picker_frontend.py",
    )

    assert result["matched_scopes"] == ["admin_config", "frontend_static"]
    assert "tests/test_operation_member_picker_frontend.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "fast"
    assert result["needs_full_ci"] is False


def test_config_release_control_plane_selects_postgres_and_full_architecture_gate() -> None:
    result = _select(
        "aicrm_next/platform/admin_config/config_releases.py",
        "scripts/ops/manage_runtime_config_release.py",
        ".github/workflows/runtime-config-production.yml",
        "tests/test_config_releases_postgres.py",
        "tests/test_manage_runtime_config_release.py",
        "tests/test_runtime_config_production_workflow.py",
    )

    assert result["matched_scopes"] == [
        "config_release_control_plane",
        "admin_config",
        "ci_deploy",
    ]
    assert "tests/test_config_releases.py" in result["python_tests"]
    assert "tests/test_config_releases_postgres.py" in result["python_tests"]
    assert "tests/test_manage_runtime_config_release.py" in result["python_tests"]
    assert "tests/test_runtime_config_production_workflow.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_scrm_low_ops_foundation_files_are_all_mapped_to_the_control_plane_scope() -> None:
    result = _select(
        "aicrm_next/capability_registry.py",
        "aicrm_next/scrm_contracts.py",
        "deploy/deployment_profiles/production-current.json",
        ".github/workflows/deployment-profile-production-diagnostics.yml",
        "scripts/ops/validate_production_deployment_profile.py",
        "scripts/run_job_catalog_scheduler.py",
        "tests/test_capability_registry.py",
        "tests/test_deployment_profile.py",
        "tests/test_deployment_profile_production_workflow.py",
        "tests/test_validate_production_deployment_profile.py",
        "tests/test_job_catalog.py",
        "tests/test_scrm_contracts.py",
    )

    assert result["matched_scopes"] == ["config_release_control_plane", "ci_deploy"]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_operation_member_wecom_sync_change_selects_admin_config_and_db_scope() -> None:
    result = _select(
        "aicrm_next/common_operation_members.py",
        "aicrm_next/crm/operation_members/application.py",
        "aicrm_next/crm/operation_members/repository.py",
        "aicrm_next/channels/integration_gateway/wecom_operation_members_client.py",
        "aicrm_next/app/admin_console/static/admin_console/operation_member_picker.js",
        "aicrm_next/app/admin_console/templates/admin_console/base.html",
        "migrations/versions/0096_admin_wecom_directory_members.py",
        "docs/architecture/route_ownership_manifest.yml",
        "docs/ci/test_scope_manifest.yml",
        "tests/test_wecom_operation_members_sync.py",
        "tests/test_operation_member_picker_frontend.py",
    )

    assert "admin_config" in result["matched_scopes"]
    assert "migration_db" in result["matched_scopes"]
    assert "ci_scope_selector" in result["matched_scopes"]
    assert "tests/test_wecom_operation_members_sync.py" in result["python_tests"]
    assert "tests/test_operation_member_picker_frontend.py" in result["python_tests"]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_wecom_tag_catalog_write_change_selects_real_tag_crud_slice() -> None:
    result = _select(
        "aicrm_next/crm/customer_tags/admin_write.py",
        "aicrm_next/channels/integration_gateway/wecom_tag_live_gateway.py",
        "docs/architecture/wecom_tag_read_route_inventory.md",
        "docs/architecture/wecom_tag_write_route_inventory.md",
        "tests/test_wecom_tag_next_sync.py",
        "tests/test_wecom_tag_write_no_real_side_effects.py",
    )

    assert result["matched_scopes"] == ["wecom_tag_catalog_write", "docs_only"]
    assert "tests/test_wecom_tag_write_no_real_side_effects.py" in result["python_tests"]
    assert "tests/test_wecom_tag_next_sync.py" in result["python_tests"]
    assert "tests/test_wecom_tag_write_commands.py" in result["python_tests"]
    assert "tests/test_wecom_tag_write_idempotency.py" in result["python_tests"]
    assert "tests/test_wecom_tag_write_inventory.py" in result["python_tests"]
    assert "tests/test_wecom_tag_read_selectors.py" in result["python_tests"]
    assert "tests/test_group_ops_queue_contract.py" not in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_ci_change_selects_contract_tests_and_full_gate() -> None:
    result = _select(".github/workflows/ci-fast.yml")

    assert "ci_deploy" in result["matched_scopes"]
    assert "tests/test_ci_workflow_contract.py" in result["python_tests"]
    assert "tests/test_select_test_scope.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_ci_shard_selector_test_change_selects_full_gate() -> None:
    for changed_path in (
        "tests/test_pytest_duration_baseline_builder.py",
        "tests/test_pytest_shard_selector.py",
    ):
        result = _select(changed_path)

        assert "ci_deploy" in result["matched_scopes"]
        assert "tests/test_pytest_duration_baseline_builder.py" in result["python_tests"]
        assert "tests/test_pytest_shard_selector.py" in result["python_tests"]
        assert result["unmatched_files"] == []
        assert result["needs_postgres"] is False
        assert result["architecture_gate"] == "full"
        assert result["needs_full_ci"] is True


def test_workflow_dispatch_with_null_inputs_does_not_break_selector(tmp_path: Path, monkeypatch) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"inputs": None}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    result = _select(".github/workflows/ci-fast.yml", inherit_ci_event=True)

    assert "ci_deploy" in result["matched_scopes"]
    assert result["force_full"] is False
    assert result["needs_full_ci"] is True


def test_runtime_units_change_selects_deploy_contract_tests() -> None:
    result = _select(
        "deploy/production_runtime_units.json",
        "scripts/ops/check_runtime_secret_readiness.py",
        "scripts/ops/manage_production_runtime_units.py",
        "tests/test_active_deploy_services_next_native.py",
        "tests/test_architecture_size_budgets.py",
        "tests/test_runtime_secret_readiness.py",
        "tests/test_runtime_units_autostart.py",
    )

    assert "ci_deploy" in result["matched_scopes"]
    assert "commerce_fulfillment_reliability" in result["matched_scopes"]
    assert "tests/test_active_deploy_services_next_native.py" in result["python_tests"]
    assert "tests/test_architecture_size_budgets.py" in result["python_tests"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert "tests/test_identity_cutover_reconciliation_contract.py" in result["python_tests"]
    assert "tests/test_runtime_secret_readiness.py" in result["python_tests"]
    assert "tests/test_runtime_units_autostart.py" in result["python_tests"]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"


def test_root_gitignore_change_selects_deploy_contract_tests() -> None:
    result = _select(".gitignore")

    assert "ci_deploy" in result["matched_scopes"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert result["architecture_gate"] == "full"
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is False


def test_database_baseline_and_ownership_change_selects_required_postgres_slice() -> None:
    result = _select(
        "migrations/baselines/0001_post_legacy.sql",
        "scripts/ops/bootstrap_database.py",
        "tools/check_repository_ownership.py",
        "docs/architecture/data_table_lifecycle_manifest.yml",
        "docs/architecture/repository_ownership.yml",
        "tests/test_database_bootstrap.py",
        "tests/test_repository_ownership_guard.py",
    )

    assert "migration_db" in result["matched_scopes"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert "tests/test_data_table_lifecycle_guard.py" in result["python_tests"]
    assert "tests/test_repository_ownership_guard.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"
    assert result["unmatched_files"] == []
    assert result["needs_full_ci"] is True


def test_deploy_smoke_session_change_selects_admin_and_deploy_contracts() -> None:
    result = _select("scripts/ops/create_deploy_smoke_session.py")

    assert result["matched_scopes"] == ["admin_read_pages", "ci_deploy"]
    assert "tests/test_admin_read_pages_smoke.py" in result["python_tests"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert result["unmatched_files"] == []
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_ci_selector_governance_change_selects_both_selector_contracts() -> None:
    result = _select(
        "docs/ci/test_scope_manifest.yml",
        "docs/ci/test_scope_policy.yml",
        "docs/ci/test_scope_legacy_only_review.yml",
        "scripts/ci/audit_test_inventory.py",
        "scripts/ci/select_test_scope_v2.py",
        "scripts/ci/summarize_test_scope_shadow.py",
        "tests/test_convention_test_scope.py",
        "tests/test_select_test_scope.py",
        "tests/test_test_inventory_audit.py",
        "tests/test_test_scope_shadow_summary.py",
    )

    assert result["matched_scopes"] == ["ci_scope_selector"]
    assert result["python_tests"] == [
        "tests/test_convention_test_scope.py",
        "tests/test_select_test_scope.py",
        "tests/test_test_inventory_audit.py",
        "tests/test_test_scope_shadow_summary.py",
        "tests/test_ci_workflow_contract.py",
    ]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is False
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_pytest_marker_configuration_change_forces_full_ci() -> None:
    result = _select("pyproject.toml")

    assert result["matched_scopes"] == ["ci_deploy"]
    assert result["unmatched_files"] == []
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_ci_manifest_only_references_existing_test_files() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    missing = sorted(
        (scope["name"], test_path)
        for scope in manifest["scopes"]
        for key in ("python_tests", "frontend_tests")
        for test_path in scope.get(key, [])
        if not (ROOT / test_path).is_file()
    )

    assert missing == []


def test_frontend_static_change_runs_all_frontend_tests_without_build() -> None:
    result = _select("aicrm_next/app/admin_console/static/admin_console/cloud_plan_review.js")

    assert "frontend_static" in result["matched_scopes"]
    assert result["frontend_tests"] == [
        "tests/frontend/admin_request_security.test.mjs",
        "tests/frontend/admin_error_messages.test.mjs",
        "tests/frontend/admin_error_rendering_guard.test.mjs",
        "tests/frontend/coupon_inline_scripts_syntax.test.mjs",
        "tests/frontend/sidebar_progressive_loading.test.mjs",
        "tests/frontend/service_period_member_grid.test.mjs",
        "tests/frontend/service_period_member_grid_sharing.test.mjs",
        "tests/frontend/send_content_readonly_detail.test.mjs",
    ]
    assert "needs_frontend_build" not in result


def test_ai_audience_send_record_release_check_routes_to_production_scopes() -> None:
    result = _select("scripts/ops/check_ai_audience_send_records_release.py")

    assert result["matched_scopes"] == ["ai_audience_ops", "ci_deploy"]
    assert "tests/test_ai_audience_send_records.py" in result["python_tests"]
    assert "tests/test_ai_audience_send_record_release_check.py" in result["python_tests"]
    assert "tests/frontend/send_content_readonly_detail.test.mjs" in result["frontend_tests"]
    assert result["unmatched_files"] == []
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_sidebar_workbench_change_selects_progressive_loading_behavior_test() -> None:
    for changed_file, expected_gate, expected_full_ci in (
        ("aicrm_next/app/admin_console/static/sidebar_workbench/sidebar_workbench.js", "full", True),
        ("tests/frontend/sidebar_progressive_loading.test.mjs", "fast", False),
    ):
        result = _select(changed_file)

        assert "customer_read_model_sidebar" in result["matched_scopes"]
        assert "tests/frontend/sidebar_progressive_loading.test.mjs" in result["frontend_tests"]
        assert result["unmatched_files"] == []
        assert result["architecture_gate"] == expected_gate
        assert result["needs_full_ci"] is expected_full_ci


def test_questionnaire_change_selects_postgres_contracts_and_full_regression() -> None:
    result = _select("aicrm_next/extensions/forms/questionnaire/h5_write.py")

    assert "questionnaire" in result["matched_scopes"]
    assert "tests/test_questionnaire_h5_submit_idempotency.py" in result["python_tests"]
    assert "tests/test_internal_events_questionnaire_slice.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_security_hardening_inventory_forces_postgres_and_full_regression() -> None:
    result = _select(
        "docs/architecture/r02_sensitive_data_inventory.yml",
        "tests/test_r02_sensitive_data_inventory.py",
    )

    assert "security_hardening" in result["matched_scopes"]
    assert "tests/test_r02_sensitive_data_inventory.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_callback_change_forces_full_regression() -> None:
    result = _select("aicrm_next/channels/channel_entry/callback_ingress.py")

    assert "identity_contact" in result["matched_scopes"]
    assert "tests/test_wecom_callback_inbox.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_refund_change_forces_full_regression() -> None:
    result = _select("aicrm_next/extensions/commerce/commerce/admin_transactions.py")

    assert "commerce" in result["matched_scopes"]
    assert "tests/test_next_wechat_pay_refunds.py" in result["python_tests"]
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_group_ops_change_selects_broadcast_contracts_and_full_regression() -> None:
    result = _select("aicrm_next/automation/automation_engine/group_ops/domain.py")

    assert "broadcast_group_ops" in result["matched_scopes"]
    assert "tests/test_group_ops_token_broadcast_api.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_broadcast_job_owner_has_permanent_postgres_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/background_jobs/broadcast_job_write_port.py",
        "aicrm_next/platform/platform_foundation/background_jobs/broadcast_job_write_repository.py",
        "scripts/run_automation_ops_scheduler.py",
        "tests/test_automation_facade_guards.py",
        "tests/test_broadcast_job_owner_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "broadcast_group_ops" in result["matched_scopes"]
    assert "tests/test_broadcast_job_owner.py" in result["python_tests"]
    assert "tests/test_broadcast_job_owner_postgres.py" in result["python_tests"]
    assert "tests/test_broadcast_queue_worker_postgres_state_machine.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_cloud_broadcast_projection_owner_has_permanent_postgres_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/background_jobs/cloud_broadcast_projection_write_port.py",
        "aicrm_next/platform/platform_foundation/background_jobs/cloud_broadcast_projection_write_repository.py",
        "tests/test_cloud_broadcast_projection_owner_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "broadcast_group_ops" in result["matched_scopes"]
    assert "tests/test_cloud_broadcast_projection_owner.py" in result["python_tests"]
    assert "tests/test_cloud_broadcast_projection_owner_postgres.py" in result["python_tests"]
    assert "tests/test_broadcast_queue_worker_postgres_state_machine.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_retired_group_ops_workspace_paths_remain_mapped_to_broadcast_scope() -> None:
    changed_paths = (
        "aicrm_next/app/admin_console/routes.py",
        "aicrm_next/app/admin_console/templates/admin_shell/p1_group_ops_workspace.html",
        "aicrm_next/app/admin_console/static/admin_console/p1/p1_group_ops_workspace/workspace_api.js",
        "tests/test_p1_group_ops_workspace_final_closeout.py",
    )

    for changed_path in changed_paths:
        result = _select(changed_path)
        assert result["unmatched_files"] == []
        assert "broadcast_group_ops" in result["matched_scopes"]


def test_private_auth_cutover_maps_every_runtime_caller_and_regression_file() -> None:
    changed_paths = (
        "aicrm_next/extensions/ai/automation_agents/admin_pages.py",
        "aicrm_next/extensions/ai/automation_agents/api.py",
        "aicrm_next/extensions/ai/automation_agents/templates/admin_console/automation_agent_edit.html",
        "aicrm_next/extensions/ai/automation_agents/worker.py",
        "aicrm_next/extensions/growth/cloud_orchestrator/run_due.py",
        "aicrm_next/platform/platform_foundation/auth_platform/service.py",
        "scripts/ai_audience_apply_package_spec.py",
        "scripts/diagnose_business_closure_acceptance.py",
        "scripts/diagnose_ops_plan_broadcast_blocker.py",
        "scripts/ops/bootstrap_auth_clients.py",
        "scripts/ops/check_auth_readiness.py",
        "scripts/ops/manage_auth_clients.py",
        "scripts/run_message_activity_sync.py",
        "tests/admin_auth_test_helpers.py",
        "tests/test_active_automation_run_due_guardrails.py",
        "tests/test_active_automation_scheduled_safe_mode.py",
        "tests/test_auth_client_bootstrap_readiness.py",
        "tests/test_auth_client_ops.py",
        "tests/test_auth_platform_client_authentication.py",
        "tests/test_auth_platform_context.py",
        "tests/test_auth_platform_credentials.py",
        "tests/test_auth_platform_fastapi_protocol.py",
        "tests/test_auth_platform_postgres_repository.py",
        "tests/test_auth_platform_postgres_sessions.py",
        "tests/test_auth_platform_service.py",
        "tests/test_auth_platform_sessions.py",
        "tests/test_auth_platform_webhook_hmac.py",
        "tests/test_auth_platform_webhook_routes.py",
        "tests/test_business_closure_acceptance_diagnostics.py",
        "tests/test_cloud_orchestrator_plan_recipients.py",
        "tests/test_cloud_orchestrator_run_due_commands.py",
        "tests/test_cloud_orchestrator_run_due_idempotency.py",
        "tests/test_cloud_orchestrator_run_due_no_real_side_effects.py",
        "tests/test_cloud_orchestrator_run_due_preview.py",
        "tests/test_external_orders_api.py",
        "tests/test_internal_oauth_client_purpose.py",
        "tests/test_next_admin_jobs_native.py",
        "tests/test_run_message_activity_sync_script.py",
        "tests/test_wecom_tag_read_selectors.py",
        "tests/webhook_hmac_test_helpers.py",
    )

    result = _select(*changed_paths)

    assert result["unmatched_files"] == []
    assert "private_auth_cutover" in result["matched_scopes"]
    assert result["needs_postgres"] is True
    assert result["architecture_gate"] == "full"
    assert result["needs_full_ci"] is True


def test_retired_runtime_physical_cleanup_has_permanent_full_ci_scope() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/legacy_cleanup/service.py",
        "aicrm_next/platform/shared/retired_contracts.py",
        "migrations/versions/0105_drop_legacy_cleanup_tables.py",
        "docs/architecture/retired_runtime_registry.yml",
        "tools/check_retired_runtime_references.py",
        "tests/test_retired_runtime_contract.py",
        "tests/test_retired_runtime_reference_scanner.py",
        "tests/test_legacy_webhook_cleanup.py",
    )

    assert result["unmatched_files"] == []
    assert "retired_runtime_physical_cleanup" in result["matched_scopes"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert "tests/test_repository_ownership_guard.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_critical_read_performance_changes_force_postgres_full_ci() -> None:
    result = _select(
        "aicrm_next/platform/platform_foundation/performance_contracts.py",
        "docs/performance/critical_read_path_baselines.json",
        "tools/check_critical_read_performance.py",
        "migrations/versions/0106_critical_read_path_indexes.py",
        "tests/test_critical_read_performance_runner.py",
    )

    assert result["unmatched_files"] == []
    assert "critical_read_performance" in result["matched_scopes"]
    assert "tests/test_critical_read_performance_contracts.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_query_observability_changes_force_postgres_full_ci() -> None:
    result = _select(
        "aicrm_next/main.py",
        "aicrm_next/platform/shared/db_session.py",
        "aicrm_next/platform/shared/postgres_connection.py",
        "aicrm_next/platform/shared/query_telemetry.py",
        "docs/architecture/runtime_contract_inventory.json",
        "docs/ops/query-observability.md",
        "scripts/ops/summarize_query_observability.py",
        "tests/test_query_observability_summary.py",
        "tests/test_query_telemetry.py",
    )

    assert result["unmatched_files"] == []
    assert "query_observability" in result["matched_scopes"]
    assert "tests/test_query_observability_summary.py" in result["python_tests"]
    assert "tests/test_query_telemetry.py" in result["python_tests"]
    assert "tests/test_critical_read_performance_runner.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_data_health_snapshot_changes_force_postgres_full_ci() -> None:
    result = _select(
        "aicrm_next/insights/data_health/snapshot_repository.py",
        "aicrm_next/insights/data_health/snapshot_service.py",
        "migrations/versions/0143_data_health_snapshot.py",
        "scripts/ops/refresh_data_health_snapshot.py",
        "tests/test_data_health_snapshot.py",
        "tests/test_data_health_snapshot_postgres.py",
    )

    assert result["unmatched_files"] == []
    assert "data_health_snapshot" in result["matched_scopes"]
    assert "tests/test_data_health_snapshot.py" in result["python_tests"]
    assert "tests/test_data_health_snapshot_postgres.py" in result["python_tests"]
    assert "tests/test_database_bootstrap.py" in result["python_tests"]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_internal_worker_owner_diagnostics_uses_execution_runtime_scope() -> None:
    result = _select(
        ".github/workflows/internal-worker-production-diagnostics.yml",
        "deploy/aicrm-internal-worker.service",
        "tests/test_internal_worker_production_workflow.py",
    )

    assert result["unmatched_files"] == []
    assert "postgres_execution_runtime" in result["matched_scopes"]
    assert "ci_deploy" in result["matched_scopes"]
    assert "tests/test_internal_worker_production_workflow.py" in result[
        "python_tests"
    ]
    assert result["needs_postgres"] is True
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_nginx_query_timing_changes_have_permanent_full_ci_scope() -> None:
    result = _select(
        "deploy/nginx-query-timing.conf.example",
        "docs/ops/nginx-query-timing.md",
        "scripts/ops/check_nginx_query_timing_config.py",
        "tests/test_nginx_query_timing_config.py",
    )

    assert result["unmatched_files"] == []
    assert "nginx_query_timing" in result["matched_scopes"]
    assert "tests/test_nginx_query_timing_config.py" in result["python_tests"]
    assert "tests/test_deploy_workflow_contract.py" in result["python_tests"]
    assert result["needs_postgres"] is False
    assert result["needs_full_ci"] is True
    assert result["architecture_gate"] == "full"


def test_job_run_ledger_owner_changes_select_platform_postgres_scope() -> None:
    expected_tests = {
        "tests/test_archive_sync_stale_ledger_reconciliation_postgres.py",
        "tests/test_job_run_ledger_owner.py",
        "tests/test_job_run_ledger_owner_postgres.py",
    }

    for changed_path in (
        "aicrm_next/platform/platform_foundation/job_runs/repository.py",
        *sorted(expected_tests),
    ):
        result = _select(changed_path)
        assert result["unmatched_files"] == []
        assert result["needs_postgres"] is True
        assert result["needs_full_ci"] is True
        assert expected_tests.issubset(result["python_tests"])


def test_channel_write_owner_changes_select_identity_postgres_scope() -> None:
    expected_tests = {
        "tests/test_channel_write_owner.py",
        "tests/test_channel_write_owner_postgres.py",
    }

    for changed_path in (
        "aicrm_next/channels/channel_entry/channel_write_repository.py",
        *sorted(expected_tests),
    ):
        result = _select(changed_path)
        assert result["unmatched_files"] == []
        assert "identity_contact" in result["matched_scopes"]
        assert result["needs_postgres"] is True
        assert result["needs_full_ci"] is True
        assert expected_tests.issubset(result["python_tests"])


def test_wechat_pay_order_owner_changes_select_commerce_postgres_scope() -> None:
    expected_tests = {
        "tests/test_wechat_pay_order_owner.py",
        "tests/test_wechat_pay_order_owner_postgres.py",
    }

    for changed_path in (
        "aicrm_next/extensions/commerce/commerce/wechat_pay_order_write_port.py",
        "aicrm_next/extensions/commerce/commerce/wechat_pay_order_write_repository.py",
        "aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py",
        *sorted(expected_tests),
    ):
        result = _select(changed_path)
        assert result["unmatched_files"] == []
        assert "wechat_pay_order_owner" in result["matched_scopes"]
        assert result["needs_postgres"] is True
        assert result["needs_full_ci"] is True
        assert result["architecture_gate"] == "full"
        assert expected_tests.issubset(result["python_tests"])


def test_rate_scope_cooldown_owner_changes_select_platform_postgres_scope() -> None:
    expected_tests = {
        "tests/test_rate_scope_cooldown_owner.py",
        "tests/test_rate_scope_cooldown_owner_postgres.py",
    }

    for changed_path in (
        "aicrm_next/platform/platform_foundation/rate_scope_cooldown/repository.py",
        *sorted(expected_tests),
    ):
        result = _select(changed_path)
        assert result["unmatched_files"] == []
        assert result["needs_postgres"] is True
        assert result["needs_full_ci"] is True
        assert expected_tests.issubset(result["python_tests"])


def test_unmapped_path_fails_instead_of_falling_back_to_full_regression() -> None:
    completed = subprocess.run(
        [sys.executable, str(SELECTOR), "--changed-file", "aicrm_next/new_context/api.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert "No CI test scope matched" in completed.stderr
    assert "aicrm_next/new_context/api.py" in completed.stderr
