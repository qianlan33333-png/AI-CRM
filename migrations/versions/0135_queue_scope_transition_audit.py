"""Audit fail-closed queue runtime scope transitions.

Revision ID: 0135_queue_scope_transition_audit
Revises: 0134_execution_timeline_graph_indexes
"""

from __future__ import annotations

from alembic import op


revision = "0135_queue_scope_transition_audit"
down_revision = "0134_execution_timeline_graph_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_runtime_scope_transition_audit (
            transition_id TEXT PRIMARY KEY,
            active_generation INTEGER NOT NULL,
            from_policy_version TEXT NOT NULL,
            to_policy_version TEXT NOT NULL,
            from_scope TEXT NOT NULL,
            to_scope TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            policy_json_before JSONB NOT NULL,
            policy_json_after JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_queue_scope_transition_direction CHECK (
                (from_scope = 'test_loopback' AND to_scope = 'allowlisted')
                OR (from_scope = 'allowlisted' AND to_scope = 'test_loopback')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_scope_transition_generation_created
        ON queue_runtime_scope_transition_audit (active_generation, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_runtime_canary_config_audit (
            config_audit_id TEXT PRIMARY KEY,
            active_generation INTEGER NOT NULL,
            policy_version TEXT NOT NULL,
            config_mode TEXT NOT NULL CHECK (config_mode IN ('enable', 'disable')),
            config_hash_before TEXT NOT NULL CHECK (length(config_hash_before) = 64),
            config_hash_after TEXT NOT NULL CHECK (length(config_hash_after) = 64),
            allowlist_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_queue_canary_config_generation_created
        ON queue_runtime_canary_config_audit (active_generation, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'queue runtime audit evidence is append-only';
        END;
        $$
        """
    )
    for table in (
        "queue_runtime_scope_transition_audit",
        "queue_runtime_canary_config_audit",
    ):
        trigger = f"trg_{table}_append_only"
        truncate_trigger = f"trg_{table}_reject_truncate"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {truncate_trigger} ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER {trigger}
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {truncate_trigger}
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT
            EXECUTE FUNCTION aicrm_reject_queue_runtime_audit_mutation()
            """
        )


def downgrade() -> None:
    # Production rollback keeps additive audit evidence readable.
    pass
