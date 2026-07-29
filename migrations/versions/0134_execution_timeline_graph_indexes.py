"""Index explicit parent links used by the bounded execution graph timeline.

Revision ID: 0134_execution_timeline_graph_indexes
Revises: 0133_sidebar_customer_timeline
"""

from __future__ import annotations

from alembic import op


revision = "0134_execution_timeline_graph_indexes"
down_revision = "0133_sidebar_customer_timeline"
branch_labels = None
depends_on = None


PARENT_EXECUTION_INDEXES = (
    (
        "idx_external_effect_parent_execution",
        "external_effect_job",
    ),
    (
        "idx_internal_event_parent_execution",
        "internal_event",
    ),
    (
        "idx_internal_run_parent_execution",
        "internal_event_consumer_run",
    ),
    (
        "idx_internal_outbox_parent_execution",
        "internal_event_outbox",
    ),
    (
        "idx_webhook_inbox_parent_execution",
        "webhook_inbox",
    ),
)


def _require_standby() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM queue_runtime_control
                WHERE singleton = TRUE
                  AND (active_generation <> 0 OR claim_enabled)
            ) THEN
                RAISE EXCEPTION 'execution timeline index migration requires standby generation 0';
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    _require_standby()
    for index_name, table_name in PARENT_EXECUTION_INDEXES:
        op.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {index_name}
            ON {table_name} (parent_execution_id, execution_id)
            WHERE parent_execution_id <> ''
            """
        )


def downgrade() -> None:
    _require_standby()
    for index_name, _table_name in reversed(PARENT_EXECUTION_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
