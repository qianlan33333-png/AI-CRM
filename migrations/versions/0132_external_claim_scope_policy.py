"""Persist the external canary claim scope in PostgreSQL policy.

Revision ID: 0132_external_claim_scope_policy
Revises: 0131_external_effect_continuation_fanout
"""

from __future__ import annotations

from alembic import op


revision = "0132_external_claim_scope_policy"
down_revision = "0131_external_effect_continuation_fanout"
branch_labels = None
depends_on = None


POLICY_VERSION = "queue-v2-test-loopback"
PREVIOUS_POLICY_VERSION = "queue-v1"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM queue_runtime_control
                WHERE singleton = TRUE
                  AND (active_generation <> 0 OR claim_enabled)
            ) THEN
                RAISE EXCEPTION 'external claim scope migration requires standby generation 0';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        ALTER TABLE queue_runtime_control
        ADD COLUMN IF NOT EXISTS external_claim_scope TEXT NOT NULL DEFAULT 'test_loopback'
        """
    )
    op.execute(
        """
        ALTER TABLE queue_runtime_control
        DROP CONSTRAINT IF EXISTS ck_queue_runtime_external_claim_scope,
        ADD CONSTRAINT ck_queue_runtime_external_claim_scope
            CHECK (external_claim_scope IN ('blocked', 'test_loopback', 'allowlisted', 'all'))
        """
    )
    op.execute(
        f"""
        INSERT INTO queue_policy_snapshot (
            policy_version, policy_json, created_by, created_reason
        ) VALUES (
            '{POLICY_VERSION}',
            jsonb_build_object(
                'heartbeat_seconds', 10,
                'lease_ttl_seconds', 30,
                'fallback_drain_seconds', 30,
                'external_claim_scope', 'test_loopback',
                'external_scope_modes', jsonb_build_array(
                    'blocked', 'test_loopback', 'allowlisted', 'all'
                ),
                'outbound_webhook_default', 'blocked',
                'lane_capacities', jsonb_build_object(
                    'internal_general', 4,
                    'internal_financial', 1,
                    'webhook_inbox', 4,
                    'wecom_interactive', 4,
                    'wecom_bulk', 1,
                    'wecom_media', 2,
                    'outbound_webhook', 4
                )
            ),
            'migration',
            'PR-3 fail-closed external canary scope is a durable database policy'
        )
        ON CONFLICT (policy_version) DO NOTHING
        """
    )
    op.execute(
        f"""
        UPDATE queue_runtime_control
        SET external_claim_scope = 'test_loopback',
            policy_version = '{POLICY_VERSION}',
            updated_by = 'migration',
            updated_reason = 'durable external test-loopback claim scope',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )
    op.execute(
        f"""
        UPDATE queue_lane_policy
        SET policy_version = '{POLICY_VERSION}',
            updated_by = 'migration',
            updated_reason = 'durable external test-loopback claim scope',
            updated_at = CURRENT_TIMESTAMP
        """
    )
    for table in (
        "external_effect_job",
        "internal_event_consumer_run",
        "internal_event_outbox",
        "webhook_inbox",
    ):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN policy_version SET DEFAULT '{POLICY_VERSION}'"
        )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM queue_runtime_control
                WHERE singleton = TRUE
                  AND (active_generation <> 0 OR claim_enabled)
            ) THEN
                RAISE EXCEPTION 'external claim scope downgrade requires standby generation 0';
            END IF;
        END;
        $$
        """
    )
    for table in (
        "external_effect_job",
        "internal_event_consumer_run",
        "internal_event_outbox",
        "webhook_inbox",
    ):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN policy_version SET DEFAULT '{PREVIOUS_POLICY_VERSION}'"
        )
    op.execute(
        f"""
        UPDATE queue_lane_policy
        SET policy_version = '{PREVIOUS_POLICY_VERSION}',
            updated_by = 'migration-downgrade',
            updated_reason = 'restore previous standby policy',
            updated_at = CURRENT_TIMESTAMP
        """
    )
    op.execute(
        f"""
        UPDATE queue_runtime_control
        SET policy_version = '{PREVIOUS_POLICY_VERSION}',
            updated_by = 'migration-downgrade',
            updated_reason = 'restore previous standby policy',
            updated_at = CURRENT_TIMESTAMP
        WHERE singleton = TRUE
        """
    )
    op.execute(
        """
        ALTER TABLE queue_runtime_control
        DROP CONSTRAINT IF EXISTS ck_queue_runtime_external_claim_scope,
        DROP COLUMN IF EXISTS external_claim_scope
        """
    )
