"""Prepare durable keys for incremental Customer 360 projection refreshes.

Revision ID: 0152_customer_read_model_incremental
Revises: 0151_ai_audience_hxc_projection_view
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0152_customer_read_model_incremental"
down_revision = "0151_ai_audience_hxc_projection_view"
branch_labels = None
depends_on = None


LIST_UNIONID_INDEX = "uq_customer_list_index_next_unionid"
DETAIL_UNIONID_INDEX = "uq_customer_detail_snapshot_next_unionid"
RECEIPT_GENERATION_INDEX = "idx_customer_refresh_source_generation_id"


def _create_index(*, postgres: str, portable: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(postgres)
        return
    op.execute(portable)


def _synchronize_sequence(table_name: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        SELECT setval(
            pg_get_serial_sequence('{table_name}', 'id'),
            COALESCE((SELECT MAX(id) FROM {table_name}), 1),
            EXISTS (SELECT 1 FROM {table_name})
        )
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "customer_read_model_refresh_source_receipt" in table_names:
        receipt_columns = {
            str(column["name"])
            for column in inspector.get_columns("customer_read_model_refresh_source_receipt")
        }
        if "source_event_id" not in receipt_columns:
            op.execute(
                """
                ALTER TABLE customer_read_model_refresh_source_receipt
                ADD COLUMN source_event_id TEXT NOT NULL DEFAULT ''
                """
            )
        _create_index(
            postgres=f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {RECEIPT_GENERATION_INDEX} "
            "ON customer_read_model_refresh_source_receipt (generation, id)",
            portable=f"CREATE INDEX IF NOT EXISTS {RECEIPT_GENERATION_INDEX} "
            "ON customer_read_model_refresh_source_receipt (generation, id)",
        )

    for table_name in (
        "customer_list_index_next",
        "customer_detail_snapshot_next",
        "customer_recent_message_next",
    ):
        if table_name in table_names:
            _synchronize_sequence(table_name)

    if "customer_list_index_next" in table_names:
        _create_index(
            postgres=f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {LIST_UNIONID_INDEX} "
            "ON customer_list_index_next (unionid)",
            portable=f"CREATE UNIQUE INDEX IF NOT EXISTS {LIST_UNIONID_INDEX} "
            "ON customer_list_index_next (unionid)",
        )
    if "customer_detail_snapshot_next" in table_names:
        _create_index(
            postgres=f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {DETAIL_UNIONID_INDEX} "
            "ON customer_detail_snapshot_next (unionid)",
            portable=f"CREATE UNIQUE INDEX IF NOT EXISTS {DETAIL_UNIONID_INDEX} "
            "ON customer_detail_snapshot_next (unionid)",
        )


def downgrade() -> None:
    # These keys protect the incremental writer that is released separately.
    # A code rollback remains compatible with the additive column and indexes;
    # removing them requires its own observed migration.
    pass
