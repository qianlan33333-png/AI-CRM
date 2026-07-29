"""Add the stable internal-event admin pagination index.

Revision ID: 0154_internal_event_occurred_index
Revises: 0153_customer_read_model_generation_slots
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


revision = "0154_internal_event_occurred_index"
down_revision = "0153_customer_read_model_generation_slots"
branch_labels = None
depends_on = None


TABLE_NAME = "internal_event"
INDEX_NAME = "idx_internal_event_occurred_id"


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table(TABLE_NAME):
        return
    statement = (
        f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
        f"ON {TABLE_NAME} (occurred_at DESC, id DESC)"
    )
    if bind.dialect.name == "postgresql":
        statement = statement.replace("CREATE INDEX", "CREATE INDEX CONCURRENTLY", 1)
        with op.get_context().autocommit_block():
            op.execute(statement)
        return
    op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
        return
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
