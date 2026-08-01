from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "migrations" / "versions"
NUMERIC_BIND_PATTERN = re.compile(r"(?<![:\\]):[0-9]+")
ALEMBIC_VERSION_NUM_LENGTH = 128


def _literal_assignment(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        target_name = None
        value = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    target_name = target.id
                    value = node.value
                    break
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value = node.value
        if target_name == name and value is not None:
            return ast.literal_eval(value)
    raise AssertionError(f"{name} assignment not found")


def _migration_revisions() -> dict[str, Any]:
    revisions: dict[str, Any] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _literal_assignment(tree, "revision")
        down_revision = _literal_assignment(tree, "down_revision")
        assert revision not in revisions, f"duplicate Alembic revision id {revision}"
        revisions[revision] = {"path": path, "down_revision": down_revision}
    return revisions


def _parents(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (tuple, list)):
        return [str(item) for item in value]
    raise AssertionError(f"unsupported down_revision value: {value!r}")


def test_all_alembic_down_revisions_exist() -> None:
    revisions = _migration_revisions()

    missing = {revision: parent for revision, item in revisions.items() for parent in _parents(item["down_revision"]) if parent not in revisions}

    assert missing == {}


def test_execution_timeline_graph_indexes_are_the_single_head() -> None:
    revisions = _migration_revisions()
    referenced = {parent for item in revisions.values() for parent in _parents(item["down_revision"])}
    heads = set(revisions) - referenced
    repair = VERSIONS / "0123_required_physical_schema_repair.py"
    source = repair.read_text(encoding="utf-8")
    questionnaire_compatibility = (
        VERSIONS / "0124_questionnaire_continuation_jobs_compatibility.py"
    )
    questionnaire_compatibility_source = questionnaire_compatibility.read_text(
        encoding="utf-8"
    )
    audit_repair = VERSIONS / "0124_automation_agent_audit_tables.py"
    audit_source = audit_repair.read_text(encoding="utf-8")

    runtime_correctness = VERSIONS / "0125_execution_runtime_correctness.py"
    runtime_source = runtime_correctness.read_text(encoding="utf-8")

    postgres_runtime = VERSIONS / "0126_postgres_execution_runtime.py"
    postgres_runtime_source = postgres_runtime.read_text(encoding="utf-8")
    group_ops_graph = VERSIONS / "0127_group_ops_durable_effect_graph.py"
    group_ops_graph_source = group_ops_graph.read_text(encoding="utf-8")
    audience_intents = VERSIONS / "0128_ai_audience_refresh_intents.py"
    audience_intents_source = audience_intents.read_text(encoding="utf-8")
    identity_customer = VERSIONS / "0129_identity_customer_event_driven.py"
    identity_customer_source = identity_customer.read_text(encoding="utf-8")
    welcome_media = VERSIONS / "0130_welcome_media_dependencies.py"
    welcome_media_source = welcome_media.read_text(encoding="utf-8")
    continuation_fanout = VERSIONS / "0131_external_effect_continuation_fanout.py"
    continuation_fanout_source = continuation_fanout.read_text(encoding="utf-8")
    external_scope = VERSIONS / "0132_external_claim_scope_policy.py"
    external_scope_source = external_scope.read_text(encoding="utf-8")
    sidebar_timeline = VERSIONS / "0133_sidebar_customer_timeline.py"
    sidebar_timeline_source = sidebar_timeline.read_text(encoding="utf-8")
    timeline_indexes = VERSIONS / "0134_execution_timeline_graph_indexes.py"
    timeline_indexes_source = timeline_indexes.read_text(encoding="utf-8")
    scope_transition = VERSIONS / "0135_queue_scope_transition_audit.py"
    scope_transition_source = scope_transition.read_text(encoding="utf-8")
    validation_soak = VERSIONS / "0136_queue_runtime_validation_soak.py"
    validation_soak_source = validation_soak.read_text(encoding="utf-8")
    production_scope = VERSIONS / "0137_queue_production_scope_cutover.py"
    production_scope_source = production_scope.read_text(encoding="utf-8")
    terminal_acknowledgement = VERSIONS / "0138_queue_terminal_acknowledgement.py"
    terminal_acknowledgement_source = terminal_acknowledgement.read_text(encoding="utf-8")
    terminal_acknowledgement_scope = VERSIONS / "0139_queue_terminal_acknowledgement_scope.py"
    terminal_acknowledgement_scope_source = terminal_acknowledgement_scope.read_text(encoding="utf-8")
    welcome_realtime = VERSIONS / "0140_wecom_welcome_hard_realtime_lanes.py"
    welcome_realtime_source = welcome_realtime.read_text(encoding="utf-8")
    production_welcome_ack_scope = VERSIONS / "0141_production_welcome_timeout_ack_scope.py"
    production_welcome_ack_scope_source = production_welcome_ack_scope.read_text(encoding="utf-8")
    sidebar_recent_message_index = VERSIONS / "0142_sidebar_recent_message_index.py"
    sidebar_recent_message_index_source = sidebar_recent_message_index.read_text(encoding="utf-8")
    data_health_snapshot = VERSIONS / "0143_data_health_snapshot.py"
    data_health_snapshot_source = data_health_snapshot.read_text(encoding="utf-8")
    config_release_control_plane = VERSIONS / "0144_config_release_control_plane.py"
    config_release_control_plane_source = config_release_control_plane.read_text(encoding="utf-8")
    archived_message_search_trgm = VERSIONS / "0145_archived_message_search_trgm.py"
    archived_message_search_trgm_source = archived_message_search_trgm.read_text(encoding="utf-8")
    payment_event_lookup_index = VERSIONS / "0146_wechat_pay_event_lookup_index.py"
    payment_event_lookup_index_source = payment_event_lookup_index.read_text(encoding="utf-8")
    alipay_order_created_index = VERSIONS / "0147_alipay_order_created_index.py"
    alipay_order_created_index_source = alipay_order_created_index.read_text(encoding="utf-8")
    order_source_sort_indexes = VERSIONS / "0148_order_source_sort_indexes.py"
    order_source_sort_indexes_source = order_source_sort_indexes.read_text(encoding="utf-8")
    hxc_projection = VERSIONS / "0149_ai_audience_hxc_projection_foundation.py"
    hxc_projection_source = hxc_projection.read_text(encoding="utf-8")
    identity_cursor_index = VERSIONS / "0150_crm_identity_updated_cursor_index.py"
    identity_cursor_index_source = identity_cursor_index.read_text(encoding="utf-8")
    hxc_projection_view = VERSIONS / "0151_ai_audience_hxc_projection_view.py"
    hxc_projection_view_source = hxc_projection_view.read_text(encoding="utf-8")
    customer_incremental = VERSIONS / "0152_customer_read_model_incremental_foundation.py"
    customer_incremental_source = customer_incremental.read_text(encoding="utf-8")
    customer_generation_slots = VERSIONS / "0153_customer_read_model_generation_slots.py"
    customer_generation_slots_source = customer_generation_slots.read_text(encoding="utf-8")
    internal_event_occurred_index = VERSIONS / "0154_internal_event_occurred_index.py"
    internal_event_occurred_index_source = internal_event_occurred_index.read_text(encoding="utf-8")
    private_message_contact_absence_ack_scope = (
        VERSIONS / "0155_private_message_contact_absence_ack_scope.py"
    )
    private_message_contact_absence_ack_scope_source = (
        private_message_contact_absence_ack_scope.read_text(encoding="utf-8")
    )
    ai_assistant_bulk_lane = VERSIONS / "0157_ai_assistant_bulk_lane.py"
    ai_assistant_bulk_lane_source = ai_assistant_bulk_lane.read_text(encoding="utf-8")
    ai_assistant_item_pipeline = VERSIONS / "0158_ai_assistant_item_pipeline.py"
    ai_assistant_item_pipeline_source = ai_assistant_item_pipeline.read_text(encoding="utf-8")
    ai_automation_lane_rollout_audit = (
        VERSIONS / "0159_ai_automation_lane_rollout_audit.py"
    )
    ai_automation_lane_rollout_audit_source = (
        ai_automation_lane_rollout_audit.read_text(encoding="utf-8")
    )
    ai_audience_wecom_contacts_view_repair = (
        VERSIONS / "0160_ai_audience_wecom_contacts_view_repair.py"
    )
    ai_audience_wecom_contacts_view_repair_source = (
        ai_audience_wecom_contacts_view_repair.read_text(encoding="utf-8")
    )
    archive_ledger_reconciliation = (
        VERSIONS / "0161_reconcile_archive_job_run_ledger.py"
    )
    archive_ledger_reconciliation_source = archive_ledger_reconciliation.read_text(
        encoding="utf-8"
    )

    assert heads == {"0161_reconcile_archive_job_run_ledger"}
    assert revisions["0161_reconcile_archive_job_run_ledger"]["down_revision"] == (
        "0160_ai_audience_wecom_contacts_view_repair"
    )
    assert revisions["0160_ai_audience_wecom_contacts_view_repair"]["down_revision"] == (
        "0159_ai_automation_lane_rollout_audit"
    )
    assert revisions["0159_ai_automation_lane_rollout_audit"]["down_revision"] == (
        "0158_ai_assistant_item_pipeline"
    )
    assert revisions["0158_ai_assistant_item_pipeline"]["down_revision"] == (
        "0157_ai_assistant_bulk_lane"
    )
    assert revisions["0157_ai_assistant_bulk_lane"]["down_revision"] == (
        "0156_campaign_preparation_context"
    )
    assert revisions["0156_campaign_preparation_context"]["down_revision"] == (
        "0155_private_message_contact_absence_ack_scope"
    )
    assert revisions["0155_private_message_contact_absence_ack_scope"]["down_revision"] == (
        "0154_internal_event_occurred_index"
    )
    assert revisions["0154_internal_event_occurred_index"]["down_revision"] == "0153_customer_read_model_generation_slots"
    assert revisions["0153_customer_read_model_generation_slots"]["down_revision"] == "0152_customer_read_model_incremental"
    assert revisions["0152_customer_read_model_incremental"]["down_revision"] == "0151_ai_audience_hxc_projection_view"
    assert revisions["0151_ai_audience_hxc_projection_view"]["down_revision"] == "0150_crm_identity_updated_cursor_index"
    assert revisions["0150_crm_identity_updated_cursor_index"]["down_revision"] == "0149_ai_audience_hxc_projection"
    assert revisions["0149_ai_audience_hxc_projection"]["down_revision"] == "0148_order_source_sort_indexes"
    assert revisions["0148_order_source_sort_indexes"]["down_revision"] == "0147_alipay_order_created_index"
    assert revisions["0147_alipay_order_created_index"]["down_revision"] == "0146_wechat_pay_event_lookup_index"
    assert revisions["0146_wechat_pay_event_lookup_index"]["down_revision"] == "0145_archived_message_search_trgm"
    assert revisions["0145_archived_message_search_trgm"]["down_revision"] == "0144_config_release_control_plane"
    assert revisions["0144_config_release_control_plane"]["down_revision"] == "0143_data_health_snapshot"
    assert revisions["0143_data_health_snapshot"]["down_revision"] == "0142_sidebar_recent_message_index"
    assert revisions["0142_sidebar_recent_message_index"]["down_revision"] == "0141_production_welcome_timeout_ack_scope"
    assert revisions["0141_production_welcome_timeout_ack_scope"]["down_revision"] == "0140_wecom_welcome_hard_realtime_lanes"
    assert revisions["0140_wecom_welcome_hard_realtime_lanes"]["down_revision"] == "0139_queue_terminal_acknowledgement_scope"
    assert revisions["0139_queue_terminal_acknowledgement_scope"]["down_revision"] == "0138_queue_terminal_acknowledgement"
    assert revisions["0138_queue_terminal_acknowledgement"]["down_revision"] == "0137_queue_production_scope_cutover"
    assert revisions["0137_queue_production_scope_cutover"]["down_revision"] == "0136_queue_runtime_validation_soak"
    assert revisions["0136_queue_runtime_validation_soak"]["down_revision"] == "0135_queue_scope_transition_audit"
    assert revisions["0135_queue_scope_transition_audit"]["down_revision"] == "0134_execution_timeline_graph_indexes"
    assert revisions["0134_execution_timeline_graph_indexes"]["down_revision"] == "0133_sidebar_customer_timeline"
    assert revisions["0133_sidebar_customer_timeline"]["down_revision"] == "0132_external_claim_scope_policy"
    assert revisions["0132_external_claim_scope_policy"]["down_revision"] == "0131_external_effect_continuation_fanout"
    assert revisions["0131_external_effect_continuation_fanout"]["down_revision"] == "0130_welcome_media_dependencies"
    assert revisions["0130_welcome_media_dependencies"]["down_revision"] == "0129_identity_customer_event_driven"
    assert revisions["0129_identity_customer_event_driven"]["down_revision"] == "0128_ai_audience_refresh_intents"
    assert revisions["0128_ai_audience_refresh_intents"]["down_revision"] == "0127_group_ops_durable_effect_graph"
    assert revisions["0127_group_ops_durable_effect_graph"]["down_revision"] == "0126_postgres_execution_runtime"
    assert revisions["0126_postgres_execution_runtime"]["down_revision"] == "0125_execution_runtime_correctness"
    assert revisions["0125_execution_runtime_correctness"]["down_revision"] == "0124_agent_audit_tables"
    assert revisions["0124_agent_audit_tables"]["down_revision"] == "0124_questionnaire_continuation_jobs"
    assert revisions["0124_questionnaire_continuation_jobs"]["down_revision"] == "0123_required_physical_schema_repair"
    assert revisions["0123_required_physical_schema_repair"]["down_revision"] == "0122_internal_event_fanout_manifest"
    assert "from alembic import op" not in questionnaire_compatibility_source
    assert "CREATE TABLE" not in questionnaire_compatibility_source
    assert "ALTER TABLE" not in questionnaire_compatibility_source
    assert "CREATE TABLE IF NOT EXISTS config_releases" in config_release_control_plane_source
    assert "CREATE TABLE IF NOT EXISTS deployment_profile_state" in config_release_control_plane_source
    assert "uq_config_releases_profile_published" in config_release_control_plane_source
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in archived_message_search_trgm_source
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in archived_message_search_trgm_source
    assert "ix_archived_messages_content_trgm" in archived_message_search_trgm_source
    assert "content gin_trgm_ops" in archived_message_search_trgm_source
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in archived_message_search_trgm_source
    assert "CREATE INDEX CONCURRENTLY" in payment_event_lookup_index_source
    assert "ix_wechat_pay_order_events_trade_created_id" in payment_event_lookup_index_source
    assert "(out_trade_no, created_at DESC, id DESC)" in payment_event_lookup_index_source
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in payment_event_lookup_index_source
    assert "CREATE INDEX CONCURRENTLY" in alipay_order_created_index_source
    assert "ix_alipay_pay_orders_created_id" in alipay_order_created_index_source
    assert "(created_at DESC, id DESC)" in alipay_order_created_index_source
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in alipay_order_created_index_source
    assert "CREATE INDEX CONCURRENTLY" in internal_event_occurred_index_source
    assert "idx_internal_event_occurred_id" in internal_event_occurred_index_source
    assert "(occurred_at DESC, id DESC)" in internal_event_occurred_index_source
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in internal_event_occurred_index_source
    assert "CREATE INDEX CONCURRENTLY" in order_source_sort_indexes_source
    assert "idx_wechat_pay_orders_created" in order_source_sort_indexes_source
    assert "idx_wechat_shop_orders_provider_created" in order_source_sort_indexes_source
    assert "provider, created_at DESC, id DESC" in order_source_sort_indexes_source
    assert "A release rollback must not delete pre-existing performance protection" in order_source_sort_indexes_source
    assert "CREATE TABLE IF NOT EXISTS ai_audience_hxc_member_usage_projection" in hxc_projection_source
    assert "active_generation BIGINT NOT NULL DEFAULT 0" in hxc_projection_source
    assert "idx_ai_audience_hxc_projection_member_unused" in hxc_projection_source
    assert "CREATE OR REPLACE VIEW audience_read.huangxiaocan_member_usage_status_v1" not in hxc_projection_source
    assert "external_userid TEXT" not in hxc_projection_source
    assert "CREATE INDEX CONCURRENTLY" in identity_cursor_index_source
    assert "idx_crm_user_identity_updated_unionid" in identity_cursor_index_source
    assert "(updated_at, unionid)" in identity_cursor_index_source
    assert "DROP INDEX" not in identity_cursor_index_source
    assert "CREATE OR REPLACE VIEW audience_read.huangxiaocan_member_usage_status_v1" in hxc_projection_view_source
    assert "projection.generation = (" in hxc_projection_view_source
    assert "SELECT control.active_generation" in hxc_projection_view_source
    assert "JOIN LATERAL" in hxc_projection_view_source
    assert "wecom_external_contact_identity_map identity_map" in hxc_projection_view_source
    assert "audience_read.wecom_contacts_v1" not in hxc_projection_view_source.split("def downgrade()", 1)[0]
    assert "user_ops_hxc_dashboard_snapshot" not in hxc_projection_view_source.split("def downgrade()", 1)[0]
    assert "DROP TABLE" not in hxc_projection_view_source
    assert "source_event_id TEXT NOT NULL DEFAULT ''" in customer_incremental_source
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in customer_incremental_source
    assert "CREATE INDEX CONCURRENTLY" in customer_incremental_source
    assert "uq_customer_list_index_next_unionid" in customer_incremental_source
    assert "uq_customer_detail_snapshot_next_unionid" in customer_incremental_source
    assert "idx_customer_refresh_source_generation_id" in customer_incremental_source
    assert "pg_get_serial_sequence" in customer_incremental_source
    assert "DROP INDEX" not in customer_incremental_source
    assert "DROP COLUMN" not in customer_incremental_source
    assert "CREATE TABLE IF NOT EXISTS customer_list_index_next_shadow" in customer_generation_slots_source
    assert "CREATE TABLE IF NOT EXISTS customer_detail_snapshot_next_shadow" in customer_generation_slots_source
    assert "CREATE TABLE IF NOT EXISTS customer_recent_message_next_shadow" in customer_generation_slots_source
    assert "active_slot TEXT NOT NULL DEFAULT 'primary'" in customer_generation_slots_source
    assert "active_generation BIGINT NOT NULL DEFAULT 1" in customer_generation_slots_source
    assert "INDEX CONCURRENTLY IF NOT EXISTS" in customer_generation_slots_source
    assert "uq_customer_list_index_next_shadow_unionid" in customer_generation_slots_source
    assert "DROP TABLE" not in customer_generation_slots_source
    assert "DROP COLUMN" not in customer_generation_slots_source
    assert "0018_hxc_dashboard_broadcast_tasks" in source
    assert "CREATE TABLE IF NOT EXISTS data_health_snapshot" in data_health_snapshot_source
    assert "CHECK (singleton IS TRUE)" in data_health_snapshot_source
    assert "0023_group_ops_webhook_rules" in source
    assert "0028_owner_migration_excel_sessions" in source
    assert "def downgrade()" in source
    assert "return None" in source
    assert "CREATE TABLE IF NOT EXISTS automation_agent_output" in audit_source
    assert "CREATE TABLE IF NOT EXISTS automation_agent_llm_call_log" in audit_source
    assert "idx_automation_agent_output_run_created" in audit_source
    assert "queue_runtime_scope_transition_audit" in scope_transition_source
    assert "queue_runtime_canary_config_audit" in scope_transition_source
    assert "aicrm_reject_queue_runtime_audit_mutation" in scope_transition_source
    assert "queue_runtime_validation_evidence" in validation_soak_source
    assert "queue_runtime_lease_recovery_event" in validation_soak_source
    assert "queue_runtime_soak_run" in validation_soak_source
    assert "queue_runtime_soak_snapshot" in validation_soak_source
    assert "trg_queue_runtime_validation_evidence_append_only" in validation_soak_source
    assert "trg_queue_runtime_lease_recovery_event_append_only" in validation_soak_source
    assert "trg_queue_runtime_soak_snapshot_append_only" in validation_soak_source
    assert "to_scope IN ('test_loopback', 'allowlisted', 'all')" in production_scope_source
    assert "external_claim_scope IN ('allowlisted', 'all')" in production_scope_source
    assert "queue_terminal_acknowledgement" in terminal_acknowledgement_source
    assert "pre_cutover_welcome_41050_no_replay" in terminal_acknowledgement_source
    assert "provider_success_claimed IS FALSE" in terminal_acknowledgement_source
    assert "replay_prohibited IS TRUE" in terminal_acknowledgement_source
    assert "trg_queue_terminal_ack_append_only" in terminal_acknowledgement_source
    assert "trg_queue_terminal_ack_reject_truncate" in terminal_acknowledgement_source
    assert "production_private_message_84061_no_replay" in terminal_acknowledgement_scope_source
    assert "production_wechat_refund_not_enough_no_replay" in terminal_acknowledgement_scope_source
    assert "ALTER COLUMN graph_id DROP NOT NULL" in terminal_acknowledgement_scope_source
    assert "def downgrade()" in terminal_acknowledgement_scope_source
    assert "wecom_welcome_ingress" in welcome_realtime_source
    assert "wecom_welcome" in welcome_realtime_source
    assert "production_welcome_41050_job_2157_no_replay" in production_welcome_ack_scope_source
    assert (
        "production_private_message_contact_absence_20260728_no_replay"
        in private_message_contact_absence_ack_scope_source
    )
    assert "external_contact_relationship_absent" in private_message_contact_absence_ack_scope_source
    assert "def downgrade()" in private_message_contact_absence_ack_scope_source
    assert "wecom_ai_assistant_bulk" in ai_assistant_bulk_lane_source
    assert "INITIAL_CAPACITY = 4" in ai_assistant_bulk_lane_source
    assert "MAX_RESERVED_CAPACITY = 24" in ai_assistant_bulk_lane_source
    assert "'blocked'" in ai_assistant_bulk_lane_source
    assert "ai_generation" in ai_assistant_item_pipeline_source
    assert "INITIAL_CAPACITY = 4" in ai_assistant_item_pipeline_source
    assert "MAX_RESERVED_CAPACITY = 64" in ai_assistant_item_pipeline_source
    assert "agent_config_snapshot_json" in ai_assistant_item_pipeline_source
    assert "generation_effect_job_id" in ai_assistant_item_pipeline_source
    assert "prevents destructive downgrade" in ai_assistant_item_pipeline_source
    assert "queue_lane_rollout_audit" in ai_automation_lane_rollout_audit_source
    assert "0057_huangyoucan_unregistered_ai_audience" in ai_audience_wecom_contacts_view_repair_source
    assert "_refresh_wecom_contacts_view" in ai_audience_wecom_contacts_view_repair_source
    assert "ensure_schema=False" in ai_audience_wecom_contacts_view_repair_source
    assert "DROP VIEW" not in ai_audience_wecom_contacts_view_repair_source
    assert "job_run_ledger_finish_failed_reconciled" in archive_ledger_reconciliation_source
    assert "job_run_finish_timestamptz_parameter_type_mismatch" in archive_ledger_reconciliation_source
    assert "core_sync_outcome', 'unknown'" in archive_ledger_reconciliation_source
    assert "created_at < CURRENT_TIMESTAMP - INTERVAL '2 minutes'" in archive_ledger_reconciliation_source
    assert "Reopening reconciled audit records as running" in archive_ledger_reconciliation_source
    assert "aicrm_reject_queue_runtime_audit_mutation" in ai_automation_lane_rollout_audit_source
    assert "global_max_in_flight = global_max_in_flight +" in welcome_realtime_source
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in sidebar_recent_message_index_source
    assert "ix_customer_recent_message_next_unionid_time_id" in sidebar_recent_message_index_source
    assert "(unionid, send_time DESC, id DESC)" in sidebar_recent_message_index_source
    assert "ix_automation_agent_output_unionid" in audit_source
    assert "idx_automation_agent_llm_call_log_agent_created" in audit_source
    assert "FOREIGN KEY" not in audit_source
    assert "return None" in audit_source
    assert "row_version BIGINT NOT NULL DEFAULT 1" in runtime_source
    assert "cancel_requested_at TIMESTAMPTZ" in runtime_source
    assert "uq_external_effect_attempt_open_job" in runtime_source
    assert "'dispatching'" in runtime_source
    assert "claim_enabled BOOLEAN NOT NULL DEFAULT FALSE" in postgres_runtime_source
    assert "automation_group_ops_effect_dependency" in group_ops_graph_source
    assert "'PR-2 installs in claimless standby mode'" in postgres_runtime_source
    assert '"outbound_webhook": 4' in postgres_runtime_source
    assert 'mode = "blocked" if lane == "outbound_webhook" else "standby"' in postgres_runtime_source
    assert "ALTER COLUMN available_at SET NOT NULL" in postgres_runtime_source
    assert "ALTER COLUMN available_at SET DEFAULT CURRENT_TIMESTAMP" in postgres_runtime_source
    assert "historical_freeze_orphan" in postgres_runtime_source
    assert "BEFORE UPDATE OR DELETE ON queue_policy_snapshot" in postgres_runtime_source
    assert "aicrm_reject_queue_policy_snapshot_mutation" in postgres_runtime_source
    assert "CREATE TABLE IF NOT EXISTS ai_audience_refresh_intent" in audience_intents_source
    assert "CREATE TABLE IF NOT EXISTS ai_audience_refresh_source_receipt" in audience_intents_source
    assert "identity_resolution_completion_receipt" in identity_customer_source
    assert "customer_read_model_refresh_intent" in identity_customer_source
    assert "customer_read_model_refresh_source_receipt" in identity_customer_source
    assert "provider_result_json" in identity_customer_source
    assert "pre_event_driven_cutover_requires_manual_classification" in identity_customer_source
    assert "channel_welcome_effect_graph" in welcome_media_source
    assert "channel_welcome_effect_dependency" in welcome_media_source
    assert "pre_independent_continuation_fanout_requires_manual_classification" in continuation_fanout_source
    assert "external_effect_completion_continuation_consumer" in continuation_fanout_source
    assert "external_claim_scope" in external_scope_source
    assert "uq_customer_timeline_event_next_event_id" in sidebar_timeline_source
    assert "ix_customer_timeline_event_next_unionid_time_id" in sidebar_timeline_source
    assert "ROW_NUMBER() OVER" in sidebar_timeline_source
    assert "queue-v2-test-loopback" in external_scope_source
    assert "execution timeline index migration requires standby generation 0" in timeline_indexes_source
    for index_name in (
        "idx_external_effect_parent_execution",
        "idx_internal_event_parent_execution",
        "idx_internal_run_parent_execution",
        "idx_internal_outbox_parent_execution",
        "idx_webhook_inbox_parent_execution",
    ):
        assert index_name in timeline_indexes_source
    assert "CREATE INDEX IF NOT EXISTS {index_name}" in timeline_indexes_source
    assert "DROP INDEX IF EXISTS {index_name}" in timeline_indexes_source
    for constraint_name in (
        "ck_external_effect_job_runtime_lane",
        "ck_internal_event_consumer_run_runtime_lane",
        "ck_internal_event_outbox_runtime_lane",
        "ck_webhook_inbox_runtime_lane",
    ):
        assert constraint_name in postgres_runtime_source
    for index_name in (
        "idx_internal_outbox_ordering_active",
        "idx_webhook_inbox_ordering_active",
    ):
        assert f"CREATE INDEX IF NOT EXISTS {index_name}" in postgres_runtime_source
        assert postgres_runtime_source.count(index_name) >= 2
    for table_name in (
        "queue_runtime_control",
        "queue_lane_policy",
        "queue_policy_snapshot",
        "queue_fairness_cursor",
        "queue_rate_scope_cooldown",
        "queue_worker_heartbeat",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in postgres_runtime_source


def test_alembic_revision_storage_supports_deployed_revision_ids() -> None:
    revisions = _migration_revisions()
    old_hxc_revision = "0012_hxc_dashboard_v6_" + "growth_columns"
    old_cloud_revision = "0024_cloud_plan_recipient_" + "approval"
    old_owner_revision = "0028_owner_migration_excel_" + "sessions"
    old_wechat_unionid_revision = "0029_wechat_pay_order_" + "unionid_index"

    beyond_runtime_limit = {
        revision: {"length": len(revision), "path": str(item["path"])} for revision, item in revisions.items() if len(revision) > ALEMBIC_VERSION_NUM_LENGTH
    }
    alembic_env = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")

    assert beyond_runtime_limit == {}
    assert f"ALEMBIC_VERSION_NUM_LENGTH = {ALEMBIC_VERSION_NUM_LENGTH}" in alembic_env
    assert "CREATE TABLE IF NOT EXISTS alembic_version" in alembic_env
    assert "ALTER COLUMN version_num TYPE VARCHAR" in alembic_env
    assert old_hxc_revision not in revisions
    assert old_cloud_revision not in revisions
    assert old_owner_revision not in revisions
    assert old_wechat_unionid_revision not in revisions
    assert "0012_hxc_growth_cols" in revisions
    assert "0024_cloud_plan_approval" in revisions
    assert "0028_owner_excel_sessions" in revisions
    assert "0029_user_ops_prod_tables" in revisions
    assert "0030_wechat_pay_unionid_idx" in revisions
    assert "0032_miniprogram_only_resend_20260611" in revisions
    assert "0033_complete_miniprogram_only_resend_20260611" in revisions


def test_alembic_chain_keeps_0014_parent_available() -> None:
    revisions = _migration_revisions()

    assert "0013" in revisions
    assert revisions["0014"]["down_revision"] == "0013"
    assert revisions["0013"]["down_revision"] == "0012_wechat_pay_products"


def test_user_ops_production_tables_migration_is_parent_of_wechat_unionid_index() -> None:
    revisions = _migration_revisions()

    assert revisions["0029_user_ops_prod_tables"]["down_revision"] == "0028_owner_excel_sessions"
    assert revisions["0030_wechat_pay_unionid_idx"]["down_revision"] == "0029_user_ops_prod_tables"


def test_miniprogram_reset_migration_preserves_broadcast_job_claim_token_not_null_contract() -> None:
    source = (VERSIONS / "0034_reset_miniprogram_only_material_jobs_20260611.py").read_text(encoding="utf-8")

    assert "claim_token TEXT NOT NULL DEFAULT ''" in (VERSIONS / "0012_broadcast_job_leases.py").read_text(encoding="utf-8")
    assert "claim_token = ''" in source
    assert "claim_token = NULL" not in source


def test_perf_index_migration_does_not_require_retired_conversion_table_on_fresh_db() -> None:
    source = (VERSIONS / "0002_perf_indexes_and_trace.py").read_text(encoding="utf-8")

    assert 'if _has_table("conversion_dispatch_log"):' in source
    assert "CREATE INDEX IF NOT EXISTS idx_conversion_dispatch_log_external_dispatched" in source
    assert "DO $$" not in source


def test_member_segment_migration_does_not_recreate_retired_member_table_on_fresh_db() -> None:
    source = (VERSIONS / "0003_member_segment_columns.py").read_text(encoding="utf-8")

    assert 'if not _has_table("automation_member"):' in source
    assert "return" in source
    assert "CREATE TABLE automation_member" not in source
    assert "to_regclass" not in source


def test_cloud_orchestrator_migration_skips_legacy_automation_tables_on_fresh_db() -> None:
    source = (VERSIONS / "0004_cloud_orchestrator.py").read_text(encoding="utf-8")

    assert '_create_index_if_table_exists(\n        "automation_touch_delivery_log"' in source
    assert '_create_index_if_table_exists(\n        "outbound_tasks"' in source
    assert 'and _has_table("automation_touch_delivery_log")' in source
    assert 'and _has_table("automation_ai_push_log")' in source
    assert "CREATE TABLE automation_member" not in source
    assert "to_regclass" not in source


def test_miniprogram_library_migration_skips_missing_sop_template_on_fresh_db() -> None:
    source = (VERSIONS / "0006_miniprogram_library.py").read_text(encoding="utf-8")

    assert "def _has_table" in source
    assert "if _has_table(table) and not _has_column(table, column_name):" in source
    assert 'if _has_table("automation_sop_template"):' in source
    assert "CREATE TABLE automation_sop_template" not in source


def test_radar_pdf_preview_migration_keeps_foreign_keys_optional_for_fresh_db() -> None:
    source = (VERSIONS / "0025_radar_pdf_preview_assets.py").read_text(encoding="utf-8")

    assert 'if _has_table("radar_links") else ""' in source
    assert "radar_link_id BIGINT{link_reference}" in source
    assert "link_id BIGINT NOT NULL{link_reference}" in source


def test_group_ops_admin_userids_migration_skips_legacy_group_chats_on_fresh_db() -> None:
    source = (VERSIONS / "0027_group_ops_admin_userids.py").read_text(encoding="utf-8")

    assert 'if not _has_table("wecom_group_chat_snapshots"):' in source
    assert 'if not _has_table("group_chats"):' in source
    assert "FROM group_chats" in source


def test_wechat_pay_unionid_index_migration_skips_missing_legacy_orders_on_fresh_db() -> None:
    source = (VERSIONS / "0030_wechat_pay_unionid_idx.py").read_text(encoding="utf-8")

    assert 'if not _has_table("wechat_pay_orders"):' in source
    assert "idx_wechat_pay_orders_unionid_created" in source


def test_channel_multi_staff_migration_keeps_channel_foreign_key_optional_for_fresh_db() -> None:
    source = (VERSIONS / "0036_channel_multi_staff_assignment.py").read_text(encoding="utf-8")

    assert 'if _has_table("automation_channel") else ""' in source
    assert "channel_id BIGINT NOT NULL__CHANNEL_REFERENCE__" in source
    assert '.replace("__CHANNEL_REFERENCE__", channel_reference)' in source
    assert "CREATE TABLE IF NOT EXISTS automation_channel_assignee" in source
    assert "CREATE TABLE IF NOT EXISTS automation_channel_assignment_event" in source


def test_raw_migration_sql_does_not_expose_numeric_bind_literals() -> None:
    risky_default_prefix = '"default"' + ":"
    old_sqlalchemy_rendered_default = "default" + "%("
    raw_sql_strings: list[tuple[Path, int, str]] = []

    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            raw_sql_strings.append((path, node.lineno, node.args[0].value))

    numeric_bind_risks = {
        f"{path.relative_to(ROOT)}:{lineno}": NUMERIC_BIND_PATTERN.findall(sql) for path, lineno, sql in raw_sql_strings if NUMERIC_BIND_PATTERN.search(sql)
    }
    raw_json_default_risks = {
        f"{path.relative_to(ROOT)}:{lineno}": sql
        for path, lineno, sql in raw_sql_strings
        if any(f"{risky_default_prefix}{value}" in sql for value in ("30", "3", "1")) or old_sqlalchemy_rendered_default in sql
    }

    assert numeric_bind_risks == {}
    assert raw_json_default_risks == {}

    group_ops_migration = VERSIONS / "0023_group_ops_webhook_rules.py"
    source = group_ops_migration.read_text(encoding="utf-8")
    assert "builtin:has_used_core_feature" in source
    assert '"default"' + ":30" not in source
    assert '"default"' + ":3" not in source
    assert old_sqlalchemy_rendered_default not in source


def test_alembic_commands_can_walk_revision_graph() -> None:
    for args in (("heads",), ("history", "--verbose")):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "is not present" not in result.stderr
        assert "KeyError" not in result.stderr
        if args == ("heads",):
            heads = [line for line in result.stdout.splitlines() if "(head)" in line]
            assert len(heads) == 1


def test_deployed_webhook_inbox_revision_is_merged_into_current_head() -> None:
    revisions = _migration_revisions()

    assert revisions["0054_webhook_inbox"]["down_revision"] is None
    assert set(revisions["0058_merge_webhook_inbox_and_huangyoucan_audience"]["down_revision"]) == {
        "0054_webhook_inbox",
        "0057_huangyoucan_unregistered_ai_audience",
    }
    assert revisions["0059_ai_audience_simple_sql_runtime"]["down_revision"] == "0058_merge_webhook_inbox_and_huangyoucan_audience"
    assert revisions["0060_ai_audience_hxc_member_usage_view"]["down_revision"] == "0059_ai_audience_simple_sql_runtime"


def test_legacy_webhook_retirement_migration_does_not_delete_history_data() -> None:
    source = (VERSIONS / "0044_retire_legacy_webhook_deprecations.py").read_text(encoding="utf-8")

    assert "history_data_deleted" in source
    assert "physical_delete" in source
    assert "DELETE FROM legacy_webhook_cleanup_audit" in source
    assert "DROP TABLE" not in source
