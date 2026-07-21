from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    matches = list(re.finditer(rf"^(?:def|    def) {re.escape(name)}\(", source, re.MULTILINE))
    assert matches, f"missing function {name}"
    match = matches[-1]
    tail = source[match.start() :]
    next_match = re.search(r"\n(?:def|class|    def) ", tail[len(match.group(0)) :])
    return tail if not next_match else tail[: len(match.group(0)) + next_match.start()]


def test_h5_wechat_pay_notify_after_unionid_cleanup() -> None:
    source = _read("aicrm_next/public_product/h5_wechat_pay.py")
    payment_identity_source = _function_source(source, "_resolve_payment_identity")
    paid_order_source = _function_source(source, "_paid_order_for_product_identity")
    apply_transaction_source = _function_source(source, "_apply_transaction")

    assert "resolve_identity_with_dbapi" in payment_identity_source
    assert "_resolve_payment_identity" in paid_order_source
    assert "resolved_unionid" in paid_order_source
    assert "payer_openid = %s" not in paid_order_source
    assert "unionid = %s" in paid_order_source
    assert "external_userid = %s" not in paid_order_source
    assert "payer_openid = %s" not in apply_transaction_source
    assert "notify_payload_json = %s::jsonb" in apply_transaction_source


def test_questionnaire_admin_reads_after_unionid_cleanup() -> None:
    source = _read("aicrm_next/questionnaire/repo.py")
    for name in ["list_submissions", "list_external_submissions", "find_submission_for_identity"]:
        section = _function_source(source, name)
        assert "LEFT JOIN crm_user_identity identity ON identity.unionid = qs.unionid" in section
        assert "qs.external_userid" not in section
        assert "qs.mobile_snapshot" not in section
    assert "identity.primary_external_userid" in source
    assert "identity.mobile" in source


def test_alipay_admin_transactions_after_unionid_cleanup() -> None:
    source = _read("aicrm_next/commerce/admin_transaction_detail.py")
    filter_source = _function_source(source, "_postgres_filter_clause")
    select_source = _function_source(source, "_postgres_order_select")

    assert "_identity_lookup_exists_sql" in filter_source
    assert "crm_user_identity identity" in source
    assert "where.append(\"1 = 0\")" not in filter_source
    for forbidden in ["o.mobile_snapshot", "o.identity_snapshot", "o.buyer_id", "o.userid_snapshot", "o.external_userid", "o.respondent_key"]:
        assert forbidden not in filter_source
        assert forbidden not in select_source


def test_group_ops_dispatcher_plans_one_external_effect_with_unionid_link() -> None:
    source = _read("aicrm_next/automation_engine/group_ops/action_dispatcher.py")
    enqueue_source = _function_source(source, "enqueue_private_message")

    assert "_insert_broadcast_job" not in source
    assert "WECOM_MESSAGE_PRIVATE_SEND" in enqueue_source
    assert "self._external_effect_service.plan_effect" in enqueue_source
    assert 'adapter_name="wecom_private_message"' in enqueue_source
    assert "\"unionids\"" in enqueue_source
    assert '"external_userids"' in enqueue_source
    assert '"target_unionid"' in enqueue_source
    assert '"execution_owner": "external_effect_job"' in enqueue_source


def test_cloud_broadcast_plan_dispatch_uses_unionid() -> None:
    source = _read("aicrm_next/cloud_orchestrator/repository.py")
    section = source.split("def create_or_reuse_recipient_broadcast_jobs", 1)[1].split("def create_or_reuse_plan_broadcast_job", 1)[0]

    assert "SELECT id, unionid, display_name, owner_userid" in section
    assert "COALESCE(unionid, '') <> ''" in section
    assert "target_unionids_json" in section
    assert "SELECT id, external_userid" not in section
    assert "COALESCE(external_userid, '') <> ''" not in section


def test_channel_assignment_event_writes_unionid() -> None:
    source = _read("aicrm_next/channel_entry/repo.py")
    insert_source = _function_source(source, "insert_assignment_event")
    serializer_source = _function_source(source, "_serialize_assignment_event")

    assert "unionid, wecom_user_id, source_payload_json" in insert_source
    assert "resolve_identity_with_dbapi" in insert_source
    assert "resolved_unionid" in insert_source
    assert "external_contact_id, wecom_user_id" not in insert_source
    assert '"unionid": text(row.get("unionid"))' in serializer_source


def test_customer_external_userid_lookup_exact_jsonb_membership() -> None:
    source = _read("aicrm_next/customer_read_model/repo_live_source.py")
    section = _function_source(source, "_identity_by_external_userid")
    resolver = _read("aicrm_next/identity_contact/resolver.py")

    assert "SQLAlchemyIdentityResolver" in section
    assert "identity.external_userids_json ? input.external_userid" not in resolver
    assert "identity.external_userids_json @> jsonb_build_array(input.external_userid)" in resolver
    assert "identity.openids_json @> jsonb_build_array(input.openid)" in resolver
    assert "jsonb_array_elements(identity.external_userids_json)" not in resolver
    assert "CAST(external_userids_json AS TEXT) LIKE" not in section
    assert "external_userid_like" not in section


def test_automation_agent_webhook_item_upsert_matches_partial_index() -> None:
    source = _read("aicrm_next/automation_agents/repository.py")
    assert "ON CONFLICT (batch_id, unionid) WHERE unionid <> '' DO UPDATE" in source


def test_cloud_plan_recipient_upsert_matches_partial_index() -> None:
    source = _read("aicrm_next/cloud_orchestrator/repository.py")
    assert "ON CONFLICT (plan_id, unionid) WHERE unionid <> '' DO UPDATE" in source


def test_unionid_runtime_sql_guard_blocks_removed_identity_columns() -> None:
    h5_source = _read("aicrm_next/public_product/h5_wechat_pay.py")
    questionnaire_source = _read("aicrm_next/questionnaire/repo.py")
    commerce_source = _read("aicrm_next/commerce/admin_transaction_detail.py")
    group_ops_source = _read("aicrm_next/automation_engine/group_ops/action_dispatcher.py")
    cloud_source = _read("aicrm_next/cloud_orchestrator/repository.py")
    channel_source = _read("aicrm_next/channel_entry/repo.py")
    scoped_sources = {
        "h5_wechat_pay_runtime": (
            _function_source(h5_source, "_paid_order_for_product_identity")
            + _function_source(h5_source, "_apply_transaction"),
            [
            "payer_openid = %s",
            "respondent_key = %s",
            "external_userid = %s",
            "userid_snapshot",
            "mobile_snapshot = %s",
            ],
        ),
        "questionnaire_admin_reads": (
            _function_source(questionnaire_source, "list_submissions")
            + _function_source(questionnaire_source, "list_external_submissions")
            + _function_source(questionnaire_source, "find_submission_for_identity"),
            [
            "qs.external_userid",
            "qs.mobile_snapshot",
            "qs.openid",
            "qs.respondent_key",
            ],
        ),
        "commerce_admin_transactions": (
            _function_source(commerce_source, "_postgres_filter_clause")
            + _function_source(commerce_source, "_postgres_order_select"),
            [
            "o.mobile_snapshot",
            "o.identity_snapshot",
            "o.buyer_id",
            "o.userid_snapshot",
            "o.external_userid",
            "o.respondent_key",
            ],
        ),
        "group_ops_external_effect_owner": (
            _function_source(group_ops_source, "enqueue_private_message"),
            [
            "target_external_userids",
            "'external_userid', '{}'::jsonb",
            ],
        ),
        "cloud_broadcast_planner": (
            cloud_source.split("def create_or_reuse_recipient_broadcast_jobs", 1)[1].split("def create_or_reuse_plan_broadcast_job", 1)[0],
            [
            "SELECT id, external_userid, display_name, owner_userid",
            "COALESCE(external_userid, '') <> ''",
            ],
        ),
        "channel_assignment_event": (
            _function_source(channel_source, "insert_assignment_event"),
            [
            "external_contact_id, wecom_user_id, source_payload_json",
            ],
        ),
        "customer_exact_external_lookup": (
            _function_source(_read("aicrm_next/customer_read_model/repo_live_source.py"), "_identity_by_external_userid"),
            [
            "CAST(external_userids_json AS TEXT) LIKE",
            ],
        ),
        "automation_agent_webhook_item_upsert": (
            _read("aicrm_next/automation_agents/repository.py"),
            [
            "ON CONFLICT (batch_id, unionid) DO UPDATE",
            ],
        ),
    }
    for label, (source, forbidden_tokens) in scoped_sources.items():
        for token in forbidden_tokens:
            assert token not in source, f"{label} still contains runtime SQL token: {token}"


def test_id_dev_runtime_baseline_migration_covers_exposed_missing_tables() -> None:
    source = _read("migrations/versions/0077_id_dev_runtime_baseline.py")

    assert 'down_revision = "0076_create_missing_baseline_runtime_tables"' in source
    for table_name in [
        "sidebar_customer_profile_fields",
        "wecom_customer_acquisition_links",
        "archived_messages",
        "archive_sync_state",
        "wechat_shop_orders",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in source
    assert "ADD COLUMN IF NOT EXISTS customer_name_snapshot" in source
    assert "customer_name_snapshot = COALESCE(NULLIF(customer_name_snapshot, ''), customer_name, '')" in source
    assert '"person_id", "mobile", "external_userid", "is_mobile_bound", "customer_name"' in source
    assert "DROP COLUMN IF EXISTS {column_name}" in source
    assert "buyer_mobile" in source
    assert "openid" in source


def test_identity_contact_resolve_reads_current_owner_column() -> None:
    source = _read("aicrm_next/identity_contact/resolver.py")
    application_source = _read("aicrm_next/identity_contact/application.py")
    binding_section = application_source.split("class GetSidebarContactBindingStatusQuery:", 1)[1].split("class BindMobileToExternalContactCommand:", 1)[0]

    assert "identity.primary_owner_userid AS owner_userid" in source
    assert "identity.identity_status AS status" in source
    assert "primary_follow_user_userid" not in source
    assert "identity_contact_fallback" in binding_section
    assert "customer_detail_error" in binding_section


def test_wechat_admin_order_list_projects_identity_from_unionid() -> None:
    source = _read("aicrm_next/commerce/admin_transactions.py")
    select_source = _function_source(source, "_postgres_order_select")
    orders_source = _function_source(source, "_postgres_orders")

    assert "crm_user_identity identity" in select_source
    assert "identity.unionid = wechat_pay_orders.unionid" in select_source
    assert "identity.mobile" in select_source
    assert "identity.primary_external_userid" in select_source
    for forbidden in ["wechat_pay_orders.mobile_snapshot", "wechat_pay_orders.external_userid", "wechat_pay_orders.userid_snapshot", "wechat_pay_orders.respondent_key"]:
        assert forbidden not in select_source
        assert forbidden not in orders_source


def test_sidebar_v2_reads_orders_and_messages_via_unionid_identity() -> None:
    source = _read("aicrm_next/customer_read_model/sidebar_v2.py")
    binding_source = _function_source(source, "get_contact_binding_status")
    bindable_source = _function_source(source, "get_bindable_wechat_pay_order_mobile")
    orders_source = _function_source(source, "list_customer_wechat_pay_orders")
    questionnaire_source = _function_source(source, "list_questionnaire_answers")
    messages_source = _function_source(source, "list_other_staff_messages")

    assert "_resolve_identity" in binding_source
    assert "FROM external_contact_bindings b" in binding_source
    assert "JOIN people" not in binding_source
    assert "b.first_bound_by_userid" not in binding_source
    assert "WITH identity_scope(unionid, mobile)" in bindable_source
    assert "WHERE COALESCE(m.unionid, '') <> ''" not in bindable_source
    assert "FROM wechat_pay_orders o" in bindable_source
    assert "JOIN identity_scope identity ON identity.unionid = o.unionid" in bindable_source
    assert "FROM wechat_shop_orders o" in orders_source
    assert "JOIN identity_scope identity ON identity.unionid = o.unionid" in orders_source
    assert "JOIN crm_user_identity identity ON identity.unionid = s.unionid" in questionnaire_source
    assert "JOIN crm_user_identity identity ON identity.unionid = message.unionid" in messages_source
    for forbidden in [
        "buyer_mobile",
        "openid AS payer",
        "questionnaire_submissions.external_userid",
        "archived_messages.external_userid",
        "wechat_pay_orders.mobile_snapshot",
    ]:
        assert forbidden not in source


def test_admin_read_transactions_projection_does_not_require_legacy_order_identity_columns() -> None:
    source = _read("aicrm_next/admin_read_model/projections.py")
    section = _function_source(source, "transactions_payload")

    assert "LEFT JOIN crm_user_identity identity ON identity.unionid = o.unionid" in section
    assert "identity.primary_external_userid" in section
    assert "NULLIF(external_userid" not in section
    assert "respondent_key" not in section
