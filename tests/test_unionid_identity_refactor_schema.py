from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unionid_identity_migration_creates_foundation_tables() -> None:
    source = _read("migrations/versions/0062_unionid_identity_refactor.py")

    assert "CREATE TABLE IF NOT EXISTS crm_user_identity" in source
    assert "unionid TEXT PRIMARY KEY" in source
    for column in [
        "mobile_normalized TEXT NOT NULL DEFAULT ''",
        "mobile_verified BOOLEAN NOT NULL DEFAULT FALSE",
        "customer_name TEXT NOT NULL DEFAULT ''",
        "primary_owner_userid TEXT NOT NULL DEFAULT ''",
        "legacy_person_id TEXT NOT NULL DEFAULT ''",
        "identity_status TEXT NOT NULL DEFAULT 'active'",
        "next_poll_at TIMESTAMPTZ",
    ]:
        assert column in source
    assert "CREATE TABLE IF NOT EXISTS crm_user_identity_resolution_queue" in source
    for column in [
        "source_table TEXT NOT NULL DEFAULT ''",
        "source_id TEXT NOT NULL DEFAULT ''",
        "raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "resolved_unionid TEXT NOT NULL DEFAULT ''",
        "conflict_reason TEXT NOT NULL DEFAULT ''",
        "attempt_count INTEGER NOT NULL DEFAULT 0",
        "resolved_at TIMESTAMPTZ",
    ]:
        assert column in source
    assert "CREATE TABLE IF NOT EXISTS crm_user_identity_conflicts" in source
    for column in [
        "external_userid TEXT NOT NULL DEFAULT ''",
        "openid TEXT NOT NULL DEFAULT ''",
        "mobile TEXT NOT NULL DEFAULT ''",
        "source_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "resolution_status TEXT NOT NULL DEFAULT 'open'",
        "resolved_at TIMESTAMPTZ",
    ]:
        assert column in source
    assert "CREATE TABLE IF NOT EXISTS crm_user_identity_merge_audit" in source
    assert "missing_unionid" in source


def test_business_table_migration_adds_unionid_foundation_columns() -> None:
    source = _read("migrations/versions/0063_unionid_business_table_foundation.py")

    for table_name in [
        "alipay_pay_orders",
        "customer_list_index_next",
        "customer_detail_snapshot_next",
        "customer_timeline_event_next",
        "customer_recent_message_next",
    ]:
        assert table_name in source
    assert "ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT ''" in source
    assert "jsonb_exists(cui.external_userids_json" in source
    assert "INSERT INTO crm_user_identity_resolution_queue" in source
    assert "'customer_read_model_migration'" in source
    assert "DROP COLUMN IF EXISTS {column_name}" in source
    for table_name in [
        "customer_list_index_next",
        "customer_detail_snapshot_next",
        "customer_timeline_event_next",
        "customer_recent_message_next",
    ]:
        assert table_name in source


def test_ops_automation_migration_adds_unionid_targets() -> None:
    source = _read("migrations/versions/0064_unionid_ops_automation_foundation.py")

    for table_name in [
        "contact_tags",
        "archived_messages",
        "class_user_status_current",
        "user_ops_pool_current_next",
        "user_ops_do_not_disturb_next",
        "automation_channel_contact",
        "automation_event_v2",
        "automation_membership_v2",
        "ai_audience_member_current",
        "ai_audience_member_event",
    ]:
        assert table_name in source
    assert "ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT ''" in source
    assert "target_unionids_json JSONB NOT NULL DEFAULT '[]'::jsonb" in source
    assert "jsonb_exists(cui.external_userids_json" in source
    assert "uq_automation_channel_contact_channel_unionid" in source
    assert "ALTER TABLE IF EXISTS automation_channel_contact DROP COLUMN IF EXISTS external_contact_id" in source


def test_contact_tags_mirror_has_fresh_schema_table() -> None:
    source = _read("migrations/versions/0080_create_contact_tags_mirror.py")
    manifest = _read("docs/architecture/data_table_lifecycle_manifest.yml")

    assert "CREATE TABLE IF NOT EXISTS contact_tags" in source
    for column in [
        "unionid TEXT NOT NULL DEFAULT ''",
        "userid TEXT NOT NULL DEFAULT ''",
        "tag_id TEXT NOT NULL DEFAULT ''",
        "tag_name TEXT NOT NULL DEFAULT ''",
        "raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb",
    ]:
        assert column in source
    assert "external_userid" not in source
    assert "uq_contact_tags_unionid_userid_tag_id" in source
    assert "contact_tags:" in manifest
    assert "migration_source: 0080_create_contact_tags_mirror" in manifest


def test_customer_status_baseline_tables_exist_in_fresh_schema() -> None:
    source = _read("migrations/versions/0081_create_customer_status_baseline_tables.py")
    manifest = _read("docs/architecture/data_table_lifecycle_manifest.yml")

    for table_name in ["class_user_status_current", "class_user_status_history", "owner_role_map"]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in source
        assert f"{table_name}:" in manifest
        assert "migration_source: 0081_create_customer_status_baseline_tables" in manifest

    assert "unionid TEXT PRIMARY KEY" in source
    assert "unionid TEXT NOT NULL DEFAULT ''" in source
    assert "userid TEXT PRIMARY KEY" in source
    for required in [
        "owner_userid_snapshot TEXT NOT NULL DEFAULT ''",
        "customer_name_snapshot TEXT NOT NULL DEFAULT ''",
        "signup_status TEXT NOT NULL DEFAULT ''",
        "signup_label_name TEXT NOT NULL DEFAULT ''",
        "status_flags_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "display_name TEXT NOT NULL DEFAULT ''",
    ]:
        assert required in source
    assert "external_userid" not in source
    assert "mobile_snapshot" not in source


def test_marketing_automation_config_tables_exist_in_fresh_schema() -> None:
    source = _read("migrations/versions/0083_create_marketing_automation_config_tables.py")
    manifest = _read("docs/architecture/data_table_lifecycle_manifest.yml")

    for table_name in ["marketing_automation_configs", "marketing_automation_question_rules"]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in source
        assert f"{table_name}:" in manifest

    assert "external_userid" not in source
    assert "openid" not in source
    assert "unionid" not in source
    assert "uq_marketing_automation_configs_key" in source
    assert "ix_marketing_automation_question_rules_config_active" in source


def test_id_dev_p1_baseline_tables_exist_in_fresh_schema() -> None:
    source = _read("migrations/versions/0084_id_dev_p1_baseline_tables.py")
    manifest = _read("docs/architecture/data_table_lifecycle_manifest.yml")

    for table_name in ["app_settings", "mcp_tool_settings", "wechat_shop_order_events"]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in source
        assert f"{table_name}:" in manifest
        assert "migration_source: 0084_id_dev_p1_baseline_tables" in manifest

    for required in [
        "ALTER TABLE IF EXISTS outbound_tasks ADD COLUMN IF NOT EXISTS task_type TEXT NOT NULL DEFAULT 'outbound_task'",
        "key TEXT PRIMARY KEY",
        "tool_name TEXT PRIMARY KEY",
        "raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "process_status TEXT NOT NULL DEFAULT 'received'",
        "ux_wechat_shop_order_events_source",
        "ix_wechat_shop_order_events_order_created",
    ]:
        assert required in source
    assert "external_userid" not in source
    assert "openid" not in source
    assert "mobile" not in source


def test_admin_config_audit_baseline_tables_exist_in_fresh_schema() -> None:
    source = _read("migrations/versions/0085_admin_config_audit_baseline.py")
    manifest = _read("docs/architecture/data_table_lifecycle_manifest.yml")
    admin_audit_owner_source = _read("aicrm_next/platform/platform_foundation/admin_audit/repository.py")

    for table_name in ["admin_operation_logs", "admin_users", "admin_user_roles", "admin_login_audit"]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in source
        assert f"{table_name}:" in manifest

    for required in [
        "before_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "after_json JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ux_admin_users_wecom_userid",
        "ux_admin_user_roles_user_role",
        "ix_admin_login_audit_user_created",
        "migration_source: 0085_admin_config_audit_baseline",
    ]:
        assert required in source or required in manifest
    assert "CAST(:{name} AS jsonb)" in admin_audit_owner_source
    assert 'json_expression.format(name="before_json")' in admin_audit_owner_source
    assert 'json_expression.format(name="after_json")' in admin_audit_owner_source


def test_wecom_identity_bridge_writes_new_identity_tables_not_legacy_maps() -> None:
    source = _read("aicrm_next/channels/channel_entry/identity_bridge_repo.py")

    assert "INSERT INTO crm_user_identity" in source
    assert "INSERT INTO crm_user_identity_resolution_queue" in source
    assert "INSERT INTO wecom_external_contact_identity_map" not in source
    assert "INSERT INTO wecom_external_contact_follow_users" not in source
    for legacy in [
        "external_contact_bindings",
        "INSERT INTO people",
        "FROM people",
        "FROM contacts",
        "INSERT INTO external_contact_bindings",
        "UPDATE external_contact_bindings",
    ]:
        assert legacy not in source
    questionnaire_backfill = source.split("def backfill_questionnaire_submissions_for_mobile_binding", 1)[1].split(
        "def build_identity_bridge_repository", 1
    )[0]
    assert "questionnaire_submissions_unionid_only" in questionnaire_backfill
    assert "UPDATE questionnaire_submissions" not in questionnaire_backfill
    assert "mobile_snapshot" not in questionnaire_backfill
    assert "openid = ANY" not in questionnaire_backfill


def test_runtime_jsonb_membership_avoids_placeholder_collision() -> None:
    source = _read("aicrm_next/crm/identity_contact/resolver.py")

    assert "external_userids_json ? ?" not in source
    assert "identity.external_userids_json ? input.external_userid" not in source
    assert "identity.external_userids_json @> jsonb_build_array(input.external_userid)" in source
    assert "identity.openids_json @> jsonb_build_array(input.openid)" in source
    assert 'candidate_sql = _candidate_sql(self._placeholder, fields=fields)' in source


def test_questionnaire_postgres_submit_queues_unresolved_identity_without_fake_canonical() -> None:
    source = _read("aicrm_next/extensions/forms/questionnaire/repo.py")
    queue_source = _read("aicrm_next/extensions/forms/questionnaire/identity_resolution.py")
    owner_source = _read("aicrm_next/crm/identity_contact/resolution_queue_repository.py")

    assert "enqueue_questionnaire_identity_resolution" in source
    assert "build_identity_resolution_queue_port().enqueue_dbapi" in queue_source
    assert "INSERT INTO crm_user_identity_resolution_queue" not in queue_source
    assert "INSERT INTO crm_user_identity_resolution_queue" in owner_source
    assert "identity_unresolved" in queue_source
    assert "INSERT INTO questionnaire_submissions" in source


def test_customer_detail_query_supports_unionid_native_lookup(monkeypatch) -> None:
    from aicrm_next.crm.customer_read_model.application import (
        GetCustomerDetailQuery,
        GetCustomerTimelineQuery,
        ListRecentMessagesQuery,
    )
    from aicrm_next.crm.customer_read_model.dto import CustomerDetailRequest, CustomerTimelineRequest, RecentMessagesRequest

    class Repo:
        def get_customer_by_unionid(self, unionid: str):
            if unionid != "union_customer_001":
                return None
            return {
                "unionid": unionid,
                "external_userid": "wm_legacy_boundary",
                "customer_name": "Union Customer",
                "binding": {"is_bound": True, "binding_status": "bound"},
                "identity": {"unionid": unionid},
                "follow_users": [],
                "marketing_summary": {},
                "marketing_profile": {},
                "contact": {},
                "sidebar_context": {},
            }

        def get_customer(self, external_userid: str):  # pragma: no cover - must not be used
            raise AssertionError("external_userid lookup should not run for unionid-native detail query")

        def customer_exists_by_unionid(self, unionid: str) -> bool:
            return self.get_customer_by_unionid(unionid) is not None

        def list_timeline_by_unionid(self, unionid: str, filters=None, *, limit=None, offset=0):
            return [
                {
                    "event_id": "evt_union_001",
                    "event_type": "message",
                    "event_time": "2026-07-01T00:00:00+00:00",
                    "title": "Union timeline",
                    "summary": "",
                }
            ]

        def list_recent_messages_by_unionid(self, unionid: str, *, limit=None):
            return [{"msgid": "msg_union_001", "unionid": unionid}]

    monkeypatch.setattr("aicrm_next.crm.customer_read_model.application._production_customer_data_required", lambda: True)

    repo = Repo()
    result = GetCustomerDetailQuery(repo=repo)(CustomerDetailRequest(unionid="union_customer_001"))
    timeline = GetCustomerTimelineQuery(repo=repo)(CustomerTimelineRequest(unionid="union_customer_001"))
    messages = ListRecentMessagesQuery(repo=repo)(RecentMessagesRequest(unionid="union_customer_001"))

    assert result["ok"] is True
    assert result["customer"]["unionid"] == "union_customer_001"
    assert result["source_status"] == "next_read_model"
    assert timeline["timeline"]["unionid"] == "union_customer_001"
    assert timeline["timeline"]["items"][0]["event_id"] == "evt_union_001"
    assert messages["unionid"] == "union_customer_001"
    assert messages["messages"][0]["msgid"] == "msg_union_001"


def test_customer_api_exposes_unionid_user_route() -> None:
    source = _read("aicrm_next/crm/customer_read_model/api.py")
    admin_pages = _read("aicrm_next/crm/customer_read_model/admin_pages.py")

    assert '@router.get("/api/users/{unionid}")' in source
    assert "CustomerDetailRequest(unionid=unionid)" in source
    assert '@router.get("/api/users/{unionid}/timeline")' in source
    assert '@router.get("/api/users/{unionid}/messages/recent")' in source
    assert '@router.get("/admin/customers/{unionid}"' in admin_pages
    assert 'urlencode({"unionid": unionid})' in admin_pages
    assert "def get_admin_customer_profile(\n    unionid: str | None = None" in source
    assert "def get_admin_customer_profile_tags(\n    unionid: str | None = None" in source
    assert "def get_admin_customer_profile_messages(\n    unionid: str | None = None" in source
    assert '@router.get(\n    "/api/admin/customers/{unionid}/business-profile"' in source


def test_channel_entry_business_write_requires_and_persists_unionid() -> None:
    application = _read("aicrm_next/channels/channel_entry/application.py")
    repo = _read("aicrm_next/channels/channel_entry/repo.py")
    identity_queue_owner = _read("aicrm_next/crm/identity_contact/resolution_queue_repository.py")
    cleanup = _read("migrations/versions/0069_unionid_channel_contact_cleanup.py")

    assert 'subject_type="unionid"' in application
    assert '"reason": "identity_pending_unionid"' in application
    assert "unionid = text(identity_sync.get(\"unionid\"))" in application
    assert "ProcessChannelEntryCommand(\n        unionid=unionid" in application
    assert "repo.upsert_channel_contact(\n        channel_id=channel_id,\n        unionid=command.unionid" in application
    assert "def _channel_entry_target(command: ProcessChannelEntryCommand)" in application
    assert 'return "unionid", unionid, {"target_unionid": unionid}' in application
    assert 'return "external_userid", text(command.external_contact_id), {}' in application
    assert "def process_channel_entry_runtime(" in application
    assert "def process_channel_entry_canonical(" in application
    assert "repo.upsert_channel_entry_runtime" in application
    assert "repo.enqueue_channel_entry_identity_resolution" in application
    assert "def upsert_channel_contact(*, channel_id: int, unionid: str = \"\"" in repo
    assert "INSERT INTO automation_channel_contact" in repo
    channel_contact_insert = repo.split("INSERT INTO automation_channel_contact", 1)[1].split("RETURNING *", 1)[0]
    assert "channel_id, unionid, owner_staff_id" in channel_contact_insert
    assert "external_contact_id" not in channel_contact_insert
    assert "external_userid" not in channel_contact_insert
    assert "ON CONFLICT (channel_id, unionid)" in channel_contact_insert
    assert "def upsert_channel_entry_runtime" in repo
    assert "INSERT INTO automation_channel_entry_runtime" in repo
    assert "INSERT INTO crm_user_identity_resolution_queue" not in repo
    assert "build_identity_resolution_queue_port().enqueue_dbapi" in repo
    assert "INSERT INTO crm_user_identity_resolution_queue" in identity_queue_owner

    assert "ALTER TABLE IF EXISTS automation_channel_contact DROP COLUMN IF EXISTS external_userid" in cleanup
    assert "INSERT INTO crm_user_identity_resolution_queue" in cleanup
    assert "source_type, source_key" in cleanup


def test_sidebar_bind_mobile_writes_user_identity_not_legacy_binding_tables() -> None:
    source = _read("aicrm_next/crm/sidebar_write/repo.py")
    identity_write_source = _read("aicrm_next/crm/identity_contact/write_repository.py")
    event_source = _read("aicrm_next/platform/platform_foundation/internal_events/customer_identity.py")

    postgres_section = source.split("class PostgresSidebarWriteRepository:", 1)[1]
    assert "UPDATE crm_user_identity" not in postgres_section
    assert "INSERT INTO crm_user_identity_resolution_queue" not in postgres_section
    assert "self._identity_write_port.bind_sidebar_mobile" in postgres_section
    assert "self._identity_write_port.enqueue_sidebar_identity_resolution" in postgres_section
    assert "UPDATE crm_user_identity" in identity_write_source
    assert "INSERT INTO crm_user_identity_resolution_queue" in identity_write_source
    assert "primary_owner_userid" in identity_write_source
    assert "primary_follow_user_userid" not in postgres_section
    assert "mobile_normalized = %s" in identity_write_source
    assert "mobile_source = 'sidebar_bind'" in identity_write_source
    assert "profile_json ->> 'mobile_source'" not in postgres_section
    assert "external_contact_bindings" not in postgres_section
    assert "INSERT INTO people" not in postgres_section
    assert "user_ops_lead_pool_current" not in postgres_section
    assert "unionid = _text(result.get(\"unionid\"))" in event_source
    assert 'subject_type="unionid" if unionid else "customer"' in event_source


def test_external_effect_wecom_adapters_accept_unionid_business_target() -> None:
    source = _read("aicrm_next/platform/platform_foundation/external_effects/adapters.py")

    assert "def _target_unionid" in source
    assert "def _wecom_target_mismatch" in source
    assert "target_id != target_unionid" in source
    assert '"target_unionid": _target_unionid(payload)' in source


def test_user_ops_tables_are_unionid_only_business_models() -> None:
    model_source = _read("aicrm_next/automation/ops_enrollment/models.py")
    migration_source = _read("migrations/versions/0029_user_ops_prod_tables.py")

    assert 'Column("unionid"' in model_source
    assert 'Column("customer_name_snapshot"' in model_source
    assert 'Column("target_unionids_json"' in model_source
    assert "unionid VARCHAR(128) NOT NULL" in migration_source
    assert "target_unionids_json JSONB NOT NULL DEFAULT '[]'::jsonb" in migration_source

    for source in (model_source, migration_source):
        assert "person_id" not in source
        assert "Column(\"external_userid\"" not in source
        assert "Column(\"mobile\"" not in source
        assert "external_userid VARCHAR" not in source
        assert "mobile VARCHAR" not in source
        assert "is_mobile_bound BOOLEAN" not in source


def test_user_ops_legacy_runtime_tables_are_retired() -> None:
    identity_contact_source = _read("aicrm_next/crm/identity_contact/repo.py")
    external_campaign_source = _read("aicrm_next/extensions/ai/ai_assist/external_campaigns.py")
    external_campaign_repo_source = _read("aicrm_next/extensions/ai/ai_assist/external_campaigns_repo.py")
    admin_jobs_source = _read("aicrm_next/platform/admin_jobs/repository.py")
    owner_migration_source = _read("aicrm_next/crm/owner_migration/repo.py")
    manifest_source = _read("docs/architecture/data_table_lifecycle_manifest.yml")

    runtime_sources = {
        "identity_contact": identity_contact_source,
        "external_campaign": external_campaign_source,
        "external_campaign_repo": external_campaign_repo_source,
        "admin_jobs": admin_jobs_source,
        "owner_migration": owner_migration_source,
    }
    forbidden_sql_patterns = [
        r"\bFROM\s+user_ops_lead_pool_current\b",
        r"\bINSERT\s+INTO\s+user_ops_lead_pool_current\b",
        r"\bUPDATE\s+user_ops_lead_pool_current\b",
        r"\bDELETE\s+FROM\s+user_ops_lead_pool_current\b",
        r"\bINSERT\s+INTO\s+user_ops_lead_pool_history\b",
        r"\bFROM\s+user_ops_pool_current\b",
        r"\bFROM\s+user_ops_send_records\b",
        r"\bFROM\s+user_ops_deferred_jobs\b",
        r"\bUPDATE\s+user_ops_deferred_jobs\b",
    ]
    for name, source in runtime_sources.items():
        for pattern in forbidden_sql_patterns:
            assert re.search(pattern, source) is None, f"{name} still matches {pattern}"

    assert "UPDATE crm_user_identity" in identity_contact_source
    assert "INSERT INTO crm_user_identity_resolution_queue" in identity_contact_source
    assert "SendTargetResolver" in external_campaign_source
    assert "resolve_identity_with_dbapi" in external_campaign_repo_source
    assert "user_ops_pool_current_next" not in external_campaign_source
    assert "user_ops_pool_current_next" not in external_campaign_repo_source
    assert "identity.id" not in external_campaign_repo_source
    assert "return _status_counts([])" in admin_jobs_source
    assert "user_ops_lead_pool_current:\n    domain: user_ops\n    lifecycle: retired" in manifest_source
    assert "user_ops_deferred_jobs:\n    domain: user_ops\n    lifecycle: retired" in manifest_source


def test_message_batch_legacy_runtime_tables_are_retired() -> None:
    admin_jobs_source = _read("aicrm_next/platform/admin_jobs/repository.py")
    manifest_source = _read("docs/architecture/data_table_lifecycle_manifest.yml")

    forbidden_sql_patterns = [
        r"\bFROM\s+message_batches\b",
        r"\bUPDATE\s+message_batches\b",
        r"\bINSERT\s+INTO\s+message_batches\b",
        r"\bFROM\s+message_batch_items\b",
        r"\bJOIN\s+message_batch_items\b",
    ]
    for pattern in forbidden_sql_patterns:
        assert re.search(pattern, admin_jobs_source) is None

    assert "FROM broadcast_jobs" in admin_jobs_source
    assert "FROM broadcast_job_events" in admin_jobs_source
    assert "message_batch_ack" in admin_jobs_source
    assert "message_batches:\n    domain: messaging\n    lifecycle: retired" in manifest_source
    assert "message_batch_items:\n    domain: messaging\n    lifecycle: retired" in manifest_source


def test_customer_read_model_tables_are_unionid_only() -> None:
    model_source = _read("aicrm_next/crm/customer_read_model/models.py")
    migration_source = _read("migrations/versions/0026_customer_read_model_next.py")
    slot_migration_source = _read("migrations/versions/0153_customer_read_model_generation_slots.py")

    for table_name in [
        "customer_list_index_next",
        "customer_detail_snapshot_next",
        "customer_timeline_event_next",
        "customer_recent_message_next",
    ]:
        assert table_name in model_source
        assert table_name in migration_source

    for table_name in [
        "customer_list_index_next_shadow",
        "customer_detail_snapshot_next_shadow",
        "customer_recent_message_next_shadow",
    ]:
        assert table_name in model_source
        assert table_name in slot_migration_source

    assert model_source.count('Column("unionid"') == 7
    assert migration_source.count("unionid TEXT NOT NULL") == 4
    assert slot_migration_source.count("unionid TEXT NOT NULL") == 3
    assert "ix_customer_list_index_next_unionid" in migration_source
    assert "ix_customer_detail_snapshot_next_unionid" in migration_source
    assert "ix_customer_timeline_event_next_unionid" in migration_source
    assert "ix_customer_recent_message_next_unionid" in migration_source

    for source in (model_source, migration_source, slot_migration_source):
        assert "person_id" not in source
        assert "external_userid TEXT" not in source
        assert 'Column("external_userid"' not in source
        assert "ix_customer_list_index_next_external_userid" not in source
        assert "ix_customer_detail_snapshot_next_external_userid" not in source
        assert "ix_customer_timeline_event_next_external_userid" not in source
        assert "ix_customer_recent_message_next_external_userid" not in source


def test_automation_runtime_v2_tables_are_unionid_only() -> None:
    source = _read("migrations/versions/0031_automation_runtime_v2.py")

    assert "CREATE TABLE IF NOT EXISTS automation_event_v2" in source
    assert "CREATE TABLE IF NOT EXISTS automation_membership_v2" in source
    assert "unionid TEXT NOT NULL" in source
    assert "idx_automation_event_v2_unionid" in source
    assert "idx_automation_membership_v2_unionid" in source
    assert "uq_automation_membership_v2_program_unionid" in source
    assert "external_userid TEXT" not in source
    assert "person_id BIGINT" not in source
    assert "idx_automation_event_v2_external" not in source
    assert "uq_automation_membership_v2_program_external" not in source


def test_retired_automation_member_table_is_physically_removed() -> None:
    source = _read("migrations/versions/0070_retire_automation_member_table.py")
    sidebar_source = _read("aicrm_next/crm/customer_read_model/sidebar_v2.py")

    assert '"automation_member"' in source
    assert '"automation_member_interaction_stats"' in source
    assert "DROP VIEW IF EXISTS {view_name}" in source
    assert "DROP TABLE IF EXISTS {table_name} CASCADE" in source
    assert "FROM automation_member" not in sidebar_source


def test_retired_conversion_trace_tables_are_physically_removed() -> None:
    source = _read("migrations/versions/0071_retire_conversion_trace_tables.py")
    runtime_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for root in [ROOT / "aicrm_next", ROOT / "scripts"]
        for path in root.rglob("*.py")
        if path.name != "precheck_retired_automation_tables.py"
    )

    assert '"automation_execution_trace"' in source
    assert '"conversion_dispatch_log"' in source
    assert "DROP INDEX IF EXISTS {index_name}" in source
    assert "DROP TABLE IF EXISTS {table_name}" in source
    assert "automation_execution_trace" not in runtime_sources
    assert "conversion_dispatch_log" not in runtime_sources


def test_hxc_snapshot_drops_external_field_after_unionid_foundation() -> None:
    source = _read("migrations/versions/0072_hxc_snapshot_unionid_foundation.py")
    cleanup_source = _read("migrations/versions/0073_drop_hxc_snapshot_external_userid.py")
    repo_source = _read("aicrm_next/extensions/hxc/hxc_dashboard/postgres_repo.py")

    assert "ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT ''" in source
    assert "idx_hxc_snapshot_unionid" in source
    assert "jsonb_exists(identity.external_userids_json, snapshot.external_userid)" in source
    assert "DROP COLUMN IF EXISTS external_userid" in cleanup_source
    assert "s.unionid = ANY(%s)" in repo_source
    assert "user_ops_do_not_disturb_next" in repo_source
    assert "FROM user_ops_do_not_disturb dnd" not in repo_source
    assert "s.external_userid" not in repo_source


def test_alipay_orders_are_unionid_only_customer_identity() -> None:
    source = _read("migrations/versions/0014_alipay_pay.py")
    cleanup_source = _read("migrations/versions/0063_unionid_business_table_foundation.py")

    assert "CREATE TABLE IF NOT EXISTS alipay_pay_orders" in source
    assert "unionid TEXT NOT NULL DEFAULT ''" in source
    assert "idx_alipay_pay_orders_unionid_created" in source
    for checked in (source, cleanup_source):
        assert "buyer_id TEXT" not in checked
        assert "mobile_snapshot TEXT" not in checked
        assert "identity_snapshot TEXT" not in checked
        assert "idx_alipay_pay_orders_mobile_created" not in checked


def test_ai_audience_member_tables_are_unionid_only_business_state() -> None:
    source = _read("migrations/versions/0045_ai_audience_ops.py")
    cleanup_source = _read("migrations/versions/0064_unionid_ops_automation_foundation.py")
    repository_source = _read("aicrm_next/extensions/ai/ai_audience_ops/repository.py")
    refresh_source = _read("aicrm_next/extensions/ai/ai_audience_ops/refresh_service.py")

    assert "CREATE TABLE IF NOT EXISTS ai_audience_member_current" in source
    assert "CREATE TABLE IF NOT EXISTS ai_audience_member_event" in source
    assert "unionid TEXT NOT NULL DEFAULT ''" in source
    assert "DROP COLUMN IF EXISTS person_id" in cleanup_source
    assert "DROP COLUMN IF EXISTS external_userid" in cleanup_source
    assert "resolve_member_unionid" in repository_source
    assert "enqueue_identity_resolution" in repository_source
    assert '"reason": "missing_unionid"' not in refresh_source

    member_section = source.split("CREATE TABLE IF NOT EXISTS ai_audience_member_current", 1)[1].split(
        "CREATE TABLE IF NOT EXISTS ai_audience_outbound_subscription", 1
    )[0]
    assert "person_id" not in member_section
    assert "external_userid TEXT" not in member_section
    assert "person_id = EXCLUDED" not in repository_source
    assert "external_userid = EXCLUDED" not in repository_source


def test_questionnaire_and_wechat_pay_facts_drop_legacy_identity_columns() -> None:
    cleanup_source = _read("migrations/versions/0065_unionid_submission_payment_cleanup.py")
    questionnaire_repo = _read("aicrm_next/extensions/forms/questionnaire/repo.py")
    wechat_pay_source = _read(
        "aicrm_next/extensions/commerce/commerce/wechat_pay_order_write_repository.py"
    )
    wechat_pay_h5_source = _read("aicrm_next/extensions/commerce/public_product/h5_wechat_pay.py")

    assert '["identity_map_id", "respondent_key", "openid", "external_userid", "mobile_snapshot"]' in cleanup_source
    assert '["payer_openid", "respondent_key", "external_userid", "userid_snapshot", "mobile_snapshot"]' in cleanup_source
    assert "ALTER TABLE IF EXISTS questionnaire_submissions DROP COLUMN IF EXISTS {column_name}" in cleanup_source
    assert "ALTER TABLE IF EXISTS wechat_pay_orders DROP COLUMN IF EXISTS {column_name}" in cleanup_source

    questionnaire_insert = questionnaire_repo.split("INSERT INTO questionnaire_submissions", 1)[1].split("RETURNING id, submitted_at", 1)[0]
    for forbidden in ["identity_map_id", "respondent_key", "openid", "external_userid", "mobile_snapshot"]:
        assert forbidden not in questionnaire_insert

    wechat_order_insert = wechat_pay_source.split("INSERT INTO wechat_pay_orders", 1)[1].split("RETURNING *", 1)[0]
    for forbidden in ["payer_openid", "respondent_key", "external_userid", "userid_snapshot", "mobile_snapshot"]:
        assert forbidden not in wechat_order_insert
    assert '"payer_identity"' in wechat_pay_h5_source


def test_customer_fact_read_sources_drop_legacy_identity_columns() -> None:
    cleanup_source = _read("migrations/versions/0068_unionid_customer_fact_cleanup.py")
    customer_repo_source = _read("aicrm_next/crm/customer_read_model/repo_live_source.py")
    message_archive_source = _read("aicrm_next/extensions/archive/message_archive/repo.py")
    channel_entry_repo_source = _read("aicrm_next/channels/channel_entry/repo.py")
    contact_tag_projection_source = _read("aicrm_next/crm/customer_tags/projection_repository.py")
    identity_queue_source = _read("aicrm_next/crm/identity_contact/resolution_queue_repository.py")

    for table_name in ["contact_tags", "archived_messages", "class_user_status_current", "class_user_status_history"]:
        assert table_name in cleanup_source
    assert '"contact_tags": ["external_userid"]' in cleanup_source
    assert '"archived_messages": ["external_userid"]' in cleanup_source
    assert '"class_user_status_current": ["external_userid", "mobile_snapshot"]' in cleanup_source
    assert '"class_user_status_history": ["external_userid", "mobile_snapshot"]' in cleanup_source
    assert '"wechat_shop_orders": ["buyer_mobile", "openid"]' in cleanup_source
    assert "INSERT INTO crm_user_identity_resolution_queue" in cleanup_source
    assert "DROP COLUMN IF EXISTS {column_name}" in cleanup_source

    live_source_sql = customer_repo_source.split("def _customer_decorated_sql", 1)[1].split("def _customer_where", 1)[0]
    assert "SELECT unionid FROM archived_messages" in live_source_sql
    assert "SELECT unionid FROM contact_tags" in live_source_sql
    assert "SELECT unionid FROM class_user_status_current" in live_source_sql
    assert "class_status.mobile_snapshot" not in live_source_sql
    assert "identity.profile_json" in live_source_sql
    assert "CAST(latest_messages.last_message_at AS TEXT)" in live_source_sql

    archive_insert = message_archive_source.split("INSERT INTO archived_messages", 1)[1].split("ON CONFLICT (msgid)", 1)[0]
    assert "unionid" in archive_insert
    assert "external_userid" not in archive_insert
    assert "INSERT INTO crm_user_identity_resolution_queue" not in message_archive_source
    assert "build_identity_resolution_queue_port().enqueue_dbapi" in message_archive_source

    tag_insert = contact_tag_projection_source.split("INSERT INTO contact_tags", 1)[1]
    assert "unionid" in tag_insert
    assert "external_userid" not in tag_insert
    assert "build_customer_tag_projection_port().save_channel_snapshot_dbapi" in channel_entry_repo_source
    assert "INSERT INTO contact_tags" not in channel_entry_repo_source
    assert "INSERT INTO crm_user_identity_resolution_queue" not in channel_entry_repo_source
    assert "build_identity_resolution_queue_port().enqueue_dbapi" in channel_entry_repo_source
    assert "INSERT INTO crm_user_identity_resolution_queue" in identity_queue_source


def test_wechat_shop_orders_keep_customer_identity_in_unionid_and_raw_payload() -> None:
    cleanup_source = _read("migrations/versions/0068_unionid_customer_fact_cleanup.py")
    shop_source = _read("aicrm_next/extensions/commerce/commerce/wechat_shop_service.py")
    transaction_detail_source = _read("aicrm_next/extensions/commerce/commerce/admin_transaction_detail.py")

    assert '"wechat_shop_orders": ["buyer_mobile", "openid"]' in cleanup_source
    assert "ALTER TABLE IF EXISTS {table_name} DROP COLUMN IF EXISTS {column_name}" in cleanup_source

    postgres_insert = shop_source.split("INSERT INTO wechat_shop_orders (\n                order_id, provider", 1)[1].split("ON CONFLICT (order_id)", 1)[0]
    assert "unionid" in postgres_insert
    assert "raw_order_json" in postgres_insert
    assert "buyer_mobile" not in postgres_insert
    assert "openid" not in postgres_insert

    shop_select = transaction_detail_source.split('if provider == "wechat_shop":', 1)[1].split("return f", 1)[0]
    assert "WECHAT_SHOP_BUYER_MOBILE_SQL" in transaction_detail_source
    assert "WECHAT_SHOP_OPENID_SQL" in transaction_detail_source
    assert "o.buyer_mobile" not in shop_select
    assert "o.openid" not in shop_select


def test_broadcast_cloud_and_agent_targets_are_unionid_only() -> None:
    broadcast_migration = _read("migrations/versions/0008_broadcast_jobs.py")
    cloud_migration = _read("migrations/versions/0024_cloud_plan_recipient_approval.py")
    agent_migration = _read("migrations/versions/0054_automation_agent_runtime_config.py")
    cleanup_source = _read("migrations/versions/0066_unionid_broadcast_target_cleanup.py")
    worker_source = _read("aicrm_next/automation/background_jobs/broadcast_queue_worker.py")
    cloud_repo_source = _read("aicrm_next/extensions/growth/cloud_orchestrator/repository.py")
    broadcast_owner_source = _read(
        "aicrm_next/platform/platform_foundation/background_jobs/broadcast_job_write_repository.py"
    )
    agent_repo_source = _read("aicrm_next/extensions/ai/automation_agents/repository.py")
    agent_worker_source = _read("aicrm_next/extensions/ai/automation_agents/worker.py")

    assert "target_unionids_json JSONB NOT NULL DEFAULT '[]'::jsonb" in broadcast_migration
    assert "target_external_userids JSONB" not in broadcast_migration
    assert "'blocked'" in broadcast_migration

    assert "unionid TEXT NOT NULL" in cloud_migration
    assert "external_userid TEXT NOT NULL" not in cloud_migration
    assert "uq_cloud_broadcast_plan_recipients_plan_unionid" in cloud_migration

    assert "unionid TEXT NOT NULL" in agent_migration
    assert "external_userid TEXT NOT NULL" not in agent_migration
    assert "uq_automation_agent_webhook_item_batch_unionid" in agent_migration

    assert "DROP COLUMN IF EXISTS target_external_userids" in cleanup_source
    assert "DROP COLUMN IF EXISTS external_userid" in cleanup_source
    assert "DROP COLUMN IF EXISTS external_contact_id" in cleanup_source

    assert "_resolve_private_targets_by_unionid" in worker_source
    assert "target_external_userids_missing" not in worker_source
    assert "target_unionids_missing" in worker_source
    assert "identity_external_userid_missing" in worker_source

    assert 'target_unionids=(_text(recipient.get("unionid")),)' in cloud_repo_source
    assert "target_unionids_json" in broadcast_owner_source
    assert "target_external_userids" not in cloud_repo_source
    assert "target_kind" in cloud_repo_source
    assert "unionid" in cloud_repo_source

    assert "INSERT INTO automation_agent_webhook_item" in agent_repo_source
    agent_item_insert = agent_repo_source.split("INSERT INTO automation_agent_webhook_item", 1)[1].split("RETURNING *", 1)[0]
    assert "unionid" in agent_item_insert
    assert "external_userid" not in agent_item_insert
    assert "resolve_external_userid_for_unionid" in agent_worker_source


def test_campaign_frequency_and_agent_outputs_are_unionid_only() -> None:
    cloud_orchestrator_migration = _read("migrations/versions/0004_cloud_orchestrator.py")
    campaign_migration = _read("migrations/versions/0005_segments_and_campaigns.py")
    cleanup_source = _read("migrations/versions/0067_unionid_campaign_frequency_cleanup.py")
    campaign_repo_source = _read("aicrm_next/extensions/growth/cloud_orchestrator/repository.py")
    external_campaign_repo_source = _read("aicrm_next/extensions/ai/ai_assist/external_campaigns_repo.py")
    broadcast_owner_source = _read(
        "aicrm_next/platform/platform_foundation/background_jobs/broadcast_job_write_repository.py"
    )
    agent_copywriting_source = _read("aicrm_next/extensions/ai/ai_audience_ops/agent_copywriting.py")
    admin_projection_source = _read("aicrm_next/insights/admin_read_model/projections.py")
    agent_run_repo_source = _read("aicrm_next/automation/automation_engine/agent_run_sqlalchemy_repository.py")
    agent_run_domain_source = _read("aicrm_next/automation/automation_engine/agent_runs.py")
    agent_output_repo_source = _read("aicrm_next/automation/automation_engine/agent_output_sqlalchemy_repository.py")
    agent_output_domain_source = _read("aicrm_next/automation/automation_engine/agent_outputs.py")

    assert "unionid TEXT NOT NULL DEFAULT ''" in campaign_migration
    assert "external_contact_id TEXT NOT NULL DEFAULT ''" not in campaign_migration
    assert "idx_campaign_members_unionid" in campaign_migration

    assert "idx_automation_frequency_consumption_unionid_window" in cloud_orchestrator_migration
    assert "idx_automation_frequency_consumption_external_window" not in cloud_orchestrator_migration

    for table in ["segment_member_snapshots", "campaign_members", "automation_frequency_consumption", "automation_agent_run", "automation_agent_output"]:
        assert table in cleanup_source
    assert "DROP COLUMN IF EXISTS external_contact_id" in cleanup_source
    assert "ix_{table}_unionid" in cleanup_source

    campaign_insert = broadcast_owner_source.split("INSERT INTO broadcast_jobs", 1)[1].split("RETURNING *", 1)[0]
    assert "target_unionids_json" in campaign_insert
    assert "target_kind" in campaign_insert
    assert "target_external_userids" not in campaign_insert
    assert "external_contact_id" not in campaign_insert
    assert "BroadcastJobCreate(" in external_campaign_repo_source
    assert "target_unionids=tuple(" in external_campaign_repo_source
    assert "_CAMPAIGN_QUEUE_TARGET_KIND = \"unionid\"" in campaign_repo_source
    assert 'target_unionids=(_text(recipient.get("unionid")),)' in campaign_repo_source

    assert '"unionid": _text(member_event.get("unionid")' in agent_copywriting_source
    assert '"external_contact_id": _text(member_event' not in agent_copywriting_source
    agent_projection_source = admin_projection_source.split("def funnel_payload", 1)[0]
    assert "SELECT id, run_id, agent_code, status, unionid" in agent_projection_source
    assert "external_contact_id" not in agent_projection_source
    assert '"unionid",' in agent_run_repo_source
    assert '"external_contact_id",' not in agent_run_repo_source
    assert '"unionid": _text(source.get("unionid"))' in agent_run_domain_source
    assert '"external_contact_id": _text(source.get("external_contact_id"))' not in agent_run_domain_source
    assert '"unionid", "userid", "agent_code"' in agent_output_repo_source
    assert '"external_contact_id", "userid", "agent_code"' not in agent_output_repo_source
    assert '"unionid": _text(source.get("unionid"))' in agent_output_domain_source
    assert '"external_contact_id": _text(source.get("external_contact_id"))' not in agent_output_domain_source


def test_final_legacy_identity_cleanup_removes_non_boundary_columns() -> None:
    cleanup_source = _read("migrations/versions/0078_final_legacy_identity_column_cleanup.py")
    target_cleanup_source = _read("migrations/versions/0079_final_target_schema_cleanup.py")
    channel_repo_source = _read("aicrm_next/channels/channel_entry/repo.py")
    channel_app_source = _read("aicrm_next/channels/channel_entry/application.py")
    contact_sync_source = _read("aicrm_next/automation/background_jobs/external_contact_sync.py")
    sidebar_source = _read("aicrm_next/crm/customer_read_model/sidebar_v2.py")
    identity_contact_source = _read("aicrm_next/crm/identity_contact/repo.py")
    admin_projection_source = _read("aicrm_next/insights/admin_read_model/projections.py")
    external_campaigns_source = _read("aicrm_next/extensions/ai/ai_assist/external_campaigns_repo.py")
    owner_migration_source = _read("aicrm_next/crm/owner_migration/repo.py")

    assert "down_revision = \"0077_id_dev_runtime_baseline\"" in cleanup_source
    assert "ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT ''" in cleanup_source
    assert "jsonb_build_object('external_contact_id', log.external_contact_id)" in cleanup_source
    assert "DROP COLUMN IF EXISTS external_contact_id" in cleanup_source
    assert "ALTER TABLE IF EXISTS contacts DROP COLUMN IF EXISTS external_userid" in cleanup_source
    assert "automation_touch_delivery_log DROP COLUMN IF EXISTS external_userid" in cleanup_source
    assert "user_ops_do_not_disturb_next DROP COLUMN IF EXISTS external_userid" in cleanup_source
    assert "down_revision = \"0078_final_legacy_identity_cleanup\"" in target_cleanup_source
    assert "customer_timeline_event_next DROP COLUMN IF EXISTS person_id" in target_cleanup_source
    assert "DELETE FROM contacts WHERE COALESCE(unionid, '') = ''" in target_cleanup_source

    effect_log_insert = channel_repo_source.split("INSERT INTO automation_channel_entry_effect_log", 1)[1].split("ON CONFLICT", 1)[0]
    assert "unionid" in effect_log_insert
    assert "external_contact_id" not in effect_log_insert
    assert "EXCLUDED.unionid" in channel_repo_source
    assert "EXCLUDED.external_contact_id" not in channel_repo_source
    assert "source_request.setdefault(\"external_contact_id\"" in channel_repo_source
    assert "unionid=command.unionid" in channel_app_source
    for source in [
        contact_sync_source,
        sidebar_source,
        identity_contact_source,
        admin_projection_source,
        external_campaigns_source,
        owner_migration_source,
    ]:
        assert "FROM contacts" not in source
        assert "INSERT INTO contacts" not in source
        assert "UPDATE contacts" not in source
