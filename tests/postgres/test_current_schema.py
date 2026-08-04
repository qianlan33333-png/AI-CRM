from __future__ import annotations

import pytest


pytestmark = pytest.mark.postgres


CRITICAL_CURRENT_TABLES = {
    "contacts",
    "crm_user_identity",
    "customer_list_index_next",
    "customer_timeline_event_next",
    "automation_agents",
    "external_effect_job",
    "internal_event",
    "webhook_inbox",
    "questionnaires",
    "wechat_pay_orders",
    "service_period_entitlements",
    "data_health_snapshot",
    "config_releases",
    "ai_audience_package",
}


def test_current_schema_contains_each_live_domain(pg_connection) -> None:
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = {row[0] for row in cursor.fetchall()}
    assert CRITICAL_CURRENT_TABLES <= tables


def test_identity_and_queue_columns_match_current_runtime_contract(pg_connection) -> None:
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN ('crm_user_identity', 'external_effect_job', 'webhook_inbox')
            """
        )
        columns: dict[str, set[str]] = {}
        for table_name, column_name in cursor.fetchall():
            columns.setdefault(table_name, set()).add(column_name)
    assert {"unionid", "primary_external_userid", "primary_openid"} <= columns["crm_user_identity"]
    assert {"tenant_id", "idempotency_key", "lane", "lease_token", "status"} <= columns["external_effect_job"]
    assert {"idempotency_key", "status", "attempt_count"} <= columns["webhook_inbox"]


def test_external_effect_idempotency_is_enforced_by_postgres(pg_connection) -> None:
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'external_effect_job'
            """
        )
        definitions = [row[0].lower().replace('"', '') for row in cursor.fetchall()]
    assert any("unique" in definition and "tenant_id" in definition and "idempotency_key" in definition for definition in definitions)


def test_product_wecom_tagging_configuration_is_in_current_schema(pg_connection) -> None:
    with pg_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'wechat_pay_products'
              AND column_name = 'wecom_tagging_json'
            """
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == "jsonb"
    assert row[1] == "NO"
    assert "enabled" in str(row[2])
